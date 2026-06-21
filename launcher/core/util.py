from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from launcher import APP_NAME, __version__
from launcher.models.logger import Logger
from launcher.platform.paths import LauncherPaths, PathService
from launcher.platform.resources import ResourceService
from launcher.platform.security import SecurityService
from launcher.platform.system import SystemService

_DEFAULT_LAUNCHER_VERSION = __version__
_DEFAULT_LAUNCHER_NAME = APP_NAME
CLIENT_ID: str | None = None
CLIENT_SECRET: str | None = None


class UtilService:
    def __init__(self) -> None:
        self.path_service = PathService()
        self.paths = self.path_service.paths
        self.resources = ResourceService(self.path_service)
        self.security = SecurityService()
        self.system = SystemService(self.path_service)
        self.launcher_version = _DEFAULT_LAUNCHER_VERSION
        self.launcher_name = _DEFAULT_LAUNCHER_NAME
        self.redirect_url = "http://localhost:8080/callback"
        self.minecraft_dir_error: Optional[str] = None

    def refresh_paths(self) -> None:
        self.path_service.refresh()
        self.paths = self.path_service.paths

    def init(self, *, create_minecraft_dirs: bool = True) -> None:
        self.refresh_paths()
        self.minecraft_dir_error = None
        self.path_service.migrate_legacy_app_state()
        if create_minecraft_dirs:
            try:
                self.path_service.init_directories()
            except OSError as exc:
                self.minecraft_dir_error = str(exc)
                Logger.warning(f"Configured Minecraft directory '{self.paths.minecraft_dir}' is unavailable: {exc!r}")
        self.paths = self.path_service.paths
        self.path_service.minecraft_dir_error = self.minecraft_dir_error

    def set_minecraft_dir_override(self, value: Optional[str]) -> bool:
        accepted = self.path_service.set_minecraft_dir_override(value)
        self.paths = self.path_service.paths
        return accepted

    def set_app_state_dir_override(self, value: Optional[str], *, persist: bool = True) -> bool:
        accepted = self.path_service.set_app_state_dir_override(value, persist=persist)
        self.paths = self.path_service.paths
        return accepted

    def get_resource_path(self, *path_parts: str) -> Optional[Path]:
        return self.resources.get_resource_path(*path_parts)

    def get_background_path(self) -> Optional[Path]:
        return self.resources.get_background_path()

    def open_mc_dir(self, *paths: str) -> Optional[str]:
        return self.system.open_mc_dir(*paths)

    def is_macos(self) -> bool:
        return self.system.is_macos()

    def open_macos_microphone_settings(self) -> bool:
        return self.system.open_macos_microphone_settings()

    def request_macos_microphone_access(self) -> str:
        return self.system.request_macos_microphone_access()

    def reset_macos_microphone_access(self) -> bool:
        return self.system.reset_macos_microphone_access()

    def check_connection(self, timeout: float = 2.0) -> bool:
        return self.system.check_connection(timeout=timeout)

    def get_client_secret(self) -> Optional[Dict[str, str]]:
        return self.security.get_client_secret()

    def get_skin_url(self, name: str | None) -> str | None:
        return self.system.get_skin_url(name)

    def get_cached_skin_url(self, name: str | None, *, allow_stale: bool = True) -> str | None:
        return self.system.get_cached_skin_url(name, allow_stale=allow_stale)

    def prefetch_skin(self, name: str | None, on_ready=None) -> bool:
        return self.system.prefetch_skin(name, on_ready=on_ready)

    def get_all_java(self) -> List[Dict[str, str]]:
        return self.system.get_all_java()

    def get_user_secret(self) -> str:
        return self.security.get_user_secret(self.paths.app_state_dir)

    def get_legacy_user_secret(self) -> str:
        return self.security.get_legacy_user_secret()

    @staticmethod
    def normalize_string(text: object) -> str:
        return SecurityService.normalize_string(text)


_util_service = UtilService()


def _sync_public_paths() -> None:
    global app_dir, app_state_dir, games_path, minecraft_dir, minecraft_dir_error, paths
    paths = _util_service.paths
    app_dir = str(_util_service.paths.app_dir)
    app_state_dir = str(_util_service.paths.app_state_dir)
    minecraft_dir = str(_util_service.paths.minecraft_dir)
    games_path = str(_util_service.paths.games_dir)
    minecraft_dir_error = _util_service.minecraft_dir_error


_sync_public_paths()
launcher_version = _util_service.launcher_version
launcher_name = _util_service.launcher_name
REDIRECT_URL = _util_service.redirect_url


def init(*, create_minecraft_dirs: bool = True) -> None:
    _util_service.init(create_minecraft_dirs=create_minecraft_dirs)
    _sync_public_paths()


def open_mc_dir(*paths: str) -> Optional[str]:
    return _util_service.open_mc_dir(*paths)


def is_macos() -> bool:
    return _util_service.is_macos()


def open_macos_microphone_settings() -> bool:
    return _util_service.open_macos_microphone_settings()


def request_macos_microphone_access() -> str:
    return _util_service.request_macos_microphone_access()


def reset_macos_microphone_access() -> bool:
    return _util_service.reset_macos_microphone_access()


def get_skin_url(name: str | None) -> str | None:
    return _util_service.get_skin_url(name)


def get_cached_skin_url(name: str | None, *, allow_stale: bool = True) -> str | None:
    return _util_service.get_cached_skin_url(name, allow_stale=allow_stale)


def prefetch_skin(name: str | None, on_ready=None) -> bool:
    return _util_service.prefetch_skin(name, on_ready=on_ready)


def get_all_java() -> List[Dict[str, str]]:
    return _util_service.get_all_java()


def check_connection(timeout: float = 2.0) -> bool:
    return _util_service.check_connection(timeout=timeout)


def get_user_secret() -> str:
    return _util_service.get_user_secret()


def get_legacy_user_secret() -> str:
    return _util_service.get_legacy_user_secret()


def get_client_secret() -> Optional[Dict[str, str]]:
    global CLIENT_ID, CLIENT_SECRET
    secrets = _util_service.get_client_secret()
    if secrets:
        CLIENT_ID = secrets.get("id")
        CLIENT_SECRET = secrets.get("secret")
    return secrets


def normalize_string(text: object) -> str:
    return _util_service.normalize_string(text)


def get_background_path() -> Optional[Path]:
    return _util_service.get_background_path()


def get_resource_path(*path_parts: str) -> Optional[Path]:
    return _util_service.get_resource_path(*path_parts)


def set_minecraft_dir_override(value: Optional[str]) -> bool:
    accepted = _util_service.set_minecraft_dir_override(value)
    _sync_public_paths()
    return accepted


def set_app_state_dir_override(value: Optional[str], *, persist: bool = True) -> bool:
    accepted = _util_service.set_app_state_dir_override(value, persist=persist)
    _sync_public_paths()
    return accepted


__all__ = [
    "CLIENT_ID",
    "CLIENT_SECRET",
    "LauncherPaths",
    "REDIRECT_URL",
    "app_dir",
    "app_state_dir",
    "check_connection",
    "get_cached_skin_url",
    "games_path",
    "get_all_java",
    "get_background_path",
    "get_client_secret",
    "get_resource_path",
    "get_skin_url",
    "get_legacy_user_secret",
    "prefetch_skin",
    "get_user_secret",
    "init",
    "is_macos",
    "launcher_name",
    "launcher_version",
    "minecraft_dir",
    "minecraft_dir_error",
    "normalize_string",
    "open_macos_microphone_settings",
    "open_mc_dir",
    "paths",
    "request_macos_microphone_access",
    "reset_macos_microphone_access",
    "set_minecraft_dir_override",
    "set_app_state_dir_override",
]
