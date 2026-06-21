from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.application.world_backups import WorldBackupService
from tests.conftest import DummyConfig, DummyLogger


def _version(game_dir: Path):
    return SimpleNamespace(
        name="Aeronautics",
        version_id="aeronautics",
        id="aeronautics",
        path=str(game_dir),
    )


def _world(game_dir: Path, folder: str = "New World") -> Path:
    world_dir = game_dir / "saves" / folder
    (world_dir / "region").mkdir(parents=True)
    (world_dir / "level.dat").write_bytes(b"level")
    (world_dir / "region" / "r.0.0.mca").write_bytes(b"region")
    (world_dir / "session.lock").write_bytes(b"locked")
    return world_dir


def _service(tmp_path: Path, *, keep_count: int = 3) -> WorldBackupService:
    config = DummyConfig(
        {
            "world_backups_enabled": "yes",
            "world_backups_dir": str(tmp_path / "backup-root"),
            "world_backups_keep_count": keep_count,
        }
    )
    return WorldBackupService(tmp_path / "minecraft", config, DummyLogger())


def test_world_backup_creates_zip_metadata_and_skips_session_lock(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir, "Новий світ")
    service = _service(tmp_path)

    result = service.auto_backup_changed_worlds(_version(game_dir))

    assert result.created == 1
    backup = service.scan_backups(_version(game_dir), world_dir)[0]
    assert backup.kind == "auto"
    assert backup.world_folder == "Новий світ"
    assert backup.path.exists()
    assert backup.metadata_path.exists()

    metadata = json.loads(backup.metadata_path.read_text(encoding="utf-8"))
    assert metadata["version_id"] == "aeronautics"
    assert metadata["world_folder"] == "Новий світ"
    assert metadata["source_path"] == str(world_dir)

    with zipfile.ZipFile(backup.path) as archive:
        assert "level.dat" in archive.namelist()
        assert "region/r.0.0.mca" in archive.namelist()
        assert "session.lock" not in archive.namelist()


def test_world_backup_skips_unchanged_world_when_latest_auto_backup_is_current(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)

    first = service.auto_backup_changed_worlds(version)
    second = service.auto_backup_changed_worlds(version)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    assert len(service.scan_backups(version, world_dir)) == 1


def test_world_backup_retention_keeps_latest_auto_backups_per_world(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, keep_count=2)
    version = _version(game_dir)

    for index in range(4):
        os.utime(world_dir / "level.dat", (2_000_000_000 + index, 2_000_000_000 + index))
        service.auto_backup_changed_worlds(version)

    backups = service.scan_backups(version, world_dir)

    assert len(backups) == 2
    assert all(backup.kind == "auto" for backup in backups)


def test_world_backup_restore_replaces_world_and_keeps_failed_restore_recoverable(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    service.create_backup(version, world_dir, kind="manual")
    backup = service.scan_backups(version, world_dir)[0]

    (world_dir / "level.dat").write_bytes(b"changed")
    service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"level"


def test_world_backup_restore_uses_current_world_path_after_game_dir_moves(tmp_path: Path):
    old_game_dir = tmp_path / "old-minecraft" / "games" / "aeronautics"
    old_world_dir = _world(old_game_dir)
    service = _service(tmp_path)
    version = _version(old_game_dir)
    service.create_backup(version, old_world_dir, kind="manual")

    new_game_dir = tmp_path / "new-minecraft" / "games" / "aeronautics"
    new_world_dir = _world(new_game_dir)
    (new_world_dir / "level.dat").write_bytes(b"changed")
    moved_version = _version(new_game_dir)

    backup = service.scan_backups(moved_version, new_world_dir)[0]
    service.restore_backup(backup)

    assert (new_world_dir / "level.dat").read_bytes() == b"level"
    assert old_world_dir.exists()


def test_world_backup_restore_rejects_unsafe_archive_paths(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup_dir = service.version_backup_dir(version) / "New World"
    backup_dir.mkdir(parents=True)
    zip_path = backup_dir / "unsafe.zip"
    metadata_path = zip_path.with_suffix(zip_path.suffix + ".json")
    metadata_path.write_text(
        json.dumps(
            {
                "version_id": version.version_id,
                "world_folder": world_dir.name,
                "world_name": world_dir.name,
                "source_path": str(world_dir),
                "created_at": "2026-05-22T00:00:00+00:00",
                "created_timestamp": 1,
                "kind": "manual",
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../escape.txt", "bad")

    with pytest.raises(ValueError):
        service.restore_backup(zip_path)

    assert not (world_dir.parent / "escape.txt").exists()
    assert (world_dir / "level.dat").read_bytes() == b"level"


def test_world_backups_disabled_does_not_scan_or_create(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    _world(game_dir)
    config = DummyConfig({"world_backups_enabled": "no"})
    service = WorldBackupService(tmp_path / "minecraft", config, DummyLogger())

    result = service.auto_backup_changed_worlds(_version(game_dir))

    assert result.created == 0
    assert result.skipped == 0
