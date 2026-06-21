from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from launcher.application.modrinth_mods import ModInstallFile
from launcher.application.version_content import VersionContentService


class DummyLog:
    def debug(self, *_args, **_kwargs) -> None:
        return None


def test_version_content_resolves_relative_directories(tmp_path: Path):
    service = VersionContentService(tmp_path, DummyLog())
    version = SimpleNamespace(path="versions/fabric-1.20.1", client="fabric")

    mods_dir = service.get_mods_directory(version)
    resourcepacks_dir = service.get_resourcepacks_directory(version)
    shaderpacks_dir = service.get_shaderpacks_directory(version)

    assert mods_dir == tmp_path / "versions" / "fabric-1.20.1" / "mods"
    assert resourcepacks_dir == tmp_path / "versions" / "fabric-1.20.1" / "resourcepacks"
    assert shaderpacks_dir == tmp_path / "versions" / "fabric-1.20.1" / "shaderpacks"
    assert mods_dir.exists()
    assert resourcepacks_dir.exists()
    assert shaderpacks_dir.exists()


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
