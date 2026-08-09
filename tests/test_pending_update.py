from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher import __version__
from launcher.core.pending_update import (
    LEGACY_PENDING_UPDATE_MARKER,
    PENDING_UPDATE_MARKER,
    PENDING_UPDATE_SCHEMA,
    PENDING_UPDATE_STAGING_DIR,
    WINDOWS_CREATE_NEW_CONSOLE,
    pending_update_marker_path,
    resume_pending_update_if_needed,
    write_pending_update_marker,
)
from launcher.core.updater import AutoUpdater

UPDATER_ASSET_ROOT = Path("launcher/assets/updater")


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


def _copy_updater_asset(destination: Path, asset_name: str) -> Path:
    destination.write_bytes((UPDATER_ASSET_ROOT / asset_name).read_bytes())
    return destination


def _write_windows_marker(
    monkeypatch,
    root: Path,
    *,
    target_exists: bool = True,
) -> tuple[Path, dict[str, object]]:
    source = root / "TensaLauncher.new.exe"
    target = root / "TensaLauncher.exe"
    updater_script = _copy_updater_asset(root / "tensalauncher_update.bat", "windows_update.bat")
    command = root / "tensalauncher_start_update.bat"
    source.write_bytes(b"new")
    command.write_text("@echo off\n", encoding="ascii")
    if target_exists:
        target.write_bytes(b"old")

    monkeypatch.setattr("launcher.core.pending_update.sys.executable", str(target))
    marker_path = write_pending_update_marker(
        temp_dir=root,
        platform_name="windows",
        command=command,
        updater_script=updater_script,
        source=source,
        target=target,
    )
    return marker_path, json.loads(marker_path.read_text(encoding="utf-8"))


def test_windows_update_prepares_typed_private_marker(monkeypatch, tmp_path: Path):
    current_exe = tmp_path / "TensaLauncher.exe"
    current_exe.write_bytes(b"old")
    downloaded_update = tmp_path / "TensaLauncher.new.exe"
    downloaded_update.write_bytes(b"new")

    updater = AutoUpdater(_app())
    updater._temp_dir = tmp_path
    monkeypatch.setattr("launcher.core.updater.sys.executable", str(current_exe))
    monkeypatch.setattr("launcher.core.updater.os.getpid", lambda: 12345)

    update_marker = updater.apply_update_windows(downloaded_update)

    marker_path = pending_update_marker_path(tmp_path)
    assert update_marker == marker_path
    assert marker_path.exists()

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["schema"] == PENDING_UPDATE_SCHEMA
    assert marker["platform"] == "windows"
    assert marker["source"] == str(tmp_path / "tensalauncher_update_payload.exe")
    assert marker["target"] == str(current_exe)
    assert marker["updater_script"] == str(tmp_path / "tensalauncher_update.bat")
    assert len(marker["source_sha256"]) == 64
    assert len(marker["updater_sha256"]) == 64
    assert "command" not in marker

    assert not (tmp_path / "tensalauncher_start_update.bat").exists()


def test_windows_update_script_stages_and_restores_before_touching_target():
    script = (UPDATER_ASSET_ROOT / "windows_update.bat").read_text(encoding="utf-8")

    assert 'copy /y "%SOURCE%" "%STAGED%"' in script
    assert 'move /y "%TARGET%" "%BACKUP%"' in script
    assert 'copy /y "%STAGED%" "%TARGET%"' in script
    assert 'copy /y "%BACKUP%" "%TARGET%"' in script
    assert 'del /f /q "%TARGET%"' not in script


@pytest.mark.parametrize("asset_name", ["linux_update.sh", "macos_update.sh"])
def test_unix_update_scripts_stage_and_restore_before_replacing(asset_name: str):
    script = (UPDATER_ASSET_ROOT / asset_name).read_text(encoding="utf-8")

    assert 'STAGED="${TARGET}.new"' in script or 'STAGED="${APP_PATH}.new"' in script
    assert 'BACKUP="${TARGET}.bak"' in script or 'BACKUP="${APP_PATH}.bak"' in script
    assert "restore_previous" in script
    assert 'rm -f -- "$MARKER"' in script
    assert "rm -rf \"$APP_PATH\"" not in script
    assert 'rm -f "$TARGET"' not in script


def test_resume_pending_update_uses_validated_windows_argv(monkeypatch, tmp_path: Path):
    marker_path, marker = _write_windows_marker(monkeypatch, tmp_path)
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

    assert resumed is True
    assert popen_calls == [
        (
            [
                marker["updater_script"],
                marker["source"],
                marker["target"],
                "24680",
                str(marker_path),
            ],
            {"shell": False, "creationflags": WINDOWS_CREATE_NEW_CONSOLE},
        )
    ]
    assert marker_path.exists()


def test_resume_pending_update_allows_missing_windows_target(monkeypatch, tmp_path: Path):
    marker_path, marker = _write_windows_marker(monkeypatch, tmp_path, target_exists=False)
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

    assert resumed is True
    assert popen_calls[0][0] == [
        marker["updater_script"],
        marker["source"],
        marker["target"],
        "13579",
        str(marker_path),
    ]
    assert popen_calls[0][1]["shell"] is False


def test_legacy_marker_command_is_ignored(monkeypatch, tmp_path: Path):
    source = tmp_path / "legacy-update.exe"
    target = tmp_path / "TensaLauncher.exe"
    updater_script = _copy_updater_asset(tmp_path / "tensalauncher_update.bat", "windows_update.bat")
    planted_output = tmp_path / "marker-command-ran.txt"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    marker_path = tmp_path / LEGACY_PENDING_UPDATE_MARKER
    malicious_command = f'echo exploited > "{planted_output}"'
    marker_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": "windows",
                "command": malicious_command,
                "updater_script": str(updater_script),
                "source": str(source),
                "target": str(target),
            }
        ),
        encoding="utf-8",
    )
    popen_calls: list[tuple[object, dict]] = []
    monkeypatch.setattr("launcher.core.pending_update.sys.executable", str(target))
    monkeypatch.setattr("launcher.core.pending_update.os.getpid", lambda: 97531)

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=lambda args, **kwargs: popen_calls.append((args, kwargs)),
        platform_name="windows",
    )

    assert resumed is True
    assert popen_calls == [
        (
            [str(updater_script), str(source), str(target), "97531", str(marker_path)],
            {"shell": False, "creationflags": WINDOWS_CREATE_NEW_CONSOLE},
        )
    ]
    assert malicious_command not in popen_calls[0][0]
    assert not planted_output.exists()


def test_schema_two_marker_rejects_injected_command(monkeypatch, tmp_path: Path):
    marker_path, marker = _write_windows_marker(monkeypatch, tmp_path)
    marker["command"] = "arbitrary marker command"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    popen_calls: list[object] = []

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=lambda *args, **_kwargs: popen_calls.append(args),
        platform_name="windows",
    )

    assert resumed is False
    assert popen_calls == []
    assert not marker_path.exists()


@pytest.mark.parametrize("out_of_scope_field", ["source", "target"])
def test_resume_rejects_out_of_scope_marker_paths(monkeypatch, tmp_path: Path, out_of_scope_field: str):
    staging = tmp_path / "staging"
    staging.mkdir()
    source = staging / "legacy-update.exe"
    expected_target = tmp_path / "TensaLauncher.exe"
    updater_script = _copy_updater_asset(staging / "tensalauncher_update.bat", "windows_update.bat")
    source.write_bytes(b"new")
    expected_target.write_bytes(b"old")
    marker = {
        "schema": 1,
        "platform": "windows",
        "command": "ignored",
        "updater_script": str(updater_script),
        "source": str(source),
        "target": str(expected_target),
    }
    marker[out_of_scope_field] = str(tmp_path / f"foreign-{out_of_scope_field}.exe")
    foreign_path = Path(marker[out_of_scope_field])
    foreign_path.write_bytes(b"foreign")
    marker_path = staging / PENDING_UPDATE_MARKER
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    popen_calls: list[object] = []
    monkeypatch.setattr("launcher.core.pending_update.sys.executable", str(expected_target))

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=staging,
        popen=lambda *args, **_kwargs: popen_calls.append(args),
        platform_name="windows",
    )

    assert resumed is False
    assert popen_calls == []
    assert not marker_path.exists()


def test_shared_temp_marker_is_not_considered(monkeypatch, tmp_path: Path):
    shared_temp = tmp_path / "shared"
    private_cache = tmp_path / "private-cache"
    shared_temp.mkdir()
    shared_marker = shared_temp / PENDING_UPDATE_MARKER
    shared_marker.write_text(json.dumps({"command": "arbitrary marker command"}), encoding="utf-8")
    popen_calls: list[object] = []

    monkeypatch.setattr("launcher.core.pending_update.tempfile.gettempdir", lambda: str(shared_temp))
    monkeypatch.setattr(
        "launcher.core.pending_update.PathPolicy.default_cache_dir",
        lambda: private_cache,
    )

    resumed = resume_pending_update_if_needed(
        _logger(),
        popen=lambda *args, **_kwargs: popen_calls.append(args),
        platform_name="windows",
    )

    assert pending_update_marker_path() == private_cache / PENDING_UPDATE_STAGING_DIR / PENDING_UPDATE_MARKER
    assert resumed is False
    assert popen_calls == []
    assert shared_marker.exists()


@pytest.mark.parametrize(
    ("platform_name", "asset_name", "source_name"),
    [
        ("linux", "linux_update.sh", "TensaLauncher.AppImage"),
        ("macos", "macos_update.sh", "TensaLauncher.dmg"),
    ],
)
def test_resume_pending_update_uses_shell_free_argv_on_unix_platforms(
    monkeypatch,
    tmp_path: Path,
    platform_name: str,
    asset_name: str,
    source_name: str,
):
    source = tmp_path / source_name
    source.write_bytes(b"new")
    updater_script = _copy_updater_asset(tmp_path / "tensalauncher_update.sh", asset_name)
    command = tmp_path / "ignored-command"
    command.write_text("ignored", encoding="ascii")

    if platform_name == "linux":
        target = tmp_path / "TensaLauncher-current.AppImage"
        target.write_bytes(b"old")
        executable = target
        monkeypatch.delenv("APPIMAGE", raising=False)
    else:
        target = tmp_path / "TensaLauncher.app"
        executable = target / "Contents" / "MacOS" / "TensaLauncher"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"old")

    monkeypatch.setattr("launcher.core.pending_update.sys.executable", str(executable))
    marker_path = write_pending_update_marker(
        temp_dir=tmp_path,
        platform_name=platform_name,
        command=command,
        updater_script=updater_script,
        source=source,
        target=target,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    popen_calls: list[tuple[object, dict]] = []
    monkeypatch.setattr("launcher.core.pending_update.os.getpid", lambda: 86420)

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=lambda args, **kwargs: popen_calls.append((args, kwargs)),
        platform_name=platform_name,
    )

    assert resumed is True
    assert popen_calls == [
        (
            [marker["updater_script"], marker["source"], marker["target"], "86420", str(marker_path)],
            {"shell": False, "start_new_session": True},
        )
    ]


def test_resume_pending_update_removes_stale_marker(tmp_path: Path):
    marker_path = tmp_path / PENDING_UPDATE_MARKER
    marker_path.write_text(
        json.dumps(
            {
                "schema": PENDING_UPDATE_SCHEMA,
                "platform": "windows",
                "updater_script": str(tmp_path / "missing_update.bat"),
                "source": str(tmp_path / "missing_update.exe"),
                "target": str(tmp_path / "TensaLauncher.exe"),
                "source_sha256": "0" * 64,
                "updater_sha256": "0" * 64,
                "created_at": 1,
            }
        ),
        encoding="utf-8",
    )

    resumed = resume_pending_update_if_needed(
        _logger(),
        temp_dir=tmp_path,
        popen=lambda *_args, **_kwargs: None,
        platform_name="windows",
    )

    assert resumed is False
    assert not marker_path.exists()


def test_resume_pending_update_continues_when_storage_is_unavailable(monkeypatch, tmp_path: Path):
    warnings: list[str] = []
    logger = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda message, *_args, **_kwargs: warnings.append(str(message)),
        error=lambda *_args, **_kwargs: None,
    )
    blocked = tmp_path / PENDING_UPDATE_STAGING_DIR
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("launcher.core.pending_update.PathPolicy.default_cache_dir", lambda: tmp_path)

    resumed = resume_pending_update_if_needed(logger, platform_name="windows")

    assert resumed is False
    assert any("continuing startup" in message for message in warnings)


def test_resume_pending_update_continues_when_updater_cannot_start(monkeypatch, tmp_path: Path):
    marker_path, _marker = _write_windows_marker(monkeypatch, tmp_path)
    warnings: list[str] = []
    logger = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda message, *_args, **_kwargs: warnings.append(str(message)),
        error=lambda *_args, **_kwargs: None,
    )

    def fail_start(*_args, **_kwargs):
        raise OSError("blocked by policy")

    resumed = resume_pending_update_if_needed(
        logger,
        temp_dir=tmp_path,
        popen=fail_start,
        platform_name="windows",
    )

    assert resumed is False
    assert not marker_path.exists()
    assert any("continuing startup" in message for message in warnings)
