from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from launcher.core.pending_update import (
    clear_pending_update_marker,
    pending_update_marker_path,
    write_pending_update_marker,
)
from launcher.models.logger import Logger

WINDOWS_CREATE_NEW_CONSOLE = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


class AutoUpdater:

    UPDATE_CHECK_URL = "https://gigabait.uk/api/mods/launcher/update"
    UPDATE_BETA_CHECK_URL = "https://gigabait.uk/api/mods/launcher/update-beta"
    _SCRIPT_ROOT = Path(__file__).resolve().parent.parent / "assets" / "updater"
    _PLATFORM_SUFFIXES = {'windows': '.exe', 'macos': '.dmg'}
    DOWNLOAD_CHUNK_SIZE = 262144

    def __init__(self, app):
        self.app = app
        self.current_version = app.util.launcher_version
        self.logger = getattr(app, "log", Logger())
        self.platform = self._detect_platform()
        self.appimage_path = self._resolve_appimage_path()
        self._temp_dir = Path(tempfile.gettempdir())
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": f"TensaLauncher/{self.current_version}"})

    @staticmethod
    def _resolve_appimage_path() -> Optional[Path]:
        appimage = os.environ.get("APPIMAGE")
        if not appimage:
            return None
        try:
            return Path(appimage).resolve()
        except OSError:
            return Path(appimage)

    @staticmethod
    def _normalize_platform(value: str | None) -> str:
        platform_name = str(value or "").strip().lower()
        aliases = {
            "darwin": "macos",
            "macosx": "macos",
            "osx": "macos",
        }
        return aliases.get(platform_name, platform_name)

    @classmethod
    def _detect_platform(cls) -> str:
        system = platform.system()
        if system == 'Windows':
            return 'windows'
        elif system == 'Linux':
            return 'linux'
        elif system == 'Darwin':
            return 'macos'
        else:
            return 'unknown'

    @staticmethod
    def _normalize_version(value: str | None) -> tuple[int, ...]:
        raw = str(value or "").strip()
        if not raw:
            return ()
        parts: list[int] = []
        for token in raw.replace("-", ".").split("."):
            digits = "".join(ch for ch in token if ch.isdigit())
            if digits:
                parts.append(int(digits))
            else:
                parts.append(0)
        return tuple(parts)

    @classmethod
    def _is_newer_version(cls, latest: str | None, current: str | None) -> bool:
        latest_parts = cls._normalize_version(latest)
        current_parts = cls._normalize_version(current)
        if not latest_parts:
            return False
        for left, right in zip_longest(latest_parts, current_parts, fillvalue=0):
            if left > right:
                return True
            if left < right:
                return False
        return False

    def _update_check_url(self) -> str:
        beta_enabled = self.app.config.get("include_beta_updates", "no") == "yes"
        return self.UPDATE_BETA_CHECK_URL if beta_enabled else self.UPDATE_CHECK_URL

    def _update_check_urls(self) -> list[str]:
        beta_enabled = self.app.config.get("include_beta_updates", "no") == "yes"
        if beta_enabled:
            return [self.UPDATE_BETA_CHECK_URL, self.UPDATE_CHECK_URL]
        return [self.UPDATE_CHECK_URL]

    def _extract_download_urls(self, data: dict[str, Any]) -> list[str]:
        urls: list[str] = []

        raw_download = data.get("download")
        nested_download: dict[str, Any] = raw_download if isinstance(raw_download, dict) else {}
        api_download_urls = data.get("download_urls")
        if isinstance(api_download_urls, list):
            urls.extend(str(url) for url in api_download_urls if str(url).strip())

        for candidate in (nested_download.get("url"), data.get("download_url"), data.get("file_url")):
            if candidate:
                urls.append(str(candidate))

        deduped: list[str] = []
        for url in urls:
            if url not in deduped:
                deduped.append(url)
        return deduped

    def _update_info_from_payload(self, data: dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload_platform = self._normalize_platform(str(data.get("platform") or ""))
        current_platform = self._normalize_platform(self.platform)
        if payload_platform and payload_platform != current_platform:
            self.logger.warning(
                f"Update endpoint returned mismatched platform '{payload_platform}' for '{current_platform}'"
            )
            return None

        latest_version = str(data.get('version') or '').strip() or None
        if not latest_version:
            self.logger.warning("Update endpoint returned payload without version")
            return None

        if not self._is_newer_version(latest_version, self.current_version):
            return None

        download_urls = self._extract_download_urls(data)
        if not download_urls:
            self.logger.warning("Update endpoint did not provide download URLs")
            return None

        changelog = (
            data.get('notes')
            or data.get('changelog')
            or data.get('description')
            or data.get('release_notes')
            or ''
        )
        return {
            'version': latest_version,
            'channel': data.get('channel'),
            'changelog': changelog,
            'download_url': download_urls[0],
            'download_urls': download_urls,
            'download_hash': data.get('file_hash') or (data.get('download') or {}).get('file_hash'),
            'download_hash_algorithm': data.get('file_hash_algorithm') or (data.get('download') or {}).get('file_hash_algorithm'),
            'download_file_name': data.get('file_name') or (data.get('download') or {}).get('file_name'),
        }

    def _check_update_url(self, url: str, params: dict[str, str]) -> Optional[Dict[str, Any]]:
        try:
            response = self._session.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            self.logger.warning(f"Failed to check for updates: {exc}")
            return None
        except Exception as exc:
            self.logger.error(f"Unexpected error checking updates: {exc}")
            return None

        if not isinstance(data, dict):
            self.logger.warning("Update endpoint returned unexpected payload")
            return None

        return self._update_info_from_payload(data)

    def check_for_updates(self) -> Optional[Dict[str, Any]]:
        self.logger.info(f"Checking for updates (current version: {self.current_version})")
        params = {"platform": self.platform, "current_version": self.current_version}
        best_update: Optional[Dict[str, Any]] = None
        for url in self._update_check_urls():
            update = self._check_update_url(url, params)
            if update is None:
                continue
            if best_update is None or self._is_newer_version(update.get("version"), best_update.get("version")):
                best_update = update

        if best_update is None:
            self.logger.info("No updates available")
            return None

        self.logger.info(f"Update available: {best_update['version']}")
        return best_update

    def _download_suffix(self) -> str:
        if self.platform == "linux" and self.appimage_path is not None:
            return ".AppImage"
        return self._PLATFORM_SUFFIXES.get(self.platform, '')

    @staticmethod
    def _sanitize_file_component(value: str | None) -> str:
        raw = str(value or "").strip() or "latest"
        return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in raw)

    def _download_paths(self, update_info: Dict[str, Any], suffix: str) -> tuple[Path, Path]:
        version = self._sanitize_file_component(update_info.get("version"))
        raw_file_name = str(update_info.get("download_file_name") or "").strip()
        base_name = (
            self._sanitize_file_component(raw_file_name)
            if raw_file_name
            else f"tensalauncher-update-{self.platform}-{version}"
        )
        if suffix and not base_name.endswith(suffix):
            base_name = f"{base_name}{suffix}"
        final_path = self._temp_dir / base_name
        partial_path = final_path.with_name(f"{final_path.name}.part")
        return final_path, partial_path

    @staticmethod
    def _total_size_from_response(response: requests.Response, resume_from: int) -> int:
        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[-1].strip()
            if total.isdigit():
                return int(total)
        content_length = response.headers.get("content-length", "0")
        if str(content_length).isdigit():
            return int(content_length) + resume_from
        return 0

    def _stream_download(
        self,
        url: str,
        partial_path: Path,
        *,
        progress_callback=None,
    ) -> Path:
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        resume_from = partial_path.stat().st_size if partial_path.exists() else 0
        headers: dict[str, str] = {}
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        with self._session.get(url, headers=headers, stream=True, timeout=30) as response:
            if resume_from and response.status_code == 200:
                partial_path.unlink(missing_ok=True)
                resume_from = 0
            response.raise_for_status()

            mode = "ab" if resume_from and response.status_code == 206 else "wb"
            downloaded = resume_from if mode == "ab" else 0
            total_size = self._total_size_from_response(response, downloaded)

            with open(partial_path, mode) as temp_file:
                for chunk in response.iter_content(chunk_size=self.DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    temp_file.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size:
                        progress_callback(downloaded, total_size)

        return partial_path

    async def check_for_updates_async(self):
        await asyncio.sleep(2)
        update_info = self.check_for_updates()
        if update_info:
            self.show_update_dialog(update_info)

    @staticmethod
    def _calculate_hash(file_path: Path, algorithm: str) -> str:
        hasher = hashlib.new(algorithm)
        with open(file_path, 'rb') as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def download_update(self, update_info: Dict[str, Any], progress_callback=None) -> Optional[Path]:
        urls = update_info.get("download_urls") or [update_info["download_url"]]
        suffix = self._download_suffix()
        expected_hash = str(update_info.get("download_hash") or "").strip().lower() or None
        expected_hash_algorithm = str(update_info.get("download_hash_algorithm") or "").strip().lower() or None
        final_path, partial_path = self._download_paths(update_info, suffix)

        last_error: Optional[Exception] = None
        for url in urls:
            try:
                self.logger.info(f"Downloading update from {url}")
                temp_path = self._stream_download(url, partial_path, progress_callback=progress_callback)
                if expected_hash and expected_hash_algorithm:
                    actual_hash = self._calculate_hash(temp_path, expected_hash_algorithm)
                    if actual_hash != expected_hash:
                        temp_path.unlink(missing_ok=True)
                        raise ValueError(
                            f"Downloaded update hash mismatch: expected {expected_hash}, got {actual_hash}"
                        )

                final_path.unlink(missing_ok=True)
                shutil.move(str(temp_path), str(final_path))
                self.logger.info(f"Update downloaded to {final_path}")
                return final_path
            except Exception as exc:
                last_error = exc
                self.logger.warning(f"Failed to download from {url}: {exc}")

        if last_error is not None:
            self.logger.error(f"Failed to download update: {last_error}")
        return None

    def show_update_dialog(self, update_info: Dict[str, Any]) -> None:
        changelog = update_info.get("changelog", self.app.trans("update_available_message"))
        self.app.feedback.confirm(
            title=self.app.trans("update_available_title", version=update_info["version"]),
            question=changelog,
            callback=lambda confirmed: self.start_update_download(update_info) if confirmed else None,
        )

    async def start_update_download(self, update_info: Dict[str, Any]) -> None:
        operation = self.app.feedback.begin_operation(
            self.app.trans("update_downloading"),
            kind="launcher_update",
            status=self.app.trans("update_downloading"),
            progress=0,
            total=100,
        )

        def progress_callback(downloaded, total):
            total = max(total or 0, 1)
            percent = int((downloaded / total) * 100)
            operation.update(f"{self.app.trans('update_downloading')} {percent}%", progress=percent, total=100)

        download_path = await asyncio.to_thread(
            self.download_update,
            update_info,
            progress_callback=progress_callback,
        )

        if download_path:
            operation.update(self.app.trans("update_applying"), progress=100, total=100)
            update_cmd = await asyncio.to_thread(self.prepare_update, download_path)
            if update_cmd:
                operation.finish(show_success=False)
                self.app.feedback.confirm(
                    title=self.app.trans("update_ready_title"),
                    question=self.app.trans("update_ready_message"),
                    callback=lambda confirmed: (
                        self.execute_update(update_cmd)
                        if confirmed
                        else clear_pending_update_marker(self._temp_dir)
                    ),
                )
            else:
                operation.finish(show_success=False)
                self.app.feedback.warning(self.app.trans("update_prepare_failed"))
        else:
            operation.finish(show_success=False)
            self.app.feedback.warning(self.app.trans("update_download_failed"))

    def execute_update(self, update_cmd: str) -> None:
        """Run prepared updater command and close launcher."""
        if self.platform == "windows":
            subprocess.Popen([update_cmd], shell=True, creationflags=WINDOWS_CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(update_cmd, shell=True, start_new_session=True)
        self.app.stop()

    def _copy_updater_asset(
        self,
        source_name: str,
        target_name: Optional[str] = None,
        make_executable: bool = False,
    ) -> Optional[Path]:
        source_path = self._SCRIPT_ROOT / source_name
        if not source_path.exists():
            self.logger.error(f"Update script not found: {source_path}")
            return None

        destination = self._temp_dir / (target_name or source_name)
        shutil.copy2(source_path, destination)
        if make_executable:
            destination.chmod(0o755)
        return destination

    @staticmethod
    def _format_nohup_command(script_path: Path, *args: str) -> str:
        quoted_args = " ".join(f'"{arg}"' for arg in args)
        return f'nohup "{script_path}" {quoted_args} > /dev/null 2>&1 &'

    def apply_update_windows(self, new_binary_path: Path) -> Optional[str]:
        """Підготовка оновлення для Windows. Повертає шлях до батч-файлу"""
        try:
            current_exe = Path(sys.executable)
            current_pid = os.getpid()

            batch_path = self._copy_updater_asset("windows_update.bat", "tensalauncher_update.bat")
            if not batch_path:
                return None

            launcher_bat = self._temp_dir / "tensalauncher_start_update.bat"
            marker_path = pending_update_marker_path(self._temp_dir)
            launcher_script = (
                '@echo off\n'
                f'call "{batch_path}" "{new_binary_path}" "{current_exe}" {current_pid} "{marker_path}"\n'
            )
            launcher_bat.write_text(launcher_script, encoding='ascii')
            write_pending_update_marker(
                temp_dir=self._temp_dir,
                platform_name="windows",
                command=launcher_bat,
                updater_script=batch_path,
                source=new_binary_path,
                target=current_exe,
            )

            self.logger.info(f"Update ready. Script: {batch_path}")
            return str(launcher_bat)

        except Exception as exc:
            self.logger.error(f"Failed to prepare Windows update: {exc}")
            return None

    def apply_update_macos(self, dmg_path: Path) -> Optional[str]:
        """Підготовка оновлення для macOS. Повертає шлях до скрипта"""
        try:
            current_pid = os.getpid()

            # Знаходимо .app bundle
            current_app = Path(sys.executable)
            while current_app.suffix != '.app' and current_app != current_app.parent:
                current_app = current_app.parent

            if current_app.suffix != '.app':
                self.logger.error("Could not find .app bundle")
                return None

            script_path = self._copy_updater_asset("macos_update.sh", "tensalauncher_update.sh", make_executable=True)
            if not script_path:
                return None

            cmd = self._format_nohup_command(
                script_path,
                str(dmg_path),
                str(current_app),
                str(current_pid),
            )

            self.logger.info(f"Update ready. Script: {script_path}")
            return cmd

        except Exception as exc:
            self.logger.error(f"Failed to prepare macOS update: {exc}")
            return None

    def apply_update_linux(self, new_binary_path: Path) -> Optional[str]:
        """Підготовка оновлення для Linux. Повертає шлях до скрипта"""
        try:
            current_binary = self.appimage_path or Path(sys.executable)
            current_pid = os.getpid()

            script_path = self._copy_updater_asset("linux_update.sh", "tensalauncher_update.sh", make_executable=True)
            if not script_path:
                return None

            cmd = self._format_nohup_command(
                script_path,
                str(new_binary_path),
                str(current_binary),
                str(current_pid),
            )

            self.logger.info(f"Update ready. Script: {script_path}")
            return cmd

        except Exception as exc:
            self.logger.error(f"Failed to prepare Linux update: {exc}")
            return None

    def prepare_update(self, new_binary_path: Path) -> Optional[str]:
        """Підготовка оновлення. Повертає команду для запуску оновлювача"""
        if not getattr(sys, 'frozen', False):
            self.logger.warning("Cannot apply update: running in development mode")
            return None

        handlers = {
            'windows': self.apply_update_windows,
            'linux': self.apply_update_linux,
            'macos': self.apply_update_macos,
        }
        handler = handlers.get(self.platform)
        if not handler:
            self.logger.warning(f"Unsupported platform '{self.platform}'; cannot prepare update")
            return None
        return handler(new_binary_path)
