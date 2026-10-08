from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from .adapters import absolute_path
from .models import JsonObject, MigrationError


def safe_path(path: Path) -> Path:
    absolute_path(str(path), path)
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise MigrationError("unsafe_path")
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise MigrationError("unsafe_path")
        if parent == path and stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise MigrationError("unsafe_path")
    return path


def fingerprint(path: Path) -> str | None:
    safe_path(path)
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def read_object(path: Path, *, missing: bool = False) -> JsonObject:
    safe_path(path)
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("oversized metadata")
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite number")))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except FileNotFoundError:
        if missing:
            return {}
        raise MigrationError("invalid_json", path.name) from None
    except (ValueError, UnicodeError):
        raise MigrationError("invalid_json", path.name) from None


def tree_files(root: Path) -> list[Path]:
    safe_path(root)
    files = []

    def failed(error: OSError):
        raise error

    for parent, directories, names in os.walk(root, followlinks=False, onerror=failed):
        for name in directories:
            safe_path(Path(parent) / name)
        for name in names:
            path = safe_path(Path(parent) / name)
            if not path.is_file():
                raise MigrationError("unsafe_path")
            files.append(path)
    return sorted(files)
