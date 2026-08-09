from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.application.instance_operations import InstanceOperationCoordinator
from launcher.application.world_backups import RESTORE_JOURNAL_FILE, WorldBackupService
from launcher.core.game import Game
from tests.conftest import DummyConfig, DummyLogger


class _SimulatedCrash(BaseException):
    pass


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


def _service(
    tmp_path: Path,
    *,
    keep_count: int = 3,
    instance_operations: InstanceOperationCoordinator | None = None,
) -> WorldBackupService:
    config = DummyConfig(
        {
            "world_backups_enabled": "yes",
            "world_backups_dir": str(tmp_path / "backup-root"),
            "world_backups_keep_count": keep_count,
        }
    )
    return WorldBackupService(
        tmp_path / "minecraft",
        config,
        DummyLogger(),
        translator=lambda key, **_kwargs: key,
        instance_operations=instance_operations,
    )


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


def test_manual_world_backup_rejects_concurrent_instance_operation(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, instance_operations=coordinator)
    version = _version(game_dir)

    with coordinator.operation(game_dir, "launch"):
        with pytest.raises(RuntimeError, match="instance_operation_busy"):
            service.create_backup(version, world_dir, kind="manual")

    assert service.scan_backups(version, world_dir) == []


def test_manual_world_backup_rechecks_running_game_under_instance_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    coordinator = InstanceOperationCoordinator()
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, instance_operations=coordinator)
    version = _version(game_dir)
    monkeypatch.setattr(
        Game,
        "is_game_dir_active",
        classmethod(lambda cls, path: Path(path) == game_dir),
    )

    with pytest.raises(RuntimeError, match="instance_game_running"):
        service.create_backup(version, world_dir, kind="manual")

    assert coordinator.active_kind(game_dir) is None
    assert service.scan_backups(version, world_dir) == []


def test_prelaunch_auto_backup_accepts_explicit_borrowed_lease(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, instance_operations=coordinator)
    version = _version(game_dir)

    with coordinator.operation(game_dir, "launch") as lease:
        result = service.auto_backup_changed_worlds(version, lease=lease)

        assert result.created == 1
        assert lease.active is True
        assert coordinator.active_kind(game_dir) == "launch"

    assert len(service.scan_backups(version, world_dir)) == 1
    assert coordinator.active_kind(game_dir) is None


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


def test_world_backup_restore_rejects_concurrent_instance_operation(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, instance_operations=coordinator)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")

    with coordinator.operation(game_dir, "launch"):
        with pytest.raises(RuntimeError, match="instance_operation_busy"):
            service.restore_backup(backup)


def test_world_backup_restore_rechecks_running_game_under_instance_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    coordinator = InstanceOperationCoordinator()
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path, instance_operations=coordinator)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    monkeypatch.setattr(
        Game,
        "is_game_dir_active",
        classmethod(lambda cls, path: Path(path) == game_dir),
    )

    with pytest.raises(RuntimeError, match="instance_game_running"):
        service.restore_backup(backup)


def test_world_backup_restore_rejects_empty_archive_before_replacing_world(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    (world_dir / "level.dat").write_bytes(b"current")
    with zipfile.ZipFile(backup.path, "w"):
        pass

    with pytest.raises(ValueError, match="level.dat"):
        service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"current"


def test_world_backup_restore_rejects_archive_without_level_dat(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    (world_dir / "level.dat").write_bytes(b"current")
    with zipfile.ZipFile(backup.path, "w") as archive:
        archive.writestr("region/r.0.0.mca", b"region")

    with pytest.raises(ValueError, match="level.dat"):
        service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"current"


def test_world_backup_restore_rolls_back_when_staged_world_cannot_be_activated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    (world_dir / "level.dat").write_bytes(b"current")
    original_rename = Path.rename

    def fail_staged_activation(path: Path, target: Path):
        if (
            path.name.startswith(f".{world_dir.name}.restore-")
            and not path.name.endswith(".previous")
            and Path(target) == world_dir
        ):
            raise OSError("simulated activation failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staged_activation)

    with pytest.raises(OSError, match="simulated activation failure"):
        service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"current"
    assert not list(world_dir.parent.glob(f".{world_dir.name}.restore-*"))


def test_world_backup_restore_recovers_crash_before_original_moved_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    (world_dir / "level.dat").write_bytes(b"current")
    original_rename = Path.rename

    def crash_after_moving_current(path: Path, target: Path):
        if path == world_dir and Path(target).name.endswith(".previous"):
            original_rename(path, target)
            raise _SimulatedCrash("simulated process crash")
        return original_rename(path, target)

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(Path, "rename", crash_after_moving_current)
        with pytest.raises(_SimulatedCrash):
            service.restore_backup(backup)

    journal_path = world_dir.parent / RESTORE_JOURNAL_FILE
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "prepared"
    assert not world_dir.exists()
    assert (world_dir.parent / journal["staged_name"]).exists()
    assert (world_dir.parent / journal["previous_name"]).exists()

    recovered_service = _service(tmp_path)

    def fail_new_extract(*_args):
        raise ValueError("stop after recovery")

    monkeypatch.setattr(recovered_service, "_extract_zip_safely", fail_new_extract)
    with pytest.raises(ValueError, match="stop after recovery"):
        recovered_service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"current"
    assert not journal_path.exists()
    assert not list(world_dir.parent.glob(f".{world_dir.name}.restore-*"))


def test_world_backup_restore_recovers_crash_before_staged_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    (world_dir / "level.dat").write_bytes(b"current")
    original_rename = Path.rename

    def crash_before_activating_staged(path: Path, target: Path):
        if (
            path.name.startswith(f".{world_dir.name}.restore-")
            and not path.name.endswith(".previous")
            and Path(target) == world_dir
        ):
            raise _SimulatedCrash("simulated process crash")
        return original_rename(path, target)

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(Path, "rename", crash_before_activating_staged)
        with pytest.raises(_SimulatedCrash):
            service.restore_backup(backup)

    journal_path = world_dir.parent / RESTORE_JOURNAL_FILE
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "original_moved"
    assert not world_dir.exists()
    assert (world_dir.parent / journal["staged_name"]).exists()
    assert (world_dir.parent / journal["previous_name"]).exists()

    recovered_service = _service(tmp_path)

    def fail_new_extract(*_args):
        raise ValueError("stop after recovery")

    monkeypatch.setattr(recovered_service, "_extract_zip_safely", fail_new_extract)
    with pytest.raises(ValueError, match="stop after recovery"):
        recovered_service.restore_backup(backup)

    assert (world_dir / "level.dat").read_bytes() == b"current"
    assert not journal_path.exists()
    assert not list(world_dir.parent.glob(f".{world_dir.name}.restore-*"))


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

    backup = service._backup_info_from_files(
        zip_path,
        metadata_path,
        restore_path=world_dir,
    )
    with pytest.raises(ValueError):
        service.restore_backup(backup)

    assert not (world_dir.parent / "escape.txt").exists()
    assert (world_dir / "level.dat").read_bytes() == b"level"


def test_world_backup_restore_does_not_trust_source_path_from_raw_metadata(
    tmp_path: Path,
):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    service = _service(tmp_path)
    version = _version(game_dir)
    backup = service.create_backup(version, world_dir, kind="manual")
    outside_world = _world(tmp_path / "outside-instance")
    (outside_world / "level.dat").write_bytes(b"outside")
    metadata = json.loads(backup.metadata_path.read_text(encoding="utf-8"))
    metadata["source_path"] = str(outside_world)
    backup.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="selected for a known launcher world"):
        service.restore_backup(backup.path)

    assert (outside_world / "level.dat").read_bytes() == b"outside"


def test_world_backup_rejects_output_inside_source_world(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    world_dir = _world(game_dir)
    nested_backup_root = world_dir / "backup-root"
    config = DummyConfig(
        {
            "world_backups_enabled": "yes",
            "world_backups_dir": str(nested_backup_root),
        }
    )
    service = WorldBackupService(tmp_path / "minecraft", config, DummyLogger())

    with pytest.raises(ValueError, match="inside the source world"):
        service.create_backup(_version(game_dir), world_dir)

    assert not nested_backup_root.exists()


def test_world_backups_disabled_does_not_scan_or_create(tmp_path: Path):
    game_dir = tmp_path / "minecraft" / "games" / "aeronautics"
    _world(game_dir)
    config = DummyConfig({"world_backups_enabled": "no"})
    service = WorldBackupService(tmp_path / "minecraft", config, DummyLogger())

    result = service.auto_backup_changed_worlds(_version(game_dir))

    assert result.created == 0
    assert result.skipped == 0
