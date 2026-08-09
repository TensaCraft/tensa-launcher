from __future__ import annotations

import hashlib
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, NamedTuple, Sequence

from launcher.application.file_transaction import FileTransaction, FileTransactionPlan
from launcher.core.async_downloader import DownloadTask
from launcher.models.logger import Logger


class FileMetadata(NamedTuple):
    name: str
    size: int
    hash_value: str
    hash_algorithm: str


@dataclass(frozen=True, slots=True)
class RemoteFile:
    relative_path: PurePosixPath
    url: str
    task_id: str
    metadata: FileMetadata


@dataclass(frozen=True, slots=True)
class OverrideFile:
    relative_path: PurePosixPath
    size: int
    source_path: Path | None = None
    archive_index: int | None = None
    archive_name: str | None = None


@dataclass(frozen=True, slots=True)
class CurseForgeInstallLimits:
    override_members: int
    override_entry_size: int
    override_total_size: int


class _DownloadStageFailed(RuntimeError):
    pass


class CurseForgeInstallService:
    """Build and apply a validated CurseForge content transaction."""

    _WINDOWS_RESERVED_NAMES = {
        "aux",
        "clock$",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "con",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
        "nul",
        "prn",
    }

    def __init__(
        self,
        *,
        limits: CurseForgeInstallLimits,
        downloader_factory: Callable[..., Any],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._limits = limits
        self._downloader_factory = downloader_factory
        self._progress_callback = progress_callback

    def plan_manifest_files(
        self,
        file_entries: Sequence[dict[str, Any]],
        game_path: Path,
        *,
        metadata_resolver: Callable[[int, int], FileMetadata],
        download_url: Callable[[int, int], str],
    ) -> tuple[list[RemoteFile], dict[str, Any]]:
        result = self.empty_result()
        if not file_entries:
            Logger.info("CurseForge manifest has no files to download")
            return [], result

        remote_files: list[RemoteFile] = []
        seen_paths: set[str] = set()
        for entry in file_entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("required", True) is False:
                result["skipped"] += 1
                continue

            project_id = self._safe_int(entry.get("projectID"))
            file_id = self._safe_int(entry.get("fileID"))
            if project_id is None or file_id is None:
                result["failed"] += 1
                result["errors"].append(f"Invalid manifest file entry: {entry}")
                continue

            try:
                metadata = metadata_resolver(project_id, file_id)
                destination = self.safe_mod_destination(
                    game_path / "mods",
                    metadata.name,
                )
                relative_path = PurePosixPath("mods", destination.name)
                self.assert_safe_destination(game_path, relative_path)
                path_key = relative_path.as_posix().casefold()
                if path_key in seen_paths:
                    raise ValueError(
                        f"duplicate destination {relative_path.as_posix()}"
                    )
                seen_paths.add(path_key)
            except ValueError as exc:
                result["failed"] += 1
                result["errors"].append(f"{project_id}:{file_id}: {exc}")
                continue

            remote_files.append(
                RemoteFile(
                    relative_path=relative_path,
                    url=download_url(project_id, file_id),
                    task_id=f"{project_id}:{file_id}",
                    metadata=metadata,
                )
            )
        return remote_files, result

    def plan_overrides(
        self,
        source_path: Path,
        source_kind: str,
        manifest: dict[str, Any],
        game_path: Path,
    ) -> list[OverrideFile]:
        overrides_value = str(manifest.get("overrides") or "overrides").strip()
        if not overrides_value:
            return []

        overrides_path = self.safe_relative_path(
            overrides_value,
            label="overrides directory",
        )
        if source_kind == "zip":
            return self._plan_zip_overrides(
                source_path,
                overrides_path,
                game_path,
            )
        if source_kind == "manifest":
            return self._plan_local_overrides(
                source_path,
                overrides_path,
                game_path,
            )
        raise ValueError(f"Unsupported CurseForge source type: {source_kind}")

    def install_content(
        self,
        *,
        game_path: Path,
        remote_files: Sequence[RemoteFile],
        overrides: Sequence[OverrideFile],
        source_path: Path | None,
        source_kind: str | None,
        operation_name: str,
        result: dict[str, Any] | None = None,
        commit_callback: Callable[[], None] | None = None,
        commit_key: str | None = None,
    ) -> dict[str, Any]:
        outcome = result if result is not None else self.empty_result()
        replacements = [item.relative_path for item in remote_files]
        replacements.extend(item.relative_path for item in overrides)
        if not replacements:
            if commit_callback is not None:
                commit_callback()
            return outcome

        transaction = FileTransaction(
            game_path,
            FileTransactionPlan(
                operation=operation_name,
                replacements=replacements,
                staged_bytes=sum(
                    item.metadata.size for item in remote_files
                )
                + sum(item.size for item in overrides),
            ),
        )

        def stage(current: FileTransaction) -> None:
            download_result = self.stage_remote_files(remote_files, current)
            self._merge_result(outcome, download_result)
            if outcome["failed"] > 0:
                raise _DownloadStageFailed
            if overrides:
                if source_path is None or source_kind is None:
                    raise ValueError("CurseForge override source is missing")
                self.stage_overrides(
                    source_path,
                    source_kind,
                    overrides,
                    current,
                )

        def validate() -> None:
            self.assert_safe_destinations(game_path, replacements)
            self._validate_staged_files(transaction, remote_files, overrides)

        try:
            transaction.execute(
                stage,
                validate=validate,
                commit=commit_callback,
                commit_key=commit_key,
            )
        except _DownloadStageFailed:
            return outcome
        return outcome

    def stage_remote_files(
        self,
        remote_files: Sequence[RemoteFile],
        transaction: FileTransaction,
    ) -> dict[str, Any]:
        if not remote_files:
            return self.empty_result()

        tasks: list[DownloadTask] = []
        for item in remote_files:
            destination = transaction.stage_path(item.relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            tasks.append(
                DownloadTask(
                    url=item.url,
                    destination=destination,
                    expected_size=item.metadata.size,
                    expected_hash=item.metadata.hash_value,
                    expected_hash_algorithm=item.metadata.hash_algorithm,
                    task_id=item.task_id,
                )
            )

        result = self._downloader_factory(max_workers=6).download_files(
            tasks,
            progress_callback=self._progress_callback,
            skip_existing=False,
            verify_existing_hash=True,
        )
        if result["failed"] > 0:
            return result

        for item in remote_files:
            self.verify_staged_file(
                transaction.stage_path(item.relative_path),
                item.metadata,
            )
        return result

    def stage_overrides(
        self,
        source_path: Path,
        source_kind: str,
        overrides: Sequence[OverrideFile],
        transaction: FileTransaction,
    ) -> None:
        if not overrides:
            return
        if source_kind == "zip":
            self._stage_zip_overrides(source_path, overrides, transaction)
            return
        if source_kind != "manifest":
            raise ValueError(f"Unsupported CurseForge source type: {source_kind}")

        Logger.info(f"Staging {len(overrides)} local CurseForge override files")
        for item in overrides:
            if item.source_path is None:
                raise ValueError(
                    f"Local override source is missing: {item.relative_path}"
                )
            self.assert_safe_existing_path(
                source_path.parent,
                item.source_path,
                label="CurseForge local override",
            )
            target = transaction.stage_path(item.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            copied = self._copy_limited(item.source_path, target, item.size)
            if copied != item.size:
                raise ValueError(
                    "CurseForge override size changed while staging: "
                    f"{item.relative_path} ({copied} != {item.size})"
                )

    def _plan_zip_overrides(
        self,
        archive_path: Path,
        overrides_path: PurePosixPath,
        game_path: Path,
    ) -> list[OverrideFile]:
        self.assert_safe_existing_path(
            archive_path.parent,
            archive_path,
            label="CurseForge archive",
        )
        prefix = f"{overrides_path.as_posix().rstrip('/')}/"
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = [
                (index, info)
                for index, info in enumerate(archive.infolist())
                if info.filename.replace("\\", "/").startswith(prefix)
            ]
            if not members:
                Logger.info(
                    f"No overrides found in archive: {overrides_path.as_posix()}/"
                )
                return []

            files = [
                (index, info)
                for index, info in members
                if not info.is_dir()
            ]
            if len(files) > self._limits.override_members:
                raise ValueError(
                    "CurseForge overrides contain too many files: "
                    f"{len(files)} > {self._limits.override_members}"
                )

            plan: list[OverrideFile] = []
            seen_paths: set[str] = set()
            total_size = 0
            for archive_index, info in files:
                member = info.filename.replace("\\", "/")
                if (
                    info.file_size < 0
                    or info.file_size > self._limits.override_entry_size
                ):
                    raise ValueError(
                        f"CurseForge override is too large: {member} "
                        f"({info.file_size} bytes)"
                    )
                total_size += info.file_size
                if total_size > self._limits.override_total_size:
                    raise ValueError(
                        "CurseForge overrides are too large: "
                        f"{total_size} > {self._limits.override_total_size} bytes"
                    )

                unix_mode = info.external_attr >> 16
                if unix_mode and stat.S_ISLNK(unix_mode):
                    raise ValueError(
                        "CurseForge override cannot be a symbolic link: "
                        f"{member}"
                    )
                relative_name = member[len(prefix) :]
                if not relative_name:
                    continue
                relative_path = self.safe_relative_path(
                    relative_name,
                    label=f"override path {member}",
                )
                self._add_unique_path(seen_paths, relative_path)
                self.assert_safe_destination(game_path, relative_path)
                plan.append(
                    OverrideFile(
                        relative_path=relative_path,
                        size=info.file_size,
                        archive_index=archive_index,
                        archive_name=info.filename,
                    )
                )
        return plan

    def _plan_local_overrides(
        self,
        manifest_path: Path,
        overrides_path: PurePosixPath,
        game_path: Path,
    ) -> list[OverrideFile]:
        source_base = manifest_path.parent
        source_root = source_base.joinpath(*overrides_path.parts)
        if not source_root.exists():
            Logger.info("No local overrides directory found next to manifest.json")
            return []

        self.assert_safe_existing_path(
            source_base,
            source_root,
            label="CurseForge overrides directory",
        )
        if not source_root.is_dir():
            raise ValueError("CurseForge overrides path is not a directory")

        plan: list[OverrideFile] = []
        seen_paths: set[str] = set()
        total_size = 0
        for entry in source_root.rglob("*"):
            self.assert_safe_existing_path(
                source_root,
                entry,
                label="CurseForge local override",
            )
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise ValueError(f"Unsupported CurseForge override entry: {entry}")

            file_size = entry.stat().st_size
            if file_size > self._limits.override_entry_size:
                raise ValueError(
                    f"CurseForge override is too large: {entry.name} "
                    f"({file_size} bytes)"
                )
            total_size += file_size
            if total_size > self._limits.override_total_size:
                raise ValueError(
                    "CurseForge overrides are too large: "
                    f"{total_size} > {self._limits.override_total_size} bytes"
                )

            relative_path = self.safe_relative_path(
                entry.relative_to(source_root).as_posix(),
                label=f"override path {entry}",
            )
            self._add_unique_path(seen_paths, relative_path)
            self.assert_safe_destination(game_path, relative_path)
            plan.append(
                OverrideFile(
                    relative_path=relative_path,
                    size=file_size,
                    source_path=entry,
                )
            )

        if len(plan) > self._limits.override_members:
            raise ValueError(
                "CurseForge overrides contain too many files: "
                f"{len(plan)} > {self._limits.override_members}"
            )
        return plan

    def _stage_zip_overrides(
        self,
        archive_path: Path,
        overrides: Sequence[OverrideFile],
        transaction: FileTransaction,
    ) -> None:
        Logger.info(
            f"Staging {len(overrides)} CurseForge override files from archive"
        )
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            for item in overrides:
                if item.archive_index is None or item.archive_name is None:
                    raise ValueError(
                        f"Archive override metadata is missing: {item.relative_path}"
                    )
                if item.archive_index >= len(members):
                    raise ValueError("CurseForge archive changed after preflight")
                info = members[item.archive_index]
                if (
                    info.filename != item.archive_name
                    or info.file_size != item.size
                ):
                    raise ValueError("CurseForge archive changed after preflight")

                target = transaction.stage_path(item.relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                try:
                    with archive.open(info) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if (
                                copied > item.size
                                or copied > self._limits.override_entry_size
                            ):
                                raise ValueError(
                                    "CurseForge override exceeded its declared "
                                    f"size: {info.filename}"
                                )
                            output.write(chunk)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                if copied != item.size:
                    target.unlink(missing_ok=True)
                    raise ValueError(
                        "CurseForge override size mismatch: "
                        f"{info.filename} ({copied} != {item.size})"
                    )

    def _validate_staged_files(
        self,
        transaction: FileTransaction,
        remote_files: Sequence[RemoteFile],
        overrides: Sequence[OverrideFile],
    ) -> None:
        for item in remote_files:
            self.verify_staged_file(
                transaction.stage_path(item.relative_path),
                item.metadata,
            )
        for item in overrides:
            staged = transaction.stage_path(item.relative_path)
            if not staged.is_file() or staged.stat().st_size != item.size:
                raise ValueError(
                    f"CurseForge staged override is invalid: {item.relative_path}"
                )

    def _copy_limited(
        self,
        source: Path,
        destination: Path,
        expected_size: int,
    ) -> int:
        copied = 0
        try:
            with source.open("rb") as input_file, destination.open("wb") as output:
                while chunk := input_file.read(1024 * 1024):
                    copied += len(chunk)
                    if (
                        copied > expected_size
                        or copied > self._limits.override_entry_size
                    ):
                        raise ValueError(
                            f"CurseForge override changed while staging: {source}"
                        )
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return copied

    @staticmethod
    def verify_staged_file(path: Path, metadata: FileMetadata) -> None:
        if not path.is_file():
            raise ValueError(
                f"CurseForge download is missing after staging: {path.name}"
            )
        actual_size = path.stat().st_size
        if actual_size != metadata.size:
            raise ValueError(
                f"CurseForge download size mismatch: {path.name} "
                f"({actual_size} != {metadata.size})"
            )
        digest = hashlib.new(metadata.hash_algorithm)
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest().lower() != metadata.hash_value:
            raise ValueError(
                f"CurseForge download checksum mismatch: {path.name}"
            )

    @classmethod
    def safe_relative_path(cls, value: str, *, label: str) -> PurePosixPath:
        text = value.strip().replace("\\", "/").rstrip("/")
        windows_path = PureWindowsPath(text)
        path = PurePosixPath(text)
        if (
            not text
            or "\x00" in text
            or path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in path.parts
            or any(
                not part
                or part in {".", ".."}
                or part.endswith((" ", "."))
                or any(character in '<>:"|?*' for character in part)
                or part.split(".", 1)[0].casefold()
                in cls._WINDOWS_RESERVED_NAMES
                for part in path.parts
            )
        ):
            raise ValueError(
                f"CurseForge contains an unsafe {label}: {value}"
            )
        return path

    @classmethod
    def safe_mod_destination(cls, mods_dir: Path, file_name: str) -> Path:
        value = file_name.strip()
        windows_path = PureWindowsPath(value)
        posix_path = PurePosixPath(value)
        normalized = value.replace("\\", "/")
        if (
            not value
            or "\x00" in value
            or "/" in normalized
            or "\\" in value
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or posix_path.is_absolute()
            or ".." in posix_path.parts
            or value.endswith((" ", "."))
            or any(character in '<>:"|?*' for character in value)
            or value.split(".", 1)[0].casefold()
            in cls._WINDOWS_RESERVED_NAMES
        ):
            raise ValueError(f"CurseForge returned an unsafe fileName: {file_name}")
        destination = mods_dir / value
        cls.assert_safe_destination(
            mods_dir.parent,
            PurePosixPath(mods_dir.name, destination.name),
        )
        return destination

    @classmethod
    def assert_safe_destinations(
        cls,
        game_path: Path,
        relative_paths: Sequence[PurePosixPath],
    ) -> None:
        for relative_path in relative_paths:
            cls.assert_safe_destination(game_path, relative_path)

    @classmethod
    def assert_safe_destination(
        cls,
        game_path: Path,
        relative_path: PurePosixPath,
    ) -> None:
        root = game_path.absolute()
        candidate = root.joinpath(*relative_path.parts)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "CurseForge destination escapes the game directory: "
                f"{relative_path}"
            ) from exc

        current = root
        for part in relative_path.parts:
            current = current / part
            if cls.is_link_or_reparse(current):
                raise ValueError(
                    "CurseForge destination uses a symbolic link or reparse "
                    f"point: {relative_path}"
                )
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        if not resolved_candidate.is_relative_to(resolved_root):
            raise ValueError(
                "CurseForge destination escapes the game directory: "
                f"{relative_path}"
            )

    @classmethod
    def assert_safe_existing_path(
        cls,
        root: Path,
        candidate: Path,
        *,
        label: str,
    ) -> None:
        absolute_root = root.absolute()
        absolute_candidate = candidate.absolute()
        try:
            relative = absolute_candidate.relative_to(absolute_root)
        except ValueError as exc:
            raise ValueError(f"{label} is outside its source directory") from exc

        current = absolute_root
        for part in relative.parts:
            current = current / part
            if cls.is_link_or_reparse(current):
                raise ValueError(
                    f"{label} cannot use a symbolic link or reparse point: {current}"
                )
        if not absolute_candidate.exists():
            raise ValueError(f"{label} does not exist: {absolute_candidate}")
        if not absolute_candidate.resolve().is_relative_to(
            absolute_root.resolve()
        ):
            raise ValueError(f"{label} resolves outside its source directory")

    @staticmethod
    def is_link_or_reparse(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return False
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def empty_result() -> dict[str, Any]:
        return {
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _add_unique_path(
        seen_paths: set[str],
        relative_path: PurePosixPath,
    ) -> None:
        path_key = relative_path.as_posix().casefold()
        if path_key in seen_paths:
            raise ValueError(
                "CurseForge overrides contain a duplicate path: "
                f"{relative_path}"
            )
        seen_paths.add(path_key)

    @staticmethod
    def _merge_result(
        outcome: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        outcome["success"] += result["success"]
        outcome["failed"] += result["failed"]
        outcome["skipped"] += result["skipped"]
        outcome["errors"].extend(result["errors"])


__all__ = [
    "CurseForgeInstallLimits",
    "CurseForgeInstallService",
    "FileMetadata",
    "OverrideFile",
    "RemoteFile",
]
