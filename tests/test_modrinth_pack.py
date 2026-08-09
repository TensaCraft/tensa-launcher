from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import launcher.core.loaders.modrinth as modrinth_loader_module
from launcher.application.file_sync_journal import SYNC_WORK_DIRECTORY, FileSyncJournal
from launcher.application.modrinth_pack import ModrinthPackService


def _symlink_or_skip(
    target: Path,
    link: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")


def _transaction_pack(tmp_path: Path) -> tuple[Path, Path, dict, bytes]:
    game_path = tmp_path / "game"
    mrpack_path = tmp_path / "pack.mrpack"
    indexed_content = b"new indexed content"
    index = {
        "files": [
            {
                "path": "mods/managed.jar",
                "downloads": ["https://example.com/managed.jar"],
                "hashes": {"sha1": hashlib.sha1(indexed_content).hexdigest()},
                "fileSize": len(indexed_content),
            }
        ]
    }
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/config/settings.txt", "new override")
    return game_path, mrpack_path, index, indexed_content


def _prepare_existing_transaction_files(game_path: Path) -> dict[str, Path]:
    paths = {
        "override": game_path / "config" / "settings.txt",
        "indexed": game_path / "mods" / "managed.jar",
        "stale": game_path / "mods" / "stale.jar",
        "unknown": game_path / "mods" / "unknown-local.jar",
    }
    paths["override"].parent.mkdir(parents=True)
    paths["indexed"].parent.mkdir(parents=True)
    paths["override"].write_text("old override", encoding="utf-8")
    paths["indexed"].write_bytes(b"old indexed content")
    paths["stale"].write_bytes(b"old stale content")
    paths["unknown"].write_bytes(b"unknown local content")
    ModrinthPackService._write_managed_metadata(
        game_path / ModrinthPackService.MANAGED_METADATA_PATH,
        [
            PurePosixPath("config/settings.txt"),
            PurePosixPath("mods/managed.jar"),
            PurePosixPath("mods/stale.jar"),
        ],
    )
    return paths


def _install_transaction(
    game_path: Path,
    mrpack_path: Path,
    index: dict,
    indexed_content: bytes,
    *,
    commit_callback=None,
) -> None:

    class SuccessfulDownloader:
        def __init__(self, **_kwargs) -> None:
            pass

        def download_files(self, tasks, **_kwargs):
            for task in tasks:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(indexed_content)
            return {
                "success": len(tasks),
                "failed": 0,
                "skipped": 0,
                "errors": [],
            }

    ModrinthPackService(
        downloader_factory=SuccessfulDownloader,
    ).install_content(
        mrpack_path=mrpack_path,
        index=index,
        game_path=game_path,
        commit_callback=commit_callback,
    )


def _journal_status(game_path: Path) -> str:
    payload = FileSyncJournal(game_path).read()
    assert payload is not None
    return str(payload.get("status") or "")


def test_modrinth_pack_reads_index_and_resolves_loader(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr(
            "modrinth.index.json",
            json.dumps(
                {
                    "dependencies": {
                        "minecraft": "1.20.1",
                        "fabric-loader": "0.16.0",
                    },
                    "files": [],
                }
            ),
        )

    service = ModrinthPackService()
    index = service.read_index(mrpack_path)
    mc_version, loader_id, loader_version = service.resolve_loader(index)

    assert mc_version == "1.20.1"
    assert loader_id == "fabric-loader"
    assert loader_version == "0.16.0"


def test_modrinth_pack_builds_download_tasks(tmp_path: Path):
    service = ModrinthPackService()
    tasks = service._build_download_tasks(
        {
            "files": [
                {
                    "path": "mods/example.jar",
                    "downloads": ["https://example.com/mod.jar"],
                    "hashes": {"sha1": "abc"},
                    "fileSize": 123,
                }
            ]
        },
        tmp_path,
    )

    assert len(tasks) == 1
    assert tasks[0].destination == tmp_path / "mods" / "example.jar"


def test_modrinth_pack_installs_overrides_transactionally(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    game_path = tmp_path / "game"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/config/example.txt", "value")

    ModrinthPackService().install_content(
        mrpack_path=mrpack_path,
        index={"files": []},
        game_path=game_path,
    )

    assert (game_path / "config" / "example.txt").read_text(encoding="utf-8") == "value"


def test_modrinth_pack_rejects_override_outside_game_directory(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    game_path = tmp_path / "game"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/../escaped.txt", "unsafe")

    with pytest.raises(ValueError, match="unsafe path"):
        ModrinthPackService().install_content(
            mrpack_path=mrpack_path,
            index={"files": []},
            game_path=game_path,
        )

    assert not (tmp_path / "escaped.txt").exists()


def test_modrinth_pack_rejects_zip_symlink_override(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    link_info = zipfile.ZipInfo("overrides/config/linked.txt")
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr(link_info, "../outside.txt")

    with pytest.raises(ValueError, match="symbolic link"):
        ModrinthPackService._preflight_overrides(
            mrpack_path,
            tmp_path / "game",
        )


def test_modrinth_pack_rejects_symlinked_live_destination(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/config/settings.txt", "pack value")

    game_path = tmp_path / "game"
    game_path.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "settings.txt"
    outside_file.write_text("local value", encoding="utf-8")
    _symlink_or_skip(outside, game_path / "config", target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        ModrinthPackService._preflight_overrides(mrpack_path, game_path)

    assert outside_file.read_text(encoding="utf-8") == "local value"


def test_modrinth_pack_override_limit_is_checked_before_replacement(
    monkeypatch,
    tmp_path: Path,
):
    mrpack_path = tmp_path / "pack.mrpack"
    game_path = tmp_path / "game"
    existing = game_path / "config" / "settings.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("local value", encoding="utf-8")
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/config/settings.txt", "pack value")
        archive.writestr("overrides/config/too-large.txt", "12345")

    monkeypatch.setattr(ModrinthPackService, "MAX_OVERRIDE_ENTRY_SIZE", 4)

    with pytest.raises(ValueError, match="too large"):
        ModrinthPackService().install_content(
            mrpack_path=mrpack_path,
            index={"files": []},
            game_path=game_path,
        )

    assert existing.read_text(encoding="utf-8") == "local value"
    assert not (game_path / "config" / "too-large.txt").exists()


def test_modrinth_pack_rejects_duplicate_override_and_indexed_path(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    game_path = tmp_path / "game"
    existing = game_path / "mods" / "example.jar"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"local value")
    content = b"indexed value"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/mods/Example.jar", "override value")

    with pytest.raises(ValueError, match="duplicate managed file path"):
        ModrinthPackService().install_content(
            mrpack_path=mrpack_path,
            index={
                "files": [
                    {
                        "path": "mods/example.jar",
                        "downloads": ["https://example.com/example.jar"],
                        "hashes": {"sha1": hashlib.sha1(content).hexdigest()},
                        "fileSize": len(content),
                    }
                ]
            },
            game_path=game_path,
        )

    assert existing.read_bytes() == b"local value"


@pytest.mark.parametrize("unsafe_path", ["../escaped.jar", "/absolute.jar", "C:/escaped.jar", "..\\escaped.jar"])
def test_modrinth_pack_rejects_download_outside_game_directory(tmp_path: Path, unsafe_path: str):
    with pytest.raises(ValueError, match="unsafe path"):
        ModrinthPackService._build_download_tasks(
            {
                "files": [
                    {
                        "path": unsafe_path,
                        "downloads": ["https://example.com/mod.jar"],
                    }
                ]
            },
            tmp_path / "game",
        )


def test_modrinth_pack_prefers_strongest_supported_hash(tmp_path: Path):
    tasks = ModrinthPackService._build_download_tasks(
        {
            "files": [
                {
                    "path": "mods/example.jar",
                    "downloads": ["https://example.com/mod.jar"],
                    "hashes": {"sha1": "sha1-value", "sha512": "sha512-value"},
                    "fileSize": 123,
                }
            ]
        },
        tmp_path,
    )

    assert tasks[0].expected_hash_algorithm == "sha512"
    assert tasks[0].expected_hash == "sha512-value"


def test_modrinth_pack_rejects_file_without_positive_declared_size(tmp_path: Path):
    with pytest.raises(ValueError, match="positive declared size"):
        ModrinthPackService._build_download_tasks(
            {
                "files": [
                    {
                        "path": "mods/example.jar",
                        "downloads": ["https://example.com/mod.jar"],
                        "hashes": {"sha1": "abc"},
                    }
                ]
            },
            tmp_path,
        )


def test_modrinth_pack_rejects_unverified_or_insecure_downloads(tmp_path: Path):
    with pytest.raises(ValueError, match="missing a supported hash"):
        ModrinthPackService._build_download_tasks(
            {"files": [{"path": "mods/example.jar", "downloads": ["https://example.com/mod.jar"]}]},
            tmp_path,
        )

    with pytest.raises(ValueError, match="unsafe download URL"):
        ModrinthPackService._build_download_tasks(
            {
                "files": [
                    {
                        "path": "mods/example.jar",
                        "downloads": ["http://example.com/mod.jar"],
                        "hashes": {"sha1": "abc"},
                        "fileSize": 123,
                    }
                ]
            },
            tmp_path,
        )


def test_modrinth_pack_skips_server_only_files(tmp_path: Path):
    tasks = ModrinthPackService._build_download_tasks(
        {
            "files": [
                {
                    "path": "mods/server-only.jar",
                    "downloads": [],
                    "hashes": {"sha1": "abc"},
                    "env": {"client": "unsupported", "server": "required"},
                }
            ]
        },
        tmp_path,
    )

    assert tasks == []


def test_modrinth_pack_download_failure_rolls_back_staging(tmp_path: Path):
    captured = {}

    class FailedDownloader:
        def __init__(self, **_kwargs) -> None:
            pass

        def download_files(self, _tasks, **kwargs):
            captured.update(kwargs)
            return {
                "success": 0,
                "failed": 1,
                "skipped": 0,
                "errors": ["example.jar: checksum mismatch"],
            }

    game_path, mrpack_path, index, _indexed_content = _transaction_pack(tmp_path)

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ModrinthPackService(
            downloader_factory=FailedDownloader,
        ).install_content(
            mrpack_path=mrpack_path,
            index=index,
            game_path=game_path,
        )

    assert captured["skip_existing"] is False
    assert captured["verify_existing_hash"] is True
    assert "verify_existing_sha1" not in captured
    assert not (game_path / "config" / "settings.txt").exists()
    assert not (game_path / "mods" / "managed.jar").exists()
    assert _journal_status(game_path) == "rolled_back"


def test_modrinth_pack_rejects_corrupted_staged_download(tmp_path: Path):
    game_path, mrpack_path, index, indexed_content = _transaction_pack(tmp_path)

    class CorruptingDownloader:
        def __init__(self, **_kwargs) -> None:
            pass

        def download_files(self, tasks, **_kwargs):
            corrupted = bytes([indexed_content[0] ^ 0xFF]) + indexed_content[1:]
            for task in tasks:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(corrupted)
            return {
                "success": len(tasks),
                "failed": 0,
                "skipped": 0,
                "errors": [],
            }

    with pytest.raises(ValueError, match="checksum mismatch"):
        ModrinthPackService(
            downloader_factory=CorruptingDownloader,
        ).install_content(
            mrpack_path=mrpack_path,
            index=index,
            game_path=game_path,
        )

    assert not (game_path / "config" / "settings.txt").exists()
    assert not (game_path / "mods" / "managed.jar").exists()
    assert _journal_status(game_path) == "rolled_back"


def test_modrinth_loader_rejects_pack_without_positive_declared_size(
    fake_app,
    monkeypatch,
):
    loader = modrinth_loader_module.ModrinthLoader(app=fake_app)
    version = SimpleNamespace(
        id="project",
        version="release",
        version_id="demo",
        name="Demo",
    )
    monkeypatch.setattr(
        modrinth_loader_module.ModrinthAPI,
        "get_version",
        lambda *_args: {
            "files": [
                {
                    "filename": "demo.mrpack",
                    "url": "https://example.com/demo.mrpack",
                    "hashes": {"sha1": "abc"},
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="positive declared size"):
        loader._install_locked(version)  # type: ignore[arg-type]


def test_modrinth_loader_restores_persisted_profile_when_transaction_commit_fails(
    fake_app,
    monkeypatch,
):
    loader = modrinth_loader_module.ModrinthLoader(app=fake_app)
    saved_paths: list[str] = []

    class Version:
        id = "project"
        version = "release"
        version_id = "demo"
        name = "Demo"
        path = "old-path"

        def save(self):
            saved_paths.append(self.path)

    version = Version()
    monkeypatch.setattr(
        modrinth_loader_module.ModrinthAPI,
        "get_version",
        lambda *_args: {
            "files": [
                {
                    "filename": "demo.mrpack",
                    "url": "https://example.com/demo.mrpack",
                    "size": 1,
                    "hashes": {"sha1": "abc"},
                }
            ]
        },
    )

    class SuccessfulDownloader:
        def __init__(self, **_kwargs):
            pass

        def download_files(self, _tasks, **_kwargs):
            return {"success": 1, "failed": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(modrinth_loader_module, "AsyncDownloader", SuccessfulDownloader)
    monkeypatch.setattr(
        loader.pack_service,
        "read_index",
        lambda _path: {"dependencies": {"minecraft": "1.21.1"}, "files": []},
    )

    def update_profile(profile, *_args):
        profile.path = "new-path"
        profile.save()

    def fail_after_profile_commit(*_args, commit_callback=None, **_kwargs):
        assert commit_callback is not None
        commit_callback()
        raise OSError("journal commit failed")

    monkeypatch.setattr(loader, "_update_version_entity", update_profile)
    monkeypatch.setattr(
        loader.pack_service,
        "install_content",
        fail_after_profile_commit,
    )

    with pytest.raises(OSError, match="journal commit failed"):
        loader._install_locked(version)  # type: ignore[arg-type]

    assert version.path == "old-path"
    assert saved_paths == ["new-path", "old-path"]


def test_modrinth_pack_failure_after_stage_keeps_live_files(
    monkeypatch,
    tmp_path: Path,
):
    game_path, mrpack_path, index, indexed_content = _transaction_pack(tmp_path)
    paths = _prepare_existing_transaction_files(game_path)
    original_metadata = (
        game_path / ModrinthPackService.MANAGED_METADATA_PATH
    ).read_bytes()

    def fail_after_stage(_journal: FileSyncJournal) -> None:
        raise RuntimeError("simulated failure after stage")

    monkeypatch.setattr(FileSyncJournal, "mark_prepared", fail_after_stage)

    with pytest.raises(RuntimeError, match="failure after stage"):
        _install_transaction(
            game_path,
            mrpack_path,
            index,
            indexed_content,
        )

    assert paths["override"].read_text(encoding="utf-8") == "old override"
    assert paths["indexed"].read_bytes() == b"old indexed content"
    assert paths["stale"].read_bytes() == b"old stale content"
    assert (
        game_path / ModrinthPackService.MANAGED_METADATA_PATH
    ).read_bytes() == original_metadata
    assert not (game_path / SYNC_WORK_DIRECTORY).exists()
    assert _journal_status(game_path) == "rolled_back"


def test_modrinth_pack_activation_failure_rolls_back_all_managed_files(
    monkeypatch,
    tmp_path: Path,
):
    game_path, mrpack_path, index, indexed_content = _transaction_pack(tmp_path)
    paths = _prepare_existing_transaction_files(game_path)
    original_metadata = (
        game_path / ModrinthPackService.MANAGED_METADATA_PATH
    ).read_bytes()
    original_activate = FileSyncJournal.activate

    def fail_after_activation(journal: FileSyncJournal) -> None:
        original_activate(journal)
        raise RuntimeError("simulated activation failure")

    monkeypatch.setattr(FileSyncJournal, "activate", fail_after_activation)

    with pytest.raises(RuntimeError, match="activation failure"):
        _install_transaction(
            game_path,
            mrpack_path,
            index,
            indexed_content,
        )

    assert paths["override"].read_text(encoding="utf-8") == "old override"
    assert paths["indexed"].read_bytes() == b"old indexed content"
    assert paths["stale"].read_bytes() == b"old stale content"
    assert (
        game_path / ModrinthPackService.MANAGED_METADATA_PATH
    ).read_bytes() == original_metadata
    assert _journal_status(game_path) == "rolled_back"


def test_modrinth_pack_profile_save_failure_rolls_back_activated_files(
    tmp_path: Path,
):
    game_path, mrpack_path, index, indexed_content = _transaction_pack(tmp_path)
    paths = _prepare_existing_transaction_files(game_path)
    observed_statuses: list[str] = []

    def fail_profile_save() -> None:
        observed_statuses.append(_journal_status(game_path))
        assert (game_path / SYNC_WORK_DIRECTORY).is_dir()
        raise OSError("profile is locked")

    with pytest.raises(OSError, match="profile is locked"):
        _install_transaction(
            game_path,
            mrpack_path,
            index,
            indexed_content,
            commit_callback=fail_profile_save,
        )

    assert paths["override"].read_text(encoding="utf-8") == "old override"
    assert paths["indexed"].read_bytes() == b"old indexed content"
    assert paths["stale"].read_bytes() == b"old stale content"
    assert observed_statuses == ["committing"]
    assert _journal_status(game_path) == "rolled_back"


def test_modrinth_pack_transaction_removes_only_metadata_stale_files(
    tmp_path: Path,
):
    game_path, mrpack_path, index, indexed_content = _transaction_pack(tmp_path)
    paths = _prepare_existing_transaction_files(game_path)

    _install_transaction(
        game_path,
        mrpack_path,
        index,
        indexed_content,
    )

    assert paths["override"].read_text(encoding="utf-8") == "new override"
    assert paths["indexed"].read_bytes() == indexed_content
    assert not paths["stale"].exists()
    assert paths["unknown"].read_bytes() == b"unknown local content"
    assert ModrinthPackService._read_managed_paths(game_path) == [
        PurePosixPath("config/settings.txt"),
        PurePosixPath("mods/managed.jar"),
    ]
    assert _journal_status(game_path) == "complete"


def test_modrinth_loader_rejects_concurrent_instance_operation(fake_app):
    loader = modrinth_loader_module.ModrinthLoader(app=fake_app)
    version = SimpleNamespace(
        version_id="demo",
        name="Demo",
    )
    game_path = loader.get_game_path(version.version_id)

    with fake_app.instance_operations.operation(game_path, "launch"):
        with pytest.raises(RuntimeError, match="instance_operation_busy"):
            loader.install(version)  # type: ignore[arg-type]
