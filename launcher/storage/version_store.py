from __future__ import annotations

import json
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Dict, Optional

import launcher.core.util as util
from launcher.domain.version import Version


class Versions:
    _instance: Optional["Versions"] = None
    _storage_dir: Path | None = None
    _minecraft_dir: Path | None = None

    def __init__(self, *, storage_dir: Path | None = None, minecraft_dir: Path | None = None) -> None:
        if Versions._instance is not None:
            raise RuntimeError("Use Versions.instance() instead of creating manually.")

        self.storage_dir = Path(storage_dir or self._storage_dir or util.app_state_dir)
        self.minecraft_dir = Path(minecraft_dir or self._minecraft_dir or util.minecraft_dir)
        self.filepath = self.storage_dir / "versions.json"
        self._versions: Dict[str, Version] = {}
        self._load_file()
        Versions._instance = self

    @classmethod
    def configure(cls, *, storage_dir: Path, minecraft_dir: Path) -> None:
        cls._storage_dir = Path(storage_dir)
        cls._minecraft_dir = Path(minecraft_dir)
        if cls._instance is not None:
            cls._instance = None

    @classmethod
    def instance(cls) -> "Versions":
        if cls._instance is None:
            cls._instance = Versions()
        return cls._instance

    def _load_file(self) -> None:
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            for version_id, version_data in data.items():
                self._versions[version_id] = Version(version_id, version_data)

    def _save_version(self, version: Version) -> None:
        data = {}
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        data[version.version_id] = version.to_dict()
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

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

    def find_or_create(self, version_id: str, defaults: Optional[Dict] = None) -> Version:
        version = self.get(version_id)
        if version:
            return version

        version = Version(version_id, defaults or {})
        self.add(version)
        return version

    def add(self, version: Version) -> None:
        self._versions[version.version_id] = version
        self._save_version(version)

    def remove(self, version_id: str, *, delete_files: bool = True) -> None:
        version = self._versions.get(version_id)
        if not version:
            return

        del self._versions[version_id]
        if delete_files:
            dir_path = Path(version.path)
            if not dir_path.is_absolute():
                dir_path = self.minecraft_dir / dir_path

            if dir_path.is_dir():
                with suppress(OSError):
                    shutil.rmtree(dir_path)

        data = {}
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        data.pop(version_id, None)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

    def to_dict(self) -> Dict[str, Dict]:
        return {version_id: version.to_dict() for version_id, version in self._versions.items()}
