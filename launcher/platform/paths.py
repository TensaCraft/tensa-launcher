from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from launcher import PRODUCT_NAME

STATE_FILENAMES = ("config.json", "profiles.json", "versions.json")
MINECRAFT_DIR_MARKERS = ("versions", "libraries", "assets", "runtime", "games")
DEV_ROOT_DIRNAME = ".dev"
APP_STATE_DIRNAME = PRODUCT_NAME
APP_STATE_POINTER_FILENAME = "storage.json"
APP_STATE_POINTER_KEY = "app_state_dir"
MINECRAFT_DIR_CONFIG_KEY = "minecraft_game_dir"
SETUP_WIZARD_CONFIG_KEYS = ("setup_wizard_completed", "setup_wizard_version")
_runtime_app_state_dir_override: Path | None = None
_runtime_minecraft_dir_override: Path | None = None


def _log_info(message: str) -> None:
    logging.getLogger("tensa.launcher").info(message)


def _log_warning(message: str) -> None:
    logging.getLogger("tensa.launcher").warning(message)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) is True


def safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _looks_like_project_root(path: Path) -> bool:
    return (
        (path / "pyproject.toml").is_file()
        and (path / "launcher").is_dir()
        and (path / ".tools").is_dir()
    )


def _walk_candidates(start: Path) -> list[Path]:
    resolved = safe_resolve(start)
    return [resolved, *resolved.parents]


def _home_dir() -> Path:
    return Path.home()


def _resolve_sidecar_frozen_base() -> Path:
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return safe_resolve(Path(appimage).expanduser()).parent
    return safe_resolve(Path(sys.executable)).parent


def _resolve_linux_data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return safe_resolve(candidate)
    return safe_resolve(_home_dir() / ".local" / "share")


def _resolve_linux_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return safe_resolve(candidate)
    return safe_resolve(_home_dir() / ".config")


def _resolve_linux_cache_home() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return safe_resolve(candidate)
    return safe_resolve(_home_dir() / ".cache")


def _resolve_linux_state_home() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return safe_resolve(candidate)
    return safe_resolve(_home_dir() / ".local" / "state")


def _resolve_linux_legacy_data_app_state_dir() -> Path:
    return safe_resolve(_resolve_linux_data_home() / APP_STATE_DIRNAME)


def _resolve_windows_data_home() -> Path:
    configured = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if configured:
        return safe_resolve(Path(configured).expanduser())
    home = _home_dir()
    return safe_resolve(home / "AppData" / "Local")


def _resolve_windows_roaming_data_home() -> Path:
    configured = os.environ.get("APPDATA")
    if configured:
        return safe_resolve(Path(configured).expanduser())
    return safe_resolve(_resolve_windows_profile_home() / "AppData" / "Roaming")


def _windows_profile_home_from_local_app_data(local_app_data: Path) -> Path | None:
    parts = local_app_data.parts
    lowered = tuple(part.lower() for part in parts)
    if len(parts) > 2 and lowered[-2:] == ("appdata", "local"):
        return safe_resolve(Path(*parts[:-2]))
    return None


def _resolve_windows_profile_home() -> Path:
    local_app_data = _resolve_windows_data_home()
    configured = os.environ.get("USERPROFILE")
    if configured:
        profile_home = safe_resolve(Path(configured).expanduser())
        if not (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")):
            return profile_home
        if local_app_data == safe_resolve(profile_home / "AppData" / "Local"):
            return profile_home

    profile_home = _windows_profile_home_from_local_app_data(local_app_data)
    if profile_home is not None:
        return profile_home

    if os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA"):
        return safe_resolve(local_app_data.parent)

    return safe_resolve(_home_dir())


def _resolve_windows_legacy_local_app_state_dir() -> Path:
    return safe_resolve(_resolve_windows_data_home() / APP_STATE_DIRNAME)


def _resolve_windows_legacy_profile_app_state_dir() -> Path:
    return safe_resolve(_resolve_windows_profile_home() / APP_STATE_DIRNAME)


def _resolve_windows_program_files_home() -> Path:
    configured = os.environ.get("ProgramFiles") or os.environ.get("ProgramW6432")
    if configured:
        return safe_resolve(Path(configured).expanduser())
    system_drive = os.environ.get("SystemDrive") or "C:"
    return safe_resolve(Path(system_drive) / "Program Files")


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".tensalauncher-write-test-", dir=path, delete=True):
            pass
    except OSError:
        return False
    return True


def _resolve_user_data_base() -> Path:
    if sys.platform == "darwin":
        return safe_resolve(_home_dir() / "Library" / "Application Support" / APP_STATE_DIRNAME)
    if sys.platform.startswith("linux"):
        return safe_resolve(_resolve_linux_config_home() / APP_STATE_DIRNAME)
    if sys.platform.startswith("win"):
        return safe_resolve(_resolve_windows_data_home() / APP_STATE_DIRNAME)
    return safe_resolve(_home_dir() / f".{APP_STATE_DIRNAME}")


def _resolve_app_state_pointer_base() -> Path:
    return _resolve_user_data_base()


def _state_pointer_file() -> Path:
    return _resolve_app_state_pointer_base() / APP_STATE_POINTER_FILENAME


def _state_pointer_files() -> list[Path]:
    pointers = [_state_pointer_file()]
    if sys.platform.startswith("win"):
        pointers.append(_resolve_windows_legacy_local_app_state_dir() / APP_STATE_POINTER_FILENAME)
        pointers.append(_resolve_windows_legacy_profile_app_state_dir() / APP_STATE_POINTER_FILENAME)
    if sys.platform.startswith("linux"):
        pointers.append(_resolve_linux_legacy_data_app_state_dir() / APP_STATE_POINTER_FILENAME)
    return _unique_paths(*pointers)


def _read_app_state_pointer() -> Path | None:
    for pointer in _state_pointer_files():
        try:
            raw = json.loads(pointer.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        value = str(raw.get(APP_STATE_POINTER_KEY, "") or "").strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return safe_resolve(candidate)
    return None


def _write_app_state_pointer(path: Path | None) -> None:
    pointer = _state_pointer_file()
    default_root = _resolve_user_data_base()
    if path is None or safe_resolve(path) == default_root:
        for candidate in _state_pointer_files():
            with suppress(OSError):
                candidate.unlink(missing_ok=True)
        return

    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            json.dumps({APP_STATE_POINTER_KEY: str(safe_resolve(path))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        _log_warning(f"Unable to write app-state pointer '{pointer}': {exc}")


def _resolve_configured_user_data_base() -> Path:
    if _runtime_app_state_dir_override is not None:
        return PathPolicy.select_app_state_dir(_runtime_app_state_dir_override)
    configured = _read_app_state_pointer()
    return PathPolicy.select_app_state_dir(configured)


def _unique_paths(*candidates: Path) -> list[Path]:
    unique: list[Path] = []
    for candidate in candidates:
        resolved = safe_resolve(candidate)
        if resolved not in unique:
            unique.append(resolved)
    return unique


@dataclass(frozen=True, slots=True)
class StorageLayout:
    app_state_dir: Path
    minecraft_dir: Path
    games_dir: Path


class PathPolicy:
    @staticmethod
    def default_app_state_dir() -> Path:
        return _resolve_user_data_base()

    @staticmethod
    def default_app_state_dir_for(candidate: Path | None) -> Path:
        return _resolve_user_data_base()

    @staticmethod
    def default_minecraft_dir(app_state_dir: Path) -> Path:
        if safe_resolve(app_state_dir) != _resolve_user_data_base():
            return app_state_dir / "minecraft"
        if sys.platform.startswith("win"):
            return safe_resolve(_resolve_windows_roaming_data_home() / APP_STATE_DIRNAME)
        if sys.platform.startswith("linux"):
            return safe_resolve(_resolve_linux_data_home() / APP_STATE_DIRNAME)
        return app_state_dir / "minecraft"

    @staticmethod
    def default_cache_dir() -> Path:
        if sys.platform == "darwin":
            return safe_resolve(_home_dir() / "Library" / "Caches" / APP_STATE_DIRNAME)
        if sys.platform.startswith("linux"):
            return safe_resolve(_resolve_linux_cache_home() / APP_STATE_DIRNAME)
        if sys.platform.startswith("win"):
            return safe_resolve(_resolve_windows_data_home() / APP_STATE_DIRNAME / "cache")
        return safe_resolve(_home_dir() / f".{APP_STATE_DIRNAME}" / "cache")

    @staticmethod
    def default_log_dir() -> Path:
        if sys.platform == "darwin":
            return safe_resolve(_home_dir() / "Library" / "Logs" / APP_STATE_DIRNAME)
        if sys.platform.startswith("linux"):
            return safe_resolve(_resolve_linux_state_home() / APP_STATE_DIRNAME)
        if sys.platform.startswith("win"):
            return safe_resolve(_resolve_windows_data_home() / APP_STATE_DIRNAME)
        return safe_resolve(_home_dir() / f".{APP_STATE_DIRNAME}" / "logs")

    @staticmethod
    def is_runtime_package_path(path: Path) -> bool:
        if not is_frozen():
            return False
        resolved = safe_resolve(path.expanduser())
        package_dir = safe_resolve(Path(sys.executable).expanduser()).parent
        return _is_relative_to(resolved, package_dir) and _is_protected_runtime_base(package_dir)

    @classmethod
    def is_safe_storage_path(cls, path: Path) -> bool:
        return not cls.is_runtime_package_path(path)

    @classmethod
    def select_app_state_dir(cls, candidate: Path | None) -> Path:
        default_root = cls.default_app_state_dir_for(candidate)
        if candidate is None:
            return default_root

        resolved = safe_resolve(candidate.expanduser())
        if not cls.is_safe_storage_path(resolved):
            return default_root
        if not _is_writable_directory(resolved):
            return default_root
        return resolved

    @classmethod
    def select_minecraft_dir(cls, candidate: Path | None, app_state_dir: Path) -> tuple[Path, bool]:
        default_dir = cls.default_minecraft_dir(app_state_dir)
        if candidate is None:
            return default_dir, True

        resolved = safe_resolve(candidate.expanduser())
        if not cls.is_safe_storage_path(resolved):
            return default_dir, False
        if not _is_writable_directory(resolved):
            return default_dir, False
        return resolved, True

    @classmethod
    def build_layout(cls, *, app_state_dir: Path, minecraft_dir: Path | None = None) -> StorageLayout:
        selected_minecraft_dir, _accepted = cls.select_minecraft_dir(minecraft_dir, app_state_dir)
        return StorageLayout(
            app_state_dir=app_state_dir,
            minecraft_dir=selected_minecraft_dir,
            games_dir=selected_minecraft_dir / "games",
        )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_protected_runtime_base(path: Path) -> bool:
    lowered_parts = {part.lower() for part in safe_resolve(path).parts}
    if {"windowsapps", "program files", "program files (x86)"} & lowered_parts:
        return True
    if sys.platform == "darwin" and any(part.lower().endswith(".app") for part in path.parts):
        return True
    return False


@dataclass(frozen=True)
class LauncherPaths:
    app_dir: Path
    app_state_dir: Path
    minecraft_dir: Path
    games_dir: Path

    @staticmethod
    def _resolve_frozen_base() -> Path:
        override = os.environ.get("TENSALAUNCHER_APP_BASE")
        if override:
            return PathPolicy.select_app_state_dir(Path(override).expanduser())
        return _resolve_configured_user_data_base()

    @staticmethod
    def _resolve_dev_base() -> Path:
        for candidate in _walk_candidates(Path.cwd()):
            if _looks_like_project_root(candidate):
                return candidate / DEV_ROOT_DIRNAME

        for candidate in _walk_candidates(Path(__file__).resolve()):
            if _looks_like_project_root(candidate):
                return candidate / DEV_ROOT_DIRNAME

        return safe_resolve(Path.cwd()) / DEV_ROOT_DIRNAME

    @classmethod
    def detect(cls) -> "LauncherPaths":
        base = cls._resolve_frozen_base() if is_frozen() else cls._resolve_dev_base()
        minecraft_dir, _accepted = PathPolicy.select_minecraft_dir(_runtime_minecraft_dir_override, base)
        return cls(
            app_dir=base,
            app_state_dir=base,
            minecraft_dir=minecraft_dir,
            games_dir=minecraft_dir / "games",
        )


class PathService:
    def __init__(self) -> None:
        self.paths = LauncherPaths.detect()
        self.minecraft_dir_error: str | None = None

    def refresh(self) -> None:
        self.paths = LauncherPaths.detect()

    def prepare(self) -> None:
        self.refresh()
        self.minecraft_dir_error = None
        self.migrate_legacy_app_state()
        try:
            self.init_directories()
        except OSError as exc:
            self.minecraft_dir_error = str(exc)
            _log_warning(f"Configured Minecraft directory '{self.paths.minecraft_dir}' is unavailable: {exc!r}")

    def init_directories(self) -> None:
        for directory in (self.paths.minecraft_dir, self.paths.games_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _legacy_state_roots(self) -> list[Path]:
        roots = [self.paths.minecraft_dir]
        legacy_user_data_roots = [_resolve_app_state_pointer_base()]
        if sys.platform.startswith("win"):
            legacy_user_data_roots.append(_resolve_windows_legacy_local_app_state_dir())
            legacy_user_data_roots.append(_resolve_windows_legacy_profile_app_state_dir())
        if sys.platform.startswith("linux"):
            legacy_user_data_roots.append(_resolve_linux_legacy_data_app_state_dir())
        for legacy_user_data_base in legacy_user_data_roots:
            if legacy_user_data_base != self.paths.app_state_dir:
                roots.append(legacy_user_data_base)
        if sys.platform.startswith("win"):
            legacy_program_files_base = safe_resolve(_resolve_windows_program_files_home() / APP_STATE_DIRNAME)
            if legacy_program_files_base != self.paths.app_state_dir:
                roots.append(legacy_program_files_base)
        if is_frozen():
            legacy_base = _resolve_sidecar_frozen_base()
            roots.extend((legacy_base, legacy_base / "minecraft"))
        return _unique_paths(*roots)

    def migrate_legacy_app_state(self) -> None:
        target_root = self.paths.app_state_dir
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log_warning(f"Unable to prepare app-state directory '{target_root}': {exc}")
            return

        legacy_roots = self._legacy_state_roots()
        migrated_roots: list[Path] = []
        migrated_config_root: Path | None = None
        for filename in STATE_FILENAMES:
            target_path = target_root / filename
            if target_path.exists():
                continue
            for legacy_root in legacy_roots:
                legacy_path = legacy_root / filename
                if not legacy_path.exists():
                    continue
                try:
                    shutil.copy2(legacy_path, target_path)
                except OSError as exc:
                    _log_warning(f"Unable to migrate legacy state file '{legacy_path}' -> '{target_path}': {exc}")
                    continue
                migrated_roots.append(legacy_root)
                if filename == "config.json":
                    migrated_config_root = legacy_root
                break
        minecraft_candidate_roots = _unique_paths(*(migrated_roots or []), *legacy_roots)
        self._preserve_migrated_minecraft_dir(target_root, minecraft_candidate_roots, migrated_config_root)

    def _preserve_migrated_minecraft_dir(
        self,
        target_root: Path,
        legacy_roots: list[Path],
        migrated_config_root: Path | None,
    ) -> None:
        if not legacy_roots:
            return
        config_path = target_root / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            _log_warning(f"Unable to inspect migrated config '{config_path}': {exc}")
            return
        if not isinstance(config, dict):
            return

        current_default = safe_resolve(PathPolicy.default_minecraft_dir(self.paths.app_state_dir))
        existing_value = str(config.get(MINECRAFT_DIR_CONFIG_KEY, "") or "").strip()
        config_changed = False
        if existing_value:
            existing_path = Path(existing_value).expanduser()
            if existing_path.is_absolute():
                if self._migrate_legacy_default_minecraft_dir(existing_path, target_root, migrated_config_root):
                    config_changed = self._remove_minecraft_dir_override(config) or config_changed
                    if config_changed:
                        self._write_migrated_config(config_path, config)
                    return
                if self._is_migrated_minecraft_dir_acceptable(target_root, existing_path):
                    return
                config_changed = self._remove_minecraft_dir_override(config) or config_changed
            else:
                relative_candidate = safe_resolve(target_root / existing_path)
                if self._migrate_legacy_default_minecraft_dir(relative_candidate, target_root, target_root):
                    config_changed = self._remove_minecraft_dir_override(config) or config_changed
                    if config_changed:
                        self._write_migrated_config(config_path, config)
                    return
                if self._has_minecraft_content(relative_candidate):
                    if self._is_migrated_minecraft_dir_acceptable(target_root, relative_candidate):
                        return
                    config_changed = self._remove_minecraft_dir_override(config) or config_changed
            if migrated_config_root is not None:
                candidate = safe_resolve(migrated_config_root / existing_path)
                if self._migrate_legacy_default_minecraft_dir(candidate, target_root, migrated_config_root):
                    config_changed = self._remove_minecraft_dir_override(config) or config_changed
                    if config_changed:
                        self._write_migrated_config(config_path, config)
                    return
                if self._write_migrated_minecraft_dir(config_path, config, candidate, target_root):
                    return
        elif self._has_minecraft_content(current_default):
            return

        for legacy_root in legacy_roots:
            candidate = self._legacy_minecraft_dir_for_root(legacy_root)
            if candidate is None or candidate == current_default:
                continue
            if self._migrate_legacy_default_minecraft_dir(candidate, target_root, legacy_root):
                if config_changed:
                    self._write_migrated_config(config_path, config)
                return
            if self._write_migrated_minecraft_dir(config_path, config, candidate, target_root):
                return

        if config_changed:
            self._write_migrated_config(config_path, config)

    @staticmethod
    def _is_migrated_minecraft_dir_acceptable(target_root: Path, minecraft_dir: Path) -> bool:
        selected, accepted = PathPolicy.select_minecraft_dir(minecraft_dir, target_root)
        return accepted and safe_resolve(selected) == safe_resolve(minecraft_dir)

    def _write_migrated_minecraft_dir(
        self,
        config_path: Path,
        config: dict,
        minecraft_dir: Path,
        target_root: Path,
    ) -> bool:
        if not self._is_migrated_minecraft_dir_acceptable(target_root, minecraft_dir):
            self._remove_minecraft_dir_override(config)
            _log_warning(f"Skipped unsafe legacy Minecraft directory: {minecraft_dir}")
            return False

        config[MINECRAFT_DIR_CONFIG_KEY] = str(minecraft_dir)
        if not self._write_migrated_config(config_path, config):
            return False
        _log_info(f"Preserved legacy Minecraft directory: {minecraft_dir}")
        return True

    def _migrate_legacy_default_minecraft_dir(
        self,
        minecraft_dir: Path,
        target_root: Path,
        legacy_root: Path | None,
    ) -> bool:
        source = safe_resolve(minecraft_dir.expanduser())
        if not self._is_legacy_default_minecraft_dir(source, target_root, legacy_root):
            return False
        if not PathPolicy.is_safe_storage_path(source):
            self._remove_unsafe_minecraft_override(source)
            return False

        default_dir = safe_resolve(PathPolicy.default_minecraft_dir(target_root))
        selected, accepted = PathPolicy.select_minecraft_dir(default_dir, target_root)
        if not accepted or safe_resolve(selected) != default_dir:
            return False
        if self._has_minecraft_content(default_dir):
            return True
        if not self._has_minecraft_content(source):
            return False

        try:
            default_dir.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                target = default_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, target, dirs_exist_ok=True)
                elif child.is_file() and not target.exists():
                    shutil.copy2(child, target)
        except OSError as exc:
            _log_warning(f"Unable to migrate Minecraft directory '{source}' -> '{default_dir}': {exc}")
            return False

        _log_info(f"Migrated Minecraft directory to default location: {source} -> {default_dir}")
        return True

    @staticmethod
    def _is_legacy_default_minecraft_dir(
        minecraft_dir: Path,
        target_root: Path,
        legacy_root: Path | None,
    ) -> bool:
        resolved = safe_resolve(minecraft_dir)
        default_dir = safe_resolve(PathPolicy.default_minecraft_dir(target_root))
        if resolved == default_dir:
            return False
        if sys.platform.startswith("linux"):
            if resolved == safe_resolve(target_root / "minecraft"):
                return True
            candidate_roots = [legacy_root] if legacy_root is not None else []
            candidate_roots.append(_resolve_linux_legacy_data_app_state_dir())
            for root_candidate in _unique_paths(*(root for root in candidate_roots if root is not None)):
                if resolved != safe_resolve(root_candidate / "minecraft"):
                    continue
                if root_candidate == _resolve_linux_legacy_data_app_state_dir():
                    return True
                return any((root_candidate / filename).exists() for filename in STATE_FILENAMES)
            return False
        if not sys.platform.startswith("win"):
            return False
        if resolved == safe_resolve(target_root / "minecraft"):
            return True
        if legacy_root is None:
            return False
        root = safe_resolve(legacy_root)
        if resolved != safe_resolve(root / "minecraft"):
            return False
        if is_frozen() and root == _resolve_sidecar_frozen_base():
            return True
        return any((root / filename).exists() for filename in STATE_FILENAMES)

    @staticmethod
    def _remove_unsafe_minecraft_override(minecraft_dir: Path) -> None:
        _log_warning(f"Skipped unsafe legacy Minecraft directory: {minecraft_dir}")

    @staticmethod
    def _remove_minecraft_dir_override(config: dict) -> bool:
        changed = False
        for key in (MINECRAFT_DIR_CONFIG_KEY, *SETUP_WIZARD_CONFIG_KEYS):
            if key in config:
                config.pop(key, None)
                changed = True
        return changed

    @staticmethod
    def _write_migrated_config(config_path: Path, config: dict) -> bool:
        try:
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            _log_warning(f"Unable to update migrated config '{config_path}': {exc}")
            return False
        return True

    def _legacy_minecraft_dir_for_root(self, legacy_root: Path) -> Path | None:
        candidates = (legacy_root / "minecraft", legacy_root)
        for candidate in candidates:
            resolved = safe_resolve(candidate)
            if self._has_minecraft_content(resolved):
                return resolved
        return None

    @staticmethod
    def _has_minecraft_content(path: Path) -> bool:
        if not path.is_dir():
            return False
        for marker in MINECRAFT_DIR_MARKERS:
            marker_path = path / marker
            if marker_path.is_file():
                return True
            if marker_path.is_dir():
                try:
                    next(marker_path.iterdir())
                except (OSError, StopIteration):
                    continue
                return True
        return False

    def set_minecraft_dir_override(self, value: str | None) -> bool:
        global _runtime_minecraft_dir_override
        if value is None:
            _runtime_minecraft_dir_override = None
            accepted = True
        else:
            configured = Path(value).expanduser()
            if not configured.is_absolute():
                configured = self.paths.app_state_dir / configured
            selected, accepted = PathPolicy.select_minecraft_dir(configured, self.paths.app_state_dir)
            _runtime_minecraft_dir_override = selected if accepted else None
        self.refresh()
        return accepted

    def set_app_state_dir_override(self, value: str | None, *, persist: bool = True) -> bool:
        global _runtime_app_state_dir_override
        if value is None:
            _runtime_app_state_dir_override = None
            if persist:
                _write_app_state_pointer(None)
            accepted = True
        else:
            configured = Path(value).expanduser()
            if not configured.is_absolute():
                configured = self.paths.app_state_dir / configured
            selected = PathPolicy.select_app_state_dir(configured)
            accepted = selected == safe_resolve(configured)
            _runtime_app_state_dir_override = selected if accepted else None
            if persist:
                _write_app_state_pointer(_runtime_app_state_dir_override if accepted else None)
        self.refresh()
        return accepted
