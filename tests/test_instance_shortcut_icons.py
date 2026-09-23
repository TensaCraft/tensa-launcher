from __future__ import annotations

import asyncio
import base64
import errno
import io
import json
import plistlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import requests
from PIL import Image

from launcher.pages.home import Home
from launcher.pages.versions import VersionsPage
from launcher.platform import instance_shortcuts as shortcuts


@pytest.fixture
def icon_data():
    stream = io.BytesIO()
    Image.new("RGBA", (48, 24), (27, 190, 75, 255)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("extension", [".ico", ".png", ".icns"])
@pytest.mark.parametrize("encoding", ["path", "base64", "data_url"])
def test_instance_icon_is_converted_without_changing_content(tmp_path, icon_data, extension, encoding):
    source = tmp_path / "source.png"
    source.write_bytes(icon_data)
    encoded = base64.b64encode(icon_data).decode("ascii")
    value = {"path": str(source), "base64": encoded, "data_url": "data:image/png;base64," + encoded}[encoding]

    icon = shortcuts._persistent_icon("stable-id", value, tmp_path / "icons", extension)
    source.unlink()

    assert icon.parent == tmp_path / "icons"
    with Image.open(icon) as image:
        rgba = image.convert("RGBA")
        assert rgba.getpixel((rgba.width // 2, rgba.height // 2)) == (27, 190, 75, 255)
        assert rgba.getpixel((0, 0))[3] == 0
    if extension == ".ico":
        with Image.open(icon) as image:
            assert image.ico.sizes() == {(value, value) for value in (16, 24, 32, 48, 64, 128, 256)}


def test_remote_instance_image_is_downloaded_with_limits(monkeypatch, tmp_path, icon_data):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 65536
            yield icon_data

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(shortcuts.requests, "get", get)
    icon = shortcuts._persistent_icon("id", "https://example.test/build.png", tmp_path / "icons", ".ico")
    assert icon.is_file()
    assert calls == [("https://example.test/build.png", {"stream": True, "timeout": (5, 15)})]


def test_supplied_broken_image_does_not_fall_back_to_launcher(tmp_path):
    with pytest.raises(OSError):
        shortcuts._persistent_icon("id", base64.b64encode(b"not an image").decode(), tmp_path / "icons", ".ico")
    assert not (tmp_path / "icons").exists()


def test_failed_icon_download_is_reported_without_fallback(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise requests.Timeout("offline")

    monkeypatch.setattr(shortcuts.requests, "get", fail)
    with pytest.raises(OSError, match="download"):
        shortcuts._persistent_icon("id", "https://example.test/image.png", tmp_path / "icons", ".ico")
    assert not (tmp_path / "icons").exists()


def test_none_image_uses_launcher_image(monkeypatch, tmp_path, icon_data):
    assets = tmp_path / "packaged-assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(icon_data)
    monkeypatch.setattr(shortcuts, "PACKAGE_ASSETS_DIR", assets)

    icon = shortcuts._persistent_icon("id", None, tmp_path / "icons", ".ico")
    (assets / "logo.png").unlink()

    with Image.open(icon) as image:
        assert image.convert("RGBA").getpixel((128, 128)) == (27, 190, 75, 255)


def test_icons_are_immutable_content_addressed_assets(tmp_path, icon_data):
    encoded = base64.b64encode(icon_data).decode()
    first = shortcuts._persistent_icon("id", encoded, tmp_path, ".ico")
    first_bytes = first.read_bytes()
    same = shortcuts._persistent_icon("id", encoded, tmp_path, ".ico")
    different = shortcuts._persistent_icon("id", None, tmp_path, ".ico")
    assert first == same
    assert different != first
    assert first.read_bytes() == first_bytes


def test_valid_cached_icon_is_reused_without_replacement(tmp_path, icon_data, monkeypatch):
    encoded = base64.b64encode(icon_data).decode()
    icon = shortcuts._persistent_icon("id", encoded, tmp_path, ".ico")
    original = icon.stat()
    monkeypatch.setattr(shortcuts.os, "replace", lambda *_args: pytest.fail("Valid cache must not be replaced"))
    monkeypatch.setattr(
        shortcuts.tempfile, "NamedTemporaryFile", lambda **_kwargs: pytest.fail("Valid cache needs no temporary write"),
    )

    assert shortcuts._persistent_icon("id", encoded, tmp_path, ".ico") == icon
    assert icon.stat() == original


@pytest.mark.parametrize("extension", [".ico", ".png", ".icns"])
def test_partial_cached_icon_is_repaired(tmp_path, icon_data, extension):
    encoded = base64.b64encode(icon_data).decode()
    icon = shortcuts._persistent_icon("id", encoded, tmp_path, extension)
    expected = icon.read_bytes()
    icon.write_bytes(expected[:16])

    assert shortcuts._persistent_icon("id", encoded, tmp_path, extension) == icon
    assert icon.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [icon]


@pytest.mark.parametrize("existing", [False, True])
def test_interrupted_icon_write_is_not_published_and_retry_succeeds(tmp_path, icon_data, monkeypatch, existing):
    encoded = base64.b64encode(icon_data).decode()
    icon = shortcuts._persistent_icon("id", encoded, tmp_path, ".ico")
    expected = icon.read_bytes()
    if existing:
        icon.write_bytes(expected[:16])
    else:
        icon.unlink()

    def fail_flush(_descriptor):
        if existing:
            assert icon.read_bytes() == expected[:16]
        else:
            assert not icon.exists()
        raise OSError(errno.ENOSPC, "simulated interrupted write")

    with monkeypatch.context() as patch:
        patch.setattr(shortcuts.os, "fsync", fail_flush)
        with pytest.raises(OSError, match="simulated interrupted write"):
            shortcuts._persistent_icon("id", encoded, tmp_path, ".ico")
    assert list(tmp_path.iterdir()) == ([icon] if existing else [])
    assert shortcuts._persistent_icon("id", encoded, tmp_path, ".ico") == icon
    assert icon.read_bytes() == expected


def test_concurrent_icon_publication_returns_complete_same_asset(tmp_path, icon_data):
    encoded = base64.b64encode(icon_data).decode()
    barrier = Barrier(8)

    def publish(_index):
        barrier.wait(timeout=5)
        icon = shortcuts._persistent_icon("id", encoded, tmp_path, ".png")
        return icon, icon.read_bytes()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(publish, range(8)))
    assert all(result == results[0] for result in results)
    assert list(tmp_path.iterdir()) == [results[0][0]]
    with Image.open(io.BytesIO(results[0][1])) as image:
        image.verify()


def test_icon_cache_does_not_follow_symlinks(tmp_path, icon_data):
    encoded = base64.b64encode(icon_data).decode()
    icons = tmp_path / "icons"
    icon = shortcuts._persistent_icon("id", encoded, icons, ".png")
    icon.unlink()
    victim = tmp_path / "unrelated.txt"
    victim.write_bytes(b"untouched")
    try:
        icon.symlink_to(victim)
    except OSError:
        pytest.skip("Creating symlinks requires OS privileges")

    with pytest.raises(OSError):
        shortcuts._persistent_icon("id", encoded, icons, ".png")
    assert icon.is_symlink()
    assert victim.read_bytes() == b"untouched"
    assert list(icons.iterdir()) == [icon]


def test_oversized_image_is_rejected_before_conversion(monkeypatch, tmp_path, icon_data):
    monkeypatch.setattr(shortcuts, "MAX_ICON_PIXELS", 100)
    with pytest.raises(ValueError, match="pixel limit"):
        shortcuts._persistent_icon("id", base64.b64encode(icon_data).decode(), tmp_path / "icons", ".ico")
    assert not (tmp_path / "icons").exists()


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_shortcuts_reference_the_persistent_instance_icon(monkeypatch, tmp_path, icon_data, platform):
    monkeypatch.setattr(shortcuts.sys, "platform", platform)
    icons = tmp_path / "icons"
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    command = shortcuts.ShortcutCommand("/launcher", ("--launch-version=id",), tmp_path)
    captured = []
    monkeypatch.setattr(shortcuts, "_windows_link", lambda command, icon: captured.append(icon) or b"link")

    path = shortcuts.create_desktop_shortcut(
        "id", "Build", desktop=desktop, command=command,
        icon_source=base64.b64encode(icon_data).decode(), icon_directory=icons,
    )

    icon = next(icons.iterdir())
    if platform == "linux":
        assert "Icon=" + shortcuts._desktop_string(str(icon)) in path.read_text()
    elif platform == "darwin":
        metadata = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
        bundled_icon = path / "Contents" / "Resources" / metadata["CFBundleIconFile"]
        assert bundled_icon.read_bytes() == icon.read_bytes()
    else:
        assert captured == [icon]


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_build_page_passes_displayed_instance_image(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    version = fake_app.versions.all()[0]
    version.image = "https://example.test/instance.png"
    pending = []
    monkeypatch.setattr(page._shortcut_tasks, "run", lambda task, *args: pending.append((task, args)))

    page.create_shortcut(version)

    assert pending == [(page._create_shortcut_async, (version.version_id, version.name, version.image))]


def test_shortcut_worker_passes_icon_and_configured_persistent_storage(fake_app, monkeypatch):
    page = VersionsPage(fake_app)
    calls = []
    monkeypatch.setattr("launcher.pages.version_actions.create_desktop_shortcut", lambda *args, **kwargs: calls.append(kwargs))

    asyncio.run(page._create_shortcut_async("id", "Build", "base64-icon"))

    assert calls == [{"icon_source": "base64-icon", "icon_directory": Path(fake_app.util.app_state_dir) / "shortcut-icons"}]


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows shortcut round-trip")
def test_native_windows_link_retains_icon_and_exact_instance_arguments(tmp_path, icon_data):
    command = shortcuts.ShortcutCommand(sys.executable, ('--launch-version=id with "quotes"',), tmp_path)
    link = shortcuts.create_desktop_shortcut(
        "id", "Build", desktop=tmp_path, command=command,
        icon_source=base64.b64encode(icon_data).decode(), icon_directory=tmp_path / "icons",
    )
    result = shortcuts._powershell(
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$data = $env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json; "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($data.path); "
        "@{target=$link.TargetPath; arguments=$link.Arguments; icon=$link.IconLocation} | ConvertTo-Json -Compress",
        {"path": str(link)},
    )
    metadata = json.loads(result)
    assert Path(metadata["target"]) == Path(command.executable)
    assert metadata["arguments"] == shortcuts.subprocess.list2cmdline(command.arguments)
    assert metadata["icon"] == str(next((tmp_path / "icons").iterdir())) + ",0"
