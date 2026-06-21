from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.application.installed_components import InstalledComponentsService


def _write_version_manifest(minecraft_dir: Path, version_id: str, payload: dict) -> None:
    version_dir = minecraft_dir / "versions" / version_id
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / f"{version_id}.json").write_text(
        json.dumps({"id": version_id, "type": "release", "mainClass": "Main", "libraries": [], **payload}),
        encoding="utf-8",
    )


def test_installed_components_classify_loaders_and_usage(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    _write_version_manifest(minecraft_dir, "1.21.1", {})
    _write_version_manifest(
        minecraft_dir,
        "neoforge-21.1.230",
        {
            "inheritsFrom": "1.21.1",
            "arguments": {"game": ["--fml.mcVersion", "1.21.1", "--fml.neoForgeVersion", "21.1.230"]},
        },
    )
    _write_version_manifest(
        minecraft_dir,
        "fabric-loader-0.17.3-1.21.1",
        {"inheritsFrom": "1.21.1"},
    )

    game_versions = [
        SimpleNamespace(name="Aeronautics", version="1.21.1", loader="neoforge-21.1.230"),
        SimpleNamespace(name="Fabric Test", version="1.21.1", loader="fabric-loader-0.17.3-1.21.1"),
    ]
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: game_versions)

    components = {component.version_id: component for component in service.list_installed()}

    assert components["1.21.1"].kind == "minecraft"
    assert components["1.21.1"].minecraft_version == "1.21.1"
    assert components["1.21.1"].used_by == ("Aeronautics", "Fabric Test")
    assert components["1.21.1"].dependent_components == (
        "fabric-loader-0.17.3-1.21.1",
        "neoforge-21.1.230",
    )

    neoforge = components["neoforge-21.1.230"]
    assert neoforge.kind == "neoforge"
    assert neoforge.minecraft_version == "1.21.1"
    assert neoforge.loader_version == "21.1.230"
    assert neoforge.used_by == ("Aeronautics",)

    fabric = components["fabric-loader-0.17.3-1.21.1"]
    assert fabric.kind == "fabric"
    assert fabric.minecraft_version == "1.21.1"
    assert fabric.loader_version == "0.17.3"
    assert fabric.used_by == ("Fabric Test",)


def test_expected_component_id_matches_launcher_lib_layouts():
    assert InstalledComponentsService.expected_component_id("minecraft", "1.21.1") == "1.21.1"
    assert (
        InstalledComponentsService.expected_component_id("fabric", "1.21.1", "0.17.3")
        == "fabric-loader-0.17.3-1.21.1"
    )
    assert (
        InstalledComponentsService.expected_component_id("quilt", "1.21.1", "0.30.0")
        == "quilt-loader-0.30.0-1.21.1"
    )
    assert (
        InstalledComponentsService.expected_component_id("forge", "1.20.1", "47.4.0")
        == "1.20.1-forge-47.4.0"
    )
    assert InstalledComponentsService.expected_component_id("neoforge", "1.21.1", "21.1.230") == "neoforge-21.1.230"


def test_delete_component_refuses_paths_outside_versions_root(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    with pytest.raises(ValueError):
        service.delete_component("../outside")


def test_delete_component_removes_only_selected_version_directory(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    keep_dir = minecraft_dir / "versions" / "1.21.2"
    keep_dir.mkdir(parents=True)
    (keep_dir / "1.21.2.json").write_text("{}", encoding="utf-8")
    _write_version_manifest(minecraft_dir, "1.21.1", {})
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    service.delete_component("1.21.1")

    assert not (minecraft_dir / "versions" / "1.21.1").exists()
    assert keep_dir.exists()
