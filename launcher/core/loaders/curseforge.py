from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from launcher.application.curseforge_install import (
    CurseForgeInstallLimits,
    CurseForgeInstallService,
)
from launcher.application.curseforge_install import (
    FileMetadata as _FileMetadata,
)
from launcher.application.curseforge_manifest import CurseForgeManifestService
from launcher.application.feedback import OperationHandle
from launcher.application.file_transaction import build_commit_key
from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationLease,
)
from launcher.application.version_profile_state import (
    capture_version_profile,
    restore_version_profile,
)
from launcher.core.async_downloader import AsyncDownloader
from launcher.core.versions import Version
from launcher.models.logger import Logger

from .base import BaseLoader


class CurseForgeLoader(BaseLoader):
    DOWNLOAD_URL_TEMPLATE = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}/download"
    FILE_META_URL_TEMPLATE = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}"
    MAX_OVERRIDE_MEMBERS = 20_000
    MAX_OVERRIDE_ENTRY_SIZE = 1024 * 1024 * 1024
    MAX_OVERRIDE_TOTAL_SIZE = 4 * 1024 * 1024 * 1024

    _HASH_ALGORITHM_IDS = {1: "sha1"}
    _HASH_PRIORITY = {"sha1": 1, "sha256": 2, "sha512": 3}
    _HASH_LENGTHS = {"sha1": 40, "sha256": 64, "sha512": 128}
    manifest_service = CurseForgeManifestService()

    def __init__(self, *, app: Any):
        super().__init__(app=app)
        self._file_meta_cache: Dict[Tuple[int, int], _FileMetadata] = {}

    def _install_service(
        self,
        *,
        operation: OperationHandle | None = None,
    ) -> CurseForgeInstallService:
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

        return CurseForgeInstallService(
            limits=CurseForgeInstallLimits(
                override_members=self.MAX_OVERRIDE_MEMBERS,
                override_entry_size=self.MAX_OVERRIDE_ENTRY_SIZE,
                override_total_size=self.MAX_OVERRIDE_TOTAL_SIZE,
            ),
            downloader_factory=AsyncDownloader,
            progress_callback=progress_callback,
        )

    def get_id(self) -> str:
        return "curseforge"

    def get_name(self) -> str:
        return "CurseForge"

    @classmethod
    def load_manifest(cls, source_path: str | Path) -> tuple[dict, str]:
        manifest = cls.manifest_service.load(source_path)
        return manifest.data, manifest.source_kind

    @staticmethod
    def suggest_version_name(manifest: dict) -> str:
        return CurseForgeManifestService.suggest_version_name(manifest)

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
            with self._instance_operation(game_path, "curseforge_install", lease=lease):
                self._ensure_instance_idle(game_path, version.name)
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
        version_snapshot = capture_version_profile(version)
        owns_operation = operation is None
        previous_operation = self._feedback_operation
        if operation is None:
            operation = self.begin_feedback_operation(status=self.app.trans("installation_started") if self.app else None)
        else:
            self._feedback_operation = operation
        try:
            options = version.options or {}
            source_path_raw = str(options.get("curseforge_source_path") or "").strip()
            if not source_path_raw:
                raise ValueError("CurseForge source file is missing")

            source_path = Path(source_path_raw)
            manifest_info = self.manifest_service.load(source_path)
            manifest = manifest_info.data
            source_kind = manifest_info.source_kind
            mc_version = manifest_info.minecraft_version
            loader_name = manifest_info.loader_name
            requested_loader_version = manifest_info.loader_version
            game_path = self.get_game_path(version.version_id)

            Logger.info(
                f"Installing CurseForge modpack '{manifest.get('name')}' "
                f"(MC {mc_version}, loader={loader_name}) from {source_path}"
            )

            self._update_feedback_operation(
                status=f"Installing base loader for Minecraft {mc_version}",
                progress=0,
                max_progress=100,
                operation=operation,
            )

            if loader_name == "minecraft":
                self._install_minecraft_if_needed(mc_version, operation=operation)
                installed_loader = mc_version
                actual_loader_version = None
                client_name = "Minecraft"
            else:
                installed_loader, actual_loader_version = self._install_mod_loader(
                    mc_version=mc_version,
                    loader_name=loader_name,
                    requested_loader_version=requested_loader_version,
                    operation=operation,
                )
                client_name = self.app.launcher.get_loader(loader_name).get_name()

            install_service = self._install_service(operation=operation)
            remote_files, download_result = install_service.plan_manifest_files(
                manifest.get("files") or [],
                game_path,
                metadata_resolver=self._resolve_file_metadata,
                download_url=lambda project_id, file_id: (
                    self.DOWNLOAD_URL_TEMPLATE.format(
                        project_id=project_id,
                        file_id=file_id,
                    )
                ),
            )
            if download_result["failed"] == 0:
                overrides = install_service.plan_overrides(
                    source_path,
                    source_kind,
                    manifest,
                    game_path,
                )
                download_result = install_service.install_content(
                    game_path=game_path,
                    remote_files=remote_files,
                    overrides=overrides,
                    source_path=source_path,
                    source_kind=source_kind,
                    operation_name="curseforge-install",
                    result=download_result,
                    commit_callback=lambda: self._commit_profile(
                        version,
                        game_path=game_path,
                        minecraft_version=mc_version,
                        installed_loader=installed_loader,
                        loader_version=actual_loader_version,
                        client_name=client_name,
                        manifest=manifest,
                    ),
                    commit_key=build_commit_key("curseforge-profile", manifest),
                )
            else:
                Logger.warning(
                    "CurseForge file preflight failed before override staging"
                )
            if download_result["failed"] > 0:
                errors_preview = "; ".join(download_result["errors"][:3])
                raise ValueError(
                    f"Failed to download {download_result['failed']} files."
                    f"{f' {errors_preview}' if errors_preview else ''}"
                )

            if callback:
                callback()
        except Exception:
            restore_version_profile(version, version_snapshot)
            raise
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    @staticmethod
    def _commit_profile(
        version: Version,
        *,
        game_path: Path,
        minecraft_version: str,
        installed_loader: str,
        loader_version: str | None,
        client_name: str,
        manifest: dict[str, Any],
    ) -> None:
        version.path = str(game_path)
        version.version = minecraft_version
        version.loader = installed_loader
        version.loader_version = loader_version
        version.client = client_name
        version.options = dict(version.options or {})
        version.options.pop("curseforge_source_path", None)
        version.options.pop("curseforge_source_type", None)
        version.options["curseforge_manifest_name"] = str(manifest.get("name") or "")
        version.options["curseforge_manifest_version"] = str(manifest.get("version") or "")
        version.save()

    def _resolve_file_metadata(self, project_id: int, file_id: int) -> _FileMetadata:
        cache_key = (project_id, file_id)
        cached = self._file_meta_cache.get(cache_key)
        if cached:
            return cached

        meta_url = self.FILE_META_URL_TEMPLATE.format(project_id=project_id, file_id=file_id)

        try:
            response = requests.get(meta_url, headers={"User-Agent": "launcher/3.0"}, timeout=20)
            if response.status_code != 200:
                raise ValueError(
                    f"CurseForge metadata request failed with HTTP {response.status_code}"
                )

            payload = response.json().get("data") or {}
            file_name = str(payload.get("fileName") or "").strip()
            file_size = payload.get("fileLength")
            if isinstance(file_size, bool) or not isinstance(file_size, int) or file_size <= 0:
                raise ValueError("CurseForge metadata is missing a valid fileLength")

            hash_algorithm, hash_value = self._select_strongest_hash(payload.get("hashes"))
            resolved = _FileMetadata(
                name=file_name,
                size=file_size,
                hash_value=hash_value,
                hash_algorithm=hash_algorithm,
            )
            self._file_meta_cache[cache_key] = resolved
            return resolved
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not read CurseForge metadata for {project_id}/{file_id}: {exc}"
            ) from exc

    @classmethod
    def _select_strongest_hash(cls, hashes: Any) -> tuple[str, str]:
        if not isinstance(hashes, list):
            raise ValueError("CurseForge metadata is missing file hashes")

        supported: list[tuple[int, str, str]] = []
        for item in hashes:
            if not isinstance(item, dict):
                continue

            algorithm = cls._normalize_hash_algorithm(item.get("algo"))
            value = str(item.get("value") or "").strip().lower()
            if algorithm is None or not cls._is_valid_hash(value, algorithm):
                continue
            supported.append((cls._HASH_PRIORITY[algorithm], algorithm, value))

        if not supported:
            raise ValueError("CurseForge metadata has no supported valid file hash")

        _, algorithm, value = max(supported, key=lambda candidate: candidate[0])
        return algorithm, value

    @classmethod
    def _normalize_hash_algorithm(cls, value: Any) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return cls._HASH_ALGORITHM_IDS.get(value)
        if not isinstance(value, str):
            return None

        normalized = re.sub(r"[^a-z0-9]", "", value.lower())
        if normalized.isdigit():
            return cls._HASH_ALGORITHM_IDS.get(int(normalized))
        aliases = {
            "sha1": "sha1",
            "sha256": "sha256",
            "sha512": "sha512",
        }
        return aliases.get(normalized)

    @classmethod
    def _is_valid_hash(cls, value: str, algorithm: str) -> bool:
        expected_length = cls._HASH_LENGTHS[algorithm]
        return len(value) == expected_length and all(character in "0123456789abcdef" for character in value)

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def versions(self) -> list[str]:
        return []
