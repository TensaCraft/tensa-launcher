from __future__ import annotations

import json
import os
from pathlib import Path

import minecraft_launcher_lib.command
import minecraft_launcher_lib.utils
import pytest

import launcher.core.integrity as integrity_module
from launcher.core.integrity import IntegrityChecker


def _write_manifest(minecraft_dir: Path, version_id: str = "1.21.1", **metadata) -> Path:
    path = minecraft_dir / "versions" / version_id / f"{version_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": version_id,
                "type": "release",
                "releaseTime": "2024-08-08T12:00:00+00:00",
                "mainClass": "net.minecraft.client.main.Main",
                "libraries": [],
                "arguments": {"game": []},
                **metadata,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("installed_first", [True, False])
def test_installed_detection_isolated_between_minecraft_roots(tmp_path, installed_first):
    installed_root = tmp_path / "installed"
    empty_root = tmp_path / "empty"
    _write_manifest(installed_root)
    checks = [(IntegrityChecker(installed_root), True), (IntegrityChecker(empty_root), False)]
    if not installed_first:
        checks.reverse()

    for checker, expected in checks:
        assert checker._is_version_installed("1.21.1") is expected


def test_installed_detection_observes_external_install_immediately(tmp_path):
    checker = IntegrityChecker(tmp_path)
    assert checker._is_version_installed("1.21.1") is False

    _write_manifest(tmp_path)

    assert checker._is_version_installed("1.21.1") is True


def test_installed_detection_observes_external_delete_immediately(tmp_path):
    manifest = _write_manifest(tmp_path)
    checker = IntegrityChecker(tmp_path)
    assert checker._is_version_installed("1.21.1") is True

    manifest.unlink()

    assert checker._is_version_installed("1.21.1") is False


def test_installed_detection_observes_external_manifest_rewrites(tmp_path):
    manifest = _write_manifest(tmp_path)
    checker = IntegrityChecker(tmp_path)
    assert checker._is_version_installed("1.21.1") is True

    manifest.write_text('{"id":', encoding="utf-8")
    assert checker._is_version_installed("1.21.1") is False

    _write_manifest(tmp_path)
    assert checker._is_version_installed("1.21.1") is True


@pytest.mark.parametrize(
    "content",
    [b"", b"{", b"null", b"[]", b"{}", b'{"id": "other"}', b'{"id": "\xff"}'],
)
def test_installed_detection_rejects_invalid_manifest(tmp_path, content):
    manifest = _write_manifest(tmp_path)
    manifest.write_bytes(content)

    assert IntegrityChecker(tmp_path)._is_version_installed("1.21.1") is False


@pytest.mark.parametrize("manifest_is_directory", [True, False])
def test_installed_detection_requires_manifest_file(tmp_path, manifest_is_directory):
    version_dir = tmp_path / "versions" / "1.21.1"
    version_dir.mkdir(parents=True)
    if manifest_is_directory:
        (version_dir / "1.21.1.json").mkdir()

    assert IntegrityChecker(tmp_path)._is_version_installed("1.21.1") is False


def test_installed_detection_handles_unreadable_manifest(tmp_path, monkeypatch):
    _write_manifest(tmp_path)

    def denied(*_args, **_kwargs):
        raise PermissionError("manifest access denied")

    monkeypatch.setattr(integrity_module, "open", denied, raising=False)

    assert IntegrityChecker(tmp_path)._is_version_installed("1.21.1") is False


@pytest.mark.parametrize("sibling_count", [0, 100])
def test_installed_detection_reads_only_requested_manifest(tmp_path, monkeypatch, sibling_count):
    manifest = _write_manifest(tmp_path)
    for index in range(sibling_count):
        _write_manifest(tmp_path, f"other-{index}").write_text("{", encoding="utf-8")
    opened = []

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return open(path, *args, **kwargs)

    def unexpected_scan(*_args, **_kwargs):
        pytest.fail("installed detection must not enumerate versions")

    checker = IntegrityChecker(tmp_path)
    monkeypatch.setattr(integrity_module, "open", record_open, raising=False)
    with monkeypatch.context() as patch:
        patch.setattr(minecraft_launcher_lib.utils, "get_installed_versions", unexpected_scan)
        patch.setattr(os, "listdir", unexpected_scan)
        patch.setattr(os, "scandir", unexpected_scan)
        patch.setattr(Path, "iterdir", unexpected_scan)
        for _ in range(3):
            assert checker._is_version_installed("1.21.1") is True
        assert checker._is_version_installed("missing") is False

    assert opened == [manifest] * 3 + [tmp_path / "versions" / "missing" / "missing.json"]


@pytest.mark.parametrize("version_id", ["", ".", "..", "../outside", "nested/version", r"C:\outside", "bad\0id"])
def test_installed_detection_rejects_path_ids_before_reading(tmp_path, monkeypatch, version_id):
    def unexpected_read(*_args, **_kwargs):
        pytest.fail("version IDs must identify a single local manifest")

    monkeypatch.setattr(integrity_module, "open", unexpected_read, raising=False)

    assert IntegrityChecker(tmp_path)._is_version_installed(version_id) is False


def test_installed_detection_accepts_inherited_profile_without_own_jar(tmp_path):
    version_id = "fabric-loader-0.19.2-1.21.1"
    manifest = _write_manifest(tmp_path, version_id)
    manifest.write_text(json.dumps({"id": version_id, "inheritsFrom": "1.21.1"}), encoding="utf-8")

    assert IntegrityChecker(tmp_path)._is_version_installed(version_id) is True


@pytest.mark.parametrize("broken_component", [None, "manifest", "jar", "libraries", "java"])
def test_installed_detection_does_not_replace_component_verification(tmp_path, monkeypatch, broken_component):
    manifest = _write_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if broken_component == "manifest":
        del data["mainClass"]
    elif broken_component == "libraries":
        data["libraries"] = [{"downloads": {"artifact": {"path": "missing.jar"}}}]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    if broken_component != "jar":
        manifest.with_suffix(".jar").write_bytes(b"client")

    checker = IntegrityChecker(tmp_path)
    monkeypatch.setattr(checker, "_check_java_runtime", lambda *_args: broken_component != "java")

    assert checker._is_version_installed("1.21.1") is True
    result = checker.check_version("1.21.1")
    assert result["valid"] is (broken_component is None)
    if broken_component:
        assert result["components"][broken_component] is False
        assert result["issues"]
    for assume_installed in (False, True):
        assert checker.quick_check_version("1.21.1", assume_installed=assume_installed) is (
            broken_component in (None, "libraries")
        )


@pytest.fixture
def local_checker(tmp_path, monkeypatch):
    def unexpected_network(*_args, **_kwargs):
        pytest.fail("integrity checks must use local metadata only")

    monkeypatch.setattr(integrity_module.minecraft_launcher_helper, "get_requests_response_cache", unexpected_network)
    checker = IntegrityChecker(tmp_path)
    monkeypatch.setattr(checker, "_check_java_runtime", lambda *_args: True)
    return checker


@pytest.mark.parametrize("jar_metadata_owner", ["parent", "loader"])
def test_inherited_jar_matches_generated_jvm_classpath(tmp_path, local_checker, jar_metadata_owner):
    parent = _write_manifest(tmp_path, **({"jar": "1.21.1"} if jar_metadata_owner == "parent" else {}))
    parent.with_suffix(".jar").write_bytes(b"base client")
    loader = _write_manifest(tmp_path, "loader", inheritsFrom="1.21.1")
    loader.write_text(
        json.dumps({
            "id": "loader",
            "inheritsFrom": "1.21.1",
            **({"jar": "1.21.1"} if jar_metadata_owner == "loader" else {}),
        }),
        encoding="utf-8",
    )
    command = minecraft_launcher_lib.command.get_minecraft_command("loader", tmp_path, {"executablePath": "java"})
    assert Path(command[command.index("-cp") + 1]) == parent.with_suffix(".jar")
    assert not loader.with_suffix(".jar").exists()

    assert local_checker.check_version("loader", check_java=False)["valid"] is True
    assert local_checker.quick_check_version("loader") is True

    parent.with_suffix(".jar").unlink()
    assert local_checker._check_version_jar("loader") is False


def test_inherited_profile_without_jar_override_requires_derived_jvm_jar(tmp_path, local_checker):
    parent = _write_manifest(tmp_path)
    parent.with_suffix(".jar").write_bytes(b"base client")
    loader = _write_manifest(tmp_path, "loader", inheritsFrom="1.21.1")
    command = minecraft_launcher_lib.command.get_minecraft_command("loader", tmp_path, {"executablePath": "java"})
    assert Path(command[command.index("-cp") + 1]) == loader.with_suffix(".jar")

    assert local_checker._check_version_jar("loader") is False
    loader.with_suffix(".jar").write_bytes(b"derived client")
    assert local_checker.check_version("loader", check_java=False)["valid"] is True


@pytest.mark.parametrize("parent_state", ["missing", "corrupt", "nonobject", "wrong-id", "self-cycle", "cycle"])
def test_invalid_inheritance_fails_even_with_loader_jar(tmp_path, local_checker, parent_state):
    loader = _write_manifest(tmp_path, "loader", inheritsFrom="1.21.1")
    loader.with_suffix(".jar").write_bytes(b"derived client")
    if parent_state != "missing":
        parent = _write_manifest(tmp_path)
        if parent_state == "corrupt":
            parent.write_text("{", encoding="utf-8")
        elif parent_state == "nonobject":
            parent.write_text("[]", encoding="utf-8")
        elif parent_state == "wrong-id":
            _write_manifest(tmp_path, id="other")
        elif parent_state == "self-cycle":
            _write_manifest(tmp_path, "loader", inheritsFrom="loader")
        elif parent_state == "cycle":
            _write_manifest(tmp_path, inheritsFrom="loader")

    assert local_checker._is_version_installed("loader") is True
    result = local_checker.check_version("loader", check_java=False)
    assert result["valid"] is False
    assert result["components"]["manifest"] is False
    assert result["components"]["libraries"] is False
    assert local_checker.quick_check_version("loader") is False


@pytest.mark.parametrize("explicit_path", [True, False])
def test_inherited_missing_base_libraries_are_checked(tmp_path, local_checker, explicit_path):
    library = {"name": "example:base:1.0"}
    if explicit_path:
        library["downloads"] = {"artifact": {"path": "example/base/1.0/base-1.0.jar"}}
    _write_manifest(tmp_path, libraries=[library])
    loader = _write_manifest(tmp_path, "loader", inheritsFrom="1.21.1")
    loader.with_suffix(".jar").write_bytes(b"derived client")

    result = local_checker.check_version("loader", check_java=False)
    assert result["components"]["manifest"] is True
    assert result["components"]["jar"] is True
    assert result["components"]["libraries"] is False
    assert result["valid"] is False
    assert local_checker.quick_check_version("loader") is True

    artifact = Path(integrity_module.minecraft_launcher_helper.get_library_path(library["name"], tmp_path))
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"library")
    assert local_checker.check_version("loader", check_java=False)["valid"] is True
    artifact.unlink()
    assert local_checker._check_libraries("loader") is False


def test_inherited_libraries_preserve_upstream_overrides_and_rules(tmp_path, local_checker):
    _write_manifest(tmp_path, libraries=[
        {"name": "example:shared:1.0"},
        {"name": "example:excluded:1.0", "rules": [{"action": "disallow"}]},
    ])
    _write_manifest(tmp_path, "loader", inheritsFrom="1.21.1", libraries=[{"name": "example:shared:2.0"}])
    artifact = Path(integrity_module.minecraft_launcher_helper.get_library_path("example:shared:2.0", tmp_path))
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"override library")

    assert local_checker._check_libraries("loader") is True
    artifact.unlink()
    assert local_checker._check_libraries("loader") is False
