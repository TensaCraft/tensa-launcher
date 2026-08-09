from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.application.mod_identity import (
    ModIdentityService,
    ModMatchKind,
    compute_file_hash,
)
from launcher.application.modrinth_mods import ModInstallFile
from launcher.application.version_content import VersionContentService


class DummyLog:
    def debug(self, *_args, **_kwargs) -> None:
        return None


def _write_mod_jar(path: Path, metadata_file: str, payload: object) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        content = payload if isinstance(payload, str) else json.dumps(payload)
        archive.writestr(metadata_file, content)
    return path


def _verified_install_file(path: Path) -> ModInstallFile:
    return ModInstallFile(
        url=f"https://example.com/{path.name}",
        filename=path.name,
        size=path.stat().st_size,
        file_hash=compute_file_hash(path, "sha512"),
        hash_algorithm="sha512",
    )


def test_version_content_resolves_relative_directories(tmp_path: Path):
    service = VersionContentService(tmp_path, DummyLog())
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")

    mods_dir = service.get_mods_directory(version)
    resourcepacks_dir = service.get_resourcepacks_directory(version)
    shaderpacks_dir = service.get_shaderpacks_directory(version)

    assert mods_dir == tmp_path / "versions" / "fabric-1.20.1" / "mods"
    assert resourcepacks_dir == tmp_path / "versions" / "fabric-1.20.1" / "resourcepacks"
    assert shaderpacks_dir == tmp_path / "versions" / "fabric-1.20.1" / "shaderpacks"
    assert mods_dir is not None
    assert resourcepacks_dir is not None
    assert shaderpacks_dir is not None
    assert mods_dir.exists()
    assert resourcepacks_dir.exists()
    assert shaderpacks_dir.exists()


def test_version_content_reads_quilt_and_neoforge_with_shared_parser(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    quilt = _write_mod_jar(
        mods_dir / "quilt.jar",
        "quilt.mod.json",
        {
            "quilt_loader": {
                "id": "quilt_mod",
                "version": "2.0.0",
                "metadata": {
                    "name": "Quilt Mod",
                    "description": "Quilt description",
                },
            }
        },
    )
    _write_mod_jar(
        mods_dir / "neoforge.jar",
        "META-INF/neoforge.mods.toml",
        """
[[mods]]
modId = "primary_neoforge"
version = "3.0.0"
displayName = "Primary NeoForge Mod"
description = "NeoForge description"

[[mods]]
modId = "secondary_neoforge"
version = "3.0.1"
displayName = "Secondary NeoForge Mod"
""",
    )
    service = VersionContentService(tmp_path, DummyLog())

    assert service.read_mod_metadata(quilt) == {
        "name": "Quilt Mod",
        "version": "2.0.0",
        "description": "Quilt description",
        "id": "quilt_mod",
    }

    installed = {mod["filename"]: mod for mod in service.scan_installed_mods(mods_dir)}
    assert installed["neoforge.jar"] == {
        "filename": "neoforge.jar",
        "path": str(mods_dir / "neoforge.jar"),
        "size": (mods_dir / "neoforge.jar").stat().st_size,
        "enabled": True,
        "name": "Primary NeoForge Mod",
        "version": "3.0.0",
        "description": "NeoForge description",
        "id": "primary_neoforge",
    }


def test_version_content_scans_enabled_and_disabled_resourcepacks(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    resourcepacks_dir = version_root / "resourcepacks"
    resourcepacks_dir.mkdir(parents=True)
    (version_root / "options.txt").write_text(
        'resourcePacks:["vanilla","mod_resources","file/enabled.zip"]\n'
        "incompatibleResourcePacks:[]\n"
        "lang:uk_ua\n",
        encoding="utf-8",
    )

    enabled = resourcepacks_dir / "enabled.zip"
    enabled.write_bytes(b"zip")
    disabled = resourcepacks_dir / "disabled.zip"
    disabled.write_bytes(b"zip")

    service = VersionContentService(tmp_path, DummyLog())
    packs = service.scan_installed_resourcepacks(resourcepacks_dir)

    assert [pack["filename"] for pack in packs] == ["disabled.zip", "enabled.zip"]
    assert packs[0]["enabled"] is False
    assert packs[1]["enabled"] is True


def test_version_content_toggles_resourcepacks_in_options_without_renaming(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    resourcepacks_dir = version_root / "resourcepacks"
    resourcepacks_dir.mkdir(parents=True)
    (resourcepacks_dir / "enabled pack.zip").write_bytes(b"enabled")
    (resourcepacks_dir / "disabled pack.zip").write_bytes(b"disabled")
    options_path = version_root / "options.txt"
    options_path.write_text(
        'resourcePacks:["vanilla","mod_resources","file/enabled pack.zip"]\n'
        'incompatibleResourcePacks:["file/enabled pack.zip"]\n'
        "lang:uk_ua\n",
        encoding="utf-8",
    )

    service = VersionContentService(tmp_path, DummyLog())
    packs = {pack["filename"]: pack for pack in service.scan_installed_resourcepacks(resourcepacks_dir)}

    enabled = service.toggle_resourcepack(resourcepacks_dir, packs["disabled pack.zip"])
    assert enabled is True
    assert (resourcepacks_dir / "disabled pack.zip").exists()
    options = options_path.read_text(encoding="utf-8")
    assert 'resourcePacks:["vanilla","mod_resources","file/enabled pack.zip","file/disabled pack.zip"]' in options

    packs = {pack["filename"]: pack for pack in service.scan_installed_resourcepacks(resourcepacks_dir)}
    enabled = service.toggle_resourcepack(resourcepacks_dir, packs["enabled pack.zip"])
    assert enabled is False
    assert (resourcepacks_dir / "enabled pack.zip").exists()
    options = options_path.read_text(encoding="utf-8")
    assert 'resourcePacks:["vanilla","mod_resources","file/disabled pack.zip"]' in options
    assert "incompatibleResourcePacks:[]" in options

    service.delete_resourcepack(resourcepacks_dir, packs["disabled pack.zip"])
    options = options_path.read_text(encoding="utf-8")
    assert "file/disabled pack.zip" not in options


def test_version_content_scans_toggles_and_deletes_shaderpacks(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mods_dir = version_root / "mods"
    shaderpacks_dir = version_root / "shaderpacks"
    config_dir = version_root / "config"
    mods_dir.mkdir(parents=True)
    shaderpacks_dir.mkdir(parents=True)
    config_dir.mkdir()
    (mods_dir / "iris.jar").write_bytes(b"jar")
    shader = shaderpacks_dir / "complementary.zip"
    shader.write_bytes(b"shader")
    (config_dir / "iris.properties").write_text(
        "enableShaders=true\n"
        "shaderPack=complementary.zip\n",
        encoding="utf-8",
    )

    service = VersionContentService(tmp_path, DummyLog())
    packs = service.scan_installed_shaderpacks(shaderpacks_dir, mods_dir=mods_dir)

    assert packs[0]["filename"] == "complementary.zip"
    assert packs[0]["content_type"] == "shaderpack"
    assert packs[0]["enabled"] is True

    enabled = service.toggle_shaderpack(shaderpacks_dir, packs[0])
    assert enabled is False
    assert shader.exists()
    assert "enableShaders=false" in (config_dir / "iris.properties").read_text(encoding="utf-8")

    disabled_pack = service.scan_installed_shaderpacks(shaderpacks_dir, mods_dir=mods_dir)[0]
    enabled = service.toggle_shaderpack(shaderpacks_dir, disabled_pack)
    assert enabled is True
    assert "enableShaders=true" in (config_dir / "iris.properties").read_text(encoding="utf-8")

    service.delete_shaderpack(shaderpacks_dir, packs[0])
    assert not shader.exists()


def test_version_content_reads_shader_status_from_iris_config(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mods_dir = version_root / "mods"
    shaderpacks_dir = version_root / "shaderpacks"
    config_dir = version_root / "config"
    mods_dir.mkdir(parents=True)
    shaderpacks_dir.mkdir()
    config_dir.mkdir()
    (mods_dir / "iris-neoforge.jar").write_bytes(b"jar")
    (shaderpacks_dir / "ComplementaryReimagined_r5.8.1.zip").write_bytes(b"shader")
    (shaderpacks_dir / "OtherShader.zip").write_bytes(b"shader")
    (config_dir / "iris.properties").write_text(
        "#Iris config\n"
        "enableShaders=true\n"
        "shaderPack=ComplementaryReimagined_r5.8.1.zip\n",
        encoding="utf-8",
    )

    service = VersionContentService(tmp_path, DummyLog())
    packs = service.scan_installed_shaderpacks(shaderpacks_dir, mods_dir=mods_dir)

    by_name = {pack["filename"]: pack for pack in packs}
    assert by_name["ComplementaryReimagined_r5.8.1.zip"]["enabled"] is True
    assert by_name["ComplementaryReimagined_r5.8.1.zip"]["toggle_supported"] is True
    assert by_name["OtherShader.zip"]["enabled"] is False


def test_version_content_detects_iris_mod_by_loader_filename_pattern(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "iris-neoforge-1.8.12+mc1.21.1.jar").write_bytes(b"jar")

    service = VersionContentService(tmp_path, DummyLog())

    assert service.has_iris_mod(mods_dir) is True


def test_version_content_hides_shader_toggle_without_iris(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    shaderpacks_dir = version_root / "shaderpacks"
    shaderpacks_dir.mkdir(parents=True)
    (shaderpacks_dir / "Complementary.zip").write_bytes(b"shader")

    service = VersionContentService(tmp_path, DummyLog())
    packs = service.scan_installed_shaderpacks(shaderpacks_dir, mods_dir=version_root / "mods")

    assert packs[0]["filename"] == "Complementary.zip"
    assert packs[0]["enabled"] is False
    assert packs[0]["toggle_supported"] is False


def test_version_content_toggles_shaderpack_through_iris_config(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    shaderpacks_dir = version_root / "shaderpacks"
    config_dir = version_root / "config"
    shaderpacks_dir.mkdir(parents=True)
    config_dir.mkdir()
    shader = shaderpacks_dir / "Complementary.zip"
    shader.write_bytes(b"shader")
    options_path = config_dir / "iris.properties"
    options_path.write_text(
        "#Iris config\n"
        "enableShaders=false\n"
        "shaderPack=OldShader.zip\n",
        encoding="utf-8",
    )

    service = VersionContentService(tmp_path, DummyLog())
    enabled = service.toggle_shaderpack(shaderpacks_dir, {"path": str(shader), "filename": shader.name, "enabled": False})

    assert enabled is True
    content = options_path.read_text(encoding="utf-8")
    assert "enableShaders=true" in content
    assert "shaderPack=Complementary.zip" in content

    enabled = service.toggle_shaderpack(shaderpacks_dir, {"path": str(shader), "filename": shader.name, "enabled": True})

    assert enabled is False
    content = options_path.read_text(encoding="utf-8")
    assert "enableShaders=false" in content
    assert "shaderPack=Complementary.zip" in content


def test_version_content_records_and_applies_modrinth_install_metadata(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    pack_dir = version_root / "resourcepacks"
    pack_dir.mkdir(parents=True)
    pack_path = pack_dir / "faithful.zip"
    pack_path.write_bytes(b"pack")

    service = VersionContentService(tmp_path, DummyLog())
    service.record_modrinth_content(
        version,
        "resourcepacks",
        pack_path,
        {"project_id": "faithful-project", "slug": "faithful", "title": "Faithful"},
        {"id": "version-new", "version_number": "1.0.0"},
        ModInstallFile(url="https://example.com/faithful.zip", filename="faithful.zip"),
    )

    packs = service.apply_modrinth_metadata(version, service.scan_installed_resourcepacks(pack_dir))

    assert packs[0]["modrinth_project_id"] == "faithful-project"
    assert packs[0]["modrinth_version_id"] == "version-new"


def test_modrinth_metadata_round_trips_verified_file_hash(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mod_path = version_root / "mods" / "verified.jar"
    mod_path.parent.mkdir(parents=True)
    mod_path.write_bytes(b"verified Modrinth JAR")
    install_file = _verified_install_file(mod_path)
    service = VersionContentService(tmp_path, DummyLog())

    service.record_modrinth_content(
        version,
        "mods",
        mod_path,
        {"project_id": "verified-project", "slug": "verified"},
        {"id": "verified-version", "version_number": "1.0.0"},
        install_file,
    )

    index_path = version_root / ".tensalauncher" / service.MODRINTH_METADATA_FILE
    stored = json.loads(index_path.read_text(encoding="utf-8"))
    stored_file = stored["files"]["mods/verified.jar"]
    assert stored["schema_version"] == service.MODRINTH_METADATA_SCHEMA_VERSION
    assert stored_file["hash_algorithm"] == "sha512"
    assert stored_file["file_hash"] == install_file.file_hash

    item = service.apply_modrinth_metadata(
        version,
        service.scan_installed_mods(mod_path.parent),
    )[0]
    assert item["modrinth_hash_algorithm"] == "sha512"
    assert item["modrinth_file_hash"] == install_file.file_hash
    assert item["modrinth_provenance_authoritative"] is True
    assert ModIdentityService.owns_item(item, "verified-project") is True


def test_modrinth_metadata_batch_verifies_staged_replacement_bytes(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    live_path = version_root / "mods" / "replacement.jar"
    live_path.parent.mkdir(parents=True)
    live_path.write_bytes(b"old live bytes")
    stage_root = version_root / ".tensalauncher-sync" / ("a" * 32) / "stage"
    staged_path = stage_root / "mods" / "replacement.jar"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_bytes(b"new verified bytes")
    output_path = stage_root / ".tensalauncher" / VersionContentService.MODRINTH_METADATA_FILE
    install_file = _verified_install_file(staged_path)
    service = VersionContentService(tmp_path, DummyLog())

    service.write_modrinth_content_batch(
        version,
        [
            (
                "mods",
                live_path,
                {"project_id": "replacement-project"},
                {"id": "replacement-version"},
                install_file,
            )
        ],
        output_path=output_path,
    )

    stored = json.loads(output_path.read_text(encoding="utf-8"))
    assert stored["files"]["mods/replacement.jar"]["file_hash"] == install_file.file_hash


def test_modrinth_metadata_loses_ownership_when_path_bytes_are_replaced(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    mod_path = tmp_path / "versions" / "fabric-1.20.1" / "mods" / "same-path.jar"
    mod_path.parent.mkdir(parents=True)
    mod_path.write_bytes(b"original Modrinth JAR")
    service = VersionContentService(tmp_path, DummyLog())
    service.record_modrinth_content(
        version,
        "mods",
        mod_path,
        {"project_id": "original-project", "slug": "original"},
        {"id": "original-version"},
        _verified_install_file(mod_path),
    )

    mod_path.write_bytes(b"unrelated replacement JAR")
    item = service.apply_modrinth_metadata(
        version,
        service.scan_installed_mods(mod_path.parent),
    )[0]
    match = ModIdentityService.match_project(
        [item],
        {"project_id": "original-project"},
    )

    assert item["modrinth_project_id"] == "original-project"
    assert item["modrinth_provenance_authoritative"] is False
    assert match.kind is ModMatchKind.HINT
    assert match.can_replace is False


def test_modrinth_metadata_keeps_ownership_after_disabled_rename(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    mod_path = tmp_path / "versions" / "fabric-1.20.1" / "mods" / "toggle.jar"
    mod_path.parent.mkdir(parents=True)
    mod_path.write_bytes(b"same bytes while disabled")
    service = VersionContentService(tmp_path, DummyLog())
    service.record_modrinth_content(
        version,
        "mods",
        mod_path,
        {"project_id": "toggle-project"},
        {"id": "toggle-version"},
        _verified_install_file(mod_path),
    )
    disabled_path = mod_path.with_name(f"{mod_path.name}.disabled")
    mod_path.rename(disabled_path)

    item = service.apply_modrinth_metadata(
        version,
        service.scan_installed_mods(disabled_path.parent),
    )[0]

    assert item["enabled"] is False
    assert item["path"] == str(disabled_path)
    assert item["modrinth_provenance_authoritative"] is True
    assert ModIdentityService.owns_item(item, "toggle-project") is True


def test_legacy_path_only_modrinth_metadata_is_readable_but_not_authoritative(
    tmp_path: Path,
):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mod_path = version_root / "mods" / "legacy.jar"
    mod_path.parent.mkdir(parents=True)
    mod_path.write_bytes(b"legacy bytes")
    metadata_path = version_root / ".tensalauncher" / VersionContentService.MODRINTH_METADATA_FILE
    metadata_path.parent.mkdir()
    metadata_path.write_text(
        json.dumps(
            {
                "files": {
                    "mods/legacy.jar": {
                        "content_key": "mods",
                        "filename": "legacy.jar",
                        "project_id": "legacy-project",
                        "project_slug": "legacy",
                        "version_id": "legacy-version",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    service = VersionContentService(tmp_path, DummyLog())

    item = service.apply_modrinth_metadata(
        version,
        service.scan_installed_mods(mod_path.parent),
    )[0]
    match = ModIdentityService.match_project(
        [item],
        {"project_id": "legacy-project"},
    )

    assert item["modrinth_project_id"] == "legacy-project"
    assert item["modrinth_version_id"] == "legacy-version"
    assert item["modrinth_provenance_authoritative"] is False
    assert match.kind is ModMatchKind.HINT
    assert match.can_replace is False

    service.record_modrinth_content(
        version,
        "mods",
        mod_path,
        {"project_id": "legacy-project", "slug": "legacy"},
        {"id": "confirmed-version"},
        _verified_install_file(mod_path),
    )
    migrated_item = service.apply_modrinth_metadata(
        version,
        service.scan_installed_mods(mod_path.parent),
    )[0]
    migrated = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert migrated["schema_version"] == service.MODRINTH_METADATA_SCHEMA_VERSION
    assert migrated["files"]["mods/legacy.jar"]["file_hash"]
    assert migrated_item["modrinth_version_id"] == "confirmed-version"
    assert migrated_item["modrinth_provenance_authoritative"] is True


def test_version_content_preserves_existing_modrinth_metadata_when_atomic_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mod_path = version_root / "mods" / "example.jar"
    mod_path.parent.mkdir(parents=True)
    mod_path.write_bytes(b"jar")
    service = VersionContentService(tmp_path, DummyLog())
    install_file = ModInstallFile(
        url="https://example.com/example.jar",
        filename="example.jar",
    )
    service.record_modrinth_content(
        version,
        "mods",
        mod_path,
        {"project_id": "project-old"},
        {"id": "version-old"},
        install_file,
    )
    index_path = version_root / ".tensalauncher" / service.MODRINTH_METADATA_FILE
    original = index_path.read_bytes()
    monkeypatch.setattr(
        "launcher.application.version_content.atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        service.record_modrinth_content(
            version,
            "mods",
            mod_path,
            {"project_id": "project-new"},
            {"id": "version-new"},
            install_file,
        )

    assert index_path.read_bytes() == original


def test_version_content_prepares_metadata_batch_without_changing_live_index(tmp_path: Path):
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    old_path = version_root / "mods" / "old.jar"
    keep_path = version_root / "mods" / "keep.jar"
    new_path = version_root / "mods" / "new.jar"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old")
    keep_path.write_bytes(b"keep")
    service = VersionContentService(tmp_path, DummyLog())
    install_file = ModInstallFile("https://example.com/file.jar", "file.jar")
    service.record_modrinth_content(
        version,
        "mods",
        old_path,
        {"project_id": "old-project"},
        {"id": "old-version"},
        install_file,
    )
    service.record_modrinth_content(
        version,
        "mods",
        keep_path,
        {"project_id": "keep-project"},
        {"id": "keep-version"},
        install_file,
    )
    index_path = service.get_modrinth_metadata_path(version)
    assert index_path is not None
    original = index_path.read_bytes()
    staged_index = tmp_path / "stage" / service.MODRINTH_METADATA_FILE

    service.write_modrinth_content_batch(
        version,
        [
            (
                "mods",
                new_path,
                {"project_id": "new-project"},
                {"id": "new-version"},
                ModInstallFile("https://example.com/new.jar", "new.jar"),
            )
        ],
        removed_paths=[old_path],
        output_path=staged_index,
    )

    assert index_path.read_bytes() == original
    staged = service._load_modrinth_metadata(staged_index)
    assert set(staged["files"]) == {"mods/keep.jar", "mods/new.jar"}
    assert staged["files"]["mods/new.jar"]["project_id"] == "new-project"


def test_version_content_toggle_mod_and_backup(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True)
    mod_path = mods_dir / "example.jar"
    mod_path.write_bytes(b"jar")

    service = VersionContentService(tmp_path, DummyLog())
    mod = {"path": str(mod_path), "filename": "example.jar", "enabled": True}

    enabled = service.toggle_mod(mod)

    assert enabled is False
    assert not mod_path.exists()
    assert (mods_dir / "example.jar.disabled").exists()

    disabled_mod = {"path": str(mods_dir / "example.jar.disabled"), "filename": "example.jar", "enabled": False}
    enabled = service.toggle_mod(disabled_mod)
    assert enabled is True
    assert mod_path.exists()

    assert service.create_backup(mods_dir, {"path": str(mod_path), "filename": "example.jar"}) is True
    assert service.has_backup(mods_dir, "example.jar") is True


def test_version_content_toggle_and_delete_resourcepack_folder(tmp_path: Path):
    version_root = tmp_path / "versions" / "fabric-1.20.1"
    resourcepacks_dir = version_root / "resourcepacks"
    resourcepacks_dir.mkdir(parents=True)
    options_path = version_root / "options.txt"
    options_path.write_text('resourcePacks:["vanilla","file/faithful"]\n', encoding="utf-8")
    pack_dir = resourcepacks_dir / "faithful"
    pack_dir.mkdir()
    (pack_dir / "pack.mcmeta").write_text("{}", encoding="utf-8")

    service = VersionContentService(tmp_path, DummyLog())
    rp = {"path": str(pack_dir), "filename": "faithful", "type": "resourcepack_folder", "enabled": True}

    enabled = service.toggle_resourcepack(resourcepacks_dir, rp)
    assert enabled is False
    assert pack_dir.exists()
    assert 'resourcePacks:["vanilla"]' in options_path.read_text(encoding="utf-8")

    disabled_rp = {"path": str(pack_dir), "filename": "faithful", "type": "resourcepack_folder", "enabled": False}
    enabled = service.toggle_resourcepack(resourcepacks_dir, disabled_rp)
    assert enabled is True
    assert 'resourcePacks:["vanilla","file/faithful"]' in options_path.read_text(encoding="utf-8")

    service.delete_resourcepack(resourcepacks_dir, rp)
    assert not pack_dir.exists()
    assert "file/faithful" not in options_path.read_text(encoding="utf-8")
