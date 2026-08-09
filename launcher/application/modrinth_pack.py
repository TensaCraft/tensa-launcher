from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from copy import copy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Optional, Sequence, Union
from urllib.parse import urlsplit

from launcher.application.file_transaction import (
    FileTransaction,
    FileTransactionPlan,
    build_commit_key,
)
from launcher.core.async_downloader import AsyncDownloader, DownloadTask
from launcher.models.logger import Logger


@dataclass(frozen=True, slots=True)
class _ModrinthOverrideFile:
    relative_path: PurePosixPath
    archive_index: int
    archive_name: str
    size: int
    crc: int


class ModrinthPackService:
    MANAGED_METADATA_PATH = PurePosixPath(".tensalauncher/modrinth-pack.json")
    MANAGED_METADATA_SCHEMA = 1

    MAX_INDEX_SIZE = 16 * 1024 * 1024
    MAX_INDEX_FILES = 20_000
    MAX_OVERRIDE_MEMBERS = 20_000
    MAX_OVERRIDE_ENTRY_SIZE = 1024 * 1024 * 1024
    MAX_OVERRIDE_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
    MAX_MANAGED_METADATA_SIZE = 4 * 1024 * 1024
    MAX_MANAGED_PATHS = 40_000

    _INTERNAL_PATHS = (
        ".tensalauncher-sync.json",
        ".tensalauncher-pack.mrpack",
    )
    _INTERNAL_DIRECTORY_PREFIXES = (
        ".tensalauncher-sync",
    )

    def __init__(
        self,
        *,
        downloader_factory: Callable[..., Any] = AsyncDownloader,
        logger: Any = Logger,
    ) -> None:
        self._downloader_factory = downloader_factory
        self._logger = logger

    @classmethod
    def read_index(cls, path: Union[str, os.PathLike]) -> dict[str, Any]:
        abs_path = os.fspath(path)
        with zipfile.ZipFile(abs_path, "r") as archive:
            matching = [
                info
                for info in archive.infolist()
                if info.filename.replace("\\", "/") == "modrinth.index.json"
            ]
            if len(matching) != 1:
                raise ValueError("Modrinth pack must contain exactly one modrinth.index.json")
            info = matching[0]
            if cls._zip_info_is_link(info):
                raise ValueError("modrinth.index.json cannot be a symbolic link")
            if info.file_size < 0 or info.file_size > cls.MAX_INDEX_SIZE:
                raise ValueError(
                    f"modrinth.index.json is too large: {info.file_size} > {cls.MAX_INDEX_SIZE}"
                )
            with archive.open(info, "r") as index_file:
                raw_payload = index_file.read(cls.MAX_INDEX_SIZE + 1)
        if len(raw_payload) > cls.MAX_INDEX_SIZE:
            raise ValueError("modrinth.index.json exceeded its declared size limit")
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("modrinth.index.json has an invalid format")
        return payload

    @staticmethod
    def build_launch_version(loader_id: str, mc_version: str, loader_version: str) -> str:
        if loader_id == "fabric-loader":
            return f"fabric-loader-{loader_version}-{mc_version}"
        if loader_id == "quilt-loader":
            return f"quilt-loader-{loader_version}-{mc_version}"
        if loader_id == "forge":
            return f"{mc_version}-forge-{loader_version}"
        if loader_id == "neoforge":
            return f"neoforge-{loader_version}"
        return mc_version

    @staticmethod
    def loader_key(loader_id: str) -> Optional[str]:
        return {
            "fabric-loader": "fabric",
            "quilt-loader": "quilt",
            "forge": "forge",
            "neoforge": "neoforge",
        }.get(loader_id)

    def resolve_loader(self, index: dict[str, Any]) -> tuple[str, Optional[str], Optional[str]]:
        dependencies = index.get("dependencies", {})
        if not isinstance(dependencies, dict):
            raise ValueError("Modrinth pack dependencies have an invalid format")
        mc_version = str(dependencies.get("minecraft") or "").strip()
        if not mc_version:
            raise ValueError("Modrinth pack is missing the Minecraft dependency")
        order = ["neoforge", "forge", "fabric-loader", "quilt-loader"]
        loader_id = next((key for key in order if key in dependencies), None)
        loader_version = str(dependencies.get(loader_id) or "").strip() or None if loader_id else None
        return mc_version, loader_id, loader_version

    @classmethod
    def _safe_relative_path(cls, raw_path: object) -> PurePosixPath:
        value = str(raw_path or "").strip().replace("\\", "/")
        relative = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            not value
            or "\x00" in value
            or relative.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in relative.parts
        ):
            raise ValueError(f"Modrinth pack contains an unsafe path: {value or '<empty>'}")

        parts = tuple(part for part in relative.parts if part not in {"", "."})
        if not parts:
            raise ValueError("Modrinth pack contains an empty or invalid path")
        return PurePosixPath(*parts)

    @classmethod
    def _safe_destination(cls, root: Path, raw_path: object) -> Path:
        relative = cls._safe_relative_path(raw_path)
        root_absolute = Path(root).absolute()
        cls._assert_no_links(root_absolute, relative)

        root_resolved = root_absolute.resolve()
        destination = (root_resolved / Path(*relative.parts)).resolve()
        if destination == root_resolved or not destination.is_relative_to(root_resolved):
            raise ValueError(
                f"Modrinth pack path escapes the game directory: {relative.as_posix()}"
            )
        if destination.exists() and not destination.is_file():
            raise ValueError(
                f"Modrinth pack destination is not a regular file: {relative.as_posix()}"
            )
        return destination

    @staticmethod
    def validated_download_url(raw_url: object) -> str:
        url = str(raw_url or "").strip()
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(f"Modrinth pack contains an unsafe download URL: {url or '<empty>'}") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(f"Modrinth pack contains an unsafe download URL: {url or '<empty>'}")
        return url

    @staticmethod
    def normalized_size(raw_size: object) -> int | None:
        if isinstance(raw_size, bool):
            return None
        try:
            if isinstance(raw_size, int):
                size = raw_size
            elif isinstance(raw_size, str):
                size = int(raw_size)
            else:
                return None
        except (TypeError, ValueError):
            return None
        return size if size > 0 else None

    @classmethod
    def _preflight_overrides(
        cls,
        mrpack_path: Path,
        game_path: Path,
    ) -> list[_ModrinthOverrideFile]:
        overrides: list[_ModrinthOverrideFile] = []
        total_size = 0

        with zipfile.ZipFile(str(mrpack_path), "r") as archive:
            for archive_index, info in enumerate(archive.infolist()):
                member = info.filename.replace("\\", "/")
                if not member.startswith("overrides/") or info.is_dir():
                    continue
                if cls._zip_info_is_link(info):
                    raise ValueError(f"Modrinth override cannot be a symbolic link: {member}")
                if info.file_size < 0 or info.file_size > cls.MAX_OVERRIDE_ENTRY_SIZE:
                    raise ValueError(
                        f"Modrinth override is too large: {member} ({info.file_size} bytes)"
                    )

                total_size += info.file_size
                if total_size > cls.MAX_OVERRIDE_TOTAL_SIZE:
                    raise ValueError(
                        f"Modrinth overrides are too large: "
                        f"{total_size} > {cls.MAX_OVERRIDE_TOTAL_SIZE} bytes"
                    )

                relative_path = cls._safe_relative_path(member.removeprefix("overrides/"))
                cls._assert_managed_path_allowed(relative_path)
                cls._safe_destination(game_path, relative_path)
                overrides.append(
                    _ModrinthOverrideFile(
                        relative_path=relative_path,
                        archive_index=archive_index,
                        archive_name=info.filename,
                        size=info.file_size,
                        crc=info.CRC,
                    )
                )

        if len(overrides) > cls.MAX_OVERRIDE_MEMBERS:
            raise ValueError(
                f"Modrinth overrides contain too many files: "
                f"{len(overrides)} > {cls.MAX_OVERRIDE_MEMBERS}"
            )
        cls._validate_unique_paths(
            [item.relative_path for item in overrides],
            label="override",
        )
        return overrides

    @classmethod
    def _stage_overrides(
        cls,
        mrpack_path: Path,
        overrides: Sequence[_ModrinthOverrideFile],
        destination_for: Callable[[PurePosixPath], Path],
    ) -> None:
        if not overrides:
            return

        with zipfile.ZipFile(str(mrpack_path), "r") as archive:
            members = archive.infolist()
            for item in overrides:
                if item.archive_index >= len(members):
                    raise ValueError("Modrinth archive changed after preflight")
                info = members[item.archive_index]
                if (
                    info.filename != item.archive_name
                    or info.file_size != item.size
                    or info.CRC != item.crc
                    or cls._zip_info_is_link(info)
                ):
                    raise ValueError("Modrinth archive changed after preflight")

                target = destination_for(item.relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                try:
                    with archive.open(info, "r") as source, target.open("wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > item.size or copied > cls.MAX_OVERRIDE_ENTRY_SIZE:
                                raise ValueError(
                                    f"Modrinth override exceeded its declared size: {info.filename}"
                                )
                            destination.write(chunk)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise

                if copied != item.size:
                    target.unlink(missing_ok=True)
                    raise ValueError(
                        f"Modrinth override size mismatch: "
                        f"{info.filename} ({copied} != {item.size})"
                    )
                cls._assert_staged_file(target, expected_size=item.size)

    @classmethod
    def _build_download_tasks(cls, index: dict[str, Any], game_path: Path) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        relative_paths: list[PurePosixPath] = []
        files = index.get("files", [])
        if not isinstance(files, list):
            raise ValueError("Modrinth pack files have an invalid format")
        if len(files) > cls.MAX_INDEX_FILES:
            raise ValueError(
                f"Modrinth pack contains too many indexed files: "
                f"{len(files)} > {cls.MAX_INDEX_FILES}"
            )

        for file_info in files:
            if not isinstance(file_info, dict):
                raise ValueError("Modrinth pack contains an invalid file entry")
            environment = file_info.get("env")
            if isinstance(environment, dict) and str(environment.get("client") or "").lower() == "unsupported":
                continue
            downloads = file_info.get("downloads", [])
            if not isinstance(downloads, list) or not downloads:
                raise ValueError(f"Modrinth pack file has no download URL: {file_info.get('path')}")

            relative_path = cls._safe_relative_path(file_info.get("path"))
            cls._assert_managed_path_allowed(relative_path)
            destination = cls._safe_destination(game_path, relative_path)
            hashes = file_info.get("hashes") or {}
            if not isinstance(hashes, dict):
                hashes = {}
            expected_hash_algorithm = next(
                (algorithm for algorithm in ("sha512", "sha256", "sha1") if hashes.get(algorithm)),
                None,
            )
            if expected_hash_algorithm is None:
                raise ValueError(f"Modrinth pack file is missing a supported hash: {file_info.get('path')}")
            expected_hash = str(hashes.get(expected_hash_algorithm) or "").strip().lower()
            expected_size = cls.normalized_size(file_info.get("fileSize"))
            if expected_size is None:
                raise ValueError(
                    f"Modrinth pack file is missing a positive declared size: "
                    f"{file_info.get('path')}"
                )

            relative_paths.append(relative_path)
            tasks.append(
                DownloadTask(
                    url=cls.validated_download_url(downloads[0]),
                    destination=destination,
                    expected_size=expected_size,
                    expected_hash=expected_hash,
                    expected_hash_algorithm=expected_hash_algorithm,
                    task_id=str(file_info.get("path") or destination.name),
                )
            )

        cls._validate_unique_paths(relative_paths, label="indexed file")
        return tasks

    def install_content(
        self,
        *,
        mrpack_path: Path,
        index: dict[str, Any],
        game_path: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        before_activate: Callable[[], None] | None = None,
        commit_callback: Callable[[], None] | None = None,
    ) -> None:
        game_root = game_path.resolve()
        overrides = self._preflight_overrides(mrpack_path, game_path)
        download_tasks = self._build_download_tasks(index, game_path)
        indexed_paths = [
            PurePosixPath(task.destination.resolve().relative_to(game_root).as_posix())
            for task in download_tasks
        ]
        managed_paths = [*(item.relative_path for item in overrides), *indexed_paths]
        self._validate_unique_paths(managed_paths, label="managed file")
        metadata_path = self.MANAGED_METADATA_PATH
        replacements = [*managed_paths, metadata_path]
        self._validate_unique_paths(replacements, label="transaction")
        current_keys = {path.as_posix().casefold() for path in managed_paths}
        stale_paths = [
            path
            for path in self._read_managed_paths(game_path)
            if path.as_posix().casefold() not in current_keys
        ]
        for stale_path in stale_paths:
            self._safe_destination(game_path, stale_path)

        transaction = FileTransaction(
            game_path,
            FileTransactionPlan(
                operation="modrinth-pack-install",
                replacements=replacements,
                stale=stale_paths,
                staged_bytes=(
                    sum(item.size for item in overrides)
                    + sum(
                        max(0, int(task.expected_size or 0))
                        for task in download_tasks
                    )
                    + self.MAX_MANAGED_METADATA_SIZE
                ),
            ),
        )

        def stage(current: FileTransaction) -> None:
            if overrides:
                self._logger.info(f"Staging {len(overrides)} Modrinth override files")
                self._stage_overrides(
                    mrpack_path,
                    overrides,
                    current.stage_path,
                )
            self._stage_downloads(
                current,
                download_tasks,
                game_path,
                progress_callback=progress_callback,
            )
            self._write_managed_metadata(
                current.stage_path(metadata_path),
                managed_paths,
            )

        def validate() -> None:
            self._assert_safe_destinations(
                game_path,
                [*replacements, *stale_paths],
            )

        transaction.execute(
            stage,
            validate=validate,
            before_activate=before_activate,
            commit=commit_callback,
            commit_key=(
                build_commit_key("modrinth-pack-profile", index)
                if commit_callback is not None
                else None
            ),
        )

    def _stage_downloads(
        self,
        transaction: FileTransaction,
        download_tasks: Sequence[DownloadTask],
        game_path: Path,
        *,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> None:
        if not download_tasks:
            self._logger.info("No Modrinth pack files to download")
            return

        game_root = game_path.resolve()
        staged_tasks: list[DownloadTask] = []
        for task in download_tasks:
            staged_task = copy(task)
            relative_path = task.destination.resolve().relative_to(game_root)
            staged_task.destination = transaction.stage_path(relative_path.as_posix())
            staged_tasks.append(staged_task)

        self._logger.info(
            f"Starting parallel download of {len(staged_tasks)} Modrinth pack files"
        )
        result = self._downloader_factory(max_workers=6).download_files(
            staged_tasks,
            progress_callback=progress_callback,
            skip_existing=False,
            verify_existing_hash=True,
        )
        self._logger.info(
            f"Downloaded {result['success']} files, "
            f"skipped {result['skipped']} existing files, "
            f"{result['failed']} failed",
        )
        for error in result["errors"][:5]:
            self._logger.error(f"Download error: {error}")
        if result["failed"] or result["errors"]:
            details = (
                "; ".join(result["errors"][:5])
                if result["errors"]
                else f"{result['failed']} files failed"
            )
            raise RuntimeError(f"Failed to install Modrinth pack files: {details}")

        self._verify_staged_downloads(
            download_tasks,
            transaction.stage_path,
            game_path,
        )

    @classmethod
    def _read_managed_paths(cls, game_path: Path) -> list[PurePosixPath]:
        metadata_path = cls._safe_destination(game_path, cls.MANAGED_METADATA_PATH)
        if not metadata_path.exists():
            return []
        if metadata_path.stat().st_size > cls.MAX_MANAGED_METADATA_SIZE:
            raise ValueError("Modrinth pack managed metadata is too large")

        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Modrinth pack managed metadata is invalid") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != cls.MANAGED_METADATA_SCHEMA
            or not isinstance(payload.get("managed_files"), list)
        ):
            raise ValueError("Modrinth pack managed metadata has an invalid format")

        values = payload["managed_files"]
        if len(values) > cls.MAX_MANAGED_PATHS:
            raise ValueError("Modrinth pack managed metadata contains too many paths")
        if any(not isinstance(value, str) for value in values):
            raise ValueError("Modrinth pack managed metadata contains an invalid path")
        paths = [cls._safe_relative_path(value) for value in values]
        for path in paths:
            cls._assert_managed_path_allowed(path)
        cls._validate_unique_paths(paths, label="managed metadata")
        return paths

    @classmethod
    def _write_managed_metadata(
        cls,
        output_path: Path,
        managed_paths: Sequence[PurePosixPath],
    ) -> None:
        cls._validate_unique_paths(managed_paths, label="managed metadata")
        payload = {
            "schema_version": cls.MANAGED_METADATA_SCHEMA,
            "managed_files": [path.as_posix() for path in managed_paths],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cls._assert_staged_file(output_path)

    @classmethod
    def _verify_staged_downloads(
        cls,
        tasks: Sequence[DownloadTask],
        destination_for: Callable[[PurePosixPath], Path],
        game_path: Path,
    ) -> None:
        game_root = game_path.resolve()
        for task in tasks:
            relative_path = PurePosixPath(
                task.destination.resolve().relative_to(game_root).as_posix()
            )
            staged_path = destination_for(relative_path)
            cls._assert_staged_file(staged_path, expected_size=task.expected_size)
            if not task.expected_hash or not task.expected_hash_algorithm:
                raise ValueError(
                    f"Modrinth staged file is missing verification metadata: "
                    f"{relative_path.as_posix()}"
                )

            digest = hashlib.new(task.expected_hash_algorithm)
            with staged_path.open("rb") as staged_file:
                while chunk := staged_file.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest().lower() != task.expected_hash.lower():
                raise ValueError(
                    f"Modrinth staged file checksum mismatch: {relative_path.as_posix()}"
                )

    @classmethod
    def _assert_safe_destinations(
        cls,
        game_path: Path,
        paths: Sequence[PurePosixPath],
    ) -> None:
        for path in paths:
            cls._safe_destination(game_path, path)

    @classmethod
    def _validate_unique_paths(
        cls,
        paths: Sequence[PurePosixPath],
        *,
        label: str,
    ) -> None:
        normalized = sorted(paths, key=lambda path: path.as_posix().casefold())
        seen: dict[str, PurePosixPath] = {}
        for path in normalized:
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError(f"Modrinth pack contains a duplicate {label} path: {path}")
            for parent in path.parents:
                if parent == PurePosixPath("."):
                    continue
                parent_path = seen.get(parent.as_posix().casefold())
                if parent_path is not None:
                    raise ValueError(
                        f"Modrinth pack contains conflicting {label} paths: "
                        f"{parent_path} and {path}"
                    )
            seen[key] = path

    @classmethod
    def _assert_managed_path_allowed(cls, path: PurePosixPath) -> None:
        key = path.as_posix().casefold()
        metadata_key = cls.MANAGED_METADATA_PATH.as_posix().casefold()
        if (
            key == metadata_key
            or key.startswith(f"{metadata_key}/")
            or key in cls._INTERNAL_PATHS
            or any(
                key == prefix or key.startswith(f"{prefix}/")
                for prefix in cls._INTERNAL_DIRECTORY_PREFIXES
            )
        ):
            raise ValueError(f"Modrinth pack contains a reserved path: {path.as_posix()}")

    @classmethod
    def _assert_no_links(cls, root: Path, relative_path: PurePosixPath) -> None:
        current = root
        if cls._is_link_or_reparse(current):
            raise ValueError(
                f"Modrinth pack destination uses a symbolic link or reparse point: {root}"
            )
        last_index = len(relative_path.parts) - 1
        for index, part in enumerate(relative_path.parts):
            current = current / part
            if cls._is_link_or_reparse(current):
                raise ValueError(
                    "Modrinth pack destination uses a symbolic link or reparse point: "
                    f"{relative_path.as_posix()}"
                )
            if index < last_index and current.exists() and not current.is_dir():
                raise ValueError(
                    f"Modrinth pack path has a non-directory parent: "
                    f"{relative_path.as_posix()}"
                )

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        attributes = getattr(metadata, "st_file_attributes", 0)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def _zip_info_is_link(info: zipfile.ZipInfo) -> bool:
        unix_mode = info.external_attr >> 16
        return bool(unix_mode and stat.S_ISLNK(unix_mode))

    @classmethod
    def _assert_staged_file(
        cls,
        path: Path,
        *,
        expected_size: int | None = None,
    ) -> None:
        if cls._is_link_or_reparse(path) or not path.is_file():
            raise ValueError(f"Modrinth staged file is missing or unsafe: {path.name}")
        if expected_size is not None and path.stat().st_size != expected_size:
            raise ValueError(
                f"Modrinth staged file size mismatch: "
                f"{path.name} ({path.stat().st_size} != {expected_size})"
            )


__all__ = [
    "ModrinthPackService",
]
