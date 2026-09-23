from copy import deepcopy
from hashlib import sha512
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from launcher.application.modrinth_mods import ModrinthModsService
from launcher.core.api.modrinth import ModrinthAPI


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    path = tmp_path / "renamed.jar"
    path.write_bytes(b"actual jar bytes")
    digest = sha512(path.read_bytes()).hexdigest()
    current = {
        "id": "old", "project_id": "real-project", "version_number": "1.0",
        "game_versions": ["1.21.1"], "loaders": ["neoforge"],
        "date_published": "2026-01-01T00:00:00Z",
        "files": [{"hashes": {"sha512": digest}, "size": path.stat().st_size,
                   "filename": "original.jar", "url": "https://cdn.modrinth.com/original.jar"}],
    }
    item = {"path": str(path), "filename": path.name, "name": "Manual mod", "enabled": True}
    monkeypatch.setattr(ModrinthAPI, "get_versions_by_hashes", lambda hashes: {digest: current})
    return item, current, SimpleNamespace(loader="neoforge", version="1.21.1")


def test_identification_uses_actual_bytes_not_filename(inventory):
    item, current, _ = inventory
    result = ModrinthModsService().identify_installed_items([item])[0]
    assert result["modrinth_project_id"] == current["project_id"]
    assert result["modrinth_provenance_authoritative"] is True
    assert "modrinth_project_id" not in item


def test_identification_rejects_mismatched_api_payload(inventory):
    item, current, _ = inventory
    current["files"][0]["size"] += 1
    with pytest.raises(ValueError, match="mismatched"):
        ModrinthModsService().identify_installed_items([item])


@pytest.mark.parametrize("unavailable", ["missing", "symlink"])
def test_unverifiable_file_loses_authority(inventory, monkeypatch, unavailable):
    item, _, version = inventory
    service = ModrinthModsService()
    verified = service.identify_installed_items([item])[0]
    if unavailable == "missing":
        Path(item["path"]).unlink()
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda _self: True)
    monkeypatch.setattr(ModrinthAPI, "get_versions_by_hashes", lambda *a: pytest.fail("unverifiable file lookup"))
    monkeypatch.setattr(ModrinthAPI, "get_updates_by_hashes", lambda *a, **kw: pytest.fail("unverifiable update lookup"))

    result = service.check_installed_updates([verified], version)[0]

    assert result["modrinth_provenance_authoritative"] is False
    assert "modrinth_version_data" not in result
    assert not service.owns_installed_item(result, "real-project")
    assert not result["update_checked"]


def test_identification_rejects_files_changed_during_lookup(inventory, monkeypatch):
    item, current, _ = inventory

    def lookup(hashes):
        from pathlib import Path
        Path(item["path"]).write_bytes(b"modified")
        return {hashes[0]: current}

    monkeypatch.setattr(ModrinthAPI, "get_versions_by_hashes", lookup)
    with pytest.raises(OSError, match="changed"):
        ModrinthModsService().identify_installed_items([item])


@pytest.mark.parametrize("case,expected", [("newer", True), ("same", False), ("older", False), ("wrong_loader", False)])
def test_bulk_updates_are_compatible_and_never_downgrade(inventory, monkeypatch, case, expected):
    item, current, version = inventory
    candidate = deepcopy(current)
    if case != "same":
        candidate.update(id="new", date_published="2026-02-01T00:00:00Z")
    if case == "older":
        candidate["date_published"] = "2025-01-01T00:00:00Z"
    if case == "wrong_loader":
        candidate["loaders"] = ["forge"]
    calls = []

    def updates(hashes, **kwargs):
        calls.append(kwargs)
        return {hashes[0]: candidate}

    monkeypatch.setattr(ModrinthAPI, "get_updates_by_hashes", updates)
    result = ModrinthModsService().check_installed_updates([item], version)[0]
    assert result["update_available"] is expected
    assert result["update_checked"] is True
    assert calls == [{"game_versions": ["1.21.1"], "loaders": ["neoforge"]}]


def test_update_failure_does_not_claim_everything_is_current(inventory, monkeypatch):
    item, _, version = inventory

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(ModrinthAPI, "get_updates_by_hashes", offline)
    with pytest.raises(requests.ConnectionError):
        ModrinthModsService().check_installed_updates([item], version)


def test_disabled_mod_is_not_offered_an_update(inventory, monkeypatch):
    item, _, version = inventory
    item["enabled"] = False
    monkeypatch.setattr(ModrinthAPI, "get_updates_by_hashes", lambda *a, **kw: pytest.fail("disabled update request"))
    result = ModrinthModsService().check_installed_updates([item], version)[0]
    assert not result["update_available"]
    assert not result["update_checked"]


def test_dependency_installed_manually_is_satisfied(inventory, monkeypatch):
    item, current, version = inventory
    main = deepcopy(current)
    main.update(id="main-version", project_id="main", dependencies=[
        {"project_id": "real-project", "dependency_type": "required"},
    ])
    monkeypatch.setattr(ModrinthAPI, "get_mod_versions", lambda *a, **kw: [main])
    monkeypatch.setattr(ModrinthAPI, "get_mod", lambda project_id: {"id": project_id, "title": project_id})
    plan = ModrinthModsService().build_dependency_plan({"project_id": "main"}, version, installed_items=[item])
    assert not plan.dependencies_to_install
    assert not plan.dependencies_to_replace
    assert [candidate.project_id for candidate in plan.already_satisfied] == ["real-project"]


@pytest.mark.parametrize("disabled_flag", [False, True])
def test_identified_disabled_copy_never_duplicates_enabled_dependency(inventory, monkeypatch, disabled_flag):
    item, current, version = inventory
    active_path = Path(item["path"])
    disabled_path = active_path.with_name("backup.jar.disabled")
    original_bytes = active_path.read_bytes()
    disabled_path.write_bytes(original_bytes)
    duplicate = {"path": str(disabled_path), "filename": "backup.jar", "enabled": disabled_flag}
    main = deepcopy(current)
    main.update(id="main-version", project_id="main", dependencies=[
        {"project_id": "real-project", "dependency_type": "required"},
    ])
    monkeypatch.setattr(ModrinthAPI, "get_mod_versions", lambda project, *a: [main] if project == "main" else [current])
    monkeypatch.setattr(ModrinthAPI, "get_mod", lambda project: {"id": project, "title": project})

    plan = ModrinthModsService().build_dependency_plan(
        {"project_id": "main"}, version, installed_items=[duplicate, item],
    )

    assert plan.can_install
    assert plan.dependencies_to_install == plan.dependencies_to_replace == []
    assert len(plan.already_satisfied) == 1
    assert plan.already_satisfied[0].installed_item["path"] == str(active_path)
    assert active_path.read_bytes() == disabled_path.read_bytes() == original_bytes


def test_hash_api_batches_and_deduplicates_requests(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {value: {"id": value} for value in kwargs["json"]["hashes"]})

    monkeypatch.setattr("launcher.core.api.modrinth.requests.post", post)
    hashes = [str(number) for number in range(215)]
    assert len(ModrinthAPI.get_versions_by_hashes(hashes + hashes)) == 215
    assert [len(kwargs["json"]["hashes"]) for _, kwargs in calls] == [100, 100, 15]
    assert ModrinthAPI.get_versions_by_hashes([]) == {}
    assert len(calls) == 3
