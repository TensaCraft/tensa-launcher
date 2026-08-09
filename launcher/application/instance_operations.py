from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from launcher.application.platform_lock import FileLockOwner, OSFileLock, OSFileLockBusy

_ResultT = TypeVar("_ResultT")


class InstanceOperationBusy(RuntimeError):
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
        super().__init__(f"Instance is busy with operation '{active_kind}': {path}")


class InstanceOperationLease:
    def __init__(
        self,
        coordinator: "InstanceOperationCoordinator",
        *,
        key: str,
        path: Path,
        kind: str,
        token: str,
        file_lock: OSFileLock,
    ) -> None:
        self._coordinator = coordinator
        self.key = key
        self.path = path
        self.kind = kind
        self.token = token
        self._file_lock = file_lock
        self._released = False
        self._release_requested = False
        self._borrow_count = 0

    @property
    def active(self) -> bool:
        return self._coordinator._lease_active(self)

    def release(self) -> None:
        self._coordinator._release(self)

    def __enter__(self) -> "InstanceOperationLease":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


class InstanceOperationCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, InstanceOperationLease] = {}

    def try_acquire(self, path: str | Path, kind: str) -> InstanceOperationLease:
        resolved_path, key = self._identity(path)
        operation_kind = str(kind or "operation").strip() or "operation"
        with self._lock:
            active = self._active.get(key)
            if active is not None:
                raise InstanceOperationBusy(
                    resolved_path,
                    active.kind,
                    owner=active._file_lock.owner,
                )
            token = uuid.uuid4().hex
            try:
                file_lock = OSFileLock.try_acquire(
                    resolved_path,
                    "instance",
                    operation_kind,
                    token=token,
                )
            except OSFileLockBusy as error:
                active_kind = error.owner.kind if error.owner is not None else "external_operation"
                raise InstanceOperationBusy(
                    resolved_path,
                    active_kind,
                    owner=error.owner,
                ) from None
            try:
                lease = InstanceOperationLease(
                    self,
                    key=key,
                    path=resolved_path,
                    kind=operation_kind,
                    token=token,
                    file_lock=file_lock,
                )
            except BaseException:
                file_lock.release()
                raise
            self._active[key] = lease
            return lease

    @contextmanager
    def operation(
        self,
        path: str | Path,
        kind: str,
        *,
        lease: InstanceOperationLease | None = None,
    ) -> Iterator[InstanceOperationLease]:
        if lease is not None:
            self._pin_borrowed(path, lease)
            try:
                yield lease
            finally:
                self._unpin_borrowed(lease)
            return

        owned = self.try_acquire(path, kind)
        try:
            yield owned
        finally:
            owned.release()

    def active_kind(self, path: str | Path) -> str | None:
        _, key = self._identity(path)
        with self._lock:
            active = self._active.get(key)
            return active.kind if active is not None else None

    def execute(
        self,
        path: str | Path,
        kind: str,
        callback: Callable[..., _ResultT],
        /,
        *args: Any,
        lease: InstanceOperationLease | None = None,
        **kwargs: Any,
    ) -> _ResultT:
        with self.operation(path, kind, lease=lease):
            return callback(*args, **kwargs)

    def _pin_borrowed(
        self,
        path: str | Path,
        lease: InstanceOperationLease,
    ) -> None:
        _, key = self._identity(path)
        with self._lock:
            if (
                lease._coordinator is not self
                or lease.key != key
                or lease._released
                or lease._release_requested
                or self._active.get(key) is not lease
            ):
                raise ValueError("Borrowed instance operation lease is invalid or no longer active")
            lease._borrow_count += 1

    def _unpin_borrowed(self, lease: InstanceOperationLease) -> None:
        with self._lock:
            if lease._borrow_count <= 0:
                raise RuntimeError("Borrowed instance operation lease is not pinned")
            lease._borrow_count -= 1
            if lease._borrow_count == 0 and lease._release_requested:
                lease._released = True
                self._active.pop(lease.key, None)
                lease._file_lock.release()

    def _release(self, lease: InstanceOperationLease) -> None:
        with self._lock:
            if lease._released or lease._release_requested:
                return
            if self._active.get(lease.key) is not lease:
                lease._released = True
                lease._file_lock.release()
                return
            if lease._borrow_count:
                lease._release_requested = True
                return
            lease._released = True
            self._active.pop(lease.key, None)
            lease._file_lock.release()

    def _lease_active(self, lease: InstanceOperationLease) -> bool:
        with self._lock:
            return not lease._released and self._active.get(lease.key) is lease

    @staticmethod
    def _identity(path: str | Path) -> tuple[Path, str]:
        resolved = Path(path).expanduser().resolve(strict=False)
        return resolved, os.path.normcase(str(resolved))


__all__ = [
    "InstanceOperationBusy",
    "InstanceOperationCoordinator",
    "InstanceOperationLease",
]
