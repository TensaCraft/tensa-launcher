from __future__ import annotations

from launcher.application.version_creation import VersionCreationCatalogService, unique_version_name


def test_version_creation_catalog_filters_minecraft_snapshots(monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "1.21.1", "type": "release"},
            {"id": "25w20a", "type": "snapshot"},
            {"id": "b1.7.3", "type": "old_beta"},
        ],
    )
    service = VersionCreationCatalogService()

    stable = service.minecraft_versions(include_snapshots=False)
    with_snapshots = service.minecraft_versions(include_snapshots=True)

    assert [option.minecraft_version for option in stable] == ["1.21.1"]
    assert [option.minecraft_version for option in with_snapshots] == ["1.21.1", "25w20a"]
    assert with_snapshots[1].snapshot is True


def test_version_creation_catalog_preserves_minecraft_manifest_order_with_snapshots(monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "1.21.1", "type": "release"},
            {"id": "1.20.6", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.minecraft_versions(include_snapshots=True)

    assert [option.minecraft_version for option in options] == ["25w20a", "1.21.1", "1.20.6"]


def test_version_creation_catalog_preserves_mixed_snapshot_release_order(monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "1.21.1", "type": "release"},
            {"id": "25w19a", "type": "snapshot"},
            {"id": "1.20.6", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.minecraft_versions(include_snapshots=True)

    assert [option.minecraft_version for option in options] == ["25w20a", "1.21.1", "25w19a", "1.20.6"]


def test_version_creation_catalog_places_snapshots_before_older_releases(monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "25w19a", "type": "snapshot"},
            {"id": "1.21.3", "type": "release"},
            {"id": "1.21.2", "type": "release"},
            {"id": "1.21.1", "type": "release"},
            {"id": "1.20.6", "type": "release"},
            {"id": "1.20.5", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.minecraft_versions(include_snapshots=True)

    assert [option.minecraft_version for option in options] == [
        "25w20a",
        "25w19a",
        "1.21.3",
        "1.21.2",
        "1.21.1",
        "1.20.6",
        "1.20.5",
    ]


def test_version_creation_catalog_places_loader_snapshots_before_older_releases(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Fabric"

        def get_minecraft_versions(self, stable_only):
            if stable_only:
                return ["1.21.3", "1.21.2", "1.21.1", "1.20.6", "1.20.5"]
            return ["25w20a", "25w19a", "1.21.3", "1.21.2", "1.21.1", "1.20.6", "1.20.5"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return ["0.19.3"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "25w19a", "type": "snapshot"},
            {"id": "1.21.3", "type": "release"},
            {"id": "1.21.2", "type": "release"},
            {"id": "1.21.1", "type": "release"},
            {"id": "1.20.6", "type": "release"},
            {"id": "1.20.5", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    fabric = service.loader_versions("fabric", include_snapshots=True)
    quilt = service.loader_versions("quilt", include_snapshots=True)

    expected = ["25w20a", "25w19a", "1.21.3", "1.21.2", "1.21.1", "1.20.6", "1.20.5"]
    assert [option.minecraft_version for option in fabric] == expected
    assert [option.minecraft_version for option in quilt] == expected


def test_version_creation_catalog_uses_unstable_loader_builds_when_enabled(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Quilt"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.1"] if stable_only else ["1.21.1", "25w20a"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return ["0.16.0"]
            return ["0.17.0-beta", "0.16.0"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "1.21.1", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    stable = service.loader_versions("quilt", include_unstable_loaders=False)
    unstable = service.loader_versions("quilt", include_unstable_loaders=True)

    assert stable[0].loader_version == "0.16.0"
    assert stable[0].unstable_loader is False
    assert unstable[0].loader_version == "0.17.0-beta"
    assert unstable[0].unstable_loader is True


def test_version_creation_catalog_adds_unstable_loader_versions_to_stable_versions(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Quilt"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.1"] if stable_only else ["25w20a"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return ["0.16.0"]
            return ["0.17.0-beta"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "1.21.1", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("quilt", include_snapshots=True, include_unstable_loaders=True)

    assert [option.minecraft_version for option in options] == ["25w20a", "1.21.1"]
    stable_option = next(option for option in options if option.minecraft_version == "1.21.1")
    assert stable_option.loader_versions == ("0.17.0-beta", "0.16.0")
    assert stable_option.unstable_loader is True


def test_version_creation_catalog_preserves_loader_order_when_snapshots_are_enabled(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Quilt"

        def get_minecraft_versions(self, stable_only):
            if stable_only:
                return ["1.21.1", "1.20.6"]
            return ["25w20a", "1.21.1", "1.20.6"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return ["0.29.2"]
            return ["0.30.0-beta.7", "0.29.2"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "25w20a", "type": "snapshot"},
            {"id": "1.21.1", "type": "release"},
            {"id": "1.20.6", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("quilt", include_snapshots=True, include_unstable_loaders=True)

    assert [option.minecraft_version for option in options] == ["25w20a", "1.21.1", "1.20.6"]
    assert options[0].snapshot is True
    assert options[-1].snapshot is False


def test_version_creation_catalog_hides_unstable_only_builds_when_unstable_disabled(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "NeoForge"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.9"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return []
            return ["21.9.16-beta"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "1.21.9", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    stable = service.loader_versions("neoforge", include_unstable_loaders=False)
    unstable = service.loader_versions("neoforge", include_unstable_loaders=True)

    assert stable == []
    assert [option.loader_version for option in unstable] == ["21.9.16-beta"]


def test_version_creation_catalog_sorts_loader_minecraft_versions_by_release_order(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "NeoForge"

        def get_minecraft_versions(self, stable_only):
            return ["1.20.2", "1.21.1", "1.21.6"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return [f"{minecraft_version}-loader"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "1.21.6", "type": "release"},
            {"id": "1.21.1", "type": "release"},
            {"id": "1.20.2", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("neoforge")

    assert [option.minecraft_version for option in options] == ["1.21.6", "1.21.1", "1.20.2"]


def test_version_creation_catalog_filters_loader_versions_missing_from_minecraft_manifest(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "NeoForge"

        def get_minecraft_versions(self, stable_only):
            return ["1.21", "1.26.1", "1.21.1"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return [f"{minecraft_version}-loader"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [
            {"id": "1.21.1", "type": "release"},
            {"id": "1.21", "type": "release"},
        ],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("neoforge")

    assert [option.minecraft_version for option in options] == ["1.21.1", "1.21"]
    assert all(option.minecraft_version != "1.26.1" for option in options)


def test_version_creation_catalog_reuses_minecraft_manifest_for_one_service(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "NeoForge"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.1"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return ["21.1.230"]

    calls = []

    def get_version_list():
        calls.append(True)
        return [{"id": "1.21.1", "type": "release"}]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr("minecraft_launcher_lib.utils.get_version_list", get_version_list)
    service = VersionCreationCatalogService()

    assert service.minecraft_versions()[0].minecraft_version == "1.21.1"
    assert service.loader_versions("neoforge")[0].loader_version == "21.1.230"

    assert len(calls) == 1


def test_version_creation_catalog_reuses_loader_results_for_same_filters(monkeypatch):
    calls = {"minecraft_versions": 0, "loader_versions": 0}

    class FakeLoader:
        def get_name(self):
            return "NeoForge"

        def get_minecraft_versions(self, stable_only):
            calls["minecraft_versions"] += 1
            return ["1.21.1"]

        def get_loader_versions(self, minecraft_version, stable_only):
            calls["loader_versions"] += 1
            return ["21.1.230"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "1.21.1", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    first = service.loader_versions("neoforge")
    second = service.loader_versions("neoforge")

    assert [option.loader_version for option in first] == ["21.1.230"]
    assert [option.loader_version for option in second] == ["21.1.230"]
    assert calls == {"minecraft_versions": 1, "loader_versions": 1}


def test_version_creation_catalog_sorts_loader_builds_before_selecting_latest(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Forge"

        def get_minecraft_versions(self, stable_only):
            return ["26.1.2"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return ["64.0.0", "64.0.1", "64.0.4"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "26.1.2", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("forge")

    assert options[0].loader_version == "64.0.4"


def test_version_creation_catalog_exposes_loader_build_choices(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Fabric"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.1"]

        def get_loader_versions(self, minecraft_version, stable_only):
            return ["0.16.14", "0.17.3", "0.17.0"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "1.21.1", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("fabric")

    assert options[0].loader_version == "0.17.3"
    assert options[0].loader_versions == ("0.17.3", "0.17.0")


def test_version_creation_catalog_limits_fabric_loader_build_choices_to_current_line(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Fabric"

        def get_minecraft_versions(self, stable_only):
            return ["26.1.2"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return ["0.19.3"]
            return ["0.19.3", "0.19.2", "0.19.1", "0.19.0", "0.18.6"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "26.1.2", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    options = service.loader_versions("fabric", include_unstable_loaders=False)

    assert options[0].loader_version == "0.19.3"
    assert options[0].loader_versions == ("0.19.3", "0.19.2", "0.19.1", "0.19.0")


def test_version_creation_catalog_limits_quilt_loader_build_choices_to_current_line(monkeypatch):
    class FakeLoader:
        def get_name(self):
            return "Quilt"

        def get_minecraft_versions(self, stable_only):
            return ["1.21.1"]

        def get_loader_versions(self, minecraft_version, stable_only):
            if stable_only:
                return ["0.29.2", "0.29.1", "0.29.0", "0.28.1"]
            return ["0.30.0-beta.7", "0.30.0-beta.6", "0.29.3-beta.1", "0.29.2", "0.28.1"]

    monkeypatch.setattr("minecraft_launcher_lib.mod_loader.get_mod_loader", lambda _loader: FakeLoader())
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_version_list",
        lambda: [{"id": "1.21.1", "type": "release"}],
    )
    service = VersionCreationCatalogService()

    stable = service.loader_versions("quilt", include_unstable_loaders=False)
    unstable = service.loader_versions("quilt", include_unstable_loaders=True)

    assert stable[0].loader_version == "0.29.2"
    assert stable[0].loader_versions == ("0.29.2", "0.29.1", "0.29.0")
    assert unstable[0].loader_version == "0.30.0-beta.7"
    assert unstable[0].loader_versions == (
        "0.30.0-beta.7",
        "0.30.0-beta.6",
        "0.29.2",
        "0.29.1",
        "0.29.0",
    )


def test_version_creation_catalog_exposes_loader_specific_filter_capabilities():
    service = VersionCreationCatalogService()

    assert service.supports_snapshots("minecraft") is True
    assert service.supports_snapshots("fabric") is True
    assert service.supports_snapshots("quilt") is True
    assert service.supports_snapshots("forge") is False
    assert service.supports_snapshots("neoforge") is False

    assert service.supports_unstable_loaders("quilt") is True
    assert service.supports_unstable_loaders("neoforge") is True
    assert service.supports_unstable_loaders("fabric") is False
    assert service.supports_unstable_loaders("forge") is False


def test_unique_version_name_appends_suffix_case_insensitively():
    assert unique_version_name(["Minecraft 1.21.1", "minecraft 1.21.1 (2)"], "Minecraft 1.21.1") == "Minecraft 1.21.1 (3)"
