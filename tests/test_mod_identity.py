import json
import zipfile
from pathlib import Path

from launcher.application.mod_identity import (
    ModIdentityService,
    ModMatchKind,
    inspect_mod_jar,
)


def _write_json_jar(path: Path, members: dict[str, object]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, json.dumps(payload))
    return path


def test_exact_modrinth_provenance_wins_over_earlier_fuzzy_hint():
    fuzzy = {"filename": "sodium-extra-fabric.jar", "name": "Sodium Extra"}
    owned = {
        "filename": "renamed-by-user.jar",
        "modrinth_project_id": "sodium-project",
        "modrinth_hash_algorithm": "sha512",
        "modrinth_file_hash": "a" * 128,
        "modrinth_provenance_authoritative": True,
    }

    match = ModIdentityService.match_project(
        [fuzzy, owned],
        {
            "project_id": "sodium-project",
            "slug": "sodium",
            "title": "Sodium",
        },
    )

    assert match.kind is ModMatchKind.OWNED
    assert match.item is owned
    assert match.can_replace is True


def test_unverified_modrinth_provenance_is_only_a_non_destructive_hint():
    item = {
        "filename": "renamed-by-user.jar",
        "modrinth_project_id": "sodium-project",
    }

    match = ModIdentityService.match_project(
        [item],
        {
            "project_id": "sodium-project",
            "slug": "sodium",
            "title": "Sodium",
        },
    )

    assert match.kind is ModMatchKind.HINT
    assert match.item is item
    assert match.reason == "unverified_modrinth_provenance"
    assert match.can_replace is False


def test_unique_fuzzy_match_is_non_destructive_hint():
    item = {"filename": "sodium-extra-fabric.jar", "name": "Sodium Extra"}

    match = ModIdentityService.match_project(
        [item],
        {
            "project_id": "sodium-project",
            "slug": "sodium",
            "title": "Sodium",
        },
    )

    assert match.kind is ModMatchKind.HINT
    assert match.item is item
    assert match.can_replace is False


def test_multiple_fuzzy_matches_are_ambiguous():
    match = ModIdentityService.match_project(
        [
            {"filename": "sodium-fabric.jar"},
            {"filename": "sodium-neoforge.jar"},
        ],
        {
            "project_id": "sodium-project",
            "slug": "sodium",
            "title": "Sodium",
        },
    )

    assert match.kind is ModMatchKind.AMBIGUOUS
    assert match.item is None
    assert match.can_replace is False


def test_declared_mod_id_is_only_a_non_destructive_hint():
    item = {"id": "P7dR8mSH", "filename": "fabric-api.jar"}

    match = ModIdentityService.match_project(
        [item],
        {
            "project_id": "P7dR8mSH",
            "slug": "fabric-api",
            "title": "Fabric API",
        },
    )

    assert match.kind is ModMatchKind.HINT
    assert match.item is item
    assert match.owned is False


def test_inspect_mod_jar_prefers_metadata_for_requested_loader(tmp_path: Path):
    jar = _write_json_jar(
        tmp_path / "hybrid.jar",
        {
            "fabric.mod.json": {
                "id": "fabric_entry",
                "name": "Fabric Entry",
                "version": "1",
            },
            "quilt.mod.json": {
                "quilt_loader": {
                    "id": "quilt_entry",
                    "version": "2",
                    "metadata": {
                        "name": "Quilt Entry",
                        "description": "Preferred Quilt descriptor",
                    },
                }
            },
        },
    )

    inspection = inspect_mod_jar(jar, loader="Quilt Loader")

    assert inspection.error_kind is None
    assert inspection.metadata_file == "quilt.mod.json"
    assert inspection.primary_descriptor is not None
    assert inspection.primary_descriptor.mod_id == "quilt_entry"
    assert inspection.primary_descriptor.description == "Preferred Quilt descriptor"
