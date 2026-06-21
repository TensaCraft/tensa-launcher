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

    GITHUB_RELEASES_URL = "https://api.github.com/repos/TensaCraft/tensa-launcher/releases"
    GITHUB_API_VERSION = "2022-11-28"
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
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self.GITHUB_API_VERSION,
                "User-Agent": f"TensaLauncher/{self.current_version}",
            }
        )

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
    def _normalize_version_tag(value: str | None) -> str:
        raw = str(value or "").strip()
        if len(raw) > 1 and raw[0].lower() == "v" and raw[1].isdigit():
            return raw[1:]
        return raw

    @classmethod
    def _parse_version(cls, value: str | None) -> tuple[tuple[int, ...], tuple[int | str, ...]]:
        raw = cls._normalize_version_tag(value)
        if not raw:
            return (), ()

        without_build = raw.split("+", 1)[0]
        base, separator, prerelease = without_build.partition("-")
        base_parts: list[int] = []
        for token in base.split("."):
            digits = "".join(ch for ch in token if ch.isdigit())
            base_parts.append(int(digits) if digits else 0)

        prerelease_parts: list[int | str] = []
        if separator:
            for token in prerelease.replace("-", ".").split("."):
                if not token:
                    continue
                prerelease_parts.append(int(token) if token.isdigit() else token.lower())

        return tuple(base_parts), tuple(prerelease_parts)

    @staticmethod
    def _compare_prerelease(left: tuple[int | str, ...], right: tuple[int | str, ...]) -> int:
        if not left and not right:
            return 0
        if not left:
            return 1
        if not right:
            return -1

        for left_part, right_part in zip_longest(left, right, fillvalue=None):
            if left_part is None:
                return -1
            if right_part is None:
                return 1
            if left_part == right_part:
                continue
            if isinstance(left_part, int) and isinstance(right_part, int):
                return 1 if left_part > right_part else -1
            if isinstance(left_part, int):
                return -1
            if isinstance(right_part, int):
                return 1
            return 1 if left_part > right_part else -1

        return 0

    @classmethod
    def _is_newer_version(cls, latest: str | None, current: str | None) -> bool:
        latest_base, latest_prerelease = cls._parse_version(latest)
        current_base, current_prerelease = cls._parse_version(current)
        if not latest_base:
            return False
        for left, right in zip_longest(latest_base, current_base, fillvalue=0):
            if left > right:
                return True
            if left < right:
                return False

        return cls._compare_prerelease(latest_prerelease, current_prerelease) > 0

    def _include_beta_updates(self) -> bool:
        return self.app.config.get("include_beta_updates", "no") == "yes"

    def _github_releases(self) -> list[dict[str, Any]]:
        try:
            response = self._session.get(self.GITHUB_RELEASES_URL, params={"per_page": 100}, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            self.logger.warning(f"Failed to check for updates: {exc}")
            return []
        except Exception as exc:
            self.logger.error(f"Unexpected error checking updates: {exc}")
            return []

        if not isinstance(data, list):
            self.logger.warning("GitHub releases endpoint returned unexpected payload")
            return []

        return [release for release in data if isinstance(release, dict)]

    def _release_allowed_for_channel(self, release: dict[str, Any]) -> bool:
        if release.get("draft"):
            return False
        if self._include_beta_updates():
            return True
        return not bool(release.get("prerelease"))

    def _preferred_github_asset_names(self) -> tuple[str, ...]:
        current_platform = self._normalize_platform(self.platform)
        if current_platform == "windows":
            return ("TensaLauncher.exe",)
        if current_platform == "macos":
            return ("TensaLauncher.dmg",)
        if current_platform == "linux":
            if self.appimage_path is not None:
                return ("TensaLauncher-x86_64.AppImage", "TensaLauncher.AppImage")
            return ("TensaLauncher",)
        return ()

    def _select_github_asset(self, release: dict[str, Any]) -> Optional[dict[str, Any]]:
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            return None

        assets = [asset for asset in raw_assets if isinstance(asset, dict)]
        assets_by_name = {
            str(asset.get("name") or "").lower(): asset
            for asset in assets
            if str(asset.get("browser_download_url") or "").strip()
        }
        for asset_name in self._preferred_github_asset_names():
            asset = assets_by_name.get(asset_name.lower())
            if asset is not None:
                return asset

        current_platform = self._normalize_platform(self.platform)
        for asset in assets:
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "").strip()
            if not url:
                continue

            lower_name = name.lower()
            if current_platform == "windows" and lower_name.endswith(".exe") and "installer" not in lower_name:
                return asset
            if current_platform == "macos" and lower_name.endswith(".dmg"):
                return asset
            if current_platform == "linux":
                if self.appimage_path is not None and lower_name.endswith(".appimage"):
                    return asset
                if self.appimage_path is None and lower_name == "tensalauncher":
                    return asset

        return None

    @staticmethod
    def _github_asset_hash(asset: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        digest = str(asset.get("digest") or "").strip()
        if ":" not in digest:
            return None, None

        algorithm, expected_hash = (part.strip().lower() for part in digest.split(":", 1))
        if not algorithm or not expected_hash:
            return None, None

        try:
            hashlib.new(algorithm)
        except ValueError:
            return None, None
        return expected_hash, algorithm

    def _update_info_from_github_release(self, release: dict[str, Any]) -> Optional[Dict[str, Any]]:
        latest_version = self._normalize_version_tag(release.get("tag_name") or release.get("name"))
        if not latest_version:
            self.logger.warning("GitHub release payload did not provide a tag name")
            return None

        if not self._is_newer_version(latest_version, self.current_version):
            return None

        asset = self._select_github_asset(release)
        if asset is None:
            self.logger.warning(
                f"GitHub release {latest_version} does not provide a {self.platform} launcher asset"
            )
            return None

        download_url = str(asset.get("browser_download_url") or "").strip()
        if not download_url:
            self.logger.warning(f"GitHub release {latest_version} selected an asset without download URL")
            return None

        expected_hash, expected_hash_algorithm = self._github_asset_hash(asset)
        channel = "beta" if release.get("prerelease") else "stable"
        return {
            "version": latest_version,
            "channel": channel,
            "changelog": release.get("body") or release.get("name") or "",
            "download_url": download_url,
            "download_urls": [download_url],
            "download_hash": expected_hash,
            "download_hash_algorithm": expected_hash_algorithm,
            "download_file_name": asset.get("name"),
        }

    def check_for_updates(self) -> Optional[Dict[str, Any]]:
        self.logger.info(f"Checking for updates (current version: {self.current_version})")
        best_update: Optional[Dict[str, Any]] = None
        for release in self._github_releases():
            if not self._release_allowed_for_channel(release):
                continue
            update = self._update_info_from_github_release(release)
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
