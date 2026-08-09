from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Optional

from launcher.application.feedback import OperationHandle
from launcher.application.file_transaction import build_commit_key
from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationLease,
)
from launcher.application.tensacraft_content_install import (
    TensaCraftContentInstall,
    TensaCraftContentPlan,
)
from launcher.application.tensacraft_payload import TensaCraftPayloadService
from launcher.application.version_profile_state import (
    capture_version_profile,
    restore_version_profile,
)
from launcher.core.api import TensaCraftAPI
from launcher.core.async_downloader import AsyncDownloader
from launcher.core.versions import Version
from launcher.models.logger import Logger

from .base import BaseLoader


class TensaCraftLoader(BaseLoader):
    DOWNLOAD_WORKERS = 6

    def __init__(self, *, app: Any):
        super().__init__(app=app)
        self.api = TensaCraftAPI(app)
        self.payload = TensaCraftPayloadService()
        self.content = TensaCraftContentInstall(
            self.api,
            max_workers=self.DOWNLOAD_WORKERS,
            downloader_factory=lambda workers: AsyncDownloader(
                max_workers=workers
            ),
        )

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
        lease: InstanceOperationLease | None = None,
    ) -> None:
        game_path = self.get_game_path(version.version_id)
        try:
            with self._instance_operation(game_path, "tensacraft_install", lease=lease):
                self._install_locked(
                    version,
                    callback=callback,
                    java_path=java_path,
                    loader_version=loader_version,
                    operation=operation,
                )
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self.app.trans("instance_operation_busy", version=version.name)
            ) from exc

    def _install_locked(
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

            Logger.info(f"Installing base loader {loader_name} for Minecraft {version.version}")
            loader = self.app.launcher.get_loader(loader_name)
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

    def sync_update(
        self,
        version: Version,
        *,
        force: bool = False,
        lease: InstanceOperationLease | None = None,
    ):
        version_path = self._required_version_path(version)
        try:
            with self._instance_operation(
                version_path,
                "tensacraft_sync",
                lease=lease,
            ):
                return self._sync_update_locked(version, force=force)
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self.app.trans("instance_operation_busy", version=version.name)
            ) from exc

    def _sync_update_locked(self, version: Version, *, force: bool = False):
        ver_key = self._primary_version_key(version)
        version_snapshot = capture_version_profile(version)
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
            version_path = self._required_version_path(version)
            recovered = False
            if self.content.needs_recovery(version_path):
                self._ensure_game_directory_idle(
                    version_path,
                    self._pack_display_name(version, fallback=ver_key),
                )
                if self.content.commit_recovery_pending(version_path):
                    recovered = True
                    Logger.warning(
                        f"Deferring interrupted commit recovery for {ver_key} "
                        "until the synchronization transaction resumes"
                    )
                else:
                    recovered = self.content.recover(version_path)

            client_data = req["client"]
            client_version = client_data.get("minecraft_version") or client_data.get("version")
            mod_loader_name = client_data.get("loader_id") or client_data.get("loader")
            client_loader_version = client_data.get("loader_version")

            sync_plan = self._prepare_file_sync(
                version,
                ver_key,
                preserve_rules=client_data.get("preserve_rules"),
                force=force or recovered,
            )
            if not sync_plan.api_available:
                Logger.warning(f"Skipping Tensa sync for {ver_key}: API files unavailable")
                return

            loader_changed = self.payload.loader_changed(version, client_data)
            runtime_missing = bool(client_version) and not self.runtime.has_runtime(client_version, client_version)
            needs_runtime_prepare = (
                loader_changed or runtime_missing or sync_plan.has_changes
            )
            needs_game_directory_update = loader_changed or sync_plan.has_changes

            if needs_game_directory_update and getattr(version, "path", None):
                self._ensure_game_directory_idle(
                    self._required_version_path(version),
                    self._pack_display_name(version, fallback=ver_key),
                )

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

            if sync_plan.has_changes:
                self._update_feedback_operation(
                    self.app.trans("syncing_files"),
                    progress=25,
                    max_progress=100,
                    operation=operation,
                )
                self._sync_files(
                    version,
                    ver_key,
                    sync_plan,
                    operation=operation,
                    commit_callback=version.save,
                )
            else:
                version.save()
            completed_update = needs_runtime_prepare
        except Exception:
            restore_version_profile(version, version_snapshot)
            raise
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

    @staticmethod
    def _required_version_path(version: Version) -> Path:
        raw_path = getattr(version, "path", None)
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            raise ValueError("TensaCraft version path is missing")
        return Path(raw_path)

    def _download_pack_files(
        self,
        version_path: Path,
        files: list[dict[str, Any]],
        status_label: str,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        def progress_callback(
            completed: int,
            total: int,
            current_file: str,
        ) -> None:
            self._update_feedback_operation(
                status=current_file or status_label,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        self.content.download_pack_files(
            version_path,
            files,
            progress=progress_callback,
        )

    def _prepare_file_sync(
        self,
        version: Version,
        ver_key: str,
        preserve_rules: Any = None,
        *,
        force: bool = False,
    ) -> TensaCraftContentPlan:
        return self.content.prepare(
            self._required_version_path(version),
            ver_key,
            preserve_rules=preserve_rules,
            force=force,
        )

    def _sync_files(
        self,
        version: Version,
        ver_key: str,
        sync_plan: TensaCraftContentPlan | None = None,
        *,
        operation: OperationHandle | None = None,
        commit_callback: Callable[[], None] | None = None,
    ) -> None:
        plan = sync_plan or self._prepare_file_sync(version, ver_key)

        if plan.download_tasks:
            self._update_feedback_operation(
                self.app.trans("syncing_files"),
                progress=0,
                max_progress=max(len(plan.download_tasks), 1),
                operation=operation,
            )

        def progress_callback(
            completed: int,
            total: int,
            current_file: str,
        ) -> None:
            self._update_feedback_operation(
                status=current_file,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        def commit() -> None:
            if commit_callback is not None:
                commit_callback()

        commit_identity = self._version_commit_identity(version)
        self.content.apply(
            plan,
            version_key=ver_key,
            progress=progress_callback,
            commit=commit if commit_callback is not None else None,
            commit_key=(
                build_commit_key(
                    "tensacraft-profile",
                    {
                        "version_key": ver_key,
                        "version": commit_identity,
                    },
                )
                if commit_callback is not None
                else None
            ),
        )

    @staticmethod
    def _version_commit_identity(version: Any) -> dict[str, Any]:
        serializer = getattr(version, "to_dict", None)
        if callable(serializer):
            payload = serializer()
            if isinstance(payload, dict):
                return payload
        return {
            key: value
            for key, value in vars(version).items()
            if not key.startswith("_") and not callable(value)
        }
