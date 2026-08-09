from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from launcher.application.installed_components import InstalledComponent
from launcher.application.version_creation import VersionCreateOption
from launcher.ui.modals.version_component_modal import VersionComponentModal


class _Catalog:
    @staticmethod
    def supports_snapshots(_loader_id: str) -> bool:
        return False

    @staticmethod
    def supports_unstable_loaders(_loader_id: str) -> bool:
        return False

    @staticmethod
    def minecraft_versions(**_kwargs):
        return []

    @staticmethod
    def loader_versions(loader_id: str, **_kwargs):
        assert loader_id == "neoforge"
        return [
            VersionCreateOption(
                id="neoforge:1.21.1:21.1.236",
                name="NeoForge 1.21.1",
                minecraft_version="1.21.1",
                loader_id="neoforge",
                loader_name="NeoForge",
                loader_version="21.1.236",
                loader_versions=("21.1.236", "21.1.235"),
            )
        ]


class _Components:
    def __init__(self, component: InstalledComponent) -> None:
        self.component = component
        self.calls = []

    def get_component(self, _version_id: str):
        return self.component

    def install_profile_component(
        self,
        version,
        loader_id,
        minecraft_version,
        *,
        loader_version=None,
        operation=None,
    ):
        self.calls.append((version, loader_id, minecraft_version, loader_version, operation))
        return self.component


def _component(tmp_path: Path) -> InstalledComponent:
    return InstalledComponent(
        version_id="neoforge-21.1.236",
        kind="neoforge",
        loader_name="NeoForge",
        minecraft_version="1.21.1",
        loader_version="21.1.236",
        inherits_from="1.21.1",
        path=tmp_path / "minecraft" / "versions" / "neoforge-21.1.236",
        size_bytes=0,
        modified_at=None,
        used_by=(),
        dependent_components=(),
    )


def test_version_component_modal_loads_remote_loader_builds(fake_app, tmp_path: Path):
    component = _component(tmp_path)
    service = _Components(component)
    version = fake_app.versions.all()[0]
    version.client = "NeoForge"
    version.loader = "neoforge-21.1.230"
    version.loader_version = "21.1.230"
    modal = VersionComponentModal(fake_app, version, service)
    modal.catalog = _Catalog()
    modal.loader_select.value = "neoforge"
    modal._closed = False
    modal._load_generation = 1

    asyncio.run(modal._load_options_async("neoforge", False, 1))

    assert modal.version_select.value == "1.21.1"
    assert [option.key for option in modal.loader_build_select.options] == ["21.1.236", "21.1.235"]
    assert modal.loader_build_select.value == "21.1.236"
    assert modal.install_button.disabled is False


def test_version_component_modal_installs_selected_build_and_notifies_page(fake_app, tmp_path: Path):
    component = _component(tmp_path)
    service = _Components(component)
    version = fake_app.versions.all()[0]
    installed = []
    info = []
    fake_app.feedback.info = lambda message, **_kwargs: info.append(message)
    modal = VersionComponentModal(fake_app, version, service, on_installed=installed.append)
    option = _Catalog.loader_versions("neoforge")[0]
    operation = SimpleNamespace(
        finish=lambda *_args, **_kwargs: None,
        fail=lambda *_args, **_kwargs: None,
    )

    asyncio.run(modal._install_async(option, "21.1.235", operation))

    assert service.calls == [(version, "neoforge", "1.21.1", "21.1.235", operation)]
    assert installed == [component]
    assert info == ["version_component_install_complete (loader=NeoForge, version=1.21.1)"]
