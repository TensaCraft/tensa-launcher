from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationCoordinator,
    InstanceOperationLease,
)
from launcher.application.storage_preflight import (
    StorageRequest,
    ensure_storage_available,
)
from launcher.storage.atomic import atomic_write_json

BACKUP_SCHEMA = 1
DEFAULT_KEEP_COUNT = 3
SESSION_LOCK = "session.lock"
METADATA_SUFFIX = ".json"
RESTORE_JOURNAL_FILE = ".tensalauncher-world-restore.json"
RESTORE_JOURNAL_SCHEMA = 1
RESTORE_PHASES = {"prepared", "original_moved", "activated"}


@dataclass(frozen=True, slots=True)
class WorldInfo:
    name: str
    folder: str
    path: Path
    size: int
    modified_at: float
    backup_count: int = 0


@dataclass(frozen=True, slots=True)
class WorldBackupInfo:
    path: Path
    metadata_path: Path
    world_folder: str
    world_name: str
    version_id: str
    created_at: str
    created_timestamp: float
    kind: str
    size: int
    source_path: Path | None = None
    restore_path: Path | None = None


@dataclass(frozen=True, slots=True)
class WorldBackupResult:
    created: int
    skipped: int
    failed: int


class WorldBackupService:
    def __init__(
        self,
        minecraft_dir: str | Path,
        config,
        logger: Any,
        translator=None,
        *,
        instance_operations: InstanceOperationCoordinator | None = None,
    ) -> None:
        self.minecraft_dir = Path(minecraft_dir)
        self.config = config
        self.logger = logger
        self._translator = translator
        self._instance_operations = instance_operations

    def enabled(self) -> bool:
        return str(self.config.get("world_backups_enabled", "no")).lower() == "yes"

    def keep_count(self) -> int:
        try:
            value = int(self.config.get("world_backups_keep_count", DEFAULT_KEEP_COUNT))
        except (TypeError, ValueError):
            return DEFAULT_KEEP_COUNT
        return max(1, value)

    def backup_root(self) -> Path:
        raw = str(self.config.get("world_backups_dir", "") or "").strip()
        if raw:
            return Path(raw).expanduser()
        return self.minecraft_dir / "backups" / "worlds"

    def version_game_dir(self, version: Any) -> Path:
        raw = Path(str(getattr(version, "path", "") or getattr(version, "version_id", "") or ""))
        if raw.is_absolute():
            return raw
        return self.minecraft_dir / raw

    def saves_dir(self, version: Any) -> Path:
        return self.version_game_dir(version) / "saves"

    def scan_worlds(self, version: Any) -> list[WorldInfo]:
        saves = self.saves_dir(version)
        if not saves.exists() or not saves.is_dir():
            return []
        worlds: list[WorldInfo] = []
        for world_dir in sorted((path for path in saves.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
            if not (world_dir / "level.dat").is_file():
                continue
            backups = self.scan_backups(version, world_dir)
            worlds.append(
                WorldInfo(
                    name=world_dir.name,
                    folder=world_dir.name,
                    path=world_dir,
                    size=self._directory_size(world_dir),
                    modified_at=self._world_modified_at(world_dir),
                    backup_count=len(backups),
                )
            )
        return worlds

    def auto_backup_changed_worlds(
        self,
        version: Any,
        operation=None,
        *,
        lease: InstanceOperationLease | None = None,
    ) -> WorldBackupResult:
        if not self.enabled():
            return WorldBackupResult(created=0, skipped=0, failed=0)

        instance_path = self.version_game_dir(version)
        try:
            if self._instance_operations is not None:
                return self._instance_operations.execute(
                    instance_path,
                    "world_backup",
                    self._auto_backup_changed_worlds_locked,
                    version,
                    operation,
                    lease=lease,
                )
            return self._auto_backup_changed_worlds_locked(version, operation)
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self._translate(
                    "instance_operation_busy",
                    version=self._version_label(version, instance_path.name),
                )
            ) from exc

    def _auto_backup_changed_worlds_locked(self, version: Any, operation=None) -> WorldBackupResult:
        worlds = self.scan_worlds(version)
        if not worlds:
            return WorldBackupResult(created=0, skipped=0, failed=0)

        created = 0
        skipped = 0
        failed = 0
        total = len(worlds)
        for index, world in enumerate(worlds, start=1):
            try:
                if not self._needs_auto_backup(version, world.path):
                    skipped += 1
                    continue
                if operation is not None:
                    operation.update(
                        self._translate("world_backup_progress", world=world.name),
                        progress=index - 1,
                        total=total,
                    )
                self._create_backup_locked(version, world.path, kind="auto")
                created += 1
                if operation is not None:
                    operation.update(
                        self._translate("world_backup_progress", world=world.name),
                        progress=index,
                        total=total,
                    )
            except Exception as exc:
                failed += 1
                self._warning(f"Failed to create world backup for {world.path}: {exc!r}")

        return WorldBackupResult(created=created, skipped=skipped, failed=failed)

    def create_backup(
        self,
        version: Any,
        world_path: str | Path,
        *,
        kind: str = "manual",
        lease: InstanceOperationLease | None = None,
    ) -> WorldBackupInfo:
        world_dir = Path(world_path)
        instance_path = world_dir.parent.parent
        try:
            if self._instance_operations is not None:
                return self._instance_operations.execute(
                    instance_path,
                    "world_backup",
                    self._create_backup_locked,
                    version,
                    world_dir,
                    kind=kind,
                    lease=lease,
                )
            return self._create_backup_locked(version, world_dir, kind=kind)
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self._translate(
                    "instance_operation_busy",
                    version=self._version_label(version, instance_path.name),
                )
            ) from exc

    def _create_backup_locked(
        self,
        version: Any,
        world_dir: Path,
        *,
        kind: str,
    ) -> WorldBackupInfo:
        instance_path = world_dir.parent.parent
        self._ensure_instance_idle(
            instance_path,
            self._version_label(version, instance_path.name),
        )
        if not world_dir.exists() or not world_dir.is_dir():
            raise FileNotFoundError(f"World directory not found: {world_dir}")
        if not (world_dir / "level.dat").is_file():
            raise ValueError(f"World directory does not contain level.dat: {world_dir}")

        target_dir = self._backup_dir_for_world(version, world_dir)
        self._ensure_backup_outside_world(world_dir, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc)
        timestamp_name = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        zip_name = f"[Auto] {timestamp_name}.zip" if kind == "auto" else f"{timestamp_name}.zip"
        final_path = self._unique_path(target_dir / zip_name)
        metadata_path = final_path.with_suffix(final_path.suffix + METADATA_SUFFIX)
        ensure_storage_available(
            [
                StorageRequest(
                    target=final_path,
                    required_bytes=self._directory_size(world_dir),
                    label=f"world backup {world_dir.name}",
                )
            ]
        )

        with tempfile.NamedTemporaryFile(prefix=final_path.name, suffix=".tmp", dir=target_dir, delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            self._create_zip(world_dir, temp_path)
            final_path.unlink(missing_ok=True)
            shutil.move(str(temp_path), str(final_path))
            metadata = self._metadata_for_backup(version, world_dir, final_path, timestamp, kind)
            atomic_write_json(
                metadata_path,
                metadata,
                ensure_ascii=False,
                indent=2,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise

        if kind == "auto":
            self._prune_auto_backups(version, world_dir)
        return self._backup_info_from_files(final_path, metadata_path, restore_path=world_dir)

    def scan_backups(self, version: Any, world_path: str | Path) -> list[WorldBackupInfo]:
        backup_dir = self._backup_dir_for_world(version, Path(world_path))
        if not backup_dir.exists() or not backup_dir.is_dir():
            return []
        backups: list[WorldBackupInfo] = []
        for zip_path in backup_dir.glob("*.zip"):
            metadata_path = zip_path.with_suffix(zip_path.suffix + METADATA_SUFFIX)
            if not metadata_path.exists():
                continue
            try:
                backups.append(self._backup_info_from_files(zip_path, metadata_path, restore_path=Path(world_path)))
            except Exception as exc:
                self._warning(f"Skipping invalid world backup metadata {metadata_path}: {exc!r}")
        return sorted(backups, key=lambda backup: backup.created_timestamp, reverse=True)

    def restore_backup(self, backup: WorldBackupInfo | str | Path) -> Path:
        if not isinstance(backup, WorldBackupInfo) or backup.restore_path is None:
            raise ValueError(
                "World restore requires a backup selected for a known launcher world"
            )
        backup_info = backup
        target = self._validated_restore_target(backup_info)
        instance_path = target.parent.parent
        try:
            if self._instance_operations is not None:
                return self._instance_operations.execute(
                    instance_path,
                    "world_restore",
                    self._restore_backup_locked,
                    backup_info,
                    target,
                )
            return self._restore_backup_locked(backup_info, target)
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self._translate(
                    "instance_operation_busy",
                    version=backup_info.version_id or target.name,
                )
            ) from exc

    def _restore_backup_locked(
        self,
        backup_info: WorldBackupInfo,
        target: Path,
    ) -> Path:
        instance_path = target.parent.parent
        self._ensure_instance_idle(
            instance_path,
            backup_info.version_id or target.name,
        )
        saves_dir = target.parent
        self._recover_restore_transaction(saves_dir)
        with zipfile.ZipFile(backup_info.path) as archive:
            restore_size = sum(
                max(0, int(member.file_size))
                for member in archive.infolist()
                if not member.is_dir()
            )
        ensure_storage_available(
            [
                StorageRequest(
                    target=saves_dir,
                    required_bytes=restore_size,
                    label=f"world restore {target.name}",
                )
            ]
        )
        restore_tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=saves_dir))
        old_path = restore_tmp.with_name(f"{restore_tmp.name}.previous")
        journal_started = False
        try:
            with zipfile.ZipFile(backup_info.path) as archive:
                self._extract_zip_safely(archive, restore_tmp)
            self._validate_staged_world(restore_tmp)

            journal = {
                "schema_version": RESTORE_JOURNAL_SCHEMA,
                "phase": "prepared",
                "target_name": target.name,
                "staged_name": restore_tmp.name,
                "previous_name": old_path.name,
                "had_original": target.exists(),
            }
            self._write_restore_journal(saves_dir, journal)
            journal_started = True

            if journal["had_original"]:
                target.rename(old_path)
                self._sync_directory(saves_dir)
            self._set_restore_phase(saves_dir, journal, "original_moved")

            restore_tmp.rename(target)
            self._sync_directory(saves_dir)
            self._set_restore_phase(saves_dir, journal, "activated")

            if old_path.exists():
                try:
                    shutil.rmtree(old_path)
                    self._sync_directory(saves_dir)
                except OSError as exc:
                    self._warning(f"Restored world but could not remove previous copy {old_path}: {exc!r}")
                    return target
            self._clear_restore_journal(saves_dir)
            return target
        except Exception:
            if journal_started:
                try:
                    self._recover_restore_transaction(saves_dir)
                except Exception as recovery_error:
                    raise RuntimeError(
                        "World restore failed and automatic recovery could not be completed; "
                        f"recovery state remains at {saves_dir / RESTORE_JOURNAL_FILE}"
                    ) from recovery_error
            else:
                shutil.rmtree(restore_tmp, ignore_errors=True)
            raise

    def _recover_restore_transaction(self, saves_dir: Path) -> bool:
        journal_path = saves_dir / RESTORE_JOURNAL_FILE
        if not journal_path.exists():
            return False

        journal = self._read_restore_journal(journal_path)
        target = self._restore_journal_world_path(saves_dir, journal, "target_name")
        staged = self._restore_journal_world_path(saves_dir, journal, "staged_name")
        previous = self._restore_journal_world_path(saves_dir, journal, "previous_name")
        if len({target.name, staged.name, previous.name}) != 3:
            raise RuntimeError("World restore journal paths are not distinct")

        for path in (target, staged, previous):
            if path.exists() and not path.is_dir():
                raise RuntimeError(f"World restore recovery path is not a directory: {path}")

        had_original = journal.get("had_original")
        if not isinstance(had_original, bool):
            raise RuntimeError("World restore journal original-world state is invalid")

        if staged.exists():
            if previous.exists():
                if target.exists():
                    raise RuntimeError("World restore recovery state is ambiguous")
                previous.rename(target)
                self._sync_directory(saves_dir)
            elif had_original and not target.exists():
                raise RuntimeError("World restore recovery cannot find the original world")
            shutil.rmtree(staged)
            self._sync_directory(saves_dir)
        elif target.exists():
            if previous.exists():
                shutil.rmtree(previous)
                self._sync_directory(saves_dir)
        elif previous.exists():
            previous.rename(target)
            self._sync_directory(saves_dir)
        elif had_original:
            raise RuntimeError("World restore recovery cannot find either world copy")

        self._clear_restore_journal(saves_dir)
        return True

    def _write_restore_journal(self, saves_dir: Path, journal: dict[str, Any]) -> None:
        atomic_write_json(
            saves_dir / RESTORE_JOURNAL_FILE,
            journal,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        self._sync_directory(saves_dir)

    def _set_restore_phase(
        self,
        saves_dir: Path,
        journal: dict[str, Any],
        phase: str,
    ) -> None:
        if phase not in RESTORE_PHASES:
            raise ValueError(f"Unknown world restore phase: {phase}")
        journal["phase"] = phase
        self._write_restore_journal(saves_dir, journal)

    def _read_restore_journal(self, journal_path: Path) -> dict[str, Any]:
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("World restore journal is corrupted") from exc
        if not isinstance(journal, dict):
            raise RuntimeError("World restore journal is invalid")
        if journal.get("schema_version") != RESTORE_JOURNAL_SCHEMA:
            raise RuntimeError("World restore journal schema is unsupported")
        if journal.get("phase") not in RESTORE_PHASES:
            raise RuntimeError("World restore journal phase is invalid")
        return journal

    @staticmethod
    def _restore_journal_world_path(
        saves_dir: Path,
        journal: dict[str, Any],
        field: str,
    ) -> Path:
        name = journal.get(field)
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).is_absolute()
        ):
            raise RuntimeError(f"World restore journal {field} is invalid")
        return saves_dir / name

    def _clear_restore_journal(self, saves_dir: Path) -> None:
        (saves_dir / RESTORE_JOURNAL_FILE).unlink(missing_ok=True)
        self._sync_directory(saves_dir)

    def delete_backup(self, backup: WorldBackupInfo | str | Path) -> None:
        backup_info = self._resolve_backup_info(backup)
        backup_info.path.unlink(missing_ok=True)
        backup_info.metadata_path.unlink(missing_ok=True)

    def version_backup_dir(self, version: Any) -> Path:
        version_id = str(
            getattr(version, "version_id", None)
            or getattr(version, "id", None)
            or getattr(version, "name", None)
            or "version"
        )
        return self.backup_root() / self._safe_name(version_id)

    def delete_version_backups(self, version: Any) -> None:
        target = self.version_backup_dir(version)
        if not target.exists():
            return
        root = self.backup_root().resolve()
        resolved_target = target.resolve()
        if root == resolved_target or root not in resolved_target.parents:
            raise ValueError(f"Refusing to delete backup path outside backup root: {target}")
        shutil.rmtree(resolved_target)

    def _needs_auto_backup(self, version: Any, world_path: Path) -> bool:
        latest_auto = next((backup for backup in self.scan_backups(version, world_path) if backup.kind == "auto"), None)
        if latest_auto is None:
            return True
        level_dat = world_path / "level.dat"
        try:
            metadata = json.loads(latest_auto.metadata_path.read_text(encoding="utf-8"))
            previous_source_modified = float(metadata.get("source_modified_at") or latest_auto.created_timestamp)
            return previous_source_modified < level_dat.stat().st_mtime
        except (OSError, ValueError, json.JSONDecodeError):
            return True

    def _backup_dir_for_world(self, version: Any, world_path: Path) -> Path:
        return self.version_backup_dir(version) / self._safe_name(world_path.name)

    def _metadata_for_backup(
        self,
        version: Any,
        world_dir: Path,
        zip_path: Path,
        created_at: datetime,
        kind: str,
    ) -> dict[str, Any]:
        source_modified = self._world_modified_at(world_dir)
        return {
            "schema": BACKUP_SCHEMA,
            "kind": kind,
            "version_id": str(getattr(version, "version_id", None) or getattr(version, "id", None) or ""),
            "version_name": str(getattr(version, "name", "") or ""),
            "world_folder": world_dir.name,
            "world_name": world_dir.name,
            "source_path": str(world_dir),
            "source_modified_at": source_modified,
            "created_at": created_at.isoformat(),
            "created_timestamp": created_at.timestamp(),
            "zip_name": zip_path.name,
        }

    def _backup_info_from_files(
        self,
        zip_path: Path,
        metadata_path: Path,
        *,
        restore_path: Path | None = None,
    ) -> WorldBackupInfo:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        created_timestamp = float(data.get("created_timestamp") or zip_path.stat().st_mtime)
        raw_source_path = str(data.get("source_path") or "").strip()
        source_path = Path(raw_source_path) if raw_source_path else None
        return WorldBackupInfo(
            path=zip_path,
            metadata_path=metadata_path,
            world_folder=str(data.get("world_folder") or ""),
            world_name=str(data.get("world_name") or data.get("world_folder") or ""),
            version_id=str(data.get("version_id") or ""),
            created_at=str(data.get("created_at") or ""),
            created_timestamp=created_timestamp,
            kind=str(data.get("kind") or "manual"),
            size=zip_path.stat().st_size,
            source_path=source_path,
            restore_path=restore_path,
        )

    def _resolve_backup_info(self, backup: WorldBackupInfo | str | Path) -> WorldBackupInfo:
        if isinstance(backup, WorldBackupInfo):
            return backup
        path = Path(backup)
        return self._backup_info_from_files(path, path.with_suffix(path.suffix + METADATA_SUFFIX))

    @staticmethod
    def _validated_restore_target(backup: WorldBackupInfo) -> Path:
        world_folder = str(backup.world_folder or "").strip()
        if (
            not world_folder
            or world_folder in {".", ".."}
            or Path(world_folder).name != world_folder
            or "/" in world_folder
            or "\\" in world_folder
        ):
            raise ValueError("Backup metadata contains an invalid world folder")

        raw_target = Path(backup.restore_path or "")
        if raw_target.name != world_folder or raw_target.parent.name.casefold() != "saves":
            raise ValueError("Backup restore target does not match the selected world")
        if raw_target.is_symlink() or raw_target.parent.is_symlink():
            raise ValueError("Backup restore target cannot use a symbolic link")
        return raw_target.resolve(strict=False)

    def _prune_auto_backups(self, version: Any, world_path: Path) -> None:
        auto_backups = [backup for backup in self.scan_backups(version, world_path) if backup.kind == "auto"]
        for backup in auto_backups[self.keep_count():]:
            self.delete_backup(backup)

    def _create_zip(self, source: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for file_path in self._iter_world_files(source):
                archive.write(file_path, file_path.relative_to(source).as_posix())

    @staticmethod
    def _ensure_backup_outside_world(world_dir: Path, target_dir: Path) -> None:
        resolved_world = world_dir.resolve()
        resolved_target = target_dir.resolve()
        if resolved_target == resolved_world or resolved_world in resolved_target.parents:
            raise ValueError(f"Backup directory cannot be inside the source world: {target_dir}")

    @staticmethod
    def _validate_staged_world(staged_world: Path) -> None:
        if not (staged_world / "level.dat").is_file():
            raise ValueError("Backup archive does not contain level.dat")

    @staticmethod
    def _extract_zip_safely(archive: zipfile.ZipFile, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Backup archive contains unsafe path: {member.filename}")
        archive.extractall(destination)

    def _iter_world_files(self, source: Path) -> Iterable[Path]:
        for file_path in source.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.name == SESSION_LOCK:
                continue
            if file_path.name.endswith(".tmp"):
                continue
            yield file_path

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        for file_path in path.rglob("*"):
            try:
                if file_path.is_file() and file_path.name != SESSION_LOCK:
                    total += file_path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _world_modified_at(path: Path) -> float:
        level_dat = path / "level.dat"
        try:
            return level_dat.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(value)).strip(" .")
        return safe or "item"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists() and not path.with_suffix(path.suffix + METADATA_SUFFIX).exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(1, 1000):
            candidate = path.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists() and not candidate.with_suffix(candidate.suffix + METADATA_SUFFIX).exists():
                return candidate
        raise FileExistsError(f"Could not create a unique backup path for {path}")

    def _ensure_instance_idle(self, instance_path: Path, version_name: str) -> None:
        from launcher.core.game import Game

        if Game.is_game_dir_active(instance_path):
            raise RuntimeError(
                self._translate(
                    "instance_game_running",
                    version=version_name,
                )
            )

    @staticmethod
    def _version_label(version: Any, fallback: str) -> str:
        return str(
            getattr(version, "name", None)
            or getattr(version, "version_id", None)
            or getattr(version, "id", None)
            or fallback
        )

    @staticmethod
    def _sync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _translate(self, key: str, **kwargs: Any) -> str:
        trans = self._translator
        if callable(trans):
            return str(trans(key, **kwargs))
        if key == "world_backup_progress":
            return f"Backing up world {kwargs.get('world', '')}".strip()
        return key

    def _warning(self, message: str) -> None:
        warning = getattr(self.logger, "warning", None)
        if callable(warning):
            warning(message)


__all__ = [
    "RESTORE_JOURNAL_FILE",
    "WorldBackupInfo",
    "WorldBackupResult",
    "WorldBackupService",
    "WorldInfo",
]
