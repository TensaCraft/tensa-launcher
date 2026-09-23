from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from launcher.application.modrinth_mods import ModrinthModsService
from launcher.core.api.modrinth import ModrinthAPI


def _dependency(project, version=None, kind="required"):
    return {"project_id": project, "version_id": version, "dependency_type": kind}


def _version(project, version=None, dependencies=(), *, date="2026-01-01T00:00:00Z", loader="forge"):
    version = version or f"{project}-v1"
    return {
        "project_id": project,
        "id": version,
        "version_number": version,
        "game_versions": ["1.20.1"],
        "loaders": [loader],
        "date_published": date,
        "dependencies": list(dependencies),
        "files": [{
            "filename": f"{version}.jar", "url": f"https://example.com/{version}.jar",
            "size": 3, "hashes": {"sha512": "a" * 128},
        }],
    }


def _installed(data):
    return {
        "filename": data["files"][0]["filename"],
        "enabled": True,
        "modrinth_project_id": data["project_id"],
        "modrinth_version_id": data["id"],
        "modrinth_version_data": data,
        "modrinth_provenance_authoritative": True,
        "modrinth_hash_algorithm": "sha512",
        "modrinth_file_hash": "a" * 128,
    }


def _catalog(monkeypatch, *versions):
    service = ModrinthModsService()
    by_id = {data["id"]: data for data in versions}
    identified = []
    looked_up = []

    def identify(items):
        identified.append(items)
        return deepcopy(items)

    def lookup(project, *_args, **_kwargs):
        looked_up.append(project)
        return sorted(
            [data for data in by_id.values() if data["project_id"] == project],
            key=lambda data: data["date_published"], reverse=True,
        )

    monkeypatch.setattr(service, "identify_installed_items", identify)
    monkeypatch.setattr(ModrinthAPI, "get_mod_versions", lookup)
    monkeypatch.setattr(ModrinthAPI, "get_version_by_id", lambda version: by_id[version])
    monkeypatch.setattr(ModrinthAPI, "get_mod", lambda project: {"id": project, "title": project})
    return SimpleNamespace(
        service=service, by_id=by_id, identified=identified, looked_up=looked_up,
        version=SimpleNamespace(loader="forge", version="1.20.1"),
    )


def _build(catalog, installed=()):
    return catalog.service.build_dependency_plan(
        {"project_id": "main"}, catalog.version, installed_items=list(installed),
        include_implicit_dependencies=False,
    )


def _select(catalog, plan, selected=None, installed=()):
    return catalog.service.resolve_optional_dependencies(
        plan, plan.optional_dependencies if selected is None else selected, catalog.version,
        installed_items=list(installed), project_type="mod", game_version="1.20.1",
    )


def _versions(plan):
    return {candidate.project_id: candidate.version_id for candidate in plan.install_order}


def test_selected_optional_closure_preserves_original_main_and_required_graph(monkeypatch):
    main = _version("main", dependencies=[_dependency("base"), _dependency("optional", kind="optional")])
    catalog = _catalog(
        monkeypatch, main, _version("base"),
        _version("optional", dependencies=[_dependency("child")]),
        _version("child", dependencies=[_dependency("grandchild")]), _version("grandchild"),
    )
    plan = _build(catalog)
    original = deepcopy(plan)
    catalog.identified.clear()
    catalog.by_id["main-new"] = _version("main", "main-new", date="2026-02-01T00:00:00Z")

    resolved = _select(catalog, plan)

    assert resolved.can_install
    assert _versions(resolved) == {
        "main": "main-v1", "base": "base-v1", "optional": "optional-v1",
        "child": "child-v1", "grandchild": "grandchild-v1",
    }
    assert len(catalog.identified) == 1
    assert plan == original
    assert resolved.main.version_data == main


def test_selected_optional_version_is_pinned_even_when_latest_changes(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", "chosen", dependencies=[_dependency("child")]), _version("child"),
    )
    plan = _build(catalog)
    catalog.by_id["newer"] = _version("optional", "newer", date="2026-02-01T00:00:00Z")

    resolved = _select(catalog, plan)

    assert resolved.can_install
    assert _versions(resolved)["optional"] == "chosen"
    assert "child" in _versions(resolved)


def test_unselected_optional_does_not_add_required_closure(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", dependencies=[_dependency("missing-child")]),
    )
    plan = _build(catalog)

    unresolved = _select(catalog, plan, [])
    assert unresolved.can_install
    assert _versions(unresolved) == {"main": "main-v1"}
    assert "missing-child" not in catalog.looked_up

    resolved = _select(catalog, plan)
    assert not resolved.can_install
    assert any(issue.project_id == "missing-child" for issue in resolved.blocking_issues)


def test_selected_optional_cannot_replace_satisfied_required_exact_pin(monkeypatch):
    newer = _version("dep", "newer", date="2026-02-01T00:00:00Z")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[
            _dependency("dep", "newer"), _dependency("dep", "older", "optional"),
        ]), _version("dep", "older"), newer,
    )
    installed = [_installed(newer)]
    plan = _build(catalog, installed)

    resolved = _select(catalog, plan, installed=installed)

    assert not resolved.can_install
    assert any(issue.code == "dependency_version_conflict" for issue in resolved.blocking_issues)
    assert all(candidate.project_id != "dep" for candidate in plan.install_order_with_optional(plan.optional_dependencies))


@pytest.mark.parametrize("reverse", [False, True])
def test_later_exact_pin_recomputes_reachable_dependencies(monkeypatch, reverse):
    old = _version("b", "b-old", [_dependency("c", "c-old"), _dependency("orphan")])
    newer = _version("b", "b-new", [_dependency("c", "c-new")])
    dependencies = [_dependency("b"), _dependency("d")]
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=dependencies[::-1] if reverse else dependencies),
        old, newer, _version("d", dependencies=[_dependency("b", "b-new")]),
        _version("c", "c-old"), _version("c", "c-new"), _version("orphan"),
    )

    plan = _build(catalog, [_installed(old)])

    assert plan.can_install
    assert _versions(plan) == {"main": "main-v1", "b": "b-new", "c": "c-new", "d": "d-v1"}


def test_obsolete_resolution_errors_and_incompatibilities_are_removed(monkeypatch):
    old = _version("b", "b-old", [
        _dependency("missing"), _dependency("main", kind="incompatible"),
        _dependency("obsolete-optional", kind="optional"), _dependency("embedded", kind="embedded"),
    ])
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("b"), _dependency("d")]),
        old, _version("b", "b-new"), _version("d", dependencies=[_dependency("b", "b-new")]),
        _version("obsolete-optional"),
    )

    plan = _build(catalog, [_installed(old)])

    assert plan.can_install
    assert plan.optional_dependencies == []
    assert plan.skipped_embedded == []
    assert plan.optional_dependency_issues == []


def test_conflicting_live_required_pins_still_block(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("a"), _dependency("b")]),
        _version("a", dependencies=[_dependency("c", "c-old")]),
        _version("b", dependencies=[_dependency("c", "c-new")]),
        _version("c", "c-old"), _version("c", "c-new"),
    )

    plan = _build(catalog)

    assert not plan.can_install
    assert any(issue.code == "dependency_version_conflict" and issue.project_id == "c" for issue in plan.blocking_issues)


def test_selected_optional_cycles_terminate_and_deduplicate(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", dependencies=[_dependency("child"), _dependency("child")]),
        _version("child", dependencies=[_dependency("optional")]),
    )

    resolved = _select(catalog, _build(catalog))

    assert resolved.can_install
    assert len(resolved.install_order) == 3
    assert set(_versions(resolved)) == {"main", "optional", "child"}


def test_compatible_older_unpinned_dependency_remains_satisfied(monkeypatch):
    old = _version("dep", "old")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", dependencies=[_dependency("dep")]), old,
        _version("dep", "new", date="2026-02-01T00:00:00Z"),
    )
    installed = [_installed(old)]

    resolved = _select(catalog, _build(catalog, installed), installed=installed)

    assert resolved.can_install
    assert [(candidate.project_id, candidate.version_id) for candidate in resolved.already_satisfied] == [("dep", "old")]
    assert "dep" not in _versions(resolved)


def test_optional_closure_cannot_change_main_exact_version(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", dependencies=[_dependency("main", "other-main")]),
        _version("main", "other-main", date="2025-01-01T00:00:00Z"),
    )

    resolved = _select(catalog, _build(catalog))

    assert not resolved.can_install
    assert resolved.main.version_id == "main-v1"
    assert any(issue.code == "dependency_version_conflict" and issue.project_id == "main" for issue in resolved.blocking_issues)


def test_selected_optional_rejects_unavailable_exact_root(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", "chosen"),
    )
    plan = _build(catalog)
    del catalog.by_id["chosen"]
    catalog.by_id["other"] = _version("optional", "other")

    resolved = _select(catalog, plan)

    assert not resolved.can_install
    assert any(issue.version_id == "chosen" for issue in resolved.blocking_issues)


def test_version_selection_oscillation_fails_closed(monkeypatch):
    old = _version("dep", "old", [_dependency("dep", "new")])
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("dep")]), old, _version("dep", "new"),
    )

    plan = _build(catalog, [_installed(old)])

    assert not plan.can_install
    assert any(issue.code == "dependency_version_conflict" for issue in plan.blocking_issues)


def test_replan_does_not_inject_implicit_dependencies_disabled_in_original_plan(monkeypatch):
    catalog = _catalog(monkeypatch, _version("main", loader="fabric"))
    catalog.version.loader = "fabric"
    plan = _build(catalog)
    catalog.looked_up.clear()

    resolved = _select(catalog, plan, [])

    assert resolved.can_install
    assert _versions(resolved) == {"main": "main-v1"}
    assert not resolved.include_implicit_dependencies
    assert catalog.looked_up == []


def test_replan_reuses_previously_validated_implicit_fabric_api(monkeypatch):
    api_id = ModrinthModsService.FABRIC_API_PROJECT_ID
    catalog = _catalog(monkeypatch, _version("main", loader="fabric"), _version(api_id, loader="fabric"))
    catalog.version.loader = "fabric"
    plan = catalog.service.build_dependency_plan({"project_id": "main"}, catalog.version)

    def reject_network(*_args, **_kwargs):
        pytest.fail("The original main and previously validated implicit dependency need no new API lookup")

    monkeypatch.setattr(ModrinthAPI, "get_mod_versions", reject_network)
    monkeypatch.setattr(ModrinthAPI, "get_version_by_id", reject_network)
    monkeypatch.setattr(ModrinthAPI, "get_mod", reject_network)
    resolved = _select(catalog, plan, [])

    assert resolved.can_install
    assert resolved.include_implicit_dependencies
    assert _versions(resolved) == _versions(plan)


def test_selected_optional_required_child_incompatibility_blocks_install(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]),
        _version("optional", dependencies=[_dependency("child", "incompatible")]),
        _version("child", "incompatible", loader="fabric"),
    )

    resolved = _select(catalog, _build(catalog))

    assert not resolved.can_install
    assert any(issue.code == "dependency_incompatible" for issue in resolved.blocking_issues)


def test_live_incompatibility_in_selected_optional_closure_blocks_install(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("base"), _dependency("optional", kind="optional")]),
        _version("base"), _version("optional", dependencies=[_dependency("child")]),
        _version("child", dependencies=[_dependency("base", kind="incompatible")]),
    )

    resolved = _select(catalog, _build(catalog))

    assert not resolved.can_install
    assert any(issue.code == "incompatible_installed" and issue.project_id == "base" for issue in resolved.blocking_issues)


def test_sequential_optional_selection_preserves_prior_exact_roots_and_closure(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("base"), _dependency("a", kind="optional")]),
        _version("base"), _version("a", dependencies=[_dependency("a-child"), _dependency("b", kind="optional")]),
        _version("a-child"), _version("b", dependencies=[_dependency("b-child")]), _version("b-child"),
    )
    first = _select(catalog, _build(catalog))
    original = deepcopy(first)
    catalog.by_id["a-new"] = _version("a", "a-new", date="2026-02-01T00:00:00Z")
    assert [candidate.project_id for candidate in first.optional_dependencies] == ["b"]

    second = _select(catalog, first)

    assert second.can_install
    assert _versions(second) == {
        "main": "main-v1", "base": "base-v1", "a": "a-v1", "a-child": "a-child-v1",
        "b": "b-v1", "b-child": "b-child-v1",
    }
    assert [(candidate.project_id, candidate.version_id) for candidate in second.selected_optional_dependencies] == [
        ("a", "a-v1"), ("b", "b-v1"),
    ]
    assert first == original
    repeated = _select(catalog, second, second.selected_optional_dependencies * 2)
    assert repeated.can_install
    assert repeated.selected_optional_dependencies == second.selected_optional_dependencies
    assert _versions(_select(catalog, repeated, [])) == _versions(second)


def test_sequential_optional_selection_cannot_override_prior_exact_pin(monkeypatch):
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("a", "a-v1", "optional")]),
        _version("a", dependencies=[_dependency("a", "a-other", "optional")]), _version("a", "a-other"),
    )
    initial = _build(catalog)
    first = _select(catalog, initial)
    other = deepcopy(initial.optional_dependencies[0])
    other.version_data = catalog.by_id["a-other"]

    resolved = _select(catalog, first, [other])

    assert not resolved.can_install
    assert any(issue.code == "dependency_version_conflict" and issue.project_id == "a" for issue in resolved.blocking_issues)
    assert [(candidate.project_id, candidate.version_id) for candidate in resolved.selected_optional_dependencies] == [
        ("a", "a-v1"), ("a", "a-other"),
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_required_dependency_prefers_unique_enabled_verified_copy(monkeypatch, reverse):
    old = _version("dep", "old")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("dep")]), old,
        _version("dep", "new", date="2026-02-01T00:00:00Z"),
    )
    active = dict(_installed(old), filename="active.jar")
    disabled = dict(_installed(old), filename="backup.jar", enabled=False)
    installed = [active, disabled]

    plan = _build(catalog, installed[::-1] if reverse else installed)

    assert plan.can_install
    assert plan.dependencies_to_install == plan.dependencies_to_replace == []
    assert [(item.version_id, item.installed_item) for item in plan.already_satisfied] == [("old", active)]
    assert "dep" not in catalog.looked_up


@pytest.mark.parametrize("exact", [False, True])
def test_disabled_compatible_copy_does_not_hide_required_replacement(monkeypatch, exact):
    old = _version("dep", "old", loader="fabric" if not exact else "forge")
    new = _version("dep", "new", date="2026-02-01T00:00:00Z")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("dep", "new" if exact else None)]), old, new,
    )
    active = dict(_installed(old), filename="active.jar")
    disabled = dict(_installed(new), filename="backup.jar", enabled=False)

    plan = _build(catalog, [disabled, active])

    assert plan.can_install
    assert plan.dependencies_to_install == plan.already_satisfied == []
    assert [(item.version_id, item.installed_item) for item in plan.dependencies_to_replace] == [("new", active)]


@pytest.mark.parametrize("target", ["main", "dep", "exact-dep", "version-only-dep"])
@pytest.mark.parametrize("enabled", [True, False])
def test_unresolved_verified_duplicates_block_instead_of_adding_another_jar(monkeypatch, target, enabled):
    project = "main" if target == "main" else "dep"
    dependencies = [] if target == "main" else [_dependency(
        None if target == "version-only-dep" else "dep",
        "dep-v1" if target in {"exact-dep", "version-only-dep"} else None,
    )]
    main = _version("main", dependencies=dependencies)
    dep = _version("dep")
    catalog = _catalog(monkeypatch, main, dep)
    data = main if target == "main" else dep
    installed = [dict(_installed(data), filename=name, enabled=enabled) for name in ("first.jar", "second.jar")]

    plan = _build(catalog, installed)

    assert not plan.can_install
    assert any(issue.blocking and issue.project_id == project for issue in plan.blocking_issues)
    assert not any(item.project_id == project for item in plan.dependencies_to_install)


def test_disabled_copy_does_not_suppress_conflicting_required_exact_versions(monkeypatch):
    old, new = _version("dep", "old"), _version("dep", "new")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("dep", "old"), _dependency("dep", "new")]), old, new,
    )

    plan = _build(catalog, [_installed(old), dict(_installed(new), enabled=False)])

    assert not plan.can_install
    assert any(issue.code == "dependency_version_conflict" for issue in plan.blocking_issues)


def test_optional_selection_rechecks_newly_duplicated_installed_files(monkeypatch):
    optional = _version("optional")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]), optional,
    )
    plan = _build(catalog)
    installed = [dict(_installed(optional), filename=name) for name in ("first.jar", "second.jar")]

    resolved = _select(catalog, plan, installed=installed)

    assert not resolved.can_install
    assert any(issue.project_id == "optional" for issue in resolved.blocking_issues)


def test_unselected_optional_duplicate_does_not_block_unrelated_main(monkeypatch):
    optional = _version("optional")
    catalog = _catalog(
        monkeypatch, _version("main", dependencies=[_dependency("optional", kind="optional")]), optional,
    )
    installed = [dict(_installed(optional), filename=name) for name in ("first.jar", "second.jar")]

    plan = _build(catalog, installed)

    assert plan.can_install
    assert plan.optional_dependencies == []
    assert any(not issue.blocking and issue.project_id == "optional" for issue in plan.optional_dependency_issues)


def test_optional_replan_blocks_newly_duplicated_main(monkeypatch):
    main = _version("main", dependencies=[_dependency("optional", kind="optional")])
    catalog = _catalog(monkeypatch, main, _version("optional"))
    plan = _build(catalog)
    installed = [dict(_installed(main), filename=name) for name in ("first.jar", "second.jar")]

    resolved = _select(catalog, plan, installed=installed)

    assert not resolved.can_install
    assert any(issue.project_id == "main" for issue in resolved.blocking_issues)


def test_exact_pin_does_not_hide_another_enabled_version_of_same_project(monkeypatch):
    old, new = _version("dep", "old"), _version("dep", "new")
    catalog = _catalog(monkeypatch, _version("main", dependencies=[_dependency("dep", "old")]), old, new)

    plan = _build(catalog, [_installed(old), _installed(new)])

    assert not plan.can_install
    assert any(issue.project_id == "dep" for issue in plan.blocking_issues)
    assert plan.dependencies_to_install == plan.dependencies_to_replace == []
