from __future__ import annotations

import socket

from launcher.platform.system import CONNECTION_TEST_PORT, SystemService


def test_connection_failure_preserves_process_default_timeout(monkeypatch):
    requested_timeout = 0.125
    original_default_timeout = socket.getdefaulttimeout()
    initial_default_timeout = 17.25
    connection_attempts: list[tuple[tuple[str, int], float | None]] = []

    def fail_connection(address: tuple[str, int], timeout: float | None = None):
        connection_attempts.append((address, timeout))
        raise OSError("connection failed")

    socket.setdefaulttimeout(initial_default_timeout)
    try:
        monkeypatch.setattr("launcher.platform.system.socket.gethostbyname", lambda _host: "192.0.2.1")
        monkeypatch.setattr("launcher.platform.system.socket.create_connection", fail_connection)

        connected = SystemService(None).check_connection(timeout=requested_timeout)
        resulting_default_timeout = socket.getdefaulttimeout()
    finally:
        socket.setdefaulttimeout(original_default_timeout)

    assert connected is False
    assert resulting_default_timeout == initial_default_timeout
    assert connection_attempts == [(("192.0.2.1", CONNECTION_TEST_PORT), requested_timeout)]
