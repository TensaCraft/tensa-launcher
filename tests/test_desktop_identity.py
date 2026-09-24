from __future__ import annotations

import os
from pathlib import Path

import pytest

from launcher import APP_NAME
from launcher import main as launcher_main
from launcher.platform import desktop


@pytest.fixture
def desktop_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in tuple(os.environ):
        if key.startswith("FLET_APP_") or key == "APPIMAGE":
            monkeypatch.delenv(key)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(launcher_main.sys, "frozen", True, raising=False)
    executable = tmp_path / "Launcher folder" / "TensaLauncher.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(launcher_main.sys, "executable", str(executable))
    captured = {}
    monkeypatch.setattr(launcher_main.ft, "run", lambda *_a, **_k: captured.update(
        (key, value) for key, value in os.environ.items() if key.startswith("FLET_APP_")
    ))
    return executable, captured


def test_windows_taskbar_identity_replaces_inherited_launcher_path(monkeypatch, desktop_runtime):
    executable, captured = desktop_runtime
    monkeypatch.setattr(launcher_main.sys, "platform", "win32")
    for key in ("USER_MODEL_ID", "RELAUNCH_COMMAND", "RELAUNCH_ICON", "RELAUNCH_DISPLAY_NAME"):
        monkeypatch.setenv(f"FLET_APP_{key}", "deleted-launcher.exe")

    assert launcher_main.run_flet_with_client_cache_retries()

    assert captured["FLET_APP_USER_MODEL_ID"] == APP_NAME
    assert captured["FLET_APP_RELAUNCH_ICON"] == f"{executable},0"
    assert captured["FLET_APP_RELAUNCH_DISPLAY_NAME"] == APP_NAME
    assert captured["FLET_APP_RELAUNCH_COMMAND"] == f'"{executable}"'


def test_linux_window_matches_persistent_desktop_entry(monkeypatch, desktop_runtime):
    executable, captured = desktop_runtime
    monkeypatch.setattr(launcher_main.sys, "platform", "linux")

    assert launcher_main.run_flet_with_client_cache_retries()

    assert captured.get("FLET_APP_ID") == APP_NAME
    entry = Path(os.environ["XDG_DATA_HOME"]) / "applications" / f"{APP_NAME}.desktop"
    text = entry.read_text(encoding="utf-8")
    assert f"Name={APP_NAME}\n" in text
    assert f"StartupWMClass={APP_NAME}\n" in text
    assert f"Exec={desktop._desktop_argument(str(executable))}\n" in text
    icon = Path(os.environ["XDG_DATA_HOME"]) / APP_NAME / "logo.png"
    assert f"Icon={desktop._desktop_string(str(icon))}\n" in text
    assert icon.is_file()
    assert "_MEI" not in str(icon)


def test_linux_appimage_registration_survives_unmount_and_move(monkeypatch, desktop_runtime, tmp_path):
    _executable, _captured = desktop_runtime
    monkeypatch.setattr(launcher_main.sys, "platform", "linux")
    appimage = tmp_path / 'new folder' / 'TensaLauncher % beta.AppImage'
    monkeypatch.setenv("APPIMAGE", str(appimage))

    launcher_main.run_flet_with_client_cache_retries()

    entry = Path(os.environ["XDG_DATA_HOME"]) / "applications" / f"{APP_NAME}.desktop"
    initial = entry.read_text(encoding="utf-8")
    assert f"Exec={desktop._desktop_argument(str(appimage))}\n" in initial
    assert "%%" in initial
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "moved.AppImage"))
    launcher_main.run_flet_with_client_cache_retries()
    assert "moved.AppImage" in entry.read_text(encoding="utf-8")
    assert "beta.AppImage" not in entry.read_text(encoding="utf-8")


def test_linux_registration_does_not_rewrite_unchanged_files(monkeypatch, desktop_runtime):
    monkeypatch.setattr(launcher_main.sys, "platform", "linux")
    launcher_main.run_flet_with_client_cache_retries()
    monkeypatch.setattr(desktop, "atomic_copy_file", lambda *_a: pytest.fail("Rewrote icon"))
    monkeypatch.setattr(desktop, "atomic_write_text", lambda *_a: pytest.fail("Rewrote entry"))
    launcher_main.run_flet_with_client_cache_retries()


def test_linux_registration_preserves_user_installed_entry(monkeypatch, desktop_runtime):
    monkeypatch.setattr(launcher_main.sys, "platform", "linux")
    entry = Path(os.environ["XDG_DATA_HOME"]) / "applications" / f"{APP_NAME}.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text("[Desktop Entry]\nName=Custom launcher\n", encoding="utf-8")
    assert launcher_main.run_flet_with_client_cache_retries()
    assert entry.read_text(encoding="utf-8") == "[Desktop Entry]\nName=Custom launcher\n"


def test_linux_read_only_data_home_does_not_block_launch(monkeypatch, desktop_runtime, caplog):
    monkeypatch.setattr(launcher_main.sys, "platform", "linux")

    def denied(*_args):
        raise PermissionError("Read-only data directory")

    monkeypatch.setattr(desktop, "atomic_copy_file", denied)
    assert launcher_main.run_flet_with_client_cache_retries()
    assert "Unable to register launcher desktop identity" in caplog.text


def test_windows_source_run_uses_launcher_icon_and_separate_identity(monkeypatch, desktop_runtime):
    _executable, captured = desktop_runtime
    monkeypatch.setattr(launcher_main.sys, "platform", "win32")
    monkeypatch.setattr(launcher_main.sys, "frozen", False)
    assert launcher_main.run_flet_with_client_cache_retries()
    assert captured["FLET_APP_USER_MODEL_ID"] == f"{APP_NAME}-dev"
    assert captured["FLET_APP_RELAUNCH_ICON"] == f"{desktop.PACKAGE_ASSETS_DIR / 'logo.ico'},0"
    assert "launcher.main" in captured["FLET_APP_RELAUNCH_COMMAND"]
    assert str(desktop.PACKAGE_ROOT.parent) in desktop._command()[-1].replace("\\\\", "\\")


def test_macos_leaves_bundle_identity_unchanged(monkeypatch, desktop_runtime):
    _executable, captured = desktop_runtime
    monkeypatch.setattr(launcher_main.sys, "platform", "darwin")
    assert launcher_main.run_flet_with_client_cache_retries()
    assert captured == {}
