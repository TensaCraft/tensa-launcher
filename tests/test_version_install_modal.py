from __future__ import annotations

import asyncio
from types import SimpleNamespace

from launcher import ui
from launcher.application.version_creation import VersionCreateOption


def test_version_install_modal_updates_tensacraft_description(fake_app, monkeypatch):
    packs = [
        {
            "name": "aeronautics",
            "client": {
                "id": "aeronautics",
                "name": "Aeronautics",
                "description": "Aeronautics Create mods server TensaCraft",
            },
        },
        {
            "name": "tensa-lite",
            "client": {
                "id": "tensa-lite",
                "name": "Tensa",
                "description": "Light optimized pack",
            },
        },
    ]

    class FakeTensaCraftAPI:
        def list_versions(self):
            return packs

    fake_loader = SimpleNamespace(get_id=lambda: "tensacraft", get_name=lambda: "TensaCraft")
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.TensaCraftAPI", FakeTensaCraftAPI)
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.Launcher.loaders", lambda _self: [fake_loader])
    monkeypatch.setattr(
        "launcher.ui.modals.version_install_modal.Launcher.get_loader_versions",
        lambda _self, _loader: ["aeronautics", "tensa-lite"],
    )

    modal = ui.VersionInstallModal(fake_app)

    assert modal.tensacraft_description_panel.visible is True
    assert modal.tensacraft_description_text.value == "Aeronautics Create mods server TensaCraft"

    modal.version_select.value = "tensa-lite"
    modal.on_version_change(None)

    assert modal.tensacraft_description_text.value == "Light optimized pack"


def test_version_install_modal_marks_tensacraft_pack_pending(fake_app, monkeypatch):
    fake_loader = SimpleNamespace(get_id=lambda: "tensacraft", get_name=lambda: "TensaCraft")
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.TensaCraftAPI", lambda: SimpleNamespace(list_versions=lambda: []))
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.Launcher.loaders", lambda _self: [fake_loader])
    monkeypatch.setattr(
        "launcher.ui.modals.version_install_modal.Launcher.get_loader_versions",
        lambda _self, _loader: ["aeronautics"],
    )
    scheduled = []
    fake_app.page.run_task = lambda func, *args, **_kwargs: scheduled.append((func, args))
    fake_app.versions.get_by_name = lambda _name: None

    modal = ui.VersionInstallModal(fake_app)
    modal.version_name.value = "Aeronautics"
    modal.type_select.value = "tensacraft"
    modal.version_select.value = "aeronautics"

    modal.create_version(None)

    assert fake_app.pending_tensacraft_pack_ids == {"aeronautics"}
    assert (modal._start_install_after_close, ("Aeronautics", "tensacraft", "aeronautics")) in scheduled


def test_version_install_modal_installs_selected_loader_build(fake_app, monkeypatch):
    captured = []

    class FakeCatalog:
        def loader_versions(self, loader_id):
            assert loader_id == "fabric"
            return [
                VersionCreateOption(
                    id="fabric-1.20.1",
                    name="Fabric 1.20.1",
                    minecraft_version="1.20.1",
                    loader_id="fabric",
                    loader_name="Fabric",
                    loader_version="0.16.9",
                    loader_versions=("0.16.9", "0.15.11"),
                )
            ]

    class FakeInstalledComponentsService:
        def __init__(self, *_args, **_kwargs):
            pass

        def install_game_build(self, version, *, operation=None):
            captured.append(
                {
                    "name": version.name,
                    "client": version.client,
                    "version": version.version,
                    "loader_version": version.loader_version,
                    "operation": operation,
                }
            )

    fake_loaders = [
        SimpleNamespace(get_id=lambda: "tensacraft", get_name=lambda: "TensaCraft"),
        SimpleNamespace(get_id=lambda: "fabric", get_name=lambda: "Fabric"),
    ]
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.TensaCraftAPI", lambda: SimpleNamespace(list_versions=lambda: []))
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.Launcher.loaders", lambda _self: fake_loaders)
    monkeypatch.setattr(
        "launcher.ui.modals.version_install_modal.Launcher.get_loader_versions",
        lambda _self, _loader: ["aeronautics"],
    )
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.VersionCreationCatalogService", FakeCatalog)
    monkeypatch.setattr("launcher.ui.modals.version_install_modal.InstalledComponentsService", FakeInstalledComponentsService)

    modal = ui.VersionInstallModal(fake_app)
    modal.type_select.value = "fabric"
    modal.on_type_change(None)
    modal.loader_build_select.value = "0.15.11"
    modal.on_loader_build_change(SimpleNamespace(control=modal.loader_build_select))
    modal._pending_loader_version = modal._selected_loader_version("fabric", "1.20.1")

    operation = SimpleNamespace(finish=lambda *_args, **_kwargs: None, fail=lambda *_args, **_kwargs: None)
    asyncio.run(modal._install_version_async("Fabric Build", "fabric", "1.20.1", operation))

    assert captured == [
        {
            "name": "Fabric Build",
            "client": "fabric",
            "version": "1.20.1",
            "loader_version": "0.15.11",
            "operation": operation,
        }
    ]
