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
