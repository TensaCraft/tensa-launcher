from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict

from launcher.application.version_runtime import VersionRuntime
from launcher.domain.version import Version
from launcher.storage.atomic import atomic_write_json


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
        self._versions: Dict[str, Version] = {}
        self._runtime: VersionRuntime | None = None
        self._load_file()

    def _load_file(self) -> None:
        data = self._read_file() or {}
        for version_id, version_data in data.items():
            version = Version(version_id, version_data)
            version.bind_persistence(self._save_version)
            version.bind_runtime(self._runtime)
            self._versions[version_id] = version

    def _read_file(self) -> dict | None:
        if not self.filepath.exists():
            return None
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _save_version(self, version: Version) -> None:
        data = self._read_file() or {}
        data[version.version_id] = version.to_dict()
        atomic_write_json(self.filepath, data, ensure_ascii=False, indent=4)
        self._versions[version.version_id] = version

    def all(self) -> list[Version]:
        return list(self._versions.values())

    def get(self, version_id: str) -> Version | None:
        version = self._versions.get(version_id)
        if version:
            return version
        for item in self._versions.values():
            if getattr(item, "ver_id", None) == version_id or getattr(item, "id", None) == version_id:
                return item
        return None

    def get_by_name(self, name: str) -> Version | None:
        for version in self._versions.values():
            if version.name == name:
                return version
        return None

    def add(self, version: Version) -> None:
        self.prepare(version)
        self._save_version(version)

    def prepare(self, version: Version) -> Version:
        """Bind a new version to this store without persisting an incomplete install."""
        version.bind_persistence(self._save_version)
        version.bind_runtime(self._runtime)
        return version

    def bind_runtime(self, runtime: VersionRuntime | None) -> None:
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
        version = self._versions.get(version_id)
        if not version:
            return

        persisted = self._read_file()
        if persisted is None:
            persisted = {stored_id: stored.to_dict() for stored_id, stored in self._versions.items()}
        persisted.pop(version_id, None)
        atomic_write_json(
            self.filepath,
            persisted,
            ensure_ascii=False,
            indent=4,
        )

        candidate = dict(self._versions)
        del candidate[version_id]
        self._versions = candidate
        version.bind_persistence(None)
        version.bind_runtime(None)
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
        return {version_id: version.to_dict() for version_id, version in self._versions.items()}
