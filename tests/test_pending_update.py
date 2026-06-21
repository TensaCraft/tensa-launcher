from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from launcher import __version__
from launcher.core.pending_update import (
    LEGACY_PENDING_UPDATE_MARKER,
    WINDOWS_CREATE_NEW_CONSOLE,
    pending_update_marker_path,
    resume_pending_update_if_needed,
    write_pending_update_marker,
)
from launcher.core.updater import AutoUpdater


def _logger():
    return SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )


def _app():
    return SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=_logger(),
        config=SimpleNamespace(get=lambda _key, default=None: default),
    )


def test_windows_update_prepares_pending_marker(monkeypatch, tmp_path: Path):
    current_exe = tmp_path / "TensaLauncher.exe"
    current_exe.write_bytes(b"old")
    downloaded_update = tmp_path / "TensaLauncher.new.exe"
    downloaded_update.write_bytes(b"new")

    updater = AutoUpdater(_app())
    updater._temp_dir = tmp_path
    monkeypatch.setattr("launcher.core.updater.sys.executable", str(current_exe))
    monkeypatch.setattr("launcher.core.updater.os.getpid", lambda: 12345)

    update_command = updater.apply_update_windows(downloaded_update)

    marker_path = pending_update_marker_path(tmp_path)
    assert update_command == str(tmp_path / "tensalauncher_start_update.bat")
    assert marker_path.exists()

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["platform"] == "windows"
    assert marker["source"] == str(downloaded_update)
    assert marker["target"] == str(current_exe)
    assert marker["updater_script"] == str(tmp_path / "tensalauncher_update.bat")
    assert marker["command"] == update_command

    launcher_script = Path(update_command).read_text(encoding="ascii")
    assert f'"{marker_path}"' in launcher_script


def test_windows_update_script_stages_and_restores_before_touching_target():
    script = Path("launcher/assets/updater/windows_update.bat").read_text(encoding="utf-8")

    assert 'copy /y "%SOURCE%" "%STAGED%"' in script
    assert 'move /y "%TARGET%" "%BACKUP%"' in script
    assert 'copy /y "%STAGED%" "%TARGET%"' in script
    assert 'copy /y "%BACKUP%" "%TARGET%"' in script
    assert 'del /f /q "%TARGET%"' not in script


def test_resume_pending_update_rewrites_windows_command_with_current_pid(monkeypatch, tmp_path: Path):
    source = tmp_path / "TensaLauncher.new.exe"
    target = tmp_path / "TensaLauncher.exe"
    updater_script = tmp_path / "tensalauncher_update.bat"
    command = tmp_path / "tensalauncher_start_update.bat"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    updater_script.write_text("@echo off\n", encoding="ascii")
    command.write_text("@echo off\n", encoding="ascii")

    marker_path = write_pending_update_marker(
        temp_dir=tmp_path,
        platform_name="windows",
        command=command,
        updater_script=updater_script,
        source=source,
        target=target,
    )
    popen_calls: list[tuple[object, dict]] = []

    def fake_popen(command_args, **kwargs):
        popen_calls.append((command_args, kwargs))
        return SimpleNamespace(pid=99)

    monkeypatch.setattr("launcher.core.pending_update.os.getpid", lambda: 24680)

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=fake_popen,
        platform_name="windows",
    )

    resume_script = tmp_path / "tensalauncher_resume_update.bat"
    assert resumed is True
    assert resume_script.exists()
    assert f'"{source}" "{target}" 24680 "{marker_path}"' in resume_script.read_text(encoding="ascii")
    assert popen_calls == [([str(resume_script)], {"shell": True, "creationflags": WINDOWS_CREATE_NEW_CONSOLE})]
    assert marker_path.exists()


def test_resume_pending_update_allows_missing_windows_target(monkeypatch, tmp_path: Path):
    source = tmp_path / "TensaLauncher.new.exe"
    target = tmp_path / "TensaLauncher.exe"
    updater_script = tmp_path / "tensalauncher_update.bat"
    command = tmp_path / "tensalauncher_start_update.bat"
    source.write_bytes(b"new")
    updater_script.write_text("@echo off\n", encoding="ascii")
    command.write_text("@echo off\n", encoding="ascii")

    marker_path = write_pending_update_marker(
        temp_dir=tmp_path,
        platform_name="windows",
        command=command,
        updater_script=updater_script,
        source=source,
        target=target,
    )
    popen_calls: list[tuple[object, dict]] = []

    def fake_popen(command_args, **kwargs):
        popen_calls.append((command_args, kwargs))
        return SimpleNamespace(pid=99)

    monkeypatch.setattr("launcher.core.pending_update.os.getpid", lambda: 13579)

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=fake_popen,
        platform_name="windows",
    )

    resume_script = tmp_path / "tensalauncher_resume_update.bat"
    assert resumed is True
    assert resume_script.exists()
    assert f'"{source}" "{target}" 13579 "{marker_path}"' in resume_script.read_text(encoding="ascii")
    assert popen_calls == [([str(resume_script)], {"shell": True, "creationflags": WINDOWS_CREATE_NEW_CONSOLE})]
    assert marker_path.exists()


def test_resume_pending_update_accepts_legacy_marker_name(monkeypatch, tmp_path: Path):
    source = tmp_path / "TensaLauncher.new.exe"
    target = tmp_path / "TensaLauncher.exe"
    updater_script = tmp_path / "tensalauncher_update.bat"
    command = tmp_path / "tensalauncher_start_update.bat"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    updater_script.write_text("@echo off\n", encoding="ascii")
    command.write_text("@echo off\n", encoding="ascii")
    marker_path = tmp_path / LEGACY_PENDING_UPDATE_MARKER
    marker_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": "windows",
                "command": str(command),
                "updater_script": str(updater_script),
                "source": str(source),
                "target": str(target),
            }
        ),
        encoding="utf-8",
    )
    popen_calls: list[tuple[object, dict]] = []

    def fake_popen(command_args, **kwargs):
        popen_calls.append((command_args, kwargs))
        return SimpleNamespace(pid=99)

    monkeypatch.setattr("launcher.core.pending_update.os.getpid", lambda: 97531)

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=fake_popen,
        platform_name="windows",
    )

    resume_script = tmp_path / "tensalauncher_resume_update.bat"
    assert resumed is True
    assert resume_script.exists()
    assert f'"{source}" "{target}" 97531 "{marker_path}"' in resume_script.read_text(encoding="ascii")
    assert popen_calls == [([str(resume_script)], {"shell": True, "creationflags": WINDOWS_CREATE_NEW_CONSOLE})]


def test_resume_pending_update_removes_stale_marker(tmp_path: Path):
    marker_path = write_pending_update_marker(
        temp_dir=tmp_path,
        platform_name="windows",
        command=tmp_path / "missing_start.bat",
        updater_script=tmp_path / "missing_update.bat",
        source=tmp_path / "missing_update.exe",
        target=tmp_path / "TensaLauncher.exe",
    )

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=lambda *_args, **_kwargs: None,
        platform_name="windows",
    )

    assert resumed is False
    assert not marker_path.exists()
