from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from launcher.application.platform_lock import FileLockOwner, OSFileLock, OSFileLockBusy


class SharedResourceBusy(RuntimeError):
    def __init__(
        self,
        path: Path,
        active_kind: str,
        *,
        owner: FileLockOwner | None = None,
    ) -> None:
        self.path = path
        self.active_kind = active_kind
        self.owner = owner
        super().__init__(f"Shared resource is busy with operation '{active_kind}': {path}")


@dataclass
class _SharedOperation:
    owner_thread: int
    kind: str
    file_lock: OSFileLock
    depth: int = 1


class SharedResourceCoordinator:
    """Serialize writes to shared roots while allowing same-thread loader nesting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, _SharedOperation] = {}

    @contextmanager
    def operation(self, path: str | Path, kind: str) -> Iterator[None]:
        resolved_path, key = self._identity(path)
        owner_thread = threading.get_ident()
        operation_kind = str(kind or "operation").strip() or "operation"

        with self._lock:
            active = self._active.get(key)
            if active is None:
                try:
                    file_lock = OSFileLock.try_acquire(
                        resolved_path,
                        "shared",
                        operation_kind,
                    )
                except OSFileLockBusy as error:
                    active_kind = error.owner.kind if error.owner is not None else "external_operation"
                    raise SharedResourceBusy(
                        resolved_path,
                        active_kind,
                        owner=error.owner,
                    ) from None
                try:
                    self._active[key] = _SharedOperation(owner_thread, operation_kind, file_lock)
                except BaseException:
                    file_lock.release()
                    raise
            elif active.owner_thread == owner_thread:
                active.depth += 1
            else:
                raise SharedResourceBusy(
                    resolved_path,
                    active.kind,
                    owner=active.file_lock.owner,
                )

        try:
            yield
        finally:
            with self._lock:
                active = self._active.get(key)
                if active is None or active.owner_thread != owner_thread:
                    raise RuntimeError("Shared resource operation ownership was lost")
                active.depth -= 1
                if active.depth == 0:
                    self._active.pop(key, None)
                    active.file_lock.release()

    def active_kind(self, path: str | Path) -> str | None:
        _, key = self._identity(path)
        with self._lock:
            active = self._active.get(key)
            return active.kind if active is not None else None

    @staticmethod
    def _identity(path: str | Path) -> tuple[Path, str]:
        resolved = Path(path).expanduser().resolve(strict=False)
        return resolved, os.path.normcase(str(resolved))


__all__ = ["SharedResourceBusy", "SharedResourceCoordinator"]
