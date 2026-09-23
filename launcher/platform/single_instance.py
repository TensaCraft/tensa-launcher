from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socket
import threading
import time
from collections import deque
from contextlib import suppress
from pathlib import Path
from queue import Empty, Full, Queue

from launcher.application.platform_lock import OSFileLock, OSFileLockBusy
from launcher.platform.instance_shortcuts import validate_version_id
from launcher.platform.paths import LauncherPaths, PathPolicy, is_frozen
from launcher.storage.atomic import atomic_write_json

_MAX_MESSAGE = 8192
_IO_TIMEOUT = 0.75
_LOG = logging.getLogger("tensa.launcher")


def instance_directory() -> Path:
    # Installed copies share one owner even after moving the executable or user data.
    # Development and explicitly isolated installations keep their own owner.
    if is_frozen() and not os.environ.get("TENSALAUNCHER_APP_BASE"):
        base = PathPolicy.default_cache_dir()
    else:
        base = LauncherPaths.detect().app_dir
    return base / "launcher-instance"


def _receive(connection: socket.socket) -> dict:
    deadline = time.monotonic() + _IO_TIMEOUT
    data = bytearray()
    while len(data) < _MAX_MESSAGE:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Launcher request timed out")
        connection.settimeout(remaining)
        chunk = connection.recv(_MAX_MESSAGE - len(data))
        if not chunk:
            raise ValueError("Incomplete launcher request")
        data.extend(chunk)
        if b"\n" in chunk:
            message = json.loads(data)
            if not isinstance(message, dict):
                raise ValueError("Invalid launcher request")
            return message
    raise ValueError("Launcher request is too large")


def _send(connection: socket.socket, message: dict) -> None:
    data = json.dumps(message, ensure_ascii=True).encode("utf-8") + b"\n"
    if len(data) > _MAX_MESSAGE:
        raise ValueError("Launcher request is too large")
    connection.settimeout(_IO_TIMEOUT)
    connection.sendall(data)


class SingleInstance:
    """Own the desktop process or deliver a request to its bounded local queue."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._endpoint = directory / "endpoint.json"
        self._lock: OSFileLock | None = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._accept_guard = threading.Lock()
        self._requests: Queue[str | None] = Queue(maxsize=64)
        self._seen: deque[str] = deque(maxlen=256)
        self._token = secrets.token_hex(32)

    @property
    def closed(self) -> bool:
        return self._stopped.is_set()

    def start_or_forward(self, version_id: str | None, *, timeout: float = 5.0) -> bool:
        if version_id is not None:
            validate_version_id(version_id)
            if len(version_id) > 512:
                raise ValueError("Version ID is too long")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise OSError("Launcher IPC directory must not be a symbolic link")
        if os.name != "nt":
            if self.directory.stat().st_uid != os.getuid():
                raise PermissionError("Launcher IPC directory belongs to another user")
            self.directory.chmod(0o700)
        request_id = secrets.token_hex(16)
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._lock = OSFileLock.try_acquire(self.directory / "owner", "shared", "launcher")
            except OSFileLockBusy:
                if self._forward(version_id, request_id):
                    return False
                if time.monotonic() >= deadline:
                    raise TimeoutError("The running launcher did not accept the request") from None
                time.sleep(0.05)
                continue
            try:
                self._requests.put_nowait(version_id)
                self._start_server()
            except BaseException:
                self.close()
                raise
            return True

    def _start_server(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = listener
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        atomic_write_json(self._endpoint, {"port": listener.getsockname()[1], "token": self._token})
        self._thread = threading.Thread(target=self._serve, args=(listener,), daemon=True, name="launcher-ipc")
        self._thread.start()

    def _forward(self, version_id: str | None, request_id: str) -> bool:
        try:
            with self._endpoint.open("rb") as stream:
                endpoint = json.loads(stream.read(_MAX_MESSAGE))
            if not isinstance(endpoint, dict):
                return False
            port, token = endpoint.get("port"), endpoint.get("token")
            if type(port) is not int or not 0 < port < 65536 or not isinstance(token, str) or len(token) != 64:
                return False
            with socket.create_connection(("127.0.0.1", port), timeout=_IO_TIMEOUT) as connection:
                _send(connection, {"token": token, "id": request_id, "version_id": version_id})
                response = _receive(connection)
                return response.get("id") == request_id and response.get("accepted") is True
        except (OSError, ValueError, RecursionError):
            return False

    def _serve(self, listener: socket.socket) -> None:
        while not self.closed:
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if not self.closed:
                    _LOG.exception("Launcher IPC listener stopped unexpectedly")
                return
            with connection:
                try:
                    request = _receive(connection)
                    token, request_id = request.get("token"), request.get("id")
                    if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, self._token):
                        continue
                    if not isinstance(request_id, str) or len(request_id) != 32 or "version_id" not in request:
                        continue
                    version_id = request["version_id"]
                    if version_id is not None:
                        if not isinstance(version_id, str) or len(version_id) > 512:
                            continue
                        validate_version_id(version_id)
                    with self._accept_guard:
                        if self.closed:
                            return
                        if request_id not in self._seen:
                            self._requests.put_nowait(version_id)
                            self._seen.append(request_id)
                        _send(connection, {"id": request_id, "accepted": True})
                except (OSError, ValueError, RecursionError, Full):
                    # Invalid/local timed-out clients must not stop the listener.
                    _LOG.debug("Launcher IPC request was rejected", exc_info=True)

    def pending_requests(self) -> list[str | None]:
        requests = []
        for _ in range(64):
            try:
                requests.append(self._requests.get_nowait())
            except Empty:
                break
        return requests

    def stop_accepting(self) -> None:
        """Reject new requests while retaining process ownership until runtime exit."""
        with self._accept_guard:
            self._stopped.set()
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def close(self) -> None:
        self.stop_accepting()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._lock is not None:
            with suppress(OSError):
                self._endpoint.unlink(missing_ok=True)
            self._lock.release()
            self._lock = None

    def __enter__(self) -> SingleInstance:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
