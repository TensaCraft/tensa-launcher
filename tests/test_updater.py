from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from launcher import __version__
from launcher.core.updater import AutoUpdater


class GitHubResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            msg = f"{self.status_code} error"
            raise RuntimeError(msg)

    def json(self):
        return self._payload


def app_stub(version: str = __version__, include_beta_updates: str = "no"):
    return SimpleNamespace(
        util=SimpleNamespace(launcher_version=version),
        log=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        config=SimpleNamespace(
            get=lambda key, default=None: include_beta_updates if key == "include_beta_updates" else default
        ),
    )


def github_release(
    tag_name: str,
    *,
    prerelease: bool,
    assets: list[dict[str, str]],
    body: str = "Release notes",
    draft: bool = False,
) -> dict:
    return {
        "tag_name": tag_name,
        "name": tag_name,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets,
    }


def github_asset(name: str, url: str, digest: str | None = "sha256:abcdef") -> dict[str, str]:
    asset = {"name": name, "browser_download_url": url}
    if digest:
        asset["digest"] = digest
    return asset


def test_updater_uses_semantic_version_comparison():
    assert AutoUpdater._is_newer_version("4.0.0", "3.1.5") is True
    assert AutoUpdater._is_newer_version("3.10.0", "3.9.9") is True
    assert AutoUpdater._is_newer_version("3.1.5", "3.1.5") is False
    assert AutoUpdater._is_newer_version("4.2.0", "4.2.0-beta.1") is True
    assert AutoUpdater._is_newer_version("4.2.0-beta.1", "4.2.0") is False


def test_updater_uses_github_releases_endpoint_and_headers():
    updater = AutoUpdater(app_stub())

    assert updater.GITHUB_RELEASES_URL == "https://api.github.com/repos/TensaCraft/tensa-launcher/releases"
    assert updater._session.headers["Accept"] == "application/vnd.github+json"
    assert updater._session.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_updater_detects_darwin_as_macos(monkeypatch):
    monkeypatch.setattr("launcher.core.updater.platform.system", lambda: "Darwin")

    assert AutoUpdater._detect_platform() == "macos"


def test_updater_stable_channel_ignores_github_prereleases():
    app = app_stub(version="4.0.0", include_beta_updates="no")
    updater = AutoUpdater(app)
    updater.platform = "windows"
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return GitHubResponse(
            [
                github_release(
                    "v4.2.0-beta.1",
                    prerelease=True,
                    assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher-beta.exe")],
                ),
                github_release(
                    "v4.1.0",
                    prerelease=False,
                    assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher.exe")],
                ),
            ]
        )

    updater._session.get = fake_get

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.1.0"
    assert update["channel"] == "stable"
    assert update["download_url"] == "https://example.com/TensaLauncher.exe"
    assert calls == [{"url": updater.GITHUB_RELEASES_URL, "params": {"per_page": 100}, "timeout": 5}]


def test_updater_beta_channel_accepts_github_prereleases():
    app = app_stub(version="4.0.0", include_beta_updates="yes")
    updater = AutoUpdater(app)
    updater.platform = "windows"

    updater._session.get = lambda *args, **kwargs: GitHubResponse(
        [
            github_release(
                "v4.2.0-beta.1",
                prerelease=True,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher-beta.exe")],
            ),
            github_release(
                "v4.1.0",
                prerelease=False,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher.exe")],
            ),
        ]
    )

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.2.0-beta.1"
    assert update["channel"] == "beta"
    assert update["download_url"] == "https://example.com/TensaLauncher-beta.exe"


def test_updater_beta_channel_prefers_final_release_over_same_version_prerelease():
    app = app_stub(version="4.1.0", include_beta_updates="yes")
    updater = AutoUpdater(app)
    updater.platform = "windows"

    updater._session.get = lambda *args, **kwargs: GitHubResponse(
        [
            github_release(
                "v4.2.0-beta.1",
                prerelease=True,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher-beta.exe")],
            ),
            github_release(
                "v4.2.0",
                prerelease=False,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher.exe")],
            ),
        ]
    )

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.2.0"
    assert update["channel"] == "stable"
    assert update["download_url"] == "https://example.com/TensaLauncher.exe"


def test_updater_selects_windows_launcher_asset_and_digest():
    app = app_stub(version="4.1.0")
    updater = AutoUpdater(app)
    updater.platform = "windows"

    update = updater._update_info_from_github_release(
        github_release(
            "v4.2.0",
            prerelease=False,
            assets=[
                github_asset("TensaLauncherInstaller.exe", "https://example.com/TensaLauncherInstaller.exe"),
                github_asset(
                    "TensaLauncher.exe",
                    "https://example.com/TensaLauncher.exe",
                    "sha256:1234567890abcdef",
                ),
            ],
        )
    )

    assert update is not None
    assert update["download_url"] == "https://example.com/TensaLauncher.exe"
    assert update["download_file_name"] == "TensaLauncher.exe"
    assert update["download_hash_algorithm"] == "sha256"
    assert update["download_hash"] == "1234567890abcdef"


def test_updater_selects_platform_specific_github_assets():
    updater = AutoUpdater(app_stub(version="4.1.0"))
    release = github_release(
        "v4.2.0",
        prerelease=False,
        assets=[
            github_asset("TensaLauncher", "https://example.com/TensaLauncher"),
            github_asset("TensaLauncher-x86_64.AppImage", "https://example.com/TensaLauncher.AppImage"),
            github_asset("TensaLauncher.dmg", "https://example.com/TensaLauncher.dmg"),
            github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher.exe"),
        ],
    )

    updater.platform = "macos"
    assert updater._update_info_from_github_release(release)["download_file_name"] == "TensaLauncher.dmg"

    updater.platform = "linux"
    updater.appimage_path = Path("/tmp/TensaLauncher.AppImage")
    assert updater._update_info_from_github_release(release)["download_file_name"] == "TensaLauncher-x86_64.AppImage"

    updater.appimage_path = None
    assert updater._update_info_from_github_release(release)["download_file_name"] == "TensaLauncher"


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
    updater = AutoUpdater(app_stub(version="4.0.7", include_beta_updates="yes"))
    updater.platform = "windows"

    updater._session.get = lambda *args, **kwargs: GitHubResponse(
        [
            github_release(
                "v4.0.8-beta.1",
                prerelease=True,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher-beta.exe")],
            ),
            github_release(
                "v4.0.9",
                prerelease=False,
                assets=[github_asset("TensaLauncher.exe", "https://example.com/TensaLauncher-stable.exe")],
            ),
        ]
    )

    update = updater.check_for_updates()

    assert update is not None
    assert update["version"] == "4.0.9"
    assert update["channel"] == "stable"
    assert update["download_url"] == "https://example.com/TensaLauncher-stable.exe"


def test_updater_returns_none_when_github_releases_payload_is_unexpected():
    updater = AutoUpdater(app_stub(version="4.0.7", include_beta_updates="yes"))
    updater._session.get = lambda *args, **kwargs: GitHubResponse({"message": "unexpected"})

    assert updater.check_for_updates() is None


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
