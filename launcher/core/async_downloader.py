"""
Асинхронний завантажувач файлів з підтримкою паралельних завантажень.

Забезпечує швидке завантаження через ThreadPoolExecutor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter

from launcher.application.storage_preflight import (
    StoragePreflightError,
    StorageRequest,
    ensure_storage_available,
)
from launcher.models.logger import Logger

DEFAULT_MAX_DOWNLOAD_SIZE = 8 * 1024 * 1024 * 1024
_PARTIAL_METADATA_SCHEMA = 1
_PARTIAL_FILE_IDENTITY_LENGTH = 24
_CONTENT_RANGE_PATTERN = re.compile(
    r"^bytes\s+(\d+)-(\d+)/(\d+)$",
    re.IGNORECASE,
)


class _DownloadSizeLimitExceeded(RuntimeError):
    """Raised before a response chunk would exceed the configured byte ceiling."""


class DownloadTask:
    """Задача завантаження файлу."""

    def __init__(
        self,
        url: str,
        destination: Path,
        expected_size: Optional[int] = None,
        expected_hash: Optional[str] = None,
        expected_hash_algorithm: Optional[str] = None,
        task_id: Optional[str] = None,
        post_data: Optional[Dict] = None,
    ):
        self.url = url
        self.destination = Path(destination)
        self.expected_size = expected_size
        self.expected_hash = (expected_hash or "").strip().lower() or None
        self.expected_hash_algorithm = (expected_hash_algorithm or "").strip().lower() or None
        self.task_id = task_id or url
        self.post_data = post_data  # Для POST запитів (TensaCraft API)
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
        max_download_size: Optional[int] = DEFAULT_MAX_DOWNLOAD_SIZE,
    ):
        """
        Args:
            max_workers: Максимальна кількість паралельних завантажень
            chunk_size: Розмір чанку для завантаження (байти)
            max_download_size: Максимальний розмір завантаження без expected_size.
                None вимикає резервне обмеження для довірених джерел.
        """
        self.max_workers = max(1, max_workers)
        self.chunk_size = chunk_size
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.0, retry_delay)
        if max_download_size is not None and max_download_size <= 0:
            raise ValueError("max_download_size must be positive or None")
        self.max_download_size = max_download_size
        self.user_agent = "launcher/2.0"
        self._thread_local = threading.local()

    def download_files(
        self,
        tasks: List[DownloadTask],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        skip_existing: bool = True,
        verify_existing_sha1: bool = False,
        verify_existing_hash: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Завантажує список файлів паралельно.

        Args:
            tasks: Список задач завантаження
            progress_callback: Callback(completed, total, current_file)
            skip_existing: Пропускати існуючі файли
            verify_existing_sha1: Застаріле ім'я параметра перевірки будь-якого хешу.
            verify_existing_hash: Перевіряти хеш існуючого файлу перед пропуском.

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
        should_verify_existing_hash = (
            verify_existing_sha1
            if verify_existing_hash is None
            else verify_existing_hash
        )

        for task in tasks:
            task.downloaded = False
            task.error = None
            if skip_existing and self._should_skip(
                task,
                verify_hash=should_verify_existing_hash,
            ):
                result['skipped'] += 1
                completed += 1
                self._notify_progress(progress_callback, completed, total, f"Skipped: {task.destination.name}")
                continue
            tasks_to_download.append(task)

        if not tasks_to_download:
            Logger.info(f"All {total} files already exist, skipping download")
            return result

        try:
            ensure_storage_available(
                [
                    StorageRequest(
                        target=task.destination,
                        required_bytes=max(0, int(task.expected_size or 0)),
                        label="download",
                    )
                    for task in tasks_to_download
                ]
            )
        except StoragePreflightError as exc:
            message = str(exc)
            Logger.error(f"Download preflight failed: {message}")
            for task in tasks_to_download:
                task.error = message
                result["failed"] += 1
                result["errors"].append(f"{task.destination.name}: {message}")
                completed += 1
                self._notify_progress(
                    progress_callback,
                    completed,
                    total,
                    f"Failed: {task.destination.name}",
                )
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

    def _should_skip(
        self,
        task: DownloadTask,
        *,
        verify_sha1: bool = False,
        verify_hash: Optional[bool] = None,
    ) -> bool:
        """Перевіряє чи треба пропустити завантаження."""
        if not task.destination.exists():
            return False

        # Перевірка розміру якщо вказано
        if task.expected_size is not None:
            actual_size = task.destination.stat().st_size
            if actual_size != task.expected_size:
                Logger.debug(
                    f"Size mismatch for {task.destination.name}: "
                    f"expected {task.expected_size}, got {actual_size}"
                )
                return False

        # Hashing is expensive for large modpacks. Make it opt-in for existing files;
        # downloaded files are still validated in _validate_and_move().
        should_verify_hash = verify_sha1 if verify_hash is None else verify_hash
        if should_verify_hash and task.expected_hash and task.expected_hash_algorithm:
            actual_hash = self._calculate_hash(task.destination, task.expected_hash_algorithm)
            if actual_hash != task.expected_hash:
                Logger.debug(
                    f"{task.expected_hash_algorithm.upper()} mismatch for {task.destination.name}: "
                    f"expected {task.expected_hash}, got {actual_hash}"
                )
                return False

        return True

    @staticmethod
    def _notify_progress(
        callback: Optional[Callable[[int, int, str], None]],
        completed: int,
        total: int,
        message: str
    ) -> None:
        if callback:
            callback(completed, total, message)

    def _download_file(self, task: DownloadTask) -> bool:
        """Завантажує файл через requests GET або POST."""
        # Створюємо батьківські директорії
        task.destination.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, self.max_retries + 1):
            temp_file = self._partial_file_for(task)
            try:
                temp_file = self._prepare_partial_file(task)
                size_limit = self._download_limit(task)
                if temp_file.exists():
                    partial_size = temp_file.stat().st_size
                    if size_limit is not None and partial_size > size_limit:
                        self._clear_partial_state(task, temp_file, remove_partial=True)
                        temp_file = self._prepare_partial_file(task)
                    elif (
                        task.expected_size is not None
                        and partial_size == task.expected_size
                    ):
                        if self._validate_and_move(task, temp_file):
                            self._clear_partial_state(task, temp_file)
                            return True
                        self._clear_partial_state(task, temp_file)
                        temp_file = self._prepare_partial_file(task)

                session = self._requests_session()
                restarted_unsafe_resume = False
                while True:
                    resume_from = temp_file.stat().st_size if temp_file.exists() else 0
                    metadata = self._read_partial_metadata(
                        self._partial_metadata_file_for(task)
                    )
                    resume_validator = self._resume_validator(metadata)
                    can_resume = bool(
                        resume_from
                        and task.post_data is None
                        and resume_validator is not None
                    )
                    if resume_from and not can_resume:
                        Logger.warning(
                            f"Partial download for {task.destination.name} has no "
                            "safe validator; restarting from zero"
                        )
                        self._clear_partial_state(
                            task,
                            temp_file,
                            remove_partial=True,
                        )
                        temp_file = self._prepare_partial_file(task)
                        resume_from = 0

                    headers = {
                        "User-Agent": self.user_agent,
                        "Accept-Encoding": "identity",
                    }
                    if can_resume and resume_validator is not None:
                        headers["Range"] = f"bytes={resume_from}-"
                        headers["If-Range"] = resume_validator[1]

                    request = session.post if task.post_data is not None else session.get
                    request_kwargs: dict[str, Any] = {
                        "headers": headers,
                        "stream": True,
                        "timeout": 30,
                    }
                    if task.post_data is not None:
                        request_kwargs["data"] = task.post_data
                    response = request(task.url, **request_kwargs)

                    restart_request = False
                    with response:
                        response.raise_for_status()
                        status_code = getattr(response, "status_code", None)
                        append = False
                        resumed_range: Optional[tuple[int, int, int]] = None

                        if can_resume and status_code == 206:
                            resumed_range = self._validated_resume_range(
                                task,
                                response,
                                resume_from,
                                resume_validator,
                                size_limit,
                            )
                            if resumed_range is None:
                                restart_request = True
                        elif can_resume:
                            Logger.warning(
                                f"Download server did not safely resume "
                                f"{task.destination.name}; restarting from zero"
                            )
                            self._clear_partial_state(
                                task,
                                temp_file,
                                remove_partial=True,
                            )
                            temp_file = self._prepare_partial_file(task)
                        elif status_code == 206:
                            Logger.warning(
                                f"Download server returned an unsolicited partial "
                                f"response for {task.destination.name}"
                            )
                            restart_request = True

                        if restart_request:
                            self._clear_partial_state(
                                task,
                                temp_file,
                                remove_partial=True,
                            )
                            temp_file = self._prepare_partial_file(task)
                        else:
                            append = resumed_range is not None
                            self._store_response_validators(task, temp_file, response)
                            downloaded_size = resume_from if append else 0

                            # Завантаження по чанках
                            with open(temp_file, 'ab' if append else 'wb') as f:
                                for chunk in response.iter_content(
                                    chunk_size=self.chunk_size
                                ):
                                    if chunk:
                                        downloaded_size = self._checked_download_size(
                                            task,
                                            downloaded_size,
                                            len(chunk),
                                            size_limit,
                                        )
                                        f.write(chunk)

                            if (
                                resumed_range is not None
                                and downloaded_size != resumed_range[1] + 1
                            ):
                                task.error = (
                                    "Incomplete resumed response: expected byte "
                                    f"{resumed_range[1]}, got {downloaded_size - 1}"
                                )
                                if attempt == self.max_retries:
                                    return False
                                Logger.warning(
                                    f"Retrying incomplete resumed download "
                                    f"{task.destination.name} "
                                    f"({attempt}/{self.max_retries})"
                                )
                                break

                    if restart_request:
                        if restarted_unsafe_resume:
                            task.error = "Server returned an unsafe partial response"
                            break
                        restarted_unsafe_resume = True
                        continue
                    break

                # Валідація та переміщення
                if self._validate_and_move(task, temp_file):
                    self._clear_partial_state(task, temp_file)
                    return True
                self._clear_partial_state(task, temp_file)

                if attempt == self.max_retries:
                    return False

                Logger.warning(
                    f"Retrying download {task.destination.name} after validation failure "
                    f"({attempt}/{self.max_retries}): {task.error}"
                )

            except _DownloadSizeLimitExceeded as e:
                task.error = str(e)
                self._clear_partial_state(task, temp_file, remove_partial=True)
                Logger.error(f"Failed to download {task.destination.name}: {e}")
                return False
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
                self._clear_partial_state(task, temp_file, remove_partial=True)
                Logger.error(f"Failed to download {task.destination.name}: {e}")
                return False

            if self.retry_delay:
                time.sleep(self.retry_delay)

        return False

    def _requests_session(self):
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            return session

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
            if task.expected_size is not None:
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

            # Keep the existing file intact if Windows refuses to replace it
            # while Minecraft or antivirus still has the jar open.
            os.replace(str(temp_file), str(task.destination))

            Logger.debug(f"Downloaded: {task.destination.name}")
            return True

        except Exception as e:
            task.error = f"Validation error: {e}"
            self._safe_unlink(temp_file)
            return False

    def _download_limit(self, task: DownloadTask) -> Optional[int]:
        if task.expected_size is not None:
            return task.expected_size
        return self.max_download_size

    @staticmethod
    def _checked_download_size(
        task: DownloadTask,
        current_size: int,
        chunk_size: int,
        size_limit: Optional[int],
    ) -> int:
        next_size = current_size + chunk_size
        if size_limit is not None and next_size > size_limit:
            raise _DownloadSizeLimitExceeded(
                f"Download size limit exceeded: limit {size_limit} bytes "
                f"for {task.destination.name}"
            )
        return next_size

    @staticmethod
    def _partial_identity(task: DownloadTask) -> str:
        identity = {
            "url": task.url,
            "expected_size": task.expected_size,
            "expected_hash": task.expected_hash,
            "expected_hash_algorithm": task.expected_hash_algorithm,
            "method": "POST" if task.post_data is not None else "GET",
            "post_data": task.post_data,
        }
        encoded = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _partial_file_for(cls, task: DownloadTask) -> Path:
        identity = cls._partial_identity(task)[:_PARTIAL_FILE_IDENTITY_LENGTH]
        return task.destination.with_name(
            f"{task.destination.name}.part.{identity}.tmp"
        )

    @staticmethod
    def _partial_metadata_file_for(task: DownloadTask) -> Path:
        return task.destination.with_name(f"{task.destination.name}.part.json")

    def _prepare_partial_file(self, task: DownloadTask) -> Path:
        partial_file = self._partial_file_for(task)
        metadata_file = self._partial_metadata_file_for(task)
        identity = self._partial_identity(task)
        legacy_partial = task.destination.with_name(
            f"{task.destination.name}.part.tmp"
        )
        self._safe_unlink(legacy_partial)

        metadata = self._read_partial_metadata(metadata_file)
        if metadata is not None:
            metadata_identity = metadata.get("identity")
            metadata_name = metadata.get("partial_file")
            if (
                metadata_identity != identity
                or metadata_name != partial_file.name
            ):
                stale_partial = self._partial_from_metadata(task, metadata_name)
                if stale_partial is not None:
                    self._safe_unlink(stale_partial)
                self._safe_unlink(metadata_file)
                metadata = None

        prepared_metadata = dict(metadata or {})
        prepared_metadata.update(
            {
                "schema": _PARTIAL_METADATA_SCHEMA,
                "identity": identity,
                "partial_file": partial_file.name,
            }
        )
        self._write_partial_metadata(metadata_file, prepared_metadata)
        return partial_file

    @staticmethod
    def _response_header(response: Any, name: str) -> Optional[str]:
        value = response.headers.get(name)
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _resume_validator(
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[tuple[str, str]]:
        if metadata is None:
            return None
        etag = metadata.get("etag")
        if isinstance(etag, str):
            etag = etag.strip()
            if etag and not etag.lower().startswith("w/"):
                return "etag", etag
        last_modified = metadata.get("last_modified")
        if isinstance(last_modified, str):
            last_modified = last_modified.strip()
            if last_modified:
                return "last_modified", last_modified
        return None

    def _store_response_validators(
        self,
        task: DownloadTask,
        partial_file: Path,
        response: Any,
    ) -> None:
        metadata_file = self._partial_metadata_file_for(task)
        metadata = self._read_partial_metadata(metadata_file) or {}
        metadata.update(
            {
                "schema": _PARTIAL_METADATA_SCHEMA,
                "identity": self._partial_identity(task),
                "partial_file": partial_file.name,
            }
        )
        metadata.pop("etag", None)
        metadata.pop("last_modified", None)
        etag = self._response_header(response, "ETag")
        last_modified = self._response_header(response, "Last-Modified")
        if etag is not None:
            metadata["etag"] = etag
        if last_modified is not None:
            metadata["last_modified"] = last_modified
        self._write_partial_metadata(metadata_file, metadata)

    def _validated_resume_range(
        self,
        task: DownloadTask,
        response: Any,
        resume_from: int,
        resume_validator: Optional[tuple[str, str]],
        size_limit: Optional[int],
    ) -> Optional[tuple[int, int, int]]:
        if resume_validator is None:
            return None

        validator_header = (
            "ETag" if resume_validator[0] == "etag" else "Last-Modified"
        )
        response_validator = self._response_header(response, validator_header)
        if response_validator != resume_validator[1]:
            Logger.warning(
                f"Download validator changed for {task.destination.name}; "
                "discarding partial file"
            )
            return None

        content_range = self._response_header(response, "Content-Range")
        if content_range is None:
            Logger.warning(
                f"Missing Content-Range for resumed download "
                f"{task.destination.name}"
            )
            return None
        match = _CONTENT_RANGE_PATTERN.fullmatch(content_range)
        if match is None:
            Logger.warning(
                f"Invalid Content-Range for resumed download "
                f"{task.destination.name}: {content_range}"
            )
            return None

        start, end, total = (int(value) for value in match.groups())
        if (
            start != resume_from
            or end < start
            or total <= end
            or end != total - 1
            or (
                task.expected_size is not None
                and total != task.expected_size
            )
            or (size_limit is not None and total > size_limit)
        ):
            Logger.warning(
                f"Inconsistent Content-Range for resumed download "
                f"{task.destination.name}: {content_range}"
            )
            return None

        content_length = self._response_header(response, "Content-Length")
        if content_length is not None:
            try:
                if int(content_length) != end - start + 1:
                    return None
            except ValueError:
                return None
        return start, end, total

    @staticmethod
    def _read_partial_metadata(metadata_file: Path) -> Optional[Dict[str, Any]]:
        if not metadata_file.exists():
            return None
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("schema") != _PARTIAL_METADATA_SCHEMA
            ):
                raise ValueError("unsupported partial metadata")
            return data
        except Exception as exc:
            Logger.debug(
                f"Discarding invalid partial download metadata {metadata_file}: {exc}"
            )
            AsyncDownloader._safe_unlink(metadata_file)
            return None

    @staticmethod
    def _partial_from_metadata(
        task: DownloadTask,
        partial_name: Any,
    ) -> Optional[Path]:
        if not isinstance(partial_name, str) or Path(partial_name).name != partial_name:
            return None
        legacy_name = f"{task.destination.name}.part.tmp"
        identified_prefix = f"{task.destination.name}.part."
        if partial_name != legacy_name and not (
            partial_name.startswith(identified_prefix)
            and partial_name.endswith(".tmp")
        ):
            return None
        return task.destination.parent / partial_name

    @staticmethod
    def _write_partial_metadata(
        metadata_file: Path,
        metadata: Dict[str, Any],
    ) -> None:
        token = f"{threading.get_ident()}.{time.time_ns()}"
        temporary_metadata = metadata_file.with_name(
            f"{metadata_file.name}.{token}.tmp"
        )
        try:
            temporary_metadata.write_text(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(str(temporary_metadata), str(metadata_file))
        finally:
            AsyncDownloader._safe_unlink(temporary_metadata)

    def _clear_partial_state(
        self,
        task: DownloadTask,
        partial_file: Path,
        *,
        remove_partial: bool = False,
    ) -> None:
        if remove_partial:
            self._safe_unlink(partial_file)
        metadata_file = self._partial_metadata_file_for(task)
        metadata = self._read_partial_metadata(metadata_file)
        if metadata is None:
            return
        if (
            metadata.get("identity") == self._partial_identity(task)
            and metadata.get("partial_file") == partial_file.name
        ):
            self._safe_unlink(metadata_file)

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
__all__ = ['AsyncDownloader', 'DownloadTask']
