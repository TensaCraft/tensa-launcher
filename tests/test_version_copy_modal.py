from __future__ import annotations

from pathlib import Path
import json

import pytest

from launcher.core import util
from launcher.storage.version_store import Versions
from launcher.ui.modals.version_copy_modal import VersionCopyModal


class _TensaVersion:
    version_id = "aeronautics"
    id = "aeronautics"
    name = "Aeronautics"
    version = "1.21.1"
    client = "TensaCraft"
    loader = "neoforge-21.1.228"
    loader_version = "21.1.228"
    force_update = True
    image = "icon"

    def __init__(self, path: str) -> None:
        self.path = path
        self.options = {"gpuMode": "dgpu", "jvmArguments": ["-Xmx4G"]}


class _EmptyLauncher:
    def loaders(self):
        return []


def _empty_launcher():
    return _EmptyLauncher()


def test_tensacraft_copy_defaults_to_source_loader(fake_app, monkeypatch, tmp_path: Path):
    source_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    source_dir.mkdir(parents=True)
    source = _TensaVersion(str(source_dir))

    monkeypatch.setattr("launcher.ui.modals.version_copy_modal.Launcher", _empty_launcher)

    modal = VersionCopyModal(fake_app, source)

    assert modal.type_select.value == "neoforge"


def test_tensacraft_copy_copies_mods_to_games_dir_and_keeps_loader(fake_app, monkeypatch, tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = minecraft_dir / "games"
    source_dir = games_dir / "aeronautics"
    mods_dir = source_dir / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "create-connected.jar").write_bytes(b"mod")
    (mods_dir / "sodium.jar.disabled").write_bytes(b"disabled")
    (source_dir / "config").mkdir()
    (source_dir / "config" / "client.toml").write_text("legacy=true", encoding="utf-8")
    (source_dir / "resourcepacks").mkdir()
    (source_dir / "resourcepacks" / "pack.zip").write_bytes(b"pack")
    source = _TensaVersion(str(source_dir))

    monkeypatch.setattr(util, "minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr(util, "games_path", str(games_dir))
    Versions._instance = None
    monkeypatch.setattr(Versions, "instance", lambda: fake_app.versions)
    monkeypatch.setattr("launcher.ui.modals.version_copy_modal.Launcher", _empty_launcher)

    saved = []
    fake_app.versions.add = lambda version: saved.append(version)
    modal = VersionCopyModal(fake_app, source)

    modal._copy_version_impl("Aeronautics AMD")

    dest_dir = games_dir / "aeronautics_amd"
    assert (dest_dir / "mods" / "create-connected.jar").is_file()
    assert (dest_dir / "mods" / "sodium.jar.disabled").is_file()
    assert (dest_dir / "config" / "client.toml").read_text(encoding="utf-8") == "legacy=true"
    assert (dest_dir / "resourcepacks" / "pack.zip").is_file()
    assert len(saved) == 1
    copied = saved[0]
    assert copied.id == "aeronautics_amd"
    assert copied.version_id == "aeronautics_amd"
    assert copied.path == str(dest_dir)
    assert copied.client == "NeoForge"
    assert copied.loader == "neoforge-21.1.228"
    assert copied.force_update is False
    assert copied.is_tensacraft() is False
    assert copied.options["syncMode"] == "manual"
    assert copied.options["managedByApi"] is False

    snapshot = json.loads((dest_dir / "tensalauncher-copy.json").read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert snapshot["managed"] is False
    assert snapshot["sync_mode"] == "manual"
    assert snapshot["source"]["version_id"] == "aeronautics"
    assert snapshot["copy"]["version_id"] == "aeronautics_amd"


def test_version_copy_fails_without_source_directory(fake_app, monkeypatch, tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = minecraft_dir / "games"
    source = _TensaVersion(str(games_dir / "missing"))

    monkeypatch.setattr(util, "minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr(util, "games_path", str(games_dir))
    monkeypatch.setattr("launcher.ui.modals.version_copy_modal.Launcher", _empty_launcher)

    saved = []
    fake_app.versions.add = lambda version: saved.append(version)
    modal = VersionCopyModal(fake_app, source)

    with pytest.raises(FileNotFoundError):
        modal._copy_version_impl("Broken Copy")

    assert saved == []
