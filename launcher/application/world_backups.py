from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable


BACKUP_SCHEMA = 1
DEFAULT_KEEP_COUNT = 3
SESSION_LOCK = "session.lock"
METADATA_SUFFIX = ".json"


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
    def __init__(self, minecraft_dir: str | Path, config, logger: Any, translator=None) -> None:
        self.minecraft_dir = Path(minecraft_dir)
        self.config = config
        self.logger = logger
        self._translator = translator

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

    def auto_backup_changed_worlds(self, version: Any, operation=None) -> WorldBackupResult:
        if not self.enabled():
            return WorldBackupResult(created=0, skipped=0, failed=0)

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
                self.create_backup(version, world.path, kind="auto")
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

    def create_backup(self, version: Any, world_path: str | Path, *, kind: str = "manual") -> WorldBackupInfo:
        world_dir = Path(world_path)
        if not world_dir.exists() or not world_dir.is_dir():
            raise FileNotFoundError(f"World directory not found: {world_dir}")
        if not (world_dir / "level.dat").is_file():
            raise ValueError(f"World directory does not contain level.dat: {world_dir}")

        target_dir = self._backup_dir_for_world(version, world_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc)
        timestamp_name = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        zip_name = f"[Auto] {timestamp_name}.zip" if kind == "auto" else f"{timestamp_name}.zip"
        final_path = self._unique_path(target_dir / zip_name)
        metadata_path = final_path.with_suffix(final_path.suffix + METADATA_SUFFIX)

        with tempfile.NamedTemporaryFile(prefix=final_path.name, suffix=".tmp", dir=target_dir, delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            self._create_zip(world_dir, temp_path)
            final_path.unlink(missing_ok=True)
            shutil.move(str(temp_path), str(final_path))
            metadata = self._metadata_for_backup(version, world_dir, final_path, timestamp, kind)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
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
        backup_info = self._resolve_backup_info(backup)
        target = backup_info.restore_path or backup_info.source_path
        if target is None:
            raise ValueError("Backup metadata does not contain source_path")
        saves_dir = target.parent
        restore_tmp = saves_dir / f"{target.name}.restore-{int(time.time())}"
        old_path = saves_dir / f"{target.name}.old-{int(time.time())}"
        try:
            with zipfile.ZipFile(backup_info.path) as archive:
                self._extract_zip_safely(archive, restore_tmp)
            if target.exists():
                target.rename(old_path)
            restore_tmp.rename(target)
            if old_path.exists():
                shutil.rmtree(old_path)
            return target
        except Exception:
            if target.exists() and old_path.exists():
                shutil.rmtree(target, ignore_errors=True)
            if old_path.exists() and not target.exists():
                old_path.rename(target)
            shutil.rmtree(restore_tmp, ignore_errors=True)
            raise

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

    def _prune_auto_backups(self, version: Any, world_path: Path) -> None:
        auto_backups = [backup for backup in self.scan_backups(version, world_path) if backup.kind == "auto"]
        for backup in auto_backups[self.keep_count():]:
            self.delete_backup(backup)

    def _create_zip(self, source: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for file_path in self._iter_world_files(source):
                archive.write(file_path, file_path.relative_to(source).as_posix())

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

    def _translate(self, key: str, **kwargs: Any) -> str:
        trans = self._translator
        if callable(trans):
            return trans(key, **kwargs)
        if key == "world_backup_progress":
            return f"Backing up world {kwargs.get('world', '')}".strip()
        return key

    def _warning(self, message: str) -> None:
        warning = getattr(self.logger, "warning", None)
        if callable(warning):
            warning(message)


__all__ = ["WorldBackupInfo", "WorldBackupResult", "WorldBackupService", "WorldInfo"]
