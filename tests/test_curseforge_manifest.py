from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest

from launcher.application.curseforge_install import (
    CurseForgeInstallLimits,
    CurseForgeInstallService,
    FileMetadata,
    OverrideFile,
)
from launcher.application.curseforge_manifest import CurseForgeManifestService
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.core.loaders import curseforge as curseforge_module
from launcher.core.loaders.curseforge import CurseForgeLoader


def _loader() -> CurseForgeLoader:
    loader = object.__new__(CurseForgeLoader)
    loader._file_meta_cache = {}
    return loader


def _service(
    *,
    downloader_factory=None,
    override_members: int = 20_000,
    override_entry_size: int = 1024 * 1024 * 1024,
    override_total_size: int = 4 * 1024 * 1024 * 1024,
) -> CurseForgeInstallService:
    if downloader_factory is None:

        def downloader_factory(**_kwargs):
            raise AssertionError("Override-only transaction must not download files")

    return CurseForgeInstallService(
        limits=CurseForgeInstallLimits(
            override_members=override_members,
            override_entry_size=override_entry_size,
            override_total_size=override_total_size,
        ),
        downloader_factory=downloader_factory,
    )


def _apply_overrides(
    service: CurseForgeInstallService,
    source_path: Path,
    source_kind: str,
    manifest: dict,
    game_path: Path,
) -> None:
    overrides = service.plan_overrides(
        source_path,
        source_kind,
        manifest,
        game_path,
    )
    if not overrides:
        return
    service.install_content(
        game_path=game_path,
        remote_files=[],
        overrides=overrides,
        source_path=source_path,
        source_kind=source_kind,
        operation_name="curseforge-test-overrides",
    )


def test_curseforge_loader_rejects_concurrent_instance_operation(fake_app):
    loader = CurseForgeLoader(app=fake_app)
    version = SimpleNamespace(
        version_id="demo",
        name="Demo",
    )
    game_path = loader.get_game_path(version.version_id)

    with fake_app.instance_operations.operation(game_path, "launch"):
        with pytest.raises(RuntimeError, match="instance_operation_busy"):
            loader.install(cast(Any, version))


def _symlink_or_skip(target: Path, link: Path, *, target_is_directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")


def test_curseforge_manifest_loads_json_manifest(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "Pack",
                "version": "1.0.0",
                "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-loader-0.16.0", "primary": True}]},
                "files": [],
            }
        ),
        encoding="utf-8",
    )

    manifest = CurseForgeManifestService().load(manifest_path)

    assert manifest.source_kind == "manifest"
    assert manifest.minecraft_version == "1.20.1"
    assert manifest.loader_name == "fabric"
    assert manifest.loader_version == "0.16.0"


def test_curseforge_manifest_loads_zip_manifest(tmp_path: Path):
    archive_path = tmp_path / "modpack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "Pack",
                    "minecraft": {"version": "1.20.1", "modLoaders": []},
                    "files": [],
                }
            ),
        )

    manifest = CurseForgeManifestService().load(archive_path)

    assert manifest.source_kind == "zip"
    assert manifest.loader_name == "minecraft"
    assert manifest.loader_version is None


def test_curseforge_manifest_suggests_version_name():
    suggested = CurseForgeManifestService.suggest_version_name({"name": "Pack", "version": "2.0.0"})

    assert suggested == "Pack 2.0.0"


def test_curseforge_metadata_prefers_strongest_supported_hash(monkeypatch):
    loader = _loader()
    payload = {
        "data": {
            "fileName": "example.jar",
            "fileLength": 1024,
            "hashes": [
                {"algo": 1, "value": "1" * 40},
                {"algo": "SHA-512", "value": "2" * 128},
                {"algo": "sha256", "value": "3" * 64},
            ],
        }
    }
    monkeypatch.setattr(
        curseforge_module.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, json=lambda: payload),
    )

    metadata = loader._resolve_file_metadata(10, 20)

    assert metadata.name == "example.jar"
    assert metadata.size == 1024
    assert metadata.hash_algorithm == "sha512"
    assert metadata.hash_value == "2" * 128


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {"data": {"fileName": "example.jar", "fileLength": 0, "hashes": [{"algo": 1, "value": "1" * 40}]}},
            "fileLength",
        ),
        (
            {"data": {"fileName": "example.jar", "fileLength": 10, "hashes": []}},
            "file hash",
        ),
        (
            {
                "data": {
                    "fileName": "example.jar",
                    "fileLength": 10,
                    "hashes": [{"algo": 2, "value": "1" * 32}],
                }
            },
            "supported valid file hash",
        ),
        (
            {
                "data": {
                    "fileName": "example.jar",
                    "fileLength": 10,
                    "hashes": [{"algo": 99, "value": "1" * 40}],
                }
            },
            "supported valid file hash",
        ),
    ],
)
def test_curseforge_metadata_fails_closed_when_integrity_data_is_inadequate(
    monkeypatch,
    payload: dict,
    message: str,
):
    loader = _loader()
    monkeypatch.setattr(
        curseforge_module.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, json=lambda: payload),
    )

    with pytest.raises(ValueError, match=message):
        loader._resolve_file_metadata(10, 20)


@pytest.mark.parametrize(
    "file_name",
    [
        "",
        "../escaped.jar",
        "nested/escaped.jar",
        r"nested\escaped.jar",
        r"C:\escaped.jar",
        "/escaped.jar",
        "CON.jar",
        "trailing.jar.",
    ],
)
def test_curseforge_rejects_unsafe_remote_file_names(tmp_path: Path, file_name: str):
    with pytest.raises(ValueError, match="unsafe fileName"):
        CurseForgeInstallService.safe_mod_destination(
            tmp_path / "mods",
            file_name,
        )


def test_curseforge_download_tasks_require_size_and_hash(tmp_path: Path):
    content = b"x" * 1234
    expected_hash = hashlib.sha1(content).hexdigest()

    def resolve_metadata(_project_id: int, _file_id: int):
        return FileMetadata(
            name="example.jar",
            size=len(content),
            hash_value=expected_hash,
            hash_algorithm="sha1",
        )

    captured: dict = {}

    class RecordingDownloader:
        def __init__(self, max_workers: int):
            captured["max_workers"] = max_workers

        def download_files(self, tasks, **kwargs):
            captured["tasks"] = tasks
            captured["kwargs"] = kwargs
            for task in tasks:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(content)
            return {"success": 1, "failed": 0, "skipped": 0, "errors": []}

    service = _service(downloader_factory=RecordingDownloader)
    remote_files, result = service.plan_manifest_files(
        [{"projectID": 10, "fileID": 20, "required": True}],
        tmp_path,
        metadata_resolver=resolve_metadata,
        download_url=lambda project_id, file_id: f"https://example.com/{project_id}/{file_id}",
    )
    result = service.install_content(
        game_path=tmp_path,
        remote_files=remote_files,
        overrides=[],
        source_path=None,
        source_kind=None,
        operation_name="curseforge-test-mods",
        result=result,
    )

    task = captured["tasks"][0]
    assert result["success"] == 1
    assert task.destination.parts[-3:] == ("stage", "mods", "example.jar")
    assert task.expected_size == 1234
    assert task.expected_hash == expected_hash
    assert task.expected_hash_algorithm == "sha1"
    assert captured["kwargs"]["skip_existing"] is False
    assert captured["kwargs"]["verify_existing_hash"] is True
    assert (tmp_path / "mods" / "example.jar").read_bytes() == content


def test_curseforge_unsafe_file_name_never_reaches_downloader(tmp_path: Path):
    def resolve_metadata(_project_id: int, _file_id: int):
        return FileMetadata(
            name="../escaped.jar",
            size=1234,
            hash_value="a" * 40,
            hash_algorithm="sha1",
        )

    class UnexpectedDownloader:
        def __init__(self, **_kwargs):
            raise AssertionError("Unsafe artifact must not reach downloader")

    service = _service(downloader_factory=UnexpectedDownloader)
    remote_files, result = service.plan_manifest_files(
        [{"projectID": 10, "fileID": 20, "required": True}],
        tmp_path,
        metadata_resolver=resolve_metadata,
        download_url=lambda project_id, file_id: f"https://example.com/{project_id}/{file_id}",
    )

    assert result["failed"] == 1
    assert remote_files == []
    assert not (tmp_path / "escaped.jar").exists()


def test_curseforge_metadata_failure_prevents_all_remote_downloads(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = CurseForgeLoader(app=fake_app)

    def resolve_metadata(project_id: int, _file_id: int):
        name = "valid.jar" if project_id == 10 else "../escaped.jar"
        return FileMetadata(
            name=name,
            size=1234,
            hash_value="a" * 40,
            hash_algorithm="sha1",
        )

    class UnexpectedDownloader:
        def __init__(self, **_kwargs):
            raise AssertionError("Preflight failure must prevent all downloads")

    source = tmp_path / "source"
    source.mkdir()
    manifest_path = source / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "Pack",
                "version": "1.0.0",
                "minecraft": {"version": "1.20.1", "modLoaders": []},
                "files": [
                    {"projectID": 10, "fileID": 20, "required": True},
                    {"projectID": 11, "fileID": 21, "required": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    game_path = tmp_path / "game"
    version = SimpleNamespace(
        version_id="demo",
        name="Demo",
        options={"curseforge_source_path": str(manifest_path)},
        save=lambda: None,
    )

    monkeypatch.setattr(loader, "get_game_path", lambda _version_id: game_path)
    monkeypatch.setattr(loader, "_ensure_instance_idle", lambda *_args: None)
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loader, "_resolve_file_metadata", resolve_metadata)
    monkeypatch.setattr(curseforge_module, "AsyncDownloader", UnexpectedDownloader)

    with pytest.raises(ValueError, match="Failed to download 1 files"):
        loader.install(cast(Any, version))

    assert not (game_path / "mods" / "valid.jar").exists()


def test_curseforge_override_limits_are_checked_before_existing_files_are_replaced(
    tmp_path: Path,
):
    game_path = tmp_path / "game"
    existing = game_path / "config" / "existing.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("local value", encoding="utf-8")
    archive_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("overrides/config/existing.txt", "remote value")
        archive.writestr("overrides/config/too-large.txt", "12345")

    service = _service(override_entry_size=4)
    with pytest.raises(ValueError, match="too large"):
        _apply_overrides(
            service,
            archive_path,
            "zip",
            {"overrides": "overrides"},
            game_path,
        )

    assert existing.read_text(encoding="utf-8") == "local value"
    assert not (game_path / "config" / "too-large.txt").exists()


def test_curseforge_override_member_and_total_limits(tmp_path: Path):
    archive_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("overrides/a.txt", "123")
        archive.writestr("overrides/b.txt", "456")

    with pytest.raises(ValueError, match="too many files"):
        _service(override_members=1).plan_overrides(
            archive_path,
            "zip",
            {"overrides": "overrides"},
            tmp_path / "member-limit",
        )

    with pytest.raises(ValueError, match="overrides are too large"):
        _service(
            override_members=2,
            override_total_size=5,
        ).plan_overrides(
            archive_path,
            "zip",
            {"overrides": "overrides"},
            tmp_path / "total-limit",
        )


def test_curseforge_override_path_traversal_is_not_extracted(tmp_path: Path):
    archive_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("overrides/../escaped.txt", "unsafe")
        archive.writestr("overrides/C:/absolute.txt", "unsafe")
        archive.writestr("overrides/config/safe.txt", "safe")

    game_path = tmp_path / "game"
    with pytest.raises(ValueError, match="unsafe override path"):
        _service().plan_overrides(
            archive_path,
            "zip",
            {"overrides": "overrides"},
            game_path,
        )

    assert not (tmp_path / "escaped.txt").exists()
    assert not (game_path / "C:" / "absolute.txt").exists()
    assert not (game_path / "config" / "safe.txt").exists()


def test_curseforge_zip_extraction_failure_keeps_live_files_unchanged(
    monkeypatch,
    tmp_path: Path,
):
    archive_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("overrides/config/existing.txt", "pack value")
        archive.writestr("overrides/config/second.txt", "second value")

    game_path = tmp_path / "game"
    existing = game_path / "config" / "existing.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("user value", encoding="utf-8")
    original_open = zipfile.ZipFile.open

    def fail_second_member(archive, member, *args, **kwargs):
        member_name = member.filename if isinstance(member, zipfile.ZipInfo) else str(member)
        if member_name.endswith("second.txt"):
            raise OSError("simulated extraction failure")
        return original_open(archive, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_second_member)

    with pytest.raises(OSError, match="simulated extraction failure"):
        _apply_overrides(
            _service(),
            archive_path,
            "zip",
            {"overrides": "overrides"},
            game_path,
        )

    assert existing.read_text(encoding="utf-8") == "user value"
    assert not (game_path / "config" / "second.txt").exists()


def test_curseforge_local_override_rejects_target_symlink(
    tmp_path: Path,
):
    source = tmp_path / "source"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path = source / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    game_path = tmp_path / "game"
    game_path.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "settings.txt"
    outside_file.write_text("user value", encoding="utf-8")
    _symlink_or_skip(outside, game_path / "config", target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        _apply_overrides(
            _service(),
            manifest_path,
            "manifest",
            {"overrides": "overrides"},
            game_path,
        )

    assert outside_file.read_text(encoding="utf-8") == "user value"


def test_curseforge_local_override_rejects_source_symlink(tmp_path: Path):
    source = tmp_path / "source"
    overrides = source / "overrides"
    overrides.mkdir(parents=True)
    manifest_path = source / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    _symlink_or_skip(outside, overrides / "linked.txt")

    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        _apply_overrides(
            _service(),
            manifest_path,
            "manifest",
            {"overrides": "overrides"},
            tmp_path / "game",
        )

    assert not (tmp_path / "game" / "linked.txt").exists()


def test_curseforge_metadata_failure_does_not_apply_local_overrides(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = CurseForgeLoader(app=fake_app)
    source = tmp_path / "source"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path = source / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "Pack",
                "version": "1.0.0",
                "minecraft": {"version": "1.20.1", "modLoaders": []},
                "overrides": "overrides",
                "files": [{"projectID": 10, "fileID": 20, "required": True}],
            }
        ),
        encoding="utf-8",
    )
    game_path = tmp_path / "game"
    existing = game_path / "config" / "settings.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("user value", encoding="utf-8")
    saved: list[bool] = []
    version = SimpleNamespace(
        version_id="demo",
        name="Demo",
        options={"curseforge_source_path": str(manifest_path)},
        save=lambda: saved.append(True),
    )

    monkeypatch.setattr(loader, "get_game_path", lambda _version_id: game_path)
    monkeypatch.setattr(loader, "_ensure_instance_idle", lambda *_args: None)
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        loader,
        "_resolve_file_metadata",
        lambda *_args: (_ for _ in ()).throw(ValueError("missing hash")),
    )

    with pytest.raises(ValueError, match="Failed to download 1 files"):
        loader.install(cast(Any, version))

    assert existing.read_text(encoding="utf-8") == "user value"
    assert version.options == {"curseforge_source_path": str(manifest_path)}
    assert saved == []


def test_curseforge_download_failure_leaves_mods_and_overrides_unchanged(
    tmp_path: Path,
):
    content = b"new mod"
    metadata = FileMetadata(
        name="managed.jar",
        size=len(content),
        hash_value=hashlib.sha1(content).hexdigest(),
        hash_algorithm="sha1",
    )

    class FailingDownloader:
        def __init__(self, **_kwargs):
            pass

        def download_files(self, _tasks, **_kwargs):
            return {"success": 0, "failed": 1, "skipped": 0, "errors": ["network error"]}

    source = tmp_path / "source"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path = source / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    game_path = tmp_path / "game"
    existing_config = game_path / "config" / "settings.txt"
    existing_mod = game_path / "mods" / "managed.jar"
    existing_config.parent.mkdir(parents=True)
    existing_mod.parent.mkdir(parents=True)
    existing_config.write_text("user value", encoding="utf-8")
    existing_mod.write_bytes(b"old mod")

    service = _service(downloader_factory=FailingDownloader)
    remote_files, result = service.plan_manifest_files(
        [{"projectID": 10, "fileID": 20, "required": True}],
        game_path,
        metadata_resolver=lambda *_args: metadata,
        download_url=lambda project_id, file_id: f"https://example.com/{project_id}/{file_id}",
    )
    overrides = service.plan_overrides(
        manifest_path,
        "manifest",
        {"overrides": "overrides"},
        game_path,
    )
    result = service.install_content(
        game_path=game_path,
        remote_files=remote_files,
        overrides=overrides,
        source_path=manifest_path,
        source_kind="manifest",
        operation_name="curseforge-test",
        result=result,
    )

    assert result["failed"] == 1
    assert existing_config.read_text(encoding="utf-8") == "user value"
    assert existing_mod.read_bytes() == b"old mod"


def test_curseforge_activation_failure_rolls_back_all_files(
    monkeypatch,
    tmp_path: Path,
):
    content = b"new mod"
    metadata = FileMetadata(
        name="managed.jar",
        size=len(content),
        hash_value=hashlib.sha1(content).hexdigest(),
        hash_algorithm="sha1",
    )

    class SuccessfulDownloader:
        def __init__(self, **_kwargs):
            pass

        def download_files(self, tasks, **_kwargs):
            for task in tasks:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(content)
            return {"success": 1, "failed": 0, "skipped": 0, "errors": []}

    original_activate = FileSyncJournal.activate

    def fail_after_activation(journal: FileSyncJournal):
        original_activate(journal)
        raise RuntimeError("simulated activation failure")

    monkeypatch.setattr(FileSyncJournal, "activate", fail_after_activation)
    source = tmp_path / "source"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path = source / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    game_path = tmp_path / "game"
    existing_config = game_path / "config" / "settings.txt"
    existing_mod = game_path / "mods" / "managed.jar"
    existing_config.parent.mkdir(parents=True)
    existing_mod.parent.mkdir(parents=True)
    existing_config.write_text("user value", encoding="utf-8")
    existing_mod.write_bytes(b"old mod")
    service = _service(downloader_factory=SuccessfulDownloader)
    remote_files, result = service.plan_manifest_files(
        [{"projectID": 10, "fileID": 20, "required": True}],
        game_path,
        metadata_resolver=lambda *_args: metadata,
        download_url=lambda project_id, file_id: f"https://example.com/{project_id}/{file_id}",
    )
    overrides = service.plan_overrides(
        manifest_path,
        "manifest",
        {"overrides": "overrides"},
        game_path,
    )

    with pytest.raises(RuntimeError, match="simulated activation failure"):
        service.install_content(
            game_path=game_path,
            remote_files=remote_files,
            overrides=overrides,
            source_path=manifest_path,
            source_kind="manifest",
            operation_name="curseforge-test",
            result=result,
        )

    assert existing_config.read_text(encoding="utf-8") == "user value"
    assert existing_mod.read_bytes() == b"old mod"


def test_curseforge_transaction_preserves_unmanaged_user_files(tmp_path: Path):
    content = b"new mod"
    metadata = FileMetadata(
        name="managed.jar",
        size=len(content),
        hash_value=hashlib.sha1(content).hexdigest(),
        hash_algorithm="sha1",
    )

    class SuccessfulDownloader:
        def __init__(self, **_kwargs):
            pass

        def download_files(self, tasks, **_kwargs):
            for task in tasks:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(content)
            return {"success": 1, "failed": 0, "skipped": 0, "errors": []}

    source = tmp_path / "source"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path = source / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    game_path = tmp_path / "game"
    user_mod = game_path / "mods" / "my-private-mod.jar"
    user_mod.parent.mkdir(parents=True)
    user_mod.write_bytes(b"user mod")

    service = _service(downloader_factory=SuccessfulDownloader)
    remote_files, result = service.plan_manifest_files(
        [{"projectID": 10, "fileID": 20, "required": True}],
        game_path,
        metadata_resolver=lambda *_args: metadata,
        download_url=lambda project_id, file_id: f"https://example.com/{project_id}/{file_id}",
    )
    overrides = service.plan_overrides(
        manifest_path,
        "manifest",
        {"overrides": "overrides"},
        game_path,
    )
    result = service.install_content(
        game_path=game_path,
        remote_files=remote_files,
        overrides=overrides,
        source_path=manifest_path,
        source_kind="manifest",
        operation_name="curseforge-test",
        result=result,
    )

    assert result["failed"] == 0
    assert (game_path / "mods" / "managed.jar").read_bytes() == content
    assert (game_path / "config" / "settings.txt").read_text(encoding="utf-8") == "pack value"
    assert user_mod.read_bytes() == b"user mod"


def test_curseforge_profile_commit_failure_rolls_back_staged_content(
    tmp_path: Path,
):
    game_path = tmp_path / "game"
    live_file = game_path / "config" / "example.txt"
    live_file.parent.mkdir(parents=True)
    live_file.write_text("old", encoding="utf-8")
    manifest_path = tmp_path / "pack" / "manifest.json"
    source_file = manifest_path.parent / "overrides" / "config" / "example.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("new", encoding="utf-8")
    override = OverrideFile(
        relative_path=PurePosixPath("config/example.txt"),
        size=source_file.stat().st_size,
        source_path=source_file,
    )

    def fail_profile_commit():
        raise OSError("profile is locked")

    with pytest.raises(OSError, match="profile is locked"):
        _service().install_content(
            game_path=game_path,
            remote_files=[],
            overrides=[override],
            source_path=manifest_path,
            source_kind="manifest",
            operation_name="curseforge-install",
            commit_callback=fail_profile_commit,
            commit_key="test:curseforge-profile",
        )

    assert live_file.read_text(encoding="utf-8") == "old"
    journal = FileSyncJournal(game_path).read()
    assert journal is not None
    assert journal["status"] == "rolled_back"
