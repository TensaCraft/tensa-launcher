from __future__ import annotations

import sys
import subprocess
from types import SimpleNamespace

from launcher.core import util
from launcher.platform.system import SystemService


def test_util_exposes_macos_microphone_helpers(monkeypatch):
    opened: list[bool] = []
    requested: list[bool] = []
    reset: list[bool] = []

    monkeypatch.setattr(SystemService, "is_macos", staticmethod(lambda: True))
    monkeypatch.setattr(SystemService, "open_macos_microphone_settings", lambda _self: opened.append(True) or True)
    monkeypatch.setattr(
        SystemService,
        "request_macos_microphone_access",
        lambda _self: requested.append(True) or "authorized",
    )
    monkeypatch.setattr(SystemService, "reset_macos_microphone_access", lambda _self: reset.append(True) or True)

    assert util.is_macos() is True
    assert util.open_macos_microphone_settings() is True
    assert util.request_macos_microphone_access() == "authorized"
    assert util.reset_macos_microphone_access() is True
    assert opened == [True]
    assert requested == [True]
    assert reset == [True]


def test_system_service_requests_macos_microphone_access(monkeypatch, tmp_path):
    requests: list[str] = []
    fake_avfoundation = SimpleNamespace(
        AVMediaTypeAudio="audio",
        AVAuthorizationStatusAuthorized=3,
        AVAuthorizationStatusDenied=2,
        AVAuthorizationStatusRestricted=1,
        authorization_status=0,
    )

    class FakeCaptureDevice:
        @staticmethod
        def authorizationStatusForMediaType_(media_type):
            assert media_type == "audio"
            return fake_avfoundation.authorization_status

        @staticmethod
        def requestAccessForMediaType_completionHandler_(media_type, callback):
            requests.append(media_type)
            callback(True)

    fake_avfoundation.AVCaptureDevice = FakeCaptureDevice
    monkeypatch.setitem(sys.modules, "AVFoundation", fake_avfoundation)
    monkeypatch.setattr(SystemService, "is_macos", staticmethod(lambda: True))

    path_service = SimpleNamespace(paths=SimpleNamespace(app_state_dir=tmp_path, minecraft_dir=tmp_path))
    service = SystemService(path_service)

    assert service.request_macos_microphone_access(timeout=1) == "authorized"
    assert requests == ["audio"]


def test_system_service_resets_macos_microphone_access_for_app_bundle(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    app_bundle = tmp_path / "TensaLauncher.app"
    binary = app_bundle / "Contents" / "MacOS" / "TensaLauncher"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary", encoding="utf-8")
    info_plist = app_bundle / "Contents" / "Info.plist"
    info_plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>ua.co.tensa.TensaLauncher</string>
</dict>
</plist>
""",
        encoding="utf-8",
    )

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setattr(SystemService, "is_macos", staticmethod(lambda: True))
    monkeypatch.setattr("launcher.platform.system.subprocess.run", fake_run)
    path_service = SimpleNamespace(paths=SimpleNamespace(app_state_dir=tmp_path, minecraft_dir=tmp_path))
    service = SystemService(path_service)

    assert service.reset_macos_microphone_access() is True
    assert commands == [["tccutil", "reset", "Microphone", "ua.co.tensa.TensaLauncher"]]
