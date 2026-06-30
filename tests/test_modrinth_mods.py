from __future__ import annotations

from types import SimpleNamespace

from launcher.application.modrinth_mods import ModInstallFile, ModrinthModsService


def test_modrinth_mods_builds_facets_from_version():
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")

    facets = service.build_search_facets(version)

    assert '"project_type:mod"' in facets
    assert '"categories:fabric"' in facets
    assert '"versions:1.20.1"' in facets


def test_modrinth_mods_builds_facets_for_resourcepacks_and_shaders_without_loader():
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")

    resourcepack_facets = service.build_search_facets(
        version,
        project_type="resourcepack",
        game_version="1.21.1",
    )
    shader_facets = service.build_search_facets(
        version,
        project_type="shader",
        game_version="1.21.1",
    )

    assert '"project_type:resourcepack"' in resourcepack_facets
    assert '"project_type:shader"' in shader_facets
    assert '"versions:1.21.1"' in resourcepack_facets
    assert '"versions:1.21.1"' in shader_facets
    assert "categories:fabric" not in resourcepack_facets
    assert "categories:fabric" not in shader_facets


def test_modrinth_mods_filters_compatible_versions():
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    versions = [
        {"version_number": "2.0.0", "game_versions": ["1.20.1"], "loaders": ["fabric"]},
        {"version_number": "1.0.0", "game_versions": ["1.19.4"], "loaders": ["fabric"]},
        {"version_number": "3.0.0", "game_versions": ["1.20.1"], "loaders": ["forge"]},
    ]

    compatible = service.filter_compatible_versions(versions, version)

    assert compatible == [{"version_number": "2.0.0", "game_versions": ["1.20.1"], "loaders": ["fabric"]}]


def test_modrinth_mods_detects_available_update(monkeypatch):
    monkeypatch.setattr(
        "launcher.core.api.modrinth.ModrinthAPI.get_mod_versions",
        lambda *_args, **_kwargs: [
            {"version_number": "2.0.0", "game_versions": ["1.20.1"], "loaders": ["fabric"]},
        ],
    )

    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    installed_mod = {"id": "sodium", "enabled": True, "version": "1.0.0"}

    latest = service.find_update(installed_mod, version)

    assert latest is not None
    assert latest["version_number"] == "2.0.0"


def test_modrinth_mods_selects_primary_file():
    version_data = {
        "version_number": "1.2.3",
        "files": [
            {"filename": "secondary.jar", "url": "https://example.com/secondary.jar", "primary": False},
            {"filename": "primary.jar", "url": "https://example.com/primary.jar", "primary": True},
        ],
    }

    selected = ModrinthModsService.select_primary_file(version_data)

    assert selected == ModInstallFile(
        url="https://example.com/primary.jar",
        filename="primary.jar",
        version_number="1.2.3",
    )


def test_modrinth_mods_detects_installed_file_by_project_title_or_slug():
    installed = [
        {"filename": "Faithful-64x.zip"},
        {"filename": "complementary-reimagined.zip"},
    ]

    assert ModrinthModsService.is_installed(
        installed,
        {"project_id": "faithful-project", "title": "Faithful", "slug": "faithful"},
    )
    assert ModrinthModsService.is_installed(
        installed,
        {
            "project_id": "complementary-project",
            "title": "Complementary Reimagined",
            "slug": "complementary-reimagined",
        },
    )


def test_modrinth_mods_returns_installed_metadata_match():
    installed = [{"modrinth_project_id": "abc", "modrinth_version_id": "old-version"}]
    project = {"project_id": "abc", "latest_version": "new-version"}

    match = ModrinthModsService.find_installed(installed, project)

    assert match == installed[0]


def _modrinth_version(
    project_id: str,
    version_id: str,
    filename: str,
    *,
    number: str = "1.0.0",
    date: str = "2026-01-01T00:00:00Z",
    loaders: list[str] | None = None,
    dependencies: list[dict] | None = None,
) -> dict:
    return {
        "id": version_id,
        "project_id": project_id,
        "version_number": number,
        "game_versions": ["1.20.1"],
        "loaders": loaders or ["fabric"],
        "date_published": date,
        "files": [{"filename": filename, "url": f"https://example.com/{filename}", "primary": True}],
        "dependencies": dependencies or [],
    }


def _project(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "slug": project_id,
        "project_type": "mod",
        "title": project_id.replace("-", " ").title(),
    }


def test_dependency_plan_resolves_required_exact_version(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[
            {
                "version_id": "dep-version",
                "project_id": "dep-project",
                "dependency_type": "required",
            }
        ],
    )
    dep_version = _modrinth_version(
        "dep-project",
        "dep-version",
        "dep.jar",
        number="2.0.0",
        date="2026-01-02T00:00:00Z",
    )

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", lambda *_args, **_kwargs: [main_version])
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_version_by_id", lambda _version_id: dep_version)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert plan.main is not None
    assert plan.main.install_file.filename == "main.jar"
    assert [candidate.project_id for candidate in plan.dependencies_to_install] == ["dep-project"]
    assert plan.dependencies_to_install[0].install_file.filename == "dep.jar"
    assert not plan.blocking_issues


def test_dependency_plan_falls_back_to_project_when_dependency_version_is_incompatible(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[
            {
                "version_id": "dep-forge",
                "project_id": "dep-project",
                "dependency_type": "required",
            }
        ],
    )
    incompatible_exact = _modrinth_version(
        "dep-project",
        "dep-forge",
        "dep-forge.jar",
        loaders=["forge"],
        number="1.0.0",
        date="2026-01-02T00:00:00Z",
    )
    compatible_latest = _modrinth_version(
        "dep-project",
        "dep-fabric",
        "dep-fabric.jar",
        loaders=["fabric"],
        number="2.0.0",
        date="2026-02-02T00:00:00Z",
    )

    def fake_versions(project_id, *_args, **_kwargs):
        if project_id == "main-project":
            return [main_version]
        return [compatible_latest]

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_version_by_id", lambda _version_id: incompatible_exact)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert [candidate.version_id for candidate in plan.dependencies_to_install] == ["dep-fabric"]
    assert plan.dependencies_to_install[0].install_file.filename == "dep-fabric.jar"
    assert not plan.blocking_issues


def test_dependency_plan_adds_fabric_api_for_fabric_mod_without_declared_dependency(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version("main-project", "main-version", "main.jar")
    fabric_api_version = _modrinth_version(
        "P7dR8mSH",
        "fabric-api-version",
        "fabric-api.jar",
        number="0.99.0",
        date="2026-03-02T00:00:00Z",
    )

    def fake_versions(project_id, *_args, **_kwargs):
        return {
            "main-project": [main_version],
            "P7dR8mSH": [fabric_api_version],
        }[project_id]

    def fake_project(project_id):
        if project_id == "P7dR8mSH":
            return {
                "project_id": "P7dR8mSH",
                "slug": "fabric-api",
                "project_type": "mod",
                "title": "Fabric API",
            }
        return _project(project_id)

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", fake_project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
    )

    assert [(candidate.project_id, candidate.title) for candidate in plan.dependencies_to_install] == [
        ("P7dR8mSH", "Fabric API")
    ]
    assert plan.dependencies_to_install[0].install_file.filename == "fabric-api.jar"


def test_dependency_plan_uses_newest_project_dependency_version(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"project_id": "dep-project", "dependency_type": "required"}],
    )
    older_dep = _modrinth_version(
        "dep-project",
        "dep-old",
        "dep-old.jar",
        number="1.0.0",
        date="2026-01-02T00:00:00Z",
    )
    newer_dep = _modrinth_version(
        "dep-project",
        "dep-new",
        "dep-new.jar",
        number="2.0.0",
        date="2026-02-02T00:00:00Z",
    )

    def fake_versions(project_id, *_args, **_kwargs):
        if project_id == "main-project":
            return [main_version]
        return [older_dep, newer_dep]

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert [candidate.version_id for candidate in plan.dependencies_to_install] == ["dep-new"]


def test_dependency_plan_deduplicates_transitive_dependency_cycles(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[
            {"project_id": "dep-a", "dependency_type": "required"},
            {"project_id": "dep-a", "dependency_type": "required"},
        ],
    )
    dep_a = _modrinth_version(
        "dep-a",
        "dep-a-version",
        "dep-a.jar",
        dependencies=[{"project_id": "dep-b", "dependency_type": "required"}],
    )
    dep_b = _modrinth_version(
        "dep-b",
        "dep-b-version",
        "dep-b.jar",
        dependencies=[{"project_id": "dep-a", "dependency_type": "required"}],
    )

    def fake_versions(project_id, *_args, **_kwargs):
        return {
            "main-project": [main_version],
            "dep-a": [dep_a],
            "dep-b": [dep_b],
        }[project_id]

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert sorted(candidate.project_id for candidate in plan.dependencies_to_install) == ["dep-a", "dep-b"]
    assert not plan.blocking_issues


def test_dependency_plan_treats_same_installed_dependency_version_as_satisfied(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"project_id": "dep-project", "dependency_type": "required"}],
    )
    dep_version = _modrinth_version("dep-project", "dep-new", "dep-new.jar", number="2.0.0")
    installed = {
        "filename": "dep-new.jar",
        "path": "/tmp/dep-new.jar",
        "modrinth_project_id": "dep-project",
        "modrinth_version_id": "dep-new",
    }

    monkeypatch.setattr(
        "launcher.core.api.modrinth.ModrinthAPI.get_mod_versions",
        lambda project_id, *_args, **_kwargs: [main_version] if project_id == "main-project" else [dep_version],
    )
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_version_by_id", lambda _version_id: dep_version)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[installed],
        include_implicit_dependencies=False,
    )

    assert plan.dependencies_to_install == []
    assert plan.dependencies_to_replace == []
    assert [candidate.project_id for candidate in plan.already_satisfied] == ["dep-project"]


def test_dependency_plan_replaces_older_installed_dependency(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"project_id": "dep-project", "dependency_type": "required"}],
    )
    dep_version = _modrinth_version(
        "dep-project",
        "dep-new",
        "dep-new.jar",
        number="2.0.0",
        date="2026-02-02T00:00:00Z",
    )
    old_version = _modrinth_version(
        "dep-project",
        "dep-old",
        "dep-old.jar",
        number="1.0.0",
        date="2026-01-02T00:00:00Z",
    )
    installed_old = {
        "filename": "dep-old.jar",
        "path": "/tmp/dep-old.jar",
        "modrinth_project_id": "dep-project",
        "modrinth_version_id": "dep-old",
    }

    monkeypatch.setattr(
        "launcher.core.api.modrinth.ModrinthAPI.get_mod_versions",
        lambda project_id, *_args, **_kwargs: [main_version] if project_id == "main-project" else [dep_version],
    )
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_version_by_id", lambda _version_id: old_version)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[installed_old],
        include_implicit_dependencies=False,
    )

    assert plan.dependencies_to_install == []
    assert [candidate.project_id for candidate in plan.dependencies_to_replace] == ["dep-project"]
    assert plan.dependencies_to_replace[0].installed_item == installed_old


def test_dependency_plan_blocks_file_name_only_required_dependency(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"file_name": "external.jar", "dependency_type": "required"}],
    )

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", lambda *_args, **_kwargs: [main_version])

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert [issue.code for issue in plan.blocking_issues] == ["required_file_only"]
    assert plan.blocking_issues[0].file_name == "external.jar"


def test_dependency_plan_blocks_incompatible_installed_dependency(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"project_id": "bad-project", "dependency_type": "incompatible"}],
    )
    installed_bad = {
        "filename": "bad.jar",
        "path": "/tmp/bad.jar",
        "modrinth_project_id": "bad-project",
    }

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", lambda *_args, **_kwargs: [main_version])
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[installed_bad],
        include_implicit_dependencies=False,
    )

    assert [issue.code for issue in plan.blocking_issues] == ["incompatible_installed"]
    assert plan.blocking_issues[0].project_id == "bad-project"


def test_dependency_plan_tracks_optional_and_skips_embedded_dependencies(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[
            {"project_id": "optional-project", "dependency_type": "optional"},
            {"project_id": "embedded-project", "dependency_type": "embedded"},
        ],
    )
    optional_version = _modrinth_version(
        "optional-project",
        "optional-version",
        "optional.jar",
        number="1.1.0",
        date="2026-01-02T00:00:00Z",
    )

    def fake_versions(project_id, *_args, **_kwargs):
        if project_id == "main-project":
            return [main_version]
        if project_id == "optional-project":
            return [optional_version]
        return []

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert plan.dependencies_to_install == []
    assert [candidate.project_id for candidate in plan.optional_dependencies] == ["optional-project"]
    assert plan.optional_dependencies[0].title == "Optional Project"
    assert plan.optional_dependencies[0].install_file.filename == "optional.jar"
    assert plan.optional_dependency_issues == []
    assert plan.requires_confirmation
    assert [issue.project_id for issue in plan.skipped_embedded] == ["embedded-project"]


def test_dependency_plan_keeps_unresolved_optional_dependencies_non_blocking(monkeypatch):
    service = ModrinthModsService()
    version = SimpleNamespace(loader="fabric", client="fabric", version="1.20.1")
    main_version = _modrinth_version(
        "main-project",
        "main-version",
        "main.jar",
        dependencies=[{"project_id": "optional-project", "dependency_type": "optional"}],
    )

    def fake_versions(project_id, *_args, **_kwargs):
        if project_id == "main-project":
            return [main_version]
        return []

    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod_versions", fake_versions)
    monkeypatch.setattr("launcher.core.api.modrinth.ModrinthAPI.get_mod", _project)

    plan = service.build_dependency_plan(
        {"project_id": "main-project", "slug": "main", "title": "Main"},
        version,
        project_type="mod",
        game_version="1.20.1",
        installed_items=[],
        include_implicit_dependencies=False,
    )

    assert plan.optional_dependencies == []
    assert [issue.project_id for issue in plan.optional_dependency_issues] == ["optional-project"]
    assert plan.optional_dependency_issues[0].project_title == "Optional Project"
    assert not plan.optional_dependency_issues[0].blocking
    assert not plan.blocking_issues
