from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from launcher import PRODUCT_NAME
from launcher.platform.paths import MINECRAFT_DIR_MARKERS, STATE_FILENAMES, PathPolicy

SETUP_WIZARD_COMPLETED_KEY = "setup_wizard_completed"
SETUP_WIZARD_VERSION = 1
SETUP_WIZARD_VERSION_KEY = "setup_wizard_version"
SUPPORTED_LANGUAGE_CODES = {"uk_UA", "en_US"}
_FIRST_RUN_KEYS = {
    "lang",
    "check_updates",
    "auto_update",
    "include_beta_updates",
    "compact_sidebar",
}


@dataclass(frozen=True, slots=True)
class NormalizedMinecraftDir:
    path: Path
    stored_value: str | None


class ConfigLike(Protocol):
    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        raise NotImplementedError

    def update(self, data: dict[str, Any], persist: bool = True) -> None:
        raise NotImplementedError

    def delete(self, key: str, persist: bool = True) -> None:
        raise NotImplementedError

    def keys(self) -> Iterable[object]:
        raise NotImplementedError


class SetupWizardService:
    def should_open(self, config: ConfigLike, storage_issues: list[str]) -> bool:
        if storage_issues:
            return True
        if str(config.get(SETUP_WIZARD_COMPLETED_KEY, "no")).lower() == "yes":
            return False
        return self._looks_like_fresh_config(config)

    def mark_completed(self, config: ConfigLike) -> None:
        config.update(
            {
                SETUP_WIZARD_COMPLETED_KEY: "yes",
                SETUP_WIZARD_VERSION_KEY: SETUP_WIZARD_VERSION,
            }
        )

    def normalize_app_state_dir(self, *, current_dir: Path, raw_value: str) -> Path:
        target = self.resolve_app_state_candidate(current_dir=current_dir, raw_value=raw_value)
        if not PathPolicy.is_safe_storage_path(target):
            return PathPolicy.default_app_state_dir_for(target)
        return target

    def resolve_app_state_candidate(self, *, current_dir: Path, raw_value: str) -> Path:
        raw = str(raw_value or "").strip()
        target = current_dir if not raw else Path(raw).expanduser()
        if not target.is_absolute():
            target = current_dir / target
        target = self._safe_resolve(target)
        if PathPolicy.is_safe_storage_path(target):
            target = self._rebase_non_empty_app_state_dir(target)
        return target

    def normalize_minecraft_dir(self, *, app_dir: Path, raw_value: str) -> NormalizedMinecraftDir:
        default_dir = PathPolicy.default_minecraft_dir(app_dir)
        raw = str(raw_value or "").strip()
        target = default_dir if not raw else Path(raw).expanduser()
        if not target.is_absolute():
            target = app_dir / target
        target = self._safe_resolve(target)
        if not PathPolicy.is_safe_storage_path(target):
            target = self._safe_resolve(default_dir)
        else:
            target = self._rebase_non_empty_minecraft_dir(target, app_dir=app_dir)

        if target == self._safe_resolve(default_dir):
            return NormalizedMinecraftDir(path=target, stored_value=None)

        try:
            relative_target = target.relative_to(app_dir)
        except ValueError:
            stored_value = str(target)
        else:
            stored_value = str(relative_target)
        return NormalizedMinecraftDir(path=target, stored_value=stored_value)

    def default_world_backups_dir(self, minecraft_dir: Path) -> Path:
        return minecraft_dir / "backups" / "worlds"

    def derived_paths(self, app_state_dir: Path) -> tuple[Path, Path]:
        minecraft_dir = PathPolicy.default_minecraft_dir(app_state_dir)
        return minecraft_dir, self.default_world_backups_dir(minecraft_dir)

    def apply_paths(
        self,
        app: Any,
        *,
        app_state_dir: str | None = None,
        minecraft_dir: str,
        backups_dir: str,
        language: str | None = None,
    ) -> None:
        current_state_dir = self._current_app_state_dir(app)
        target_state_dir = self.normalize_app_state_dir(
            current_dir=current_state_dir,
            raw_value=app_state_dir or str(current_state_dir),
        )
        self.ensure_writable(target_state_dir)

        state_changed = target_state_dir != current_state_dir
        if state_changed:
            self._migrate_state_files(current_state_dir, target_state_dir)
            app.util.set_app_state_dir_override(str(target_state_dir), persist=True)
            config = self._build_config(target_state_dir)
        else:
            config = app.config

        normalized = self.normalize_minecraft_dir(
            app_dir=target_state_dir,
            raw_value=minecraft_dir,
        )
        self.ensure_writable(normalized.path)

        if normalized.stored_value is None:
            app.util.set_minecraft_dir_override(None)
            config.delete("minecraft_game_dir")
        else:
            app.util.set_minecraft_dir_override(normalized.stored_value)
            config.set("minecraft_game_dir", normalized.stored_value)

        backups_target = Path(str(backups_dir or "")).expanduser()
        if not backups_target.is_absolute():
            backups_target = normalized.path / backups_target
        backups_target = self._safe_resolve(backups_target)
        self.ensure_writable(backups_target)

        default_backups = self.default_world_backups_dir(normalized.path)
        if backups_target == self._safe_resolve(default_backups):
            config.delete("world_backups_dir")
        else:
            config.set("world_backups_dir", str(backups_target))

        if language in SUPPORTED_LANGUAGE_CODES:
            config.set("lang", language)

        self.mark_completed(config)

    def storage_issues(
        self,
        *,
        app_state_dir: Path,
        minecraft_dir: Path,
        launcher_label: str = "launcher data",
        minecraft_label: str = "minecraft data",
    ) -> list[str]:
        issues: list[str] = []
        for label, directory in (
            (launcher_label, app_state_dir),
            (minecraft_label, minecraft_dir),
        ):
            if not PathPolicy.is_safe_storage_path(directory):
                issues.append(f"{label}: unsafe package directory")
                continue
            issue = self.check_storage_candidate(directory)
            if issue:
                issues.append(f"{label}: {issue}")
        return issues

    def check_storage_candidate(self, directory: Path) -> str | None:
        try:
            self._check_storage_candidate(directory)
        except OSError as exc:
            return str(exc)
        return None

    def check_writable(self, directory: Path) -> str | None:
        try:
            self.ensure_writable(directory)
        except OSError as exc:
            return str(exc)
        return None

    def ensure_writable(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".tensalauncher-write-test-", dir=directory, delete=True):
            pass

    def _check_storage_candidate(self, directory: Path) -> None:
        directory = self._safe_resolve(directory.expanduser())
        if directory.exists() and not directory.is_dir():
            raise OSError(f"{directory} exists and is not a directory")
        probe_dir = directory if directory.is_dir() else self._first_existing_parent(directory)
        if probe_dir is None:
            raise OSError(f"{directory} has no existing writable parent directory")
        with tempfile.NamedTemporaryFile(prefix=".tensalauncher-write-test-", dir=probe_dir, delete=True):
            pass

    def _first_existing_parent(self, directory: Path) -> Path | None:
        current = directory
        while True:
            parent = current.parent
            if parent == current:
                return current if current.is_dir() else None
            if parent.exists():
                return parent if parent.is_dir() else None
            current = parent

    def _rebase_non_empty_app_state_dir(self, target: Path) -> Path:
        if not self._is_non_empty_plain_directory(
            target,
            markers=STATE_FILENAMES,
            expected_dir_names=(PRODUCT_NAME,),
        ):
            return target
        return self._safe_resolve(target / PRODUCT_NAME)

    def _rebase_non_empty_minecraft_dir(self, target: Path, *, app_dir: Path) -> Path:
        if not self._is_non_empty_plain_directory(
            target,
            markers=MINECRAFT_DIR_MARKERS,
            expected_dir_names=("minecraft",),
        ):
            return target
        if self._safe_resolve(target) == self._safe_resolve(app_dir) or target.name.casefold() == PRODUCT_NAME.casefold():
            return self._safe_resolve(target / "minecraft")
        return self._safe_resolve(target / PRODUCT_NAME / "minecraft")

    def _is_non_empty_plain_directory(
        self,
        directory: Path,
        *,
        markers: Iterable[str],
        expected_dir_names: Iterable[str],
    ) -> bool:
        if not directory.is_dir():
            return False
        if directory.name.casefold() in {name.casefold() for name in expected_dir_names}:
            return False
        if any((directory / marker).exists() for marker in markers):
            return False
        try:
            next(directory.iterdir())
        except StopIteration:
            return False
        except OSError:
            return False
        return True

    def _current_app_state_dir(self, app: Any) -> Path:
        paths = getattr(app, "paths", None)
        value = getattr(paths, "app_state_dir", None)
        if value:
            return self._safe_resolve(Path(value))
        return self._safe_resolve(Path(app.util.app_state_dir))

    def _looks_like_fresh_config(self, config: ConfigLike) -> bool:
        keys = {str(key) for key in config.keys()}
        return not (keys - _FIRST_RUN_KEYS)

    def _safe_resolve(self, path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    def _migrate_state_files(self, source_dir: Path, target_dir: Path) -> None:
        if source_dir == target_dir:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename in STATE_FILENAMES:
            source = source_dir / filename
            target = target_dir / filename
            if not source.is_file() or target.exists():
                continue
            shutil.copy2(source, target)

    @staticmethod
    def _build_config(storage_dir: Path) -> ConfigLike:
        from launcher.storage.config_store import Config

        return Config(storage_dir=storage_dir)


__all__ = [
    "NormalizedMinecraftDir",
    "SETUP_WIZARD_COMPLETED_KEY",
    "SETUP_WIZARD_VERSION",
    "SETUP_WIZARD_VERSION_KEY",
    "SUPPORTED_LANGUAGE_CODES",
    "SetupWizardService",
]
