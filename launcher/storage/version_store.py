from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Dict
from weakref import WeakKeyDictionary

from launcher.domain.version import Version
from launcher.models.logger import Logger
from launcher.storage.atomic import atomic_write_json, path_lock

if TYPE_CHECKING:
    from launcher.application.version_runtime import VersionRuntime

def _merge_changes(current: dict, baseline: dict, persisted: dict) -> dict:
    result = deepcopy(persisted)
    for key in baseline.keys() - current.keys():
        result.pop(key, None)
    for key, value in current.items():
        if key not in baseline or value != baseline[key]:
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                previous = baseline.get(key)
                result[key] = _merge_changes(value, previous if isinstance(previous, dict) else {}, result[key])
            else:
                result[key] = deepcopy(value)
    return result


class VersionDirectoryCleanupError(OSError):
    def __init__(self, version_id: str, path: Path, cause: OSError) -> None:
        self.version_id = version_id
        self.path = path
        super().__init__(
            f"Version {version_id!r} metadata was removed, but its "
            f"directory remains at {path}: {cause}"
        )


class Versions:
    def __init__(self, *, storage_dir: Path, minecraft_dir: Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.minecraft_dir = Path(minecraft_dir)
        self.filepath = self.storage_dir / "versions.json"
        self._lock = path_lock(self.filepath)
        self._versions: Dict[str, Version] = {}
        self._data: dict = {}
        self._snapshots: WeakKeyDictionary[Version, tuple[str, dict] | None] = WeakKeyDictionary()
        self._runtime: VersionRuntime | None = None
        self._load_file()

    def _load_file(self) -> None:
        with self._lock:
            self._data = self._read_file() or {}
            for version_id, version_data in self._data.items():
                if not isinstance(version_data, dict):
                    Logger.warning(f"Skipping invalid version record {version_id!r} in '{self.filepath}'.")
                    continue
                version_data = deepcopy(version_data)
                invalid_options = "options" in version_data and not isinstance(version_data["options"], dict)
                if invalid_options:
                    Logger.warning(f"Invalid options for version {version_id!r} in '{self.filepath}'; using defaults.")
                    version_data["options"] = {}
                version = self.prepare(Version(version_id, version_data))
                baseline = deepcopy(version.to_dict())
                if invalid_options:
                    baseline.pop("options", None)
                self._snapshots[version] = (version_id, baseline)
                self._versions[version_id] = version

    def _read_file(self) -> dict | None:
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            Logger.warning(f"Versions file '{self.filepath}' is not valid UTF-8 JSON; keeping cached metadata.")
            return None
        if not isinstance(data, dict):
            Logger.warning(f"Versions file '{self.filepath}' must contain an object; keeping cached metadata.")
            return None
        return data

    def _save_version(self, version: Version) -> None:
        with self._lock:
            if version not in self._snapshots:
                raise RuntimeError(f"Version {version.version_id!r} is not bound to this Versions store.")
            snapshot = self._snapshots[version]
            version_id, baseline = snapshot if snapshot is not None else (version.version_id, {})
            data = self._read_file()
            if data is None:
                data = deepcopy(self._data)
            elif snapshot is not None and version_id not in data:
                raise RuntimeError(f"Version {version_id!r} was removed from the Versions store.")
            current = deepcopy(version.to_dict())
            persisted = data.get(version_id)
            data[version_id] = _merge_changes(current, baseline, persisted) if isinstance(persisted, dict) else current
            atomic_write_json(self.filepath, data, ensure_ascii=False, indent=4)
            previous = self._versions.get(version_id)
            if previous is not None and previous is not version:
                self._unbind(previous)
            self._versions[version_id] = version
            self._snapshots[version] = (version_id, current)
            self._data = data

    def _unbind(self, version: Version) -> None:
        self._snapshots.pop(version, None)
        version.bind_persistence(None)
        version.bind_runtime(None)

    def all(self) -> list[Version]:
        with self._lock:
            return list(self._versions.values())

    def get(self, version_id: str) -> Version | None:
        with self._lock:
            version = self._versions.get(version_id)
            if version:
                return version
            for item in self._versions.values():
                if getattr(item, "ver_id", None) == version_id or getattr(item, "id", None) == version_id:
                    return item
        return None

    def get_by_name(self, name: str) -> Version | None:
        for version in self.all():
            if version.name == name:
                return version
        return None

    def add(self, version: Version) -> None:
        with self._lock:
            self.prepare(version)
            self._save_version(version)

    def prepare(self, version: Version) -> Version:
        """Bind a new version to this store without persisting an incomplete install."""
        with self._lock:
            self._snapshots.setdefault(version, None)
            version.bind_persistence(self._save_version)
            version.bind_runtime(self._runtime)
        return version

    def bind_runtime(self, runtime: VersionRuntime | None) -> None:
        with self._lock:
            self._runtime = runtime
            for version in self._versions.values():
                version.bind_runtime(runtime)

    def _version_dir_for_deletion(self, raw_path: str | Path) -> Path | None:
        try:
            allowed_root = self.minecraft_dir.resolve()
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self.minecraft_dir / candidate
            candidate = candidate.resolve()
        except (OSError, RuntimeError):
            return None

        if candidate == allowed_root or not candidate.is_relative_to(allowed_root):
            return None
        return candidate

    def remove(self, version_id: str, *, delete_files: bool = True) -> None:
        with self._lock:
            self._remove(version_id, delete_files=delete_files)

    def _remove(self, version_id: str, *, delete_files: bool) -> None:
        version = self._versions.get(version_id)
        if version is None:
            return

        persisted = self._read_file()
        if persisted is None:
            persisted = deepcopy(self._data)
        persisted.pop(version_id, None)
        atomic_write_json(
            self.filepath,
            persisted,
            ensure_ascii=False,
            indent=4,
        )

        del self._versions[version_id]
        self._data = persisted
        self._unbind(version)
        if delete_files:
            raw_path = getattr(version, "path", None)
            if isinstance(raw_path, (str, Path)) and str(raw_path).strip():
                dir_path = self._version_dir_for_deletion(raw_path)
                if dir_path is not None and dir_path.is_dir():
                    try:
                        shutil.rmtree(dir_path)
                    except OSError as exc:
                        raise VersionDirectoryCleanupError(version_id, dir_path, exc) from exc

    def to_dict(self) -> Dict[str, Dict]:
        with self._lock:
            return {version_id: version.to_dict() for version_id, version in self._versions.items()}
