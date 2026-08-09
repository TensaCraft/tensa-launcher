from __future__ import annotations

import json
from dataclasses import replace
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


def test_installed_components_skip_manifest_with_mismatched_identity(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    _write_version_manifest(minecraft_dir, "1.21.1", {"id": "1.21.2"})
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    assert service.list_installed() == []


def test_delete_component_refuses_manifest_with_mismatched_identity(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    version_dir = minecraft_dir / "versions" / "1.21.1"
    _write_version_manifest(minecraft_dir, "1.21.1", {"id": "1.21.2"})
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    with pytest.raises(RuntimeError, match="manifest id does not match requested id"):
        service.delete_component("1.21.1")

    assert version_dir.exists()


def test_delete_component_refuses_directory_without_identity_manifest(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    version_dir = minecraft_dir / "versions" / "1.21.1"
    version_dir.mkdir(parents=True)
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    with pytest.raises(FileNotFoundError, match="Component manifest not found"):
        service.delete_component("1.21.1")

    assert version_dir.exists()


def test_delete_component_uses_case_normalized_windows_identity(monkeypatch, tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    version_dir = minecraft_dir / "versions" / "Fabric-Loader-0.17.3-1.21.1"
    version_dir.mkdir(parents=True)
    (version_dir / "fabric-loader-0.17.3-1.21.1.json").write_text(
        json.dumps(
            {
                "id": "fabric-loader-0.17.3-1.21.1",
                "type": "release",
                "mainClass": "Main",
                "libraries": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        InstalledComponentsService,
        "_identity_key",
        staticmethod(lambda value: str(value).casefold()),
    )
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])

    service.delete_component("FABRIC-LOADER-0.17.3-1.21.1")

    assert not version_dir.exists()


def test_reinstall_component_refuses_mismatched_stored_path(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    _write_version_manifest(minecraft_dir, "1.21.1", {})
    service = InstalledComponentsService(minecraft_dir, versions_provider=lambda: [])
    component = service.get_component("1.21.1")
    assert component is not None
    unsafe_component = replace(component, path=tmp_path / "outside")

    with pytest.raises(RuntimeError, match="Stored component path does not match component id"):
        service.reinstall_component(unsafe_component)


@pytest.mark.parametrize("component_id", ("nested/id", r"C:\outside", "NUL", "component.", " component"))
def test_component_operations_reject_noncanonical_ids(tmp_path: Path, component_id: str):
    service = InstalledComponentsService(tmp_path / "minecraft", versions_provider=lambda: [])

    with pytest.raises(ValueError):
        service.delete_component(component_id)


def test_component_install_uses_injected_session_loader(tmp_path: Path):
    minecraft_dir = tmp_path / "minecraft"
    calls = []

    class Loader:
        def _install_minecraft_if_needed(
            self,
            minecraft_version,
            *,
            force_check,
            operation,
        ):
            calls.append((minecraft_version, force_check, operation))
            _write_version_manifest(minecraft_dir, minecraft_version, {})

    loader = Loader()
    service = InstalledComponentsService(
        minecraft_dir,
        versions_provider=lambda: [],
        loader_provider=lambda loader_id: (
            loader
            if loader_id == "minecraft"
            else (_ for _ in ()).throw(AssertionError(loader_id))
        ),
    )

    component = service.install_component("minecraft", "1.21.1")

    assert component.version_id == "1.21.1"
    assert calls == [("1.21.1", True, None)]
