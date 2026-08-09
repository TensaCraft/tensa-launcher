from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal, cast

LockNamespace = Literal["instance", "shared"]

_LOCK_DIRECTORY = ".tensalauncher-locks"
_LOCK_SENTINEL = b"\n"
_CONTENDED_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN})
_CONTENDED_WINERRORS = frozenset({32, 33})


@dataclass(frozen=True, slots=True)
class FileLockOwner:
    pid: int
    hostname: str
    kind: str
    token: str
    acquired_at: str
    resource: str

    @classmethod
    def from_payload(cls, payload: object) -> FileLockOwner | None:
        if not isinstance(payload, dict):
            return None
        pid = payload.get("pid")
        hostname = payload.get("hostname")
        kind = payload.get("kind")
        token = payload.get("token")
        acquired_at = payload.get("acquired_at")
        resource = payload.get("resource")
        if isinstance(pid, bool) or not isinstance(pid, int):
            return None
        if (
            not isinstance(hostname, str)
            or not isinstance(kind, str)
            or not isinstance(token, str)
            or not isinstance(acquired_at, str)
            or not isinstance(resource, str)
        ):
            return None
        return cls(
            pid=pid,
            hostname=hostname,
            kind=kind,
            token=token,
            acquired_at=acquired_at,
            resource=resource,
        )


class OSFileLockBusy(RuntimeError):
    def __init__(self, path: Path, owner: FileLockOwner | None) -> None:
        self.path = path
        self.owner = owner
        super().__init__(f"OS file lock is already held: {path}")


class OSFileLock:
    """A nonblocking process lock whose persistent file only stores diagnostics."""

    def __init__(
        self,
        *,
        path: Path,
        resource_path: Path,
        handle: BinaryIO,
        owner: FileLockOwner,
    ) -> None:
        self.path = path
        self.resource_path = resource_path
        self.owner = owner
        self._handle: BinaryIO | None = handle

    @classmethod
    def try_acquire(
        cls,
        resource_path: str | Path,
        namespace: LockNamespace,
        kind: str,
        *,
        token: str | None = None,
    ) -> OSFileLock:
        resolved_path = Path(resource_path).expanduser().resolve(strict=False)
        lock_path = lock_file_path(resolved_path, namespace)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = _open_lock_file(lock_path)
        try:
            try:
                _lock_nonblocking(handle)
            except _LockContended as error:
                owner = _read_owner(handle)
                raise OSFileLockBusy(lock_path, owner) from error

            owner = FileLockOwner(
                pid=os.getpid(),
                hostname=socket.gethostname(),
                kind=kind,
                token=token or uuid.uuid4().hex,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                resource=str(resolved_path),
            )
            acquired = cls(
                path=lock_path,
                resource_path=resolved_path,
                handle=handle,
                owner=owner,
            )
            try:
                acquired._write_owner_metadata(owner)
            except BaseException:
                acquired.release()
                raise
            return acquired
        except BaseException:
            if not handle.closed:
                handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
        finally:
            handle.close()

    def _write_owner_metadata(self, owner: FileLockOwner) -> None:
        handle = self._handle
        if handle is None:
            raise RuntimeError("Cannot update metadata for a released OS file lock")
        payload = {"schema": 1, **asdict(owner)}
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        handle.seek(0)
        handle.write(_LOCK_SENTINEL + encoded + b"\n")
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
        self.owner = owner

    def __enter__(self) -> OSFileLock:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


class _LockContended(Exception):
    pass


def lock_file_path(resource_path: str | Path, namespace: LockNamespace) -> Path:
    resolved_path = Path(resource_path).expanduser().resolve(strict=False)
    identity = os.path.normcase(str(resolved_path))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return resolved_path.parent / _LOCK_DIRECTORY / f"{namespace}-{digest}.lock"


def _open_lock_file(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.set_inheritable(descriptor, False)
        handle = cast(BinaryIO, os.fdopen(descriptor, "r+b", buffering=0))
    except BaseException:
        os.close(descriptor)
        raise
    try:
        if os.fstat(descriptor).st_size == 0:
            handle.write(_LOCK_SENTINEL)
            handle.flush()
        handle.seek(0)
        return handle
    except BaseException:
        handle.close()
        raise


def _lock_nonblocking(handle: BinaryIO) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in _CONTENDED_ERRNOS or getattr(error, "winerror", None) in _CONTENDED_WINERRORS:
            raise _LockContended from error
        raise


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_owner(handle: BinaryIO) -> FileLockOwner | None:
    try:
        handle.seek(len(_LOCK_SENTINEL))
        raw = handle.read()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return FileLockOwner.from_payload(payload)


__all__ = [
    "FileLockOwner",
    "LockNamespace",
    "OSFileLock",
    "OSFileLockBusy",
    "lock_file_path",
]
