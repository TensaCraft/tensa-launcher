from __future__ import annotations

import json
import plistlib
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.platform import instance_shortcuts as shortcuts


@pytest.fixture(autouse=True)
def isolate_shortcut_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.LauncherPaths, "detect", lambda: SimpleNamespace(app_state_dir=tmp_path / "state"))


@pytest.fixture
def command(tmp_path):
    return shortcuts.ShortcutCommand(
        str(tmp_path / "Launcher with spaces"),
        ("--launch-version=stable-id",),
        tmp_path,
    )


@pytest.mark.parametrize("value", ["", "\n", "a\rb", "a\x00b", "a\x7fb"])
def test_rejects_invalid_version_ids(value):
    with pytest.raises(ValueError):
        shortcuts.validate_version_id(value)


def test_source_command_uses_stable_id_not_a_name_or_path(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    monkeypatch.setattr(shortcuts.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(shortcuts, "is_frozen", lambda: False)

    command = shortcuts.launcher_command('id with "quotes";$(touch nope)')

    assert command.executable == str(tmp_path / "python")
    assert command.arguments == ("-m", "launcher.main", '--launch-version=id with "quotes";$(touch nope)')
    assert (command.working_directory / "launcher" / "main.py").is_file()


def test_frozen_appimage_uses_persistent_artifact_not_mount(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    monkeypatch.setattr(shortcuts.sys, "executable", "/tmp/.mount_launcher/internal")
    monkeypatch.setattr(shortcuts, "is_frozen", lambda: True)
    appimage = tmp_path / "Tensa Launcher.AppImage"
    monkeypatch.setenv("APPIMAGE", str(appimage))

    command = shortcuts.launcher_command("instance-id")

    assert command.executable == str(appimage)
    assert command.arguments == ("--launch-version=instance-id",)
    assert command.working_directory == tmp_path


def test_windows_source_prefers_windowed_python(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    monkeypatch.setattr(shortcuts.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(shortcuts, "is_frozen", lambda: False)
    (tmp_path / "pythonw.exe").touch()

    assert shortcuts.launcher_command("id").executable == str(tmp_path / "pythonw.exe")


def test_linux_shortcut_escapes_exec_and_cannot_inject_desktop_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    version_id = 'stable %f "id" \\ $HOME `exec`'
    command = shortcuts.ShortcutCommand("/opt/Tensa Launcher", (f"--launch-version={version_id}",), tmp_path)

    path = shortcuts.create_desktop_shortcut(
        version_id, "../Name\nExec=unwanted/../../", desktop=tmp_path, command=command
    )

    content = path.read_text(encoding="utf-8")
    assert path.parent == tmp_path
    assert path.suffix == ".desktop"
    assert len([line for line in content.splitlines() if line.startswith("Exec=")]) == 1
    assert '"/opt/Tensa Launcher"' in content
    assert "%%f" in content
    assert '\\\\"id\\\\"' in content
    assert "\\\\$HOME" in content
    assert "\\\\`exec\\\\`" in content
    assert "\\\\\\\\" in content
    assert "Terminal=false" in content


def test_mac_bundle_roundtrips_arguments_without_shell_interpolation(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    version_id = "stable-id'; touch /tmp/not-created; #"
    command = shortcuts.ShortcutCommand("/Applications/Tensa Launcher", (f"--launch-version={version_id}",), tmp_path)

    path = shortcuts.create_desktop_shortcut(version_id, "My Build", desktop=tmp_path, command=command)

    lines = (path / "Contents" / "MacOS" / "launch").read_text(encoding="utf-8").splitlines()
    assert path.suffix == ".app"
    assert lines[0] == "#!/bin/sh"
    assert shlex.split(lines[2]) == ["exec", command.executable, *command.arguments]
    assert shlex.split(lines[1])[:2] == ["cd", str(tmp_path)]
    metadata = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
    assert metadata["CFBundleExecutable"] == "launch"
    assert metadata["CFBundleIconFile"] == "instance.icns"
    assert (path / "Contents" / "Resources" / "instance.icns").is_file()


def test_windows_link_uses_static_script_and_json_data(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    command = shortcuts.ShortcutCommand(
        str(tmp_path / 'Tensa Launcher.exe'),
        ('--launch-version=id "quote"; $(unwanted)',),
        tmp_path,
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        payload = json.loads(kwargs["env"]["TENSALAUNCHER_SHORTCUT_DATA"])
        assert payload["executable"] == command.executable
        assert payload["arguments"] == subprocess.list2cmdline(command.arguments)
        assert payload["directory"] == str(tmp_path)
        assert Path(payload["icon"]).suffix == ".ico"
        assert Path(payload["icon"]).is_file()
        Path(payload["path"]).write_bytes(b"mock-link")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(shortcuts.subprocess, "run", run)

    path = shortcuts.create_desktop_shortcut("id", "My Build", desktop=tmp_path, command=command)

    assert path.suffix == ".lnk"
    assert path.read_bytes() == b"mock-link"
    argv, kwargs = calls[0]
    assert "unwanted" not in argv[-1]
    assert kwargs.get("shell", False) is False
    assert argv[argv.index("-WindowStyle") + 1] == "Hidden"
    assert kwargs["timeout"] == 20


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_shortcut_creation_never_overwrites_existing_files(monkeypatch, tmp_path, command, platform):
    monkeypatch.setattr(shortcuts.sys, "platform", platform)
    monkeypatch.setattr(shortcuts, "_windows_link", lambda _command, _icon: b"link")

    first = shortcuts.create_desktop_shortcut("id", "../bad:name", desktop=tmp_path, command=command)
    original = first / "untouched.txt" if first.is_dir() else first
    original.write_bytes(b"keep me")
    second = shortcuts.create_desktop_shortcut("id", "../bad:name", desktop=tmp_path, command=command)

    assert first != second
    assert original.read_bytes() == b"keep me"
    assert first.parent == second.parent == tmp_path


@pytest.mark.parametrize("platform,extension", [("linux", ".desktop"), ("darwin", ".app"), ("win32", ".lnk")])
def test_shortcut_name_is_only_the_instance_name(monkeypatch, tmp_path, command, platform, extension):
    monkeypatch.setattr(shortcuts.sys, "platform", platform)
    monkeypatch.setattr(shortcuts, "_windows_link", lambda _command, _icon: b"link")

    first = shortcuts.create_desktop_shortcut("internal-id", "Aeronautics", desktop=tmp_path, command=command)
    second = shortcuts.create_desktop_shortcut("other-id", "Aeronautics", desktop=tmp_path, command=command)

    assert first.name == f"Aeronautics{extension}"
    assert second.name == f"Aeronautics (2){extension}"
    if platform == "linux":
        assert "Name=Aeronautics\n" in first.read_text()
    elif platform == "darwin":
        metadata = plistlib.loads((first / "Contents" / "Info.plist").read_bytes())
        assert metadata["CFBundleName"] == "Aeronautics"


@pytest.mark.parametrize("name", ["CON", "aux.txt", "LPT1", "NUL"])
def test_reserved_shortcut_names_remain_valid_on_windows(monkeypatch, tmp_path, command, name):
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    monkeypatch.setattr(shortcuts, "_windows_link", lambda _command, _icon: b"link")

    path = shortcuts.create_desktop_shortcut("id", name, desktop=tmp_path, command=command)

    assert path.name == f"_{name}.lnk"


def test_linux_uses_xdg_desktop_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=str(tmp_path) + "\n")

    monkeypatch.setattr(shortcuts.subprocess, "run", run)
    assert shortcuts.desktop_directory() == tmp_path
    assert calls == [["xdg-user-dir", "DESKTOP"]]


def test_windows_uses_known_desktop_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    scripts = []
    monkeypatch.setattr(shortcuts, "_powershell", lambda script: scripts.append(script) or str(tmp_path))
    assert shortcuts.desktop_directory() == tmp_path
    assert "GetFolderPath('DesktopDirectory')" in scripts[0]


def test_unknown_platform_fails_without_writing(monkeypatch, tmp_path, command):
    monkeypatch.setattr(shortcuts.sys, "platform", "unsupported")
    with pytest.raises(OSError):
        shortcuts.create_desktop_shortcut("id", "Build", desktop=tmp_path, command=command)
    assert list(tmp_path.iterdir()) == []
