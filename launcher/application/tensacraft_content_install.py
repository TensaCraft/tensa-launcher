from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.file_transaction import FileTransaction, FileTransactionPlan
from launcher.core.async_downloader import AsyncDownloader, DownloadTask
from launcher.models.logger import Logger

DownloadProgress = Callable[[int, int, str], None]
DownloaderFactory = Callable[[int], AsyncDownloader]


class TensaCraftContentAPI(Protocol):
    def get_force_update_manifest(
        self,
        client: str,
        *,
        include_directory_files: bool = True,
    ) -> dict[str, Any] | None: ...

    def get_version_files(self, client: str) -> list[dict[str, Any]] | None: ...

    def relative_path(self, file_data: dict[str, Any]) -> str: ...

    def expected_hash(self, file_data: dict[str, Any]) -> tuple[str | None, str | None]: ...

    def is_mod_file(self, file_data: dict[str, Any]) -> bool: ...


@dataclass(frozen=True, slots=True)
class TensaCraftContentPlan:
    root: Path
    api_available: bool
    managed_directories: frozenset[PurePosixPath] = frozenset()
    stale_paths: tuple[PurePosixPath, ...] = ()
    download_tasks: tuple[DownloadTask, ...] = ()
    eligible_files: int = 0
    force: bool = False
    force_changes: bool = False

    @property
    def has_changes(self) -> bool:
        return self.force_changes or bool(self.stale_paths or self.download_tasks)


class TensaCraftContentInstall:
    _TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}
    _FORCE_UPDATE_KEYS = ("force_update", "forceUpdate")
    _FORCE_UPDATE_LOCKED_KEYS = ("force_update_locked", "forceUpdateLocked")
    _DIRECTORY_SCOPE_KEYS = (
        "force_update_scope",
        "forceUpdateScope",
        "sync_root",
        "syncRoot",
        "sync_directory",
        "syncDirectory",
    )
    _DIRECTORY_MARKER_KEYS = (
        "force_update_directory",
        "forceUpdateDirectory",
        "force_update_folder",
        "forceUpdateFolder",
    )

    def __init__(
        self,
        api: TensaCraftContentAPI,
        *,
        max_workers: int = 6,
        downloader_factory: DownloaderFactory | None = None,
    ) -> None:
        self.api = api
        self.max_workers = max(1, max_workers)
        self._downloader_factory = downloader_factory or (
            lambda workers: AsyncDownloader(max_workers=workers)
        )

    @staticmethod
    def needs_recovery(root: Path) -> bool:
        return FileSyncJournal(root).needs_repair()

    @staticmethod
    def recover(root: Path) -> bool:
        journal = FileSyncJournal(root)
        return journal.recover() if journal.needs_repair() else False

    @staticmethod
    def commit_recovery_pending(root: Path) -> bool:
        payload = FileSyncJournal(root).read() or {}
        return str(payload.get("status") or "").strip().lower() == "committing"

    def download_pack_files(
        self,
        root: Path,
        files: Sequence[dict[str, Any]],
        *,
        progress: DownloadProgress | None = None,
    ) -> None:
        root = Path(root).resolve()
        tasks, eligible_files = self._build_download_tasks(
            root,
            files,
            mods_only=False,
        )
        if not tasks and eligible_files == 0:
            Logger.warning("No downloadable files returned for Tensa pack install")
            return

        relative_paths = self._task_relative_paths(root, tasks)
        transaction = FileTransaction(
            root,
            FileTransactionPlan(
                operation="tensacraft_install_content",
                replacements=relative_paths,
                staged_bytes=self._staged_bytes(tasks),
            ),
        )

        def stage(current: FileTransaction) -> None:
            staged_tasks = self._staged_tasks(current, tasks, relative_paths)
            result = self._download(staged_tasks, progress=progress)
            if result["failed"]:
                details = f": {result['errors'][0]}" if result.get("errors") else ""
                raise RuntimeError(
                    f"Failed to download {result['failed']} files for Tensa pack{details}"
                )

        transaction.execute(stage)

    def prepare(
        self,
        root: Path,
        version_key: str,
        *,
        preserve_rules: Any = None,
        force: bool = False,
    ) -> TensaCraftContentPlan:
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        force_sync = bool(force or self.needs_recovery(root))

        force_manifest = self.api.get_force_update_manifest(
            version_key,
            include_directory_files=True,
        )
        manifest_mode = force_manifest is not None
        api_files = (
            self._manifest_files(force_manifest)
            if manifest_mode
            else self.api.get_version_files(version_key)
        )
        normalized_preserve_rules = self._normalize_preserve_rules(
            preserve_rules
            if preserve_rules is not None
            else (
                force_manifest.get("preserve_rules")
                if isinstance(force_manifest, dict)
                else None
            )
        )
        if api_files is None:
            Logger.warning(
                f"API files for version {version_key} could not be retrieved"
            )
            return TensaCraftContentPlan(root=root, api_available=False)

        entries: list[dict[str, Any]] = []
        has_force_metadata = manifest_mode
        managed_directories = (
            self._managed_directories_from_manifest(force_manifest)
            if manifest_mode
            else set()
        )
        for file_data in api_files:
            relative_path = self._file_relative_path(file_data)
            has_force_metadata = (
                has_force_metadata or self._has_force_metadata(file_data)
            )
            explicit_scope = self._explicit_directory_scope(
                file_data,
                relative_path,
            )
            if explicit_scope is not None:
                managed_directories.add(explicit_scope)
            entries.append(
                {
                    "file_data": file_data,
                    "relative_path": relative_path,
                    "force_update": self._force_update_enabled(file_data),
                    "force_update_locked": self._force_update_locked(file_data),
                    "downloadable": bool(file_data.get("download_url")),
                }
            )

        if has_force_metadata and not manifest_mode:
            managed_directories.update(
                self._infer_locked_directory_scopes(entries)
            )

        managed_files: list[dict[str, Any]] = []
        expected_paths: set[PurePosixPath] = set()
        for entry in entries:
            relative_path = entry["relative_path"]
            if relative_path is None or not entry["downloadable"]:
                continue

            if manifest_mode:
                is_managed = True
            elif has_force_metadata:
                is_managed = bool(entry["force_update"]) or any(
                    self._path_is_under(relative_path, directory)
                    for directory in managed_directories
                )
            else:
                is_managed = self.api.is_mod_file(entry["file_data"])

            if not is_managed:
                continue
            managed_files.append(entry["file_data"])
            expected_paths.add(relative_path)

        tasks, eligible_files = self._build_download_tasks(
            root,
            managed_files,
            mods_only=False,
        )
        if not force_sync:
            downloader = self._downloader_factory(self.max_workers)
            tasks = [
                task
                for task in tasks
                if not downloader._should_skip(task, verify_sha1=True)
            ]
        elif tasks:
            Logger.warning(
                "Forcing TensaCraft managed file repair sync "
                f"for version {version_key}"
            )

        stale_paths = self._collect_stale_paths(
            root,
            managed_directories,
            expected_paths,
            normalized_preserve_rules,
        )
        return TensaCraftContentPlan(
            root=root,
            api_available=True,
            managed_directories=frozenset(managed_directories),
            stale_paths=tuple(stale_paths),
            download_tasks=tuple(tasks),
            eligible_files=eligible_files,
            force=force_sync,
        )

    def apply(
        self,
        plan: TensaCraftContentPlan,
        *,
        version_key: str,
        progress: DownloadProgress | None = None,
        commit: Callable[[], None] | None = None,
        commit_key: str | None = None,
    ) -> None:
        if not plan.api_available:
            return
        if not plan.has_changes:
            if plan.eligible_files == 0:
                Logger.warning("No download tasks created!")
            else:
                Logger.info(
                    f"All managed files already present for version {version_key}"
                )
            return

        journal = FileSyncJournal(plan.root)
        cleaned_tmp = journal.cleanup_temporary_downloads(
            Path(directory.as_posix())
            for directory in plan.managed_directories
        )
        if cleaned_tmp:
            Logger.info(
                f"Removed {cleaned_tmp} stale temporary Tensa download files"
            )

        relative_paths = self._task_relative_paths(
            plan.root,
            plan.download_tasks,
        )
        transaction = FileTransaction(
            plan.root,
            FileTransactionPlan(
                operation="tensacraft_sync",
                replacements=relative_paths,
                stale=plan.stale_paths,
                staged_bytes=self._staged_bytes(plan.download_tasks),
            ),
        )

        def stage(current: FileTransaction) -> None:
            staged_tasks = self._staged_tasks(
                current,
                plan.download_tasks,
                relative_paths,
            )
            if not staged_tasks:
                return
            Logger.info(
                f"Downloading {len(staged_tasks)} managed files into verified staging"
            )
            result = self._download(staged_tasks, progress=progress)
            Logger.info(
                f"Download results: {result['success']} success, "
                f"{result['failed']} failed, {result['skipped']} skipped"
            )
            for error in result.get("errors", [])[:5]:
                Logger.error(f"Download error: {error}")
            if result["failed"] or result.get("errors"):
                details = (
                    "; ".join(result["errors"][:5])
                    if result.get("errors")
                    else f"{result['failed']} files failed"
                )
                raise RuntimeError(
                    f"Failed to synchronize TensaCraft files: {details}"
                )

        def commit_transaction() -> None:
            self._remove_empty_managed_directories(
                plan.root,
                plan.managed_directories,
            )
            if commit is not None:
                commit()

        transaction.execute(
            stage,
            commit=commit_transaction if commit is not None else None,
            commit_key=commit_key if commit is not None else None,
        )
        if commit is None:
            self._remove_empty_managed_directories(
                plan.root,
                plan.managed_directories,
            )
        Logger.info(
            f"Managed files synchronization complete for version: {version_key}"
        )

    def _build_download_tasks(
        self,
        root: Path,
        files: Sequence[dict[str, Any]],
        *,
        mods_only: bool,
    ) -> tuple[list[DownloadTask], int]:
        tasks: list[DownloadTask] = []
        eligible_files = 0
        resolved_root = root.resolve()
        for file_data in files:
            if mods_only and not self.api.is_mod_file(file_data):
                continue

            download_url = file_data.get("download_url")
            relative_path = self._safe_relative_path(
                self.api.relative_path(file_data)
            )
            if not download_url or relative_path is None:
                continue
            destination = (
                resolved_root / Path(relative_path.as_posix())
            ).resolve()
            if not destination.is_relative_to(resolved_root):
                Logger.warning(
                    f"Skipping unsafe Tensa destination from API: {relative_path}"
                )
                continue

            eligible_files += 1
            expected_size = self._expected_size(file_data.get("size"))
            expected_hash, expected_hash_algorithm = self.api.expected_hash(
                file_data
            )
            tasks.append(
                DownloadTask(
                    url=str(download_url),
                    destination=destination,
                    expected_size=expected_size,
                    expected_hash=expected_hash,
                    expected_hash_algorithm=expected_hash_algorithm,
                    task_id=relative_path.as_posix(),
                )
            )
        return tasks, eligible_files

    def _download(
        self,
        tasks: Sequence[DownloadTask],
        *,
        progress: DownloadProgress | None,
    ) -> dict[str, Any]:
        return self._downloader_factory(self.max_workers).download_files(
            list(tasks),
            progress_callback=progress,
            skip_existing=False,
        )

    @staticmethod
    def _staged_tasks(
        transaction: FileTransaction,
        tasks: Sequence[DownloadTask],
        relative_paths: Sequence[str],
    ) -> list[DownloadTask]:
        return [
            DownloadTask(
                url=task.url,
                destination=transaction.stage_path(relative_path),
                expected_size=task.expected_size,
                expected_hash=task.expected_hash,
                expected_hash_algorithm=task.expected_hash_algorithm,
                task_id=task.task_id,
                post_data=task.post_data,
            )
            for task, relative_path in zip(tasks, relative_paths, strict=True)
        ]

    @staticmethod
    def _staged_bytes(tasks: Sequence[DownloadTask]) -> int:
        return sum(max(0, int(task.expected_size or 0)) for task in tasks)

    @classmethod
    def _task_relative_paths(
        cls,
        root: Path,
        tasks: Sequence[DownloadTask],
    ) -> list[str]:
        relative_paths = [
            cls._relative_to_root(root, task.destination, label="destination").as_posix()
            for task in tasks
        ]
        normalized = [path.casefold() for path in relative_paths]
        if len(normalized) != len(set(normalized)):
            raise RuntimeError(
                "TensaCraft synchronization contains duplicate destinations"
            )
        return relative_paths

    @staticmethod
    def _expected_size(value: Any) -> int | None:
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                return None
        return value if isinstance(value, int) and value > 0 else None

    @classmethod
    def _is_truthy(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in cls._TRUTHY_VALUES
        return bool(value)

    @classmethod
    def _has_force_metadata(cls, file_data: dict[str, Any]) -> bool:
        keys = set(file_data)
        return bool(
            keys.intersection(cls._FORCE_UPDATE_KEYS)
            or keys.intersection(cls._FORCE_UPDATE_LOCKED_KEYS)
            or keys.intersection(cls._DIRECTORY_SCOPE_KEYS)
            or keys.intersection(cls._DIRECTORY_MARKER_KEYS)
        )

    @staticmethod
    def _manifest_files(
        manifest: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        files = manifest.get("files") if isinstance(manifest, dict) else None
        return (
            [item for item in files if isinstance(item, dict)]
            if isinstance(files, list)
            else []
        )

    @staticmethod
    def _manifest_directories(
        manifest: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        directories = (
            manifest.get("directories") if isinstance(manifest, dict) else None
        )
        if not isinstance(directories, list):
            return []
        return [
            item
            for item in directories
            if isinstance(item, dict)
            and str(item.get("sync_scope") or "directory").lower()
            == "directory"
        ]

    @classmethod
    def _force_update_enabled(cls, file_data: dict[str, Any]) -> bool:
        return any(
            cls._is_truthy(file_data.get(key))
            for key in cls._FORCE_UPDATE_KEYS
        )

    @classmethod
    def _force_update_locked(cls, file_data: dict[str, Any]) -> bool:
        return any(
            cls._is_truthy(file_data.get(key))
            for key in cls._FORCE_UPDATE_LOCKED_KEYS
        )

    @staticmethod
    def _safe_relative_path(value: Any) -> PurePosixPath | None:
        text = str(value or "").strip().replace("\\", "/").lstrip("/")
        if not text:
            return None
        if len(text) >= 2 and text[1] == ":":
            Logger.warning(f"Skipping unsafe Tensa path from API: {text}")
            return None
        relative = PurePosixPath(text)
        if relative.is_absolute() or ".." in relative.parts:
            Logger.warning(f"Skipping unsafe Tensa path from API: {text}")
            return None
        if not relative.parts or relative == PurePosixPath("."):
            return None
        return relative

    @classmethod
    def _required_relative_path(
        cls,
        value: Any,
        *,
        label: str,
    ) -> PurePosixPath:
        relative = cls._safe_relative_path(value)
        if relative is None:
            raise RuntimeError(f"TensaCraft synchronization contains an unsafe {label}")
        return relative

    @classmethod
    def _relative_to_root(
        cls,
        root: Path,
        value: Any,
        *,
        label: str,
    ) -> PurePosixPath:
        path = Path(value)
        if not path.is_absolute():
            return cls._required_relative_path(path, label=label)
        try:
            return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix())
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"TensaCraft synchronization contains an unsafe {label}"
            ) from exc

    def _file_relative_path(
        self,
        file_data: dict[str, Any],
    ) -> PurePosixPath | None:
        return self._safe_relative_path(self.api.relative_path(file_data))

    def _file_directory_path(
        self,
        file_data: dict[str, Any],
        relative_path: PurePosixPath | None,
    ) -> PurePosixPath | None:
        path = self._safe_relative_path(file_data.get("path"))
        if path is not None:
            return path
        if relative_path is None or len(relative_path.parts) <= 1:
            return None
        return relative_path.parent

    def _explicit_directory_scope(
        self,
        file_data: dict[str, Any],
        relative_path: PurePosixPath | None,
    ) -> PurePosixPath | None:
        for key in self._DIRECTORY_SCOPE_KEYS:
            value = file_data.get(key)
            if isinstance(value, str) and value.strip():
                return self._safe_relative_path(value)

        force_directory = file_data.get(
            "force_update_directory"
        ) or file_data.get("forceUpdateDirectory")
        if isinstance(force_directory, str) and force_directory.strip():
            return self._safe_relative_path(force_directory)

        if any(
            self._is_truthy(file_data.get(key))
            for key in self._DIRECTORY_MARKER_KEYS
        ):
            return self._file_directory_path(file_data, relative_path)

        if str(file_data.get("sync_scope") or "").strip().lower() == "directory":
            return self._file_directory_path(file_data, relative_path)

        entry_type = str(file_data.get("type") or "").strip().lower()
        if entry_type in {"directory", "folder", "dir"}:
            return relative_path or self._file_directory_path(
                file_data,
                relative_path,
            )

        raw_relative = str(
            file_data.get("relative_path") or ""
        ).strip().replace("\\", "/")
        if raw_relative.endswith("/") and self._force_update_enabled(file_data):
            return self._safe_relative_path(raw_relative)

        if (
            not file_data.get("download_url")
            and self._force_update_enabled(file_data)
        ):
            return relative_path or self._file_directory_path(
                file_data,
                relative_path,
            )
        return None

    def _managed_directories_from_manifest(
        self,
        manifest: dict[str, Any] | None,
    ) -> set[PurePosixPath]:
        managed_directories: set[PurePosixPath] = set()
        for directory_data in self._manifest_directories(manifest):
            path = self._safe_relative_path(directory_data.get("path"))
            if path is not None:
                managed_directories.add(path)
        return managed_directories

    @staticmethod
    def _path_is_under(path: PurePosixPath, root: PurePosixPath) -> bool:
        root_parts = tuple(part for part in root.parts if part != ".")
        if not root_parts:
            return False
        return tuple(path.parts[: len(root_parts)]) == root_parts

    def _infer_locked_directory_scopes(
        self,
        entries: list[dict[str, Any]],
    ) -> set[PurePosixPath]:
        by_directory: dict[PurePosixPath, list[dict[str, Any]]] = {}
        for entry in entries:
            relative_path = entry["relative_path"]
            if relative_path is None or not entry["downloadable"]:
                continue
            directory = self._file_directory_path(
                entry["file_data"],
                relative_path,
            )
            if directory is not None:
                by_directory.setdefault(directory, []).append(entry)

        scopes: set[PurePosixPath] = set()
        for directory, directory_entries in by_directory.items():
            all_forced = all(
                entry["force_update"] for entry in directory_entries
            )
            all_locked = all(
                entry["force_update_locked"] for entry in directory_entries
            )
            if all_forced and (
                all_locked or directory == PurePosixPath("mods")
            ):
                scopes.add(directory)
        return scopes

    def _collect_stale_paths(
        self,
        root: Path,
        managed_directories: set[PurePosixPath],
        expected_paths: set[PurePosixPath],
        preserve_rules: list[dict[str, Any]],
    ) -> list[PurePosixPath]:
        stale_paths: list[PurePosixPath] = []
        seen_paths: set[PurePosixPath] = set()
        for directory in sorted(
            managed_directories,
            key=lambda path: path.as_posix(),
        ):
            directory_root = (
                root / Path(directory.as_posix())
            ).resolve()
            if (
                not directory_root.is_relative_to(root)
                or directory_root == root
            ):
                Logger.warning(
                    f"Skipping unsafe Tensa sync directory from API: {directory}"
                )
                continue
            if not directory_root.is_dir():
                continue

            for local_path in directory_root.rglob("*"):
                if not local_path.is_file():
                    continue
                relative = PurePosixPath(
                    local_path.relative_to(root).as_posix()
                )
                if (
                    relative in seen_paths
                    or self._is_preserved_path(relative, preserve_rules)
                    or relative in expected_paths
                ):
                    continue
                seen_paths.add(relative)
                stale_paths.append(relative)
        return stale_paths

    def _normalize_preserve_rules(
        self,
        rules: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(rules, list):
            return []

        normalized: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if "enabled" in rule and not self._is_truthy(rule.get("enabled")):
                continue
            rule_type = str(rule.get("type") or "").strip().lower()
            if rule_type not in {"file", "glob", "directory"}:
                continue
            relative_path = self._safe_relative_path(rule.get("path"))
            if relative_path is not None:
                normalized.append(
                    {"type": rule_type, "path": relative_path}
                )
        return normalized

    def _is_preserved_path(
        self,
        relative_path: PurePosixPath,
        rules: list[dict[str, Any]],
    ) -> bool:
        for rule in rules:
            rule_type = str(rule.get("type") or "")
            rule_path = rule.get("path")
            if not isinstance(rule_path, PurePosixPath):
                continue
            if rule_type == "file" and relative_path == rule_path:
                return True
            if (
                rule_type == "directory"
                and self._path_is_under(relative_path, rule_path)
            ):
                return True
            if (
                rule_type == "glob"
                and relative_path.match(rule_path.as_posix())
            ):
                return True
        return False

    @staticmethod
    def _remove_empty_managed_directories(
        root: Path,
        managed_directories: frozenset[PurePosixPath],
    ) -> None:
        for directory in managed_directories:
            directory_root = (
                root / Path(directory.as_posix())
            ).resolve()
            if (
                not directory_root.is_dir()
                or not directory_root.is_relative_to(root)
            ):
                continue
            subdirectories = sorted(
                (path for path in directory_root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for path in subdirectories:
                try:
                    path.rmdir()
                except OSError:
                    continue


__all__ = [
    "TensaCraftContentAPI",
    "TensaCraftContentInstall",
    "TensaCraftContentPlan",
]
