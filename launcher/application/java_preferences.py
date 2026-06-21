from __future__ import annotations

from pathlib import Path
from typing import Any


class JavaPreferencesService:
    CUSTOM_CONFIG_KEY = "custom_java_versions"
    LAUNCHER_CACHE_KEY = "launcher_java_versions"
    LAUNCHER_CACHE_TS_KEY = "launcher_java_versions_last_scan"
    _EXECUTABLE_NAMES = {"java", "java.exe", "javaw", "javaw.exe", "minecraftjava.exe"}

    @classmethod
    def normalize_entries(cls, entries: Any) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        if not isinstance(entries, list):
            return normalized

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for raw_label, raw_path in entry.items():
                label = str(raw_label or "").strip()
                path = str(raw_path or "").strip()
                if not label or not path:
                    continue
                path_key = cls._path_key(path)
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                normalized.append({label: path})
                break
        return normalized

    @classmethod
    def merge_java_entries(cls, launcher_entries: Any, custom_entries: Any) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for entries in (launcher_entries, custom_entries):
            for entry in cls.normalize_entries(entries):
                label, path = next(iter(entry.items()))
                path_key = cls._path_key(path)
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                merged.append({label: path})
        return merged

    @classmethod
    def add_custom_java(cls, entries: Any, name: str, executable_path: str) -> list[dict[str, str]]:
        path = cls._resolve_java_executable(executable_path)
        label = (name or "").strip() or cls.label_from_path(path)
        existing = [entry for entry in cls.normalize_entries(entries) if cls._path_key(next(iter(entry.values()))) != cls._path_key(path)]
        existing.append({label: str(path)})
        return existing

    @classmethod
    def import_discovered_java(cls, entries: Any, discovered_entries: Any) -> tuple[list[dict[str, str]], int]:
        existing = cls.normalize_entries(entries)
        seen_paths = {cls._path_key(next(iter(entry.values()))) for entry in existing}
        added_count = 0

        for entry in cls.normalize_entries(discovered_entries):
            raw_label, raw_path = next(iter(entry.items()))
            try:
                path = cls._resolve_java_executable(raw_path)
            except ValueError:
                continue
            path_key = cls._path_key(path)
            if path_key in seen_paths:
                continue
            label = (raw_label or "").strip() or cls.label_from_path(path)
            existing.append({label: str(path)})
            seen_paths.add(path_key)
            added_count += 1

        return existing, added_count

    @classmethod
    def has_raw_launcher_runtime_labels(cls, entries: Any) -> bool:
        for entry in cls.normalize_entries(entries):
            label = next(iter(entry.keys()))
            if label.startswith("java-runtime-"):
                return True
        return False

    @classmethod
    def remove_custom_java(cls, entries: Any, executable_path: str) -> list[dict[str, str]]:
        target = cls._path_key(executable_path)
        return [entry for entry in cls.normalize_entries(entries) if cls._path_key(next(iter(entry.values()))) != target]

    @classmethod
    def label_from_path(cls, executable_path: Path) -> str:
        parent = executable_path.parent
        runtime_root = parent.parent if parent.name.lower() == "bin" else parent
        return runtime_root.name or executable_path.name

    @classmethod
    def _resolve_java_executable(cls, executable_path: str) -> Path:
        raw_path = (executable_path or "").strip().strip('"')
        if not raw_path:
            raise ValueError("invalid_java_executable")
        path = Path(raw_path).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise ValueError("invalid_java_executable") from exc
        if not path.is_file() or path.name.lower() not in cls._EXECUTABLE_NAMES:
            raise ValueError("invalid_java_executable")
        return path

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return str(path).strip().casefold()
