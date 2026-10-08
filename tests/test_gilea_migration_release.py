import hashlib
import threading

import pytest
import requests

from launcher.application.gilea_migration import release
from launcher.application.gilea_migration.models import MigrationError

PAYLOAD = b"MZ-fixture-not-an-executable"


@pytest.fixture
def metadata(monkeypatch):
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    monkeypatch.setattr(release, "PINNED_SHA256", digest)
    monkeypatch.setattr(release, "PINNED_SIZE", len(PAYLOAD))
    return {"tag_name": "v0.0.9", "html_url": "https://github.com/TensaCraft/GileaLauncher/releases/tag/v0.0.9",
            "draft": False, "prerelease": False, "assets": [
                {"name": "GileaLauncher-tensa.exe", "state": "uploaded", "id": 1, "size": len(PAYLOAD),
                 "digest": "sha256:" + digest, "browser_download_url": release.ASSET_URL}]}


class Response:
    def __init__(self, status=200, location=None, data=PAYLOAD):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("fixture")

    def close(self):
        pass

    def iter_content(self, chunk_size):
        yield self.data


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_selects_only_pinned_stable_tensa_portable(metadata):
    asset = release.select_asset(metadata)
    assert asset.url.endswith("/v0.0.9/GileaLauncher-tensa.exe")
    assert asset.sha256 == hashlib.sha256(PAYLOAD).hexdigest()


@pytest.mark.parametrize(("field", "value"), [("name", "GileaLauncher.exe"), ("name", "GileaLauncher-tensa-Setup.exe"),
                                              ("size", 0), ("digest", None), ("state", "new"),
                                              ("digest", "sha256:" + "0" * 64)])
def test_invalid_asset_is_rejected(metadata, field, value):
    metadata["assets"][0][field] = value
    with pytest.raises(MigrationError):
        release.select_asset(metadata)


@pytest.mark.parametrize(("field", "value"), [("draft", True), ("prerelease", True), ("tag_name", "v0.1.0"),
                                              ("html_url", "https://github.com/other/repo")])
def test_unreviewed_release_is_rejected(metadata, field, value):
    metadata[field] = value
    with pytest.raises(MigrationError):
        release.select_asset(metadata)


def test_download_validates_every_redirect_and_byte(tmp_path, metadata):
    session = Session(Response(302, "https://release-assets.githubusercontent.com/fixture?signature=x"), Response())
    target = tmp_path / "application.exe"
    events = []
    release.download_asset(release.select_asset(metadata), target, session=session,
                           cancel=threading.Event(), progress=events.append)
    assert target.read_bytes() == PAYLOAD
    assert len(session.urls) == 2
    assert events[-1].completed == len(PAYLOAD)
    assert not target.with_suffix(".exe.part").exists()


@pytest.mark.parametrize("location", ["http://github.com/a", "https://evil.test/a",
                                     "https://github.com.evil.test/a", "https://user@github.com/a",
                                     "https://github.com:8443/a"])
def test_bad_redirect_never_runs_or_publishes(tmp_path, metadata, location):
    target = tmp_path / "application.exe"
    session = Session(Response(302, location))
    with pytest.raises(MigrationError):
        release.download_asset(release.select_asset(metadata), target, session=session,
                               cancel=threading.Event(), progress=lambda event: None)
    assert not target.exists()
    assert len(session.urls) == 1


@pytest.mark.parametrize("response", [Response(data=b"bad"), Response(data=PAYLOAD + b"x"),
                                    requests.Timeout("fixture")])
def test_failed_download_has_no_executable(tmp_path, metadata, response):
    target = tmp_path / "application.exe"
    with pytest.raises(MigrationError):
        release.download_asset(release.select_asset(metadata), target, session=Session(response),
                               cancel=threading.Event(), progress=lambda event: None)
    assert not target.exists()
    assert not target.with_suffix(".exe.part").exists()


def test_cancelled_download_does_not_contact_network(tmp_path, metadata):
    cancel = threading.Event()
    cancel.set()
    session = Session()
    with pytest.raises(MigrationError, match="cancelled"):
        release.download_asset(release.select_asset(metadata), tmp_path / "x.exe", session=session,
                               cancel=cancel, progress=lambda event: None)
    assert session.urls == []


@pytest.mark.parametrize("payload", [None, [], "not a release"])
def test_non_object_release_is_rejected(payload):
    with pytest.raises(MigrationError, match="invalid_release"):
        release.select_asset(payload)
