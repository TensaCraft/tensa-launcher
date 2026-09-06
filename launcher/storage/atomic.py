from __future__ import annotations

import json
import os
import shutil
import tempfile
from _thread import RLock
from contextlib import suppress
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

_LOCKS_GUARD = RLock()
_LOCKS: WeakValueDictionary[Path, RLock] = WeakValueDictionary()


def path_lock(path: Path) -> RLock:
    """Share a process-local read/modify/write lock between stores of the same file."""
    key = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _LOCKS[key] = lock
        return lock


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        if target.exists():
            with suppress(OSError):
                temporary_path.chmod(target.stat().st_mode)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
    sort_keys: bool = False,
) -> None:
    content = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
    )
    atomic_write_text(path, content)


def atomic_copy_file(source: Path, destination: Path) -> None:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary_path = Path(raw_path)
        shutil.copy2(source, temporary_path, follow_symlinks=False)
        with temporary_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


__all__ = ["atomic_copy_file", "atomic_write_json", "atomic_write_text", "path_lock"]
