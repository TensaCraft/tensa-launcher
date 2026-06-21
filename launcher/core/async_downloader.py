"""
Асинхронний завантажувач файлів з підтримкою паралельних завантажень.

Забезпечує швидке завантаження через ThreadPoolExecutor.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import requests
    from requests.adapters import HTTPAdapter

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    HTTPAdapter = None

from launcher.models.logger import Logger


class DownloadTask:
    """Задача завантаження файлу."""

    def __init__(
        self,
        url: str,
        destination: Path,
        expected_size: Optional[int] = None,
        expected_sha1: Optional[str] = None,
        expected_hash: Optional[str] = None,
        expected_hash_algorithm: Optional[str] = None,
        task_id: Optional[str] = None,
        post_data: Optional[Dict] = None,
        use_requests: bool = False
    ):
        self.url = url
        self.destination = Path(destination)
        self.expected_size = expected_size
        self.expected_sha1 = expected_sha1
        self.expected_hash = (expected_hash or expected_sha1 or "").strip().lower() or None
        self.expected_hash_algorithm = (
            (expected_hash_algorithm or ("sha1" if expected_sha1 else "")).strip().lower() or None
        )
        self.task_id = task_id or url
        self.post_data = post_data  # Для POST запитів (TensaCraft API)
        self.use_requests = use_requests or post_data is not None
        self.downloaded: bool = False
        self.error: str | None = None


class AsyncDownloader:
    """Асинхронний завантажувач з підтримкою паралельних завантажень."""

    def __init__(
        self,
        max_workers: int = 4,
        chunk_size: int = 262144,
        max_retries: int = 3,
        retry_delay: float = 0.25,
    ):
        """
        Args:
            max_workers: Максимальна кількість паралельних завантажень
            chunk_size: Розмір чанку для завантаження (байти)
        """
        self.max_workers = max(1, max_workers)
        self.chunk_size = chunk_size
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.0, retry_delay)
        self.user_agent = "launcher/2.0"
        self._thread_local = threading.local()

    def download_files(
        self,
        tasks: List[DownloadTask],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        skip_existing: bool = True,
        verify_existing_sha1: bool = False,
    ) -> Dict[str, Any]:
        """
        Завантажує список файлів паралельно.

        Args:
            tasks: Список задач завантаження
            progress_callback: Callback(completed, total, current_file)
            skip_existing: Пропускати існуючі файли

        Returns:
            Dict з результатами:
            {
                'success': int,
                'failed': int,
                'skipped': int,
                'errors': List[str]
            }
        """
        result = {'success': 0, 'failed': 0, 'skipped': 0, 'errors': []}
        total = len(tasks)
        completed = 0
        tasks_to_download: List[DownloadTask] = []

        for task in tasks:
            task.downloaded = False
            task.error = None
            if skip_existing and self._should_skip(task, verify_sha1=verify_existing_sha1):
                result['skipped'] += 1
                completed += 1
                self._notify_progress(progress_callback, completed, total, f"Skipped: {task.destination.name}")
                continue
            tasks_to_download.append(task)

        if not tasks_to_download:
            Logger.info(f"All {total} files already exist, skipping download")
            return result

        Logger.info(f"Downloading {len(tasks_to_download)} files ({result['skipped']} skipped)")
        Logger.info(f"Using {self.max_workers} parallel workers")

        # Завантажуємо паралельно
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(self._download_file, task): task
                for task in tasks_to_download
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                completed += 1
                success = False
                error_message: Optional[str] = None
                try:
                    success = bool(future.result())
                except Exception as exc:
                    error_message = str(exc)
                    Logger.error(f"Unexpected error downloading {task.destination.name}: {error_message}")
                    task.error = error_message
                finally:
                    if success:
                        result['success'] += 1
                        task.downloaded = True
                    else:
                        result['failed'] += 1
                        message = task.error or error_message
                        if message:
                            result['errors'].append(f"{task.destination.name}: {message}")
                    status = "Downloaded" if success else "Failed"
                    self._notify_progress(progress_callback, completed, total, f"{status}: {task.destination.name}")

        Logger.info(
            f"Download complete: {result['success']} success, "
            f"{result['failed']} failed, {result['skipped']} skipped"
        )

        return result

    def _should_skip(self, task: DownloadTask, *, verify_sha1: bool = False) -> bool:
        """Перевіряє чи треба пропустити завантаження."""
        if not task.destination.exists():
            return False

        # Перевірка розміру якщо вказано
        if task.expected_size:
            actual_size = task.destination.stat().st_size
            if actual_size != task.expected_size:
                Logger.debug(
                    f"Size mismatch for {task.destination.name}: "
                    f"expected {task.expected_size}, got {actual_size}"
                )
                return False

        # SHA1 check is expensive for large modpacks. Make it opt-in for existing files;
        # downloaded files are still validated in _validate_and_move().
        if verify_sha1 and task.expected_hash and task.expected_hash_algorithm:
            actual_hash = self._calculate_hash(task.destination, task.expected_hash_algorithm)
            if actual_hash != task.expected_hash:
                Logger.debug(
                    f"{task.expected_hash_algorithm.upper()} mismatch for {task.destination.name}: "
                    f"expected {task.expected_hash}, got {actual_hash}"
                )
                return False

        return True

    def _download_file(self, task: DownloadTask) -> bool:
        """
        Завантажує один файл.

        Returns:
            bool: True якщо успішно
        """
        # Для HTTPS в packaged runtime надійніше покладатися на requests/certifi,
        # ніж на urllib/OpenSSL lookup з локального оточення.
        if HAS_REQUESTS and (task.use_requests or task.url.lower().startswith("https://")):
            return self._download_file_requests(task)
        else:
            return self._download_file_urllib(task)

    @staticmethod
    def _notify_progress(
        callback: Optional[Callable[[int, int, str], None]],
        completed: int,
        total: int,
        message: str
    ) -> None:
        if callback:
            callback(completed, total, message)

    def _download_file_urllib(self, task: DownloadTask) -> bool:
        """Завантаження через urllib (для GET запитів)."""
        temp_file = self._temp_file_for(task, attempt=1)
        try:
            # Створюємо батьківські директорії
            task.destination.parent.mkdir(parents=True, exist_ok=True)

            # Створюємо request з user agent
            request = Request(task.url)
            request.add_header('User-Agent', self.user_agent)

            with urlopen(request, timeout=30) as response:
                with open(temp_file, 'wb') as f:
                    while True:
                        chunk = response.read(self.chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)

            # Валідація та переміщення
            return self._validate_and_move(task, temp_file)

        except (URLError, HTTPError) as e:
            task.error = f"Network error: {e}"
            self._safe_unlink(temp_file)
            Logger.error(f"Failed to download {task.url}: {e}")
            return False
        except Exception as e:
            task.error = f"Error: {e}"
            self._safe_unlink(temp_file)
            Logger.error(f"Failed to download {task.destination.name}: {e}")
            return False

    def _download_file_requests(self, task: DownloadTask) -> bool:
        """Завантаження через requests (для POST/GET запитів)."""
        # Створюємо батьківські директорії
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self._partial_file_for(task)

        for attempt in range(1, self.max_retries + 1):
            try:
                if task.expected_size and temp_file.exists():
                    partial_size = temp_file.stat().st_size
                    if partial_size > task.expected_size:
                        self._safe_unlink(temp_file)
                    elif partial_size == task.expected_size and self._validate_and_move(task, temp_file):
                        return True

                resume_from = temp_file.stat().st_size if temp_file.exists() else 0
                can_resume = bool(resume_from and not task.post_data)
                headers = {
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "identity",
                }
                if can_resume:
                    headers["Range"] = f"bytes={resume_from}-"

                session = self._requests_session()
                request_kwargs = {
                    "headers": headers,
                    "stream": True,
                    "timeout": 30,
                }

                # POST або GET запит
                if task.post_data:
                    response = session.post(
                        task.url,
                        data=task.post_data,
                        **request_kwargs,
                    )
                else:
                    response = session.get(
                        task.url,
                        **request_kwargs,
                    )

                with response:
                    response.raise_for_status()
                    append = can_resume and getattr(response, "status_code", None) == 206
                    if can_resume and not append:
                        Logger.warning(
                            f"Download server did not resume {task.destination.name}; restarting from zero"
                        )
                        self._safe_unlink(temp_file)

                    # Завантаження по чанках
                    with open(temp_file, 'ab' if append else 'wb') as f:
                        for chunk in response.iter_content(chunk_size=self.chunk_size):
                            if chunk:
                                f.write(chunk)

                # Валідація та переміщення
                if self._validate_and_move(task, temp_file):
                    return True

                if attempt == self.max_retries:
                    return False

                Logger.warning(
                    f"Retrying download {task.destination.name} after validation failure "
                    f"({attempt}/{self.max_retries}): {task.error}"
                )

            except requests.RequestException as e:
                task.error = f"Network error: {e}"
                if attempt == self.max_retries:
                    Logger.error(f"Failed to download {task.url}: {e}")
                    return False

                Logger.warning(
                    f"Retrying download {task.destination.name} after network error "
                    f"({attempt}/{self.max_retries}): {e}"
                )

            except Exception as e:
                task.error = f"Error: {e}"
                self._safe_unlink(temp_file)
                Logger.error(f"Failed to download {task.destination.name}: {e}")
                return False

            if self.retry_delay:
                time.sleep(self.retry_delay)

        return False

    def _requests_session(self):
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            return session

        if HTTPAdapter is None:
            raise RuntimeError("requests HTTP adapter is unavailable")

        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.max_workers,
            pool_maxsize=self.max_workers,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": self.user_agent})
        self._thread_local.session = session
        return session

    def _validate_and_move(self, task: DownloadTask, temp_file: Path) -> bool:
        """Валідація та переміщення завантаженого файлу."""
        try:
            # Перевірка розміру
            if task.expected_size:
                actual_size = temp_file.stat().st_size
                if actual_size != task.expected_size:
                    task.error = f"Size mismatch: expected {task.expected_size}, got {actual_size}"
                    self._safe_unlink(temp_file)
                    return False

            # Перевірка хешу
            if task.expected_hash and task.expected_hash_algorithm:
                actual_hash = self._calculate_hash(temp_file, task.expected_hash_algorithm)
                if actual_hash != task.expected_hash:
                    task.error = (
                        f"{task.expected_hash_algorithm.upper()} mismatch: "
                        f"expected {task.expected_hash}, got {actual_hash}"
                    )
                    self._safe_unlink(temp_file)
                    return False

            # Переміщуємо тимчасовий файл на місце оригіналу
            shutil.move(str(temp_file), str(task.destination))

            Logger.debug(f"Downloaded: {task.destination.name}")
            return True

        except Exception as e:
            task.error = f"Validation error: {e}"
            self._safe_unlink(temp_file)
            return False

    def _temp_file_for(self, task: DownloadTask, attempt: int) -> Path:
        token = f"{threading.get_ident()}.{attempt}.{time.time_ns()}"
        return task.destination.with_name(f"{task.destination.name}.{token}.tmp")

    @staticmethod
    def _partial_file_for(task: DownloadTask) -> Path:
        return task.destination.with_name(f"{task.destination.name}.part.tmp")

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            try:
                Logger.debug(f"Could not remove temporary download file {path}: {exc}")
            except Exception:
                return

    @staticmethod
    def _calculate_hash(file_path: Path, algorithm: str) -> str:
        """Обчислює хеш файлу для підтримуваного алгоритму."""
        hasher = hashlib.new(algorithm)
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()


def download_file_simple(
    url: str,
    destination: Path,
    expected_size: Optional[int] = None,
    expected_sha1: Optional[str] = None
) -> bool:
    """
    Простий синхронний завантажувач для одного файлу.

    Returns:
        bool: True якщо успішно
    """
    downloader = AsyncDownloader(max_workers=1)
    task = DownloadTask(url, destination, expected_size, expected_sha1)
    result = downloader.download_files([task], skip_existing=True)
    return result['success'] == 1


__all__ = ['AsyncDownloader', 'DownloadTask', 'download_file_simple']
