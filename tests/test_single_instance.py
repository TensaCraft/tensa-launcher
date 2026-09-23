from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from launcher import main as launcher_main
from launcher.app import App
from launcher.application.platform_lock import OSFileLock, OSFileLockBusy
from launcher.platform import single_instance as ipc
from launcher.platform.single_instance import SingleInstance, _receive, _send


def test_second_shortcut_launch_does_not_open_another_window(monkeypatch, tmp_path):
    opened = Event()
    close = Event()
    targets = []
    monkeypatch.setattr(launcher_main, "instance_directory", lambda: tmp_path, raising=False)
    monkeypatch.setattr(launcher_main, "prepare_process_workdir", lambda: None)
    monkeypatch.setattr(launcher_main, "setup_logging", lambda: None)
    monkeypatch.setattr(launcher_main, "normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr(launcher_main, "resume_pending_update_if_needed", lambda _logger: False)
    monkeypatch.setattr(launcher_main.LauncherPaths, "detect", lambda: SimpleNamespace(app_dir=tmp_path))

    def run(target, **_kwargs):
        targets.append(target)
        if len(targets) == 1:
            opened.set()
            assert close.wait(10)

    monkeypatch.setattr(launcher_main.ft, "run", run)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(launcher_main.launch, [])
        try:
            assert opened.wait(5)
            assert launcher_main.launch(["--launch-version=exact-id"]) == 0
            assert len(targets) == 1
            assert targets[0].keywords["instance"].pending_requests() == [None, "exact-id"]
        finally:
            close.set()
        assert first.result(timeout=5) == 0


def test_separate_process_forwards_and_crashed_owner_does_not_block_restart(tmp_path):
    script = (
        "import sys, os; from pathlib import Path; "
        "from launcher.platform.single_instance import SingleInstance; "
        "instance = SingleInstance(Path(sys.argv[1])); "
        "print(instance.start_or_forward('build-id'), flush=True); os._exit(0)"
    )
    with SingleInstance(tmp_path) as first:
        assert first.start_or_forward(None)
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"
        assert first.pending_requests() == [None, "build-id"]
    crashed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert crashed.returncode == 0, crashed.stderr
    assert crashed.stdout.strip() == "True"
    with SingleInstance(tmp_path) as restarted:
        assert restarted.start_or_forward("new-id")
        assert restarted.pending_requests() == ["new-id"]


def test_simultaneous_startup_has_one_owner_and_keeps_all_requests(tmp_path):
    instances = [SingleInstance(tmp_path) for _ in range(8)]
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(lambda pair: pair[1].start_or_forward(str(pair[0])), enumerate(instances)))
        assert outcomes.count(True) == 1
        owner = instances[outcomes.index(True)]
        assert sorted(owner.pending_requests()) == [str(index) for index in range(8)]
    finally:
        for instance in instances:
            instance.close()


@pytest.mark.parametrize("token", ["wrong", "\u043f\u043e\u043c\u0438\u043b\u043a\u0430"])
def test_invalid_client_cannot_launch_or_stop_the_listener(tmp_path, token):
    with SingleInstance(tmp_path) as first:
        assert first.start_or_forward(None)
        endpoint = json.loads((tmp_path / "endpoint.json").read_text())
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as connection:
            _send(connection, {"token": token, "id": "a" * 32, "version_id": "bad"})
            assert connection.recv(1) == b""
        with SingleInstance(tmp_path) as second:
            assert not second.start_or_forward("good")
        assert first.pending_requests() == [None, "good"]


def test_retry_of_same_request_is_acknowledged_but_not_enqueued_twice(tmp_path):
    with SingleInstance(tmp_path) as first:
        assert first.start_or_forward(None)
        endpoint = json.loads((tmp_path / "endpoint.json").read_text())
        request = {"token": endpoint["token"], "id": "a" * 32, "version_id": "exact-id"}
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as connection:
                _send(connection, request)
                assert _receive(connection)["accepted"]
        assert first.pending_requests() == [None, "exact-id"]


def test_unresponsive_owner_never_opens_a_second_launcher(tmp_path):
    with OSFileLock.try_acquire(tmp_path / "owner", "shared", "launcher"):
        with SingleInstance(tmp_path) as second:
            with pytest.raises(TimeoutError):
                second.start_or_forward("exact-id", timeout=0.1)
            assert second._socket is None


@pytest.fixture
def headless_app(monkeypatch):
    def bootstrap(app):
        app.theme = object()
        app.feedback = SimpleNamespace(shutdown=Mock())

    monkeypatch.setattr(launcher_main.util, "check_connection", lambda: False)
    monkeypatch.setattr("launcher.app.ui.set_current_theme", lambda _theme: None)
    monkeypatch.setattr(App, "_bootstrap_state", bootstrap)
    for method in (
        "_configure_page", "_center_window", "_build_ui_services", "_build_stateful_models",
        "_build_shell", "_warm_up_background_tasks", "run", "_schedule_forced_exit",
    ):
        monkeypatch.setattr(App, method, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(App, "_request_window_close", lambda _app: True)

    def start(instance):
        tasks = []
        page = SimpleNamespace(window=SimpleNamespace(), run_task=lambda *args: tasks.append(args))
        asyncio.run(launcher_main.main(page, instance=instance))
        assert tasks == [(launcher_main.handle_instance_requests, page.data, instance)]
        assert page.data._startup_tasks == []
        return page.data

    return start


@pytest.mark.parametrize("shutdown", ["stop", "disconnect"])
def test_shutdown_rejects_forwarding_before_cleanup_and_holds_ownership(tmp_path, headless_app, shutdown):
    with SingleInstance(tmp_path) as owner, SingleInstance(tmp_path) as client:
        assert owner.start_or_forward(None)
        owner.pending_requests()
        app = headless_app(owner)

        def cleanup():
            assert app._terminating
            assert not client._forward("during-cleanup", "a" * 32)
            with pytest.raises(OSFileLockBusy):
                OSFileLock.try_acquire(tmp_path / "owner", "shared", "launcher")

        app._clear_install_session_state = Mock(side_effect=cleanup)
        for _ in range(2):
            if shutdown == "disconnect":
                app.page.on_disconnect(None)
            else:
                app.stop()
        app._clear_install_session_state.assert_called_once()
        assert owner.pending_requests() == []
        with pytest.raises(TimeoutError):
            client.start_or_forward("after-shutdown", timeout=0.1)


def test_secondary_retries_shutdown_owner_and_starts_after_runtime_exit(tmp_path, headless_app, monkeypatch):
    with SingleInstance(tmp_path) as owner, SingleInstance(tmp_path) as client:
        assert owner.start_or_forward(None)
        owner.pending_requests()
        app = headless_app(owner)
        app.stop()
        attempted = Event()
        forward = client._forward

        def observe(*args):
            result = forward(*args)
            attempted.set()
            return result

        monkeypatch.setattr(client, "_forward", observe)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.start_or_forward, "next-build")
            try:
                assert attempted.wait(3)
                assert not pending.done()
                assert owner.pending_requests() == []
            finally:
                owner.close()
            assert pending.result(timeout=5) is True
        assert client.pending_requests() == ["next-build"]


def test_request_in_flight_is_not_acknowledged_after_stop_accepting(tmp_path, monkeypatch):
    received, resume = Event(), Event()
    receive = ipc._receive

    def delayed_receive(connection):
        request = receive(connection)
        received.set()
        assert resume.wait(3)
        return request

    monkeypatch.setattr(ipc, "_receive", delayed_receive)
    with SingleInstance(tmp_path) as owner:
        assert owner.start_or_forward(None)
        owner.pending_requests()
        endpoint = json.loads((tmp_path / "endpoint.json").read_text())
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=3) as connection:
            _send(connection, {"token": endpoint["token"], "id": "a" * 32, "version_id": "too-late"})
            try:
                assert received.wait(3)
                owner.stop_accepting()
            finally:
                resume.set()
            assert connection.recv(1) == b""
        assert owner.pending_requests() == []


def test_in_app_restart_keeps_ipc_and_shutdown_callback(tmp_path, headless_app):
    with SingleInstance(tmp_path) as owner, SingleInstance(tmp_path) as client:
        assert owner.start_or_forward(None)
        owner.pending_requests()
        app = headless_app(owner)
        listener = owner._thread
        app.restart()
        app.restart()
        assert not app._terminating
        assert owner._thread is listener and listener.is_alive()
        assert not client.start_or_forward("after-restart")
        assert owner.pending_requests() == ["after-restart"]
        app.stop()
        assert not client._forward("after-stop", "a" * 32)


def test_shutdown_of_partially_initialized_app_needs_no_callback():
    app = App.__new__(App)
    app._terminating = False
    app._clear_install_session_state = Mock()

    assert app._begin_shutdown()
    assert app._terminating
    assert not app._begin_shutdown()
    app._clear_install_session_state.assert_called_once()


def test_requests_wait_for_ui_and_listener_stops_on_shutdown(tmp_path):
    received = []
    with SingleInstance(tmp_path) as first:
        assert first.start_or_forward("initial-id")
        with SingleInstance(tmp_path) as second:
            assert not second.start_or_forward("queued-id")
        app = SimpleNamespace(_terminating=False)

        async def handle(version_id):
            received.append(version_id)
            if version_id == "queued-id":
                app._terminating = True

        app.handle_external_launch = handle
        asyncio.run(launcher_main.handle_instance_requests(app, first))
        assert received == ["initial-id", "queued-id"]


def test_external_request_restores_window_and_uses_existing_launch_handler(fake_app):
    fake_app._terminating = False
    fake_app.page.window.visible = False
    fake_app.page.window.minimized = True
    fake_app.page.window.to_front = AsyncMock()
    fake_app.launch_version_by_id = AsyncMock()
    asyncio.run(App.handle_external_launch(fake_app, "exact-id"))
    assert fake_app.page.window.visible
    assert not fake_app.page.window.minimized
    fake_app.page.window.to_front.assert_awaited_once()
    fake_app.launch_version_by_id.assert_awaited_once_with("exact-id")


def test_plain_reopen_only_focuses_and_shutdown_ignores_request(fake_app):
    fake_app._terminating = False
    fake_app.page.window.to_front = AsyncMock()
    fake_app.launch_version_by_id = AsyncMock()
    asyncio.run(App.handle_external_launch(fake_app, None))
    fake_app.page.window.to_front.assert_awaited_once()
    fake_app.launch_version_by_id.assert_not_awaited()
    fake_app._terminating = True
    asyncio.run(App.handle_external_launch(fake_app, "exact-id"))
    fake_app.launch_version_by_id.assert_not_awaited()


def test_smoke_test_does_not_contact_the_running_desktop(monkeypatch):
    monkeypatch.setattr(launcher_main, "instance_directory", lambda: pytest.fail("Smoke test must not use IPC"))
    monkeypatch.setattr(launcher_main, "_launch_runtime", lambda **kwargs: 42 if kwargs == {"smoke_test": True} else 1)
    assert launcher_main.launch(["--smoke-test"]) == 42


def test_startup_builds_ui_on_page_loop_and_checks_network_in_worker(monkeypatch):
    ui_thread = get_ident()
    events = []

    class FakeApp:
        def __init__(self, page):
            assert get_ident() == ui_thread
            assert asyncio.get_running_loop().is_running()
            events.append("app")

        def run(self):
            events.append("ready")

    def check_network():
        assert get_ident() != ui_thread
        return True

    monkeypatch.setattr(launcher_main, "App", FakeApp)
    monkeypatch.setattr(launcher_main.util, "check_connection", check_network)
    asyncio.run(launcher_main.main(SimpleNamespace()))
    assert events == ["app", "ready"]
