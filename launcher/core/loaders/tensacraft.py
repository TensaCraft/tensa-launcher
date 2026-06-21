from __future__ import annotations
import inspect
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from launcher.application.feedback import OperationHandle
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.tensacraft_payload import TensaCraftPayloadService
from launcher.core.api import TensaCraftAPI
from launcher.core.async_downloader import AsyncDownloader, DownloadTask
from launcher.core.versions import Version
from launcher.models.logger import Logger
from .base import BaseLoader


class TensaCraftLoader(BaseLoader):
    DOWNLOAD_WORKERS = 6
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

    def __init__(self):
        super().__init__()
        self.api = TensaCraftAPI()
        self.payload = TensaCraftPayloadService()

    def get_id(self) -> str:
        return "tensacraft"

    def get_name(self) -> str:
        return "TensaCraft"

    def install(
        self,
        version: Version,
        callback: Optional[Any] = None,
        java_path: Optional[str] = None,
        loader_version: Optional[str] = None,
        operation: OperationHandle | None = None,
    ) -> None:
        owns_operation = operation is None
        previous_operation = self._feedback_operation
        if operation is None:
            operation = self.begin_feedback_operation(status=self.app.trans("installation_started") if self.app else None)
        else:
            self._feedback_operation = operation
        try:
            display_key = self._primary_version_key(version)
            game_path = self.get_game_path(version.version_id)
            self._ensure_game_directory_idle(game_path, self._pack_display_name(version, fallback=display_key))

            tensa_id, request = self._resolve_version_payload(version)
            Logger.info(f"Installing TensaCraft modpack: {tensa_id}")
            client_data = self.payload.get_client_data(request, tensa_id)
            loader_name = self.payload.apply_install_payload(version, version_key=tensa_id, client_data=client_data)

            from launcher.core import Launcher

            Logger.info(f"Installing base loader {loader_name} for Minecraft {version.version}")
            loader = Launcher.get_loader(loader_name)
            install_parameters = inspect.signature(loader.install).parameters
            if "operation" in install_parameters:
                loader.install(version=version, loader_version=version.loader_version, operation=operation)
            else:
                loader.install(version=version, loader_version=version.loader_version)

            files = self.api.get_version_files(tensa_id)
            if files is None:
                raise ValueError(f"Could not retrieve version files from API for {tensa_id}")

            self._download_pack_files(
                game_path,
                files,
                status_label=self.app.trans("installation_started"),
                operation=operation,
            )

            version.path = str(game_path)
            version.client = self.get_name()
            version.save()

            if callback:
                callback()
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    def versions(self) -> list[str]:
        versions = self.api.get_versions()
        return list(versions) if isinstance(versions, list) else []

    def sync_update(self, version: Version):
        ver_key = self._primary_version_key(version)
        operation = self.begin_feedback_operation(
            status=self.app.trans("syncing_files_check"),
            progress=0,
            max_progress=100,
            title=self.app.trans("syncing_files_check"),
            kind="sync",
            visible=False,
            auto_open=False,
        )
        completed_update = False
        try:
            resolved = self._find_version_payload(version)
            if resolved is None:
                Logger.warning(f"Skipping Tensa sync for {ver_key}: API pack metadata unavailable")
                return
            ver_key, req = resolved

            client_data = req["client"]
            client_version = client_data.get("minecraft_version") or client_data.get("version")
            mod_loader_name = client_data.get("loader_id") or client_data.get("loader")
            client_loader_version = client_data.get("loader_version")

            sync_plan = self._prepare_file_sync(version, ver_key, preserve_rules=client_data.get("preserve_rules"))
            if not sync_plan["api_available"]:
                Logger.warning(f"Skipping Tensa sync for {ver_key}: API files unavailable")
                return

            loader_changed = self.payload.loader_changed(version, client_data)
            runtime_missing = bool(client_version) and not self.runtime.has_runtime(client_version, client_version)
            needs_runtime_prepare = loader_changed or runtime_missing or sync_plan["has_changes"]
            needs_game_directory_update = loader_changed or sync_plan["has_changes"]

            if needs_game_directory_update and getattr(version, "path", None):
                self._ensure_game_directory_idle(Path(version.path), self._pack_display_name(version, fallback=ver_key))

            if loader_changed:
                try:
                    loader_label = " ".join(
                        part for part in (str(mod_loader_name or "").strip(), str(client_loader_version or "").strip()) if part
                    )
                    self._update_feedback_operation(
                        self.app.trans("syncing_loader_update", loader=loader_label or self.app.trans("loaders_label")),
                        progress=15,
                        max_progress=100,
                        operation=operation,
                    )
                    install_kwargs = {
                        "mc_version": client_version,
                        "loader_name": mod_loader_name,
                        "requested_loader_version": client_loader_version,
                    }
                    if operation is not None:
                        install_kwargs["operation"] = operation
                    installed_version_name, actual_loader_version = self._install_mod_loader(**install_kwargs)
                except Exception as exc:
                    Logger.error(f"Failed to install mod loader: {exc}")
                    raise RuntimeError(f"Failed to install mod loader: {exc}") from exc

                version.client = self.get_name()
                version.loader = installed_version_name
                version.loader_version = actual_loader_version
                version.version = client_version
            else:
                version.loader_version = client_loader_version or version.loader_version
                version.version = client_version or version.version

            java_path = (
                self._get_version_java_path(client_version, operation=operation)
                if needs_runtime_prepare
                else self._existing_java_path(client_version)
            )
            self.payload.merge_sync_payload(version, client_data, java_path=java_path)
            version.id = ver_key
            version.save()

            if sync_plan["has_changes"]:
                self._update_feedback_operation(
                    self.app.trans("syncing_files"),
                    progress=25,
                    max_progress=100,
                    operation=operation,
                )
                self._sync_files(version, ver_key, sync_plan, operation=operation)
            completed_update = needs_runtime_prepare
        finally:
            self.finish_feedback_operation(
                operation,
                self.app.trans("syncing_files_complete") if completed_update else None,
                show_success=completed_update,
            )

    def _ensure_game_directory_idle(self, game_path: Path, version_name: str) -> None:
        from launcher.core.game import Game

        if not Game.is_game_dir_active(game_path):
            return
        raise RuntimeError(self.app.trans("tensacraft_game_directory_running", version=version_name))

    def _resolve_version_payload(self, version: Version) -> tuple[str, dict[str, Any]]:
        resolved = self._find_version_payload(version)
        if resolved is not None:
            return resolved
        version_key = self._primary_version_key(version)
        if not version_key:
            raise ValueError("TensaCraft version key is missing")
        raise ValueError(f"No version data found for the specified version: {version_key}")

    def _find_version_payload(self, version: Version) -> tuple[str, dict[str, Any]] | None:
        for version_key in self._candidate_version_keys(version):
            payload = self.api.get_versions(version_key)
            if isinstance(payload, dict) and payload.get("client"):
                pack_id = self.api.pack_id(payload) or version_key
                return pack_id, payload
        return None

    @staticmethod
    def _candidate_version_keys(version: Version) -> list[str]:
        raw_options = getattr(version, "options", None)
        option_pack_id = None
        if isinstance(raw_options, dict):
            option_pack_id = raw_options.get("tensacraftPackId") or raw_options.get("tensacraft_pack_id")

        raw_candidates = (
            getattr(version, "remote_pack_id", None),
            option_pack_id,
            getattr(version, "id", None),
            getattr(version, "version_id", None),
            getattr(version, "name", None),
            getattr(version, "version", None),
        )
        candidates: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_candidates:
            value = str(raw_value or "").strip()
            key = value.lower()
            if not value or key in seen:
                continue
            seen.add(key)
            candidates.append(value)
        return candidates

    def _primary_version_key(self, version: Version) -> str:
        candidates = self._candidate_version_keys(version)
        return candidates[0] if candidates else ""

    @staticmethod
    def _pack_display_name(version: Version, fallback: str | None = None) -> str:
        raw_name = (
            getattr(version, "name", None)
            or getattr(version, "display_name", None)
            or getattr(version, "id", None)
            or getattr(version, "version", None)
            or fallback
            or "TensaCraft"
        )
        words = str(raw_name).replace("_", " ").replace("-", " ").split()
        return " ".join(word[:1].upper() + word[1:] for word in words) if words else "TensaCraft"

    def _existing_java_path(self, minecraft_version: str | None) -> str | None:
        if not minecraft_version:
            return None
        runtime_name = self.runtime.get_runtime_name(minecraft_version)
        if not runtime_name:
            return None
        java_path = self.runtime.get_executable_path(runtime_name)
        if java_path and Path(java_path).is_file():
            return java_path
        return None

    def _build_download_tasks(
        self,
        version_path: Path,
        files: list[dict[str, Any]],
        *,
        mods_only: bool,
    ) -> tuple[list[DownloadTask], int]:
        download_tasks: list[DownloadTask] = []
        eligible_files = 0

        for file_data in files:
            if mods_only and not self.api.is_mod_file(file_data):
                continue

            download_url = file_data.get("download_url")
            relative_path = self._safe_relative_path(self.api.relative_path(file_data))
            if not download_url or relative_path is None:
                continue
            destination = (version_path / Path(relative_path.as_posix())).resolve()
            if not destination.is_relative_to(version_path.resolve()):
                Logger.warning(f"Skipping unsafe Tensa destination from API: {relative_path}")
                continue

            eligible_files += 1
            task_id = relative_path.as_posix()
            expected_size = file_data.get("size")
            if isinstance(expected_size, str):
                try:
                    expected_size = int(expected_size)
                except ValueError:
                    expected_size = None
            if isinstance(expected_size, int) and expected_size <= 0:
                expected_size = None

            expected_hash, expected_hash_algorithm = self.api.expected_hash(file_data)
            download_tasks.append(
                DownloadTask(
                    url=str(download_url),
                    destination=destination,
                    expected_size=expected_size,
                    expected_hash=expected_hash,
                    expected_hash_algorithm=expected_hash_algorithm,
                    task_id=task_id,
                )
            )

        return download_tasks, eligible_files

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
    def _manifest_files(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
        files = manifest.get("files") if isinstance(manifest, dict) else None
        return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []

    @staticmethod
    def _manifest_directories(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
        directories = manifest.get("directories") if isinstance(manifest, dict) else None
        if not isinstance(directories, list):
            return []
        return [
            item
            for item in directories
            if isinstance(item, dict) and str(item.get("sync_scope") or "directory").lower() == "directory"
        ]

    @classmethod
    def _force_update_enabled(cls, file_data: dict[str, Any]) -> bool:
        return any(cls._is_truthy(file_data.get(key)) for key in cls._FORCE_UPDATE_KEYS)

    @classmethod
    def _force_update_locked(cls, file_data: dict[str, Any]) -> bool:
        return any(cls._is_truthy(file_data.get(key)) for key in cls._FORCE_UPDATE_LOCKED_KEYS)

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

    def _file_relative_path(self, file_data: dict[str, Any]) -> PurePosixPath | None:
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

        force_update_directory = file_data.get("force_update_directory") or file_data.get("forceUpdateDirectory")
        if isinstance(force_update_directory, str) and force_update_directory.strip():
            return self._safe_relative_path(force_update_directory)

        if any(self._is_truthy(file_data.get(key)) for key in self._DIRECTORY_MARKER_KEYS):
            return self._file_directory_path(file_data, relative_path)

        if str(file_data.get("sync_scope") or "").strip().lower() == "directory":
            return self._file_directory_path(file_data, relative_path)

        entry_type = str(file_data.get("type") or "").strip().lower()
        if entry_type in {"directory", "folder", "dir"}:
            return relative_path or self._file_directory_path(file_data, relative_path)

        raw_relative = str(file_data.get("relative_path") or "").strip().replace("\\", "/")
        if raw_relative.endswith("/") and self._force_update_enabled(file_data):
            return self._safe_relative_path(raw_relative)

        if not file_data.get("download_url") and self._force_update_enabled(file_data):
            return relative_path or self._file_directory_path(file_data, relative_path)

        return None

    def _managed_directories_from_manifest(self, manifest: dict[str, Any] | None) -> set[PurePosixPath]:
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

    def _infer_locked_directory_scopes(self, entries: list[dict[str, Any]]) -> set[PurePosixPath]:
        by_directory: dict[PurePosixPath, list[dict[str, Any]]] = {}
        for entry in entries:
            relative_path = entry["relative_path"]
            if relative_path is None or not entry["downloadable"]:
                continue
            directory = self._file_directory_path(entry["file_data"], relative_path)
            if directory is None:
                continue
            by_directory.setdefault(directory, []).append(entry)

        scopes: set[PurePosixPath] = set()
        for directory, directory_entries in by_directory.items():
            if not directory_entries:
                continue
            # Current API expresses a fully managed directory as files that are
            # both force-updated and locked. Plain force_update remains file-scoped.
            all_forced = all(entry["force_update"] for entry in directory_entries)
            all_locked = all(entry["force_update_locked"] for entry in directory_entries)
            if all_forced and (all_locked or directory == PurePosixPath("mods")):
                scopes.add(directory)
        return scopes

    def _collect_stale_paths(
        self,
        version_path: Path,
        managed_directories: set[PurePosixPath],
        expected_paths: set[PurePosixPath],
        preserve_rules: list[dict[str, Any]],
    ) -> list[Path]:
        stale_paths: list[Path] = []
        seen_paths: set[Path] = set()
        version_root = version_path.resolve()

        for directory in sorted(managed_directories, key=lambda path: path.as_posix()):
            root = (version_root / Path(directory.as_posix())).resolve()
            if not root.is_relative_to(version_root) or root == version_root:
                Logger.warning(f"Skipping unsafe Tensa sync directory from API: {directory}")
                continue
            if not root.exists() or not root.is_dir():
                continue

            for local_path in root.rglob("*"):
                if not local_path.is_file():
                    continue
                relative = PurePosixPath(local_path.relative_to(version_root).as_posix())
                if self._is_preserved_path(relative, preserve_rules):
                    continue
                if relative not in expected_paths:
                    if local_path in seen_paths:
                        continue
                    seen_paths.add(local_path)
                    stale_paths.append(local_path)

        return stale_paths

    def _normalize_preserve_rules(self, rules: Any) -> list[dict[str, Any]]:
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
            if relative_path is None:
                continue

            normalized.append({"type": rule_type, "path": relative_path})
        return normalized

    def _is_preserved_path(self, relative_path: PurePosixPath, rules: list[dict[str, Any]]) -> bool:
        for rule in rules:
            rule_type = str(rule.get("type") or "")
            rule_path = rule.get("path")
            if not isinstance(rule_path, PurePosixPath):
                continue

            if rule_type == "file" and relative_path == rule_path:
                return True
            if rule_type == "directory" and self._path_is_under(relative_path, rule_path):
                return True
            if rule_type == "glob" and relative_path.match(rule_path.as_posix()):
                return True
        return False

    def _remove_empty_managed_directories(
        self,
        version_path: Path,
        managed_directories: set[PurePosixPath],
    ) -> None:
        version_root = version_path.resolve()
        for directory in managed_directories:
            root = (version_root / Path(directory.as_posix())).resolve()
            if not root.exists() or not root.is_dir() or not root.is_relative_to(version_root):
                continue
            subdirectories = sorted(
                [path for path in root.rglob("*") if path.is_dir()],
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for path in subdirectories:
                try:
                    path.rmdir()
                except OSError:
                    continue

    def _download_pack_files(
        self,
        version_path: Path,
        files: list[dict[str, Any]],
        status_label: str,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        download_tasks, eligible_files = self._build_download_tasks(version_path, files, mods_only=False)
        if not download_tasks and eligible_files == 0:
            Logger.warning("No downloadable files returned for Tensa pack install")
            return

        def progress_callback(completed: int, total: int, current_file: str) -> None:
            self._update_feedback_operation(
                status=current_file or status_label,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        downloader = AsyncDownloader(max_workers=self.DOWNLOAD_WORKERS)
        result = downloader.download_files(
            download_tasks,
            progress_callback=progress_callback,
            skip_existing=False,
        )
        if result["failed"]:
            details = f": {result['errors'][0]}" if result.get("errors") else ""
            raise RuntimeError(f"Failed to download {result['failed']} files for Tensa pack{details}")

    def _prepare_file_sync(
        self,
        version: Version,
        ver_key: str,
        preserve_rules: Any = None,
    ) -> dict[str, Any]:
        version_path = Path(version.path)
        version_path.mkdir(parents=True, exist_ok=True)

        force_manifest = self.api.get_force_update_manifest(ver_key, include_directory_files=True)
        manifest_mode = force_manifest is not None
        api_files = self._manifest_files(force_manifest) if manifest_mode else self.api.get_version_files(ver_key)
        normalized_preserve_rules = self._normalize_preserve_rules(
            preserve_rules if preserve_rules is not None else (
                force_manifest.get("preserve_rules") if isinstance(force_manifest, dict) else None
            )
        )
        if api_files is None:
            Logger.warning(f"API files for version {ver_key} could not be retrieved")
            return {
                "api_available": False,
                "managed_directories": set(),
                "stale_paths": [],
                "download_tasks": [],
                "eligible_files": 0,
                "has_changes": False,
            }

        entries: list[dict[str, Any]] = []
        has_force_metadata = manifest_mode
        managed_directories = self._managed_directories_from_manifest(force_manifest) if manifest_mode else set()
        for file_data in api_files:
            relative_path = self._file_relative_path(file_data)
            has_force_metadata = has_force_metadata or self._has_force_metadata(file_data)
            explicit_scope = self._explicit_directory_scope(file_data, relative_path)
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
            managed_directories.update(self._infer_locked_directory_scopes(entries))
        elif not has_force_metadata:
            managed_directories.add(PurePosixPath("mods"))

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

        downloader = AsyncDownloader(max_workers=self.DOWNLOAD_WORKERS)
        download_tasks, eligible_files = self._build_download_tasks(version_path, managed_files, mods_only=False)
        download_tasks = [task for task in download_tasks if not downloader._should_skip(task, verify_sha1=True)]
        stale_paths = self._collect_stale_paths(
            version_path,
            managed_directories,
            expected_paths,
            normalized_preserve_rules,
        )

        return {
            "api_available": True,
            "managed_directories": managed_directories,
            "stale_paths": stale_paths,
            "download_tasks": download_tasks,
            "eligible_files": eligible_files,
            "has_changes": bool(stale_paths or download_tasks),
        }

    def _sync_files(
        self,
        version: Version,
        ver_key: str,
        sync_plan: dict[str, Any] | None = None,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        sync_plan = sync_plan or self._prepare_file_sync(version, ver_key)
        if not sync_plan.get("api_available", False):
            return

        version_path = Path(version.path)
        managed_directories = sync_plan["managed_directories"]
        download_tasks = sync_plan["download_tasks"]
        eligible_files = sync_plan["eligible_files"]
        stale_paths = sync_plan["stale_paths"]

        if not sync_plan["has_changes"]:
            if eligible_files == 0:
                Logger.warning("No download tasks created!")
            else:
                Logger.info(f"All managed files already present for version {ver_key}")
            return

        version_root = version_path.resolve()
        journal = FileSyncJournal(version_path)
        cleaned_tmp = journal.cleanup_temporary_downloads(managed_directories)
        if cleaned_tmp:
            Logger.info(f"Removed {cleaned_tmp} stale temporary Tensa download files")
        sync_errors: list[str] = []
        for stale_path in stale_paths:
            try:
                if not stale_path.resolve().is_relative_to(version_root):
                    Logger.warning(f"Skipping unsafe stale Tensa file path: {stale_path}")
                    continue
                stale_path.unlink(missing_ok=True)
                relative = stale_path.relative_to(version_root).as_posix()
                Logger.info(f"Removed outdated managed file: {relative}")
            except Exception as exc:
                try:
                    relative = stale_path.relative_to(version_root).as_posix()
                except ValueError:
                    relative = stale_path.name
                error = f"{relative}: {exc}"
                sync_errors.append(error)
                Logger.error(f"Could not remove managed file {stale_path}: {exc}")

        if sync_errors:
            details = "; ".join(sync_errors[:5])
            raise RuntimeError(f"Failed to synchronize TensaCraft files: {details}")

        self._remove_empty_managed_directories(version_path, managed_directories)

        if not download_tasks:
            self._complete_sync(version)
            Logger.info(f"Managed files synchronization complete for version: {ver_key}")
            return

        self._update_feedback_operation(
            self.app.trans("syncing_files"),
            progress=0,
            max_progress=max(len(download_tasks), 1),
            operation=operation,
        )
        Logger.info(f"Preparing to download {len(download_tasks)} managed files")
        Logger.info(f"Starting parallel download of {len(download_tasks)} managed files")
        journal.begin(operation="tensacraft_sync", downloads=len(download_tasks), stale=len(stale_paths))

        def progress_callback(completed, total, current_file):
            self._update_feedback_operation(
                status=current_file,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        downloader = AsyncDownloader(max_workers=self.DOWNLOAD_WORKERS)
        try:
            result = downloader.download_files(
                download_tasks,
                progress_callback=progress_callback,
                skip_existing=False,
            )
        except Exception as exc:
            journal.fail(exc)
            raise

        Logger.info(
            f"Download results: {result['success']} success, "
            f"{result['failed']} failed, {result['skipped']} skipped"
        )

        if result["errors"]:
            for error in result["errors"][:5]:
                Logger.error(f"Download error: {error}")

        if result["failed"] or result["errors"]:
            details = "; ".join(result["errors"][:5]) if result["errors"] else f"{result['failed']} files failed"
            journal.fail(details)
            raise RuntimeError(f"Failed to synchronize TensaCraft files: {details}")

        self._complete_sync(version)
        journal.complete()
        Logger.info(f"Managed files synchronization complete for version: {ver_key}")

    def _prepare_mod_sync(self, version: Version, ver_key: str) -> dict[str, Any]:
        return self._prepare_file_sync(version, ver_key)

    def _sync_mods(
        self,
        version: Version,
        ver_key: str,
        sync_plan: dict[str, Any] | None = None,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        self._sync_files(version, ver_key, sync_plan, operation=operation)

    def _complete_sync(self, version: Version) -> None:
        return None
