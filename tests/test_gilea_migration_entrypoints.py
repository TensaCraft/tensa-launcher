import subprocess

import pytest

from launcher.application.gilea_migration.models import MigrationError, MigrationResult
from launcher.platform import gilea_migration as windows
from launcher.platform.instance_shortcuts import ShortcutCommand
from launcher.platform.single_instance import SingleInstance


@pytest.fixture(autouse=True)
def no_live_process_probe(monkeypatch):
    monkeypatch.setattr(windows, "_gilea_running", lambda exe: False, raising=False)


def test_forwarding_preserves_build_aliases_and_recovery(tmp_path, monkeypatch):
    exe = tmp_path / "GileaLauncher-tensa.exe"
    exe.write_bytes(b"fixture")
    result = MigrationResult(exe, {"old": "Actual Folder"}, ())
    commands = []

    class Process:
        def __init__(self, args, **kwargs):
            commands.append((args, kwargs))

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("fixture", timeout)

    monkeypatch.setattr(windows.subprocess, "Popen", Process)
    monkeypatch.setenv("TENSALAUNCHER_APP_BASE", "private-old-path")
    monkeypatch.setenv("LAUNCHER_APP_BASE", "wrong-new-path")
    windows.launch_gilea(result, version_id="old")
    assert commands[0][0] == [str(exe), "--launch-version=Actual Folder"]
    assert "LAUNCHER_APP_BASE" not in commands[0][1]["env"]
    assert "TENSALAUNCHER_APP_BASE" not in commands[0][1]["env"]
    assert commands[0][1].get("shell", False) is False
    with pytest.raises(MigrationError, match="missing_build"):
        windows.launch_gilea(result, version_id="absent")


def test_secondary_recovery_request_is_not_lost(tmp_path):
    with SingleInstance(tmp_path) as primary, SingleInstance(tmp_path) as secondary:
        assert primary.start_or_forward(None)
        assert not secondary.start_or_forward(None, stay_on_tensa=True)
        assert primary.stay_on_tensa is True
        assert primary.pending_requests() == [None, None]


def test_process_spawn_is_not_claimed_as_healthy_startup(tmp_path, monkeypatch):
    exe = tmp_path / "GileaLauncher-tensa.exe"
    exe.write_bytes(b"fixture")
    result = MigrationResult(exe, {}, ())

    class Exited:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout):
            return 1

    monkeypatch.setattr(windows.subprocess, "Popen", Exited)
    with pytest.raises(MigrationError, match="launch_failed"):
        windows.launch_gilea(result)
    assert exe.read_bytes() == b"fixture"


@pytest.mark.parametrize("running,code,accepted", [(True, 0, True), (False, 0, False), (True, 1, False)])
def test_successful_secondary_handoff_is_not_replayed(tmp_path, monkeypatch, running, code, accepted):
    from types import SimpleNamespace

    exe = tmp_path / "GileaLauncher-tensa.exe"
    exe.write_bytes(b"fixture")
    result = MigrationResult(exe, {"old": "new"}, ())
    process = SimpleNamespace(wait=lambda timeout: code)
    calls = []
    monkeypatch.setattr(windows, "_gilea_running", lambda path: running)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *args, **kwargs: calls.append(args) or process)
    if accepted:
        assert windows.launch_gilea(result, version_id="old") is process
    else:
        with pytest.raises(MigrationError, match="launch_failed"):
            windows.launch_gilea(result, version_id="old")
    assert len(calls) == 1


def test_initial_launch_rechecks_pin_without_blocking_later_self_updates(tmp_path, monkeypatch):
    from types import SimpleNamespace

    exe = tmp_path / "GileaLauncher-tensa.exe"
    exe.write_bytes(b"not-the-pinned-binary")
    result = MigrationResult(exe, {}, ())
    calls = []
    monkeypatch.setattr(windows, "_gilea_running", lambda path: True)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *args, **kwargs:
                        calls.append(args) or SimpleNamespace(wait=lambda timeout: 0))
    with pytest.raises(MigrationError, match="checksum"):
        windows.launch_gilea(result, initial=True)
    assert calls == []
    windows.launch_gilea(result)
    assert len(calls) == 1


def test_shortcuts_do_not_replace_unowned_files(tmp_path, monkeypatch):
    desktop = tmp_path / "redirected desktop"
    desktop.mkdir()
    exe = tmp_path / "program/GileaLauncher-tensa.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"fixture")
    result = MigrationResult(exe, {}, ())
    recovery = ShortcutCommand("old.exe", ("--stay-on-tensa",), tmp_path)
    monkeypatch.setattr(windows, "desktop_directory", lambda: desktop)
    monkeypatch.setattr(windows, "_windows_link", lambda command, icon: repr(command).encode())
    created = windows.create_gilea_shortcuts(result, recovery_command=recovery)
    assert len(created) == 2
    assert b"--stay-on-tensa" in (desktop / "TensaLauncher Recovery.lnk").read_bytes()
    before = {p: p.stat().st_mtime_ns for p in created}
    windows.create_gilea_shortcuts(result, recovery_command=recovery)
    assert before == {p: p.stat().st_mtime_ns for p in created}
    (desktop / "GileaLauncher.lnk").write_bytes(b"owned-by-user")
    with pytest.raises(MigrationError, match="shortcut_conflict"):
        windows.create_gilea_shortcuts(result, recovery_command=recovery)
    assert (desktop / "GileaLauncher.lnk").read_bytes() == b"owned-by-user"
