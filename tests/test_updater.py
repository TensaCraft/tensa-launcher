from __future__ import annotations

import asyncio
from types import SimpleNamespace

import requests

from launcher import __version__
from launcher.core.updater import AutoUpdater


def test_updater_uses_semantic_version_comparison():
    assert AutoUpdater._is_newer_version("4.0.0", "3.1.5") is True
    assert AutoUpdater._is_newer_version("3.10.0", "3.9.9") is True
    assert AutoUpdater._is_newer_version("3.1.5", "3.1.5") is False


def test_updater_switches_to_beta_endpoint_when_enabled():
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "yes" if key == "include_beta_updates" else default),
    )

    updater = AutoUpdater(app)

    assert updater._update_check_url() == updater.UPDATE_BETA_CHECK_URL


def test_updater_detects_darwin_as_macos(monkeypatch):
    monkeypatch.setattr("launcher.core.updater.platform.system", lambda: "Darwin")

    assert AutoUpdater._detect_platform() == "macos"


def test_updater_rejects_payload_when_platform_mismatches(monkeypatch):
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
    )

    updater = AutoUpdater(app)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "version": "4.0.0",
                "channel": "stable",
                "platform": "macos",
                "download_url": "https://gigabait.uk/api/mods/launcher/update/macos?version=4.0.0&channel=stable",
            }

    monkeypatch.setattr("launcher.core.updater.requests.get", lambda *args, **kwargs: Response())
    assert updater.check_for_updates() is None


def test_updater_accepts_macos_payload_for_macos_platform():
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version="4.0.30"),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
    )

    updater = AutoUpdater(app)
    updater.platform = "macos"

    update = updater._update_info_from_payload(
        {
            "version": "4.1.2",
            "channel": "stable",
            "platform": "macos",
            "download_url": "https://example.com/TensaLauncher.dmg",
        }
    )

    assert update is not None
    assert update["download_url"] == "https://example.com/TensaLauncher.dmg"


def test_updater_accepts_macos_payload_for_legacy_macosx_platform():
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version="4.0.30"),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
    )

    updater = AutoUpdater(app)
    updater.platform = "macosx"

    update = updater._update_info_from_payload(
        {
            "version": "4.1.2",
            "channel": "stable",
            "platform": "macos",
            "download_url": "https://example.com/TensaLauncher.dmg",
        }
    )

    assert update is not None
    assert update["download_url"] == "https://example.com/TensaLauncher.dmg"


def test_updater_prefers_download_metadata_from_api_payload():
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "yes" if key == "include_beta_updates" else default),
    )

    updater = AutoUpdater(app)
    urls = updater._extract_download_urls(
        {
            "platform": "windows",
            "channel": "beta",
            "download": {"url": "https://gigabait.uk/api/mods/launcher/update-beta/windows?version=4.0.0&channel=beta"},
        }
    )

    assert urls == ["https://gigabait.uk/api/mods/launcher/update-beta/windows?version=4.0.0&channel=beta"]


def test_updater_uses_product_named_temp_file_when_download_name_missing(tmp_path):
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
    )

    updater = AutoUpdater(app)
    updater.platform = "windows"
    updater._temp_dir = tmp_path

    final_path, partial_path = updater._download_paths({"version": "4.1.8"}, ".exe")

    assert final_path == tmp_path / "tensalauncher-update-windows-4.1.8.exe"
    assert partial_path == tmp_path / "tensalauncher-update-windows-4.1.8.exe.part"


def test_updater_beta_channel_selects_newer_stable_release(monkeypatch):
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version="4.0.7"),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "yes" if key == "include_beta_updates" else default),
    )

    updater = AutoUpdater(app)
    updater.platform = "windows"
    calls: list[str] = []

    class Response:
        def __init__(self, payload: dict):
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if url == updater.UPDATE_BETA_CHECK_URL:
            return Response(
                {
                    "version": "4.0.8",
                    "channel": "beta",
                    "platform": "windows",
                    "download_url": "https://example.com/TensaLauncher-beta.exe",
                }
            )
        return Response(
            {
                "version": "4.0.9",
                "channel": "stable",
                "platform": "windows",
                "download_url": "https://example.com/TensaLauncher-stable.exe",
            }
        )

    updater._session.get = fake_get

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.0.9"
    assert update["channel"] == "stable"
    assert update["download_url"] == "https://example.com/TensaLauncher-stable.exe"
    assert calls == [updater.UPDATE_BETA_CHECK_URL, updater.UPDATE_CHECK_URL]


def test_updater_falls_back_to_stable_when_beta_channel_unavailable(monkeypatch):
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version="4.0.7"),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "yes" if key == "include_beta_updates" else default),
    )

    updater = AutoUpdater(app)
    calls: list[str] = []

    class Response:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} error")

        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if url == updater.UPDATE_BETA_CHECK_URL:
            return Response({"message": "Launcher not found."}, status_code=404)
        return Response(
            {
                "version": "4.0.9",
                "channel": "stable",
                "platform": updater.platform,
                "download_url": "https://example.com/TensaLauncher-stable.exe",
            }
        )

    updater._session.get = fake_get

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.0.9"
    assert update["channel"] == "stable"
    assert calls == [updater.UPDATE_BETA_CHECK_URL, updater.UPDATE_CHECK_URL]


def test_updater_runs_download_and_prepare_off_ui_thread(monkeypatch):
    events: list[tuple] = []
    alert_calls: list[tuple[str, str]] = []

    class Operation:
        def update(self, message, progress=0, total=100):
            events.append(("update", message, progress, total))

        def finish(self, message=None, show_success=True):
            events.append(("finish", message, show_success))

    class Feedback:
        def begin_operation(self, title, **kwargs):
            events.append(("begin", title, kwargs))
            return Operation()

        def confirm(self, title, question, callback):
            alert_calls.append((title, question))

        def warning(self, message):
            events.append(("warning", message))

    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
        feedback=Feedback(),
        trans=lambda key, **_kwargs: key,
    )

    updater = AutoUpdater(app)
    download_path = SimpleNamespace(name="update.exe")
    to_thread_calls: list[object] = []

    def fake_download(update_info, progress_callback=None):
        progress_callback(50, 100)
        return download_path

    def fake_prepare(path):
        assert path is download_path
        return "update-cmd"

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn)
        return fn(*args, **kwargs)

    monkeypatch.setattr(updater, "download_update", fake_download)
    monkeypatch.setattr(updater, "prepare_update", fake_prepare)
    monkeypatch.setattr("launcher.core.updater.asyncio.to_thread", fake_to_thread)

    asyncio.run(updater.start_update_download({"download_url": "https://example.com/update.exe"}))

    assert to_thread_calls == [fake_download, fake_prepare]
    assert events[0] == (
        "begin",
        "update_downloading",
        {
            "kind": "launcher_update",
            "status": "update_downloading",
            "progress": 0,
            "total": 100,
        },
    )
    assert ("update", "update_downloading 50%", 50, 100) in events
    assert ("update", "update_applying", 100, 100) in events
    assert ("finish", None, False) in events
    assert alert_calls == [("update_ready_title", "update_ready_message")]


def test_updater_resumes_partial_download(monkeypatch, tmp_path):
    app = SimpleNamespace(
        util=SimpleNamespace(launcher_version=__version__),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(get=lambda key, default=None: "no"),
    )
    updater = AutoUpdater(app)
    updater.platform = "windows"
    updater._temp_dir = tmp_path

    partial = tmp_path / "TensaLauncher.exe.part"
    partial.write_bytes(b"ab")
    calls = []

    class Response:
        status_code = 206
        headers = {
            "content-length": "4",
            "content-range": "bytes 2-5/6",
        }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"cd"
            yield b"ef"

    def fake_get(url, headers=None, stream=None, timeout=None):
        calls.append({"url": url, "headers": headers, "stream": stream, "timeout": timeout})
        return Response()

    updater._session.get = fake_get

    path = updater.download_update(
        {
            "version": "4.0.4",
            "download_url": "https://example.com/TensaLauncher.exe",
            "download_file_name": "TensaLauncher.exe",
        }
    )

    assert path == tmp_path / "TensaLauncher.exe"
    assert path.read_bytes() == b"abcdef"
    assert calls[0]["headers"]["Range"] == "bytes=2-"
