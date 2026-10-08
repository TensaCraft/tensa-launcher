from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .files import safe_path
from .models import JsonObject, MigrationAsset, MigrationError, MigrationProgress

REPOSITORY = "TensaCraft/GileaLauncher"
TAG = "v0.0.9"
ASSET_NAME = "GileaLauncher-tensa.exe"
RELEASE_URL = f"https://github.com/{REPOSITORY}/releases/tag/{TAG}"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{TAG}"
ASSET_URL = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{ASSET_NAME}"
PINNED_SIZE = 31633920
PINNED_SHA256 = "7adcf9e55d0cba8ec488ffe1876954cd603e89a71399714aa423ef5aed9aaeaa"
DELIVERY_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def select_asset(release: JsonObject) -> MigrationAsset:
    if (not isinstance(release, dict) or release.get("tag_name") != TAG or release.get("html_url") != RELEASE_URL
            or release.get("draft") is not False or release.get("prerelease") is not False):
        raise MigrationError("invalid_release")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise MigrationError("invalid_release")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == ASSET_NAME]
    if len(matches) != 1:
        raise MigrationError("invalid_release")
    asset = matches[0]
    if (asset.get("state") != "uploaded" or asset.get("size") != PINNED_SIZE
            or asset.get("digest") != "sha256:" + PINNED_SHA256 or asset.get("browser_download_url") != ASSET_URL
            or type(asset.get("id")) is not int or asset["id"] <= 0):
        raise MigrationError("invalid_release")
    return MigrationAsset(ASSET_URL, PINNED_SIZE, PINNED_SHA256, asset["id"])


def fetch_asset(session: requests.Session) -> MigrationAsset:
    try:
        response = session.get(API_URL, timeout=(10, 30), allow_redirects=False,
                               headers={"Accept": "application/vnd.github+json"})
        try:
            if response.status_code != 200:
                raise MigrationError("invalid_release")
            return select_asset(response.json())
        finally:
            response.close()
    except (requests.RequestException, ValueError):
        raise MigrationError("download_failed") from None


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in DELIVERY_HOSTS and not parsed.username
                 and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise MigrationError("unsafe_download")


def verify_binary(path: Path) -> None:
    safe_path(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != PINNED_SIZE or digest != PINNED_SHA256:
        raise MigrationError("checksum")


def download_asset(asset: MigrationAsset, target: Path, *, session: requests.Session,
                   cancel: threading.Event, progress: Callable[[MigrationProgress], None]) -> Path:
    if (asset.url, asset.size, asset.sha256) != (ASSET_URL, PINNED_SIZE, PINNED_SHA256):
        raise MigrationError("invalid_release")
    safe_path(target)
    part = safe_path(target.with_suffix(target.suffix + ".part"))
    if target.exists() or part.exists():
        raise MigrationError("destination_conflict")
    response = None
    owned = False
    try:
        url = asset.url
        started = time.monotonic()
        for _ in range(6):
            if cancel.is_set():
                raise MigrationError("cancelled")
            _validate_url(url)
            response = session.get(url, stream=True, timeout=(10, 30), allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                if not location:
                    raise MigrationError("unsafe_download")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise MigrationError("download_failed")
            break
        else:
            raise MigrationError("unsafe_download")
        target.parent.mkdir(parents=True, exist_ok=True)
        with part.open("xb") as stream:
            owned = True
            total = 0
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if cancel.is_set():
                    raise MigrationError("cancelled")
                if time.monotonic() - started > 600:
                    raise MigrationError("download_failed")
                total += len(chunk)
                if total > asset.size:
                    raise MigrationError("checksum")
                stream.write(chunk)
                progress(MigrationProgress("download", total, asset.size))
            stream.flush()
            os.fsync(stream.fileno())
        verify_binary(part)
        if cancel.is_set():
            raise MigrationError("cancelled")
        # Windows rename does not replace a destination created by another writer.
        part.rename(target)
        return target
    except requests.RequestException:
        raise MigrationError("download_failed") from None
    finally:
        if response is not None:
            response.close()
        if owned:
            part.unlink(missing_ok=True)
