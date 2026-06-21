from __future__ import annotations

from types import SimpleNamespace

from launcher.application.tensacraft_catalog import TensaCraftCatalogService


class FakeVersion(SimpleNamespace):
    def is_tensacraft(self) -> bool:
        return getattr(self, "client", "") == "TensaCraft"

    def is_home_pinned(self) -> bool:
        return getattr(self, "pinned", False)


def test_tensacraft_catalog_filters_local_versions():
    versions = [
        FakeVersion(client="Minecraft", id="local"),
        FakeVersion(client="TensaCraft", id="remote-hidden", pinned=False),
        FakeVersion(client="TensaCraft", id="remote-pinned", pinned=True),
    ]

    filtered = TensaCraftCatalogService.filter_local_versions(versions, show_tensacraft=False)

    assert [version.id for version in filtered] == ["local", "remote-pinned"]


def test_tensacraft_catalog_builds_remote_stub():
    stub = TensaCraftCatalogService.build_stub(
        {
            "name": "Pack",
            "client": {
                "ver_id": "pack-id",
                "image": "img",
                "description": "Create-based Tensa pack",
            },
        },
        "pack-id",
    )

    assert stub.is_remote is True
    assert stub.remote_pack_id == "pack-id"
    assert stub.id == "pack-id"
    assert stub.description == "Create-based Tensa pack"
