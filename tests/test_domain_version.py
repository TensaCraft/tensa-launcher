from __future__ import annotations

from launcher.domain.version import Version


def test_version_remote_metadata_round_trips_through_dict() -> None:
    version = Version(
        "remote-pack",
        {
            "name": "Remote Pack",
            "is_remote": True,
            "remote_pack_id": "catalog-pack-id",
            "description": "Remote catalog description",
        },
    )

    restored = Version(version.version_id, version.to_dict())

    assert restored.is_remote is True
    assert restored.remote_pack_id == "catalog-pack-id"
    assert restored.description == "Remote catalog description"
    assert restored.to_dict() == version.to_dict()
