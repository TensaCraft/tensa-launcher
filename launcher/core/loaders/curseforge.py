from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from launcher.application.feedback import OperationHandle
from launcher.application.curseforge_manifest import CurseForgeManifestService
from launcher.core.async_downloader import AsyncDownloader, DownloadTask
from launcher.core.versions import Version
from launcher.models.logger import Logger
from .base import BaseLoader


class CurseForgeLoader(BaseLoader):
    DOWNLOAD_URL_TEMPLATE = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}/download"
    FILE_META_URL_TEMPLATE = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}"
    manifest_service = CurseForgeManifestService()

    def __init__(self):
        super().__init__()
        self._file_meta_cache: Dict[Tuple[int, int], Tuple[str, Optional[int]]] = {}

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
    ) -> None:
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
                from launcher.core import Launcher

                client_name = Launcher.get_loader(loader_name).get_name()

            self._apply_overrides(source_path, source_kind, manifest, game_path)

            download_result = self._download_manifest_files(manifest.get("files") or [], game_path, operation=operation)
            if download_result["failed"] > 0:
                errors_preview = "; ".join(download_result["errors"][:3])
                raise ValueError(
                    f"Failed to download {download_result['failed']} files."
                    f"{f' {errors_preview}' if errors_preview else ''}"
                )

            version.path = str(game_path)
            version.version = mc_version
            version.loader = installed_loader
            version.loader_version = actual_loader_version
            version.client = client_name
            version.options = dict(version.options or {})
            version.options.pop("curseforge_source_path", None)
            version.options.pop("curseforge_source_type", None)
            version.options["curseforge_manifest_name"] = str(manifest.get("name") or "")
            version.options["curseforge_manifest_version"] = str(manifest.get("version") or "")
            version.save()

            if callback:
                callback()
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    def _apply_overrides(self, source_path: Path, source_kind: str, manifest: dict, game_path: Path) -> None:
        overrides_path = str(manifest.get("overrides") or "overrides").strip().replace("\\", "/").strip("/")
        if not overrides_path:
            return

        if source_kind == "zip":
            self._extract_overrides_from_zip(source_path, overrides_path, game_path)
            return

        source_root = (source_path.parent / overrides_path).resolve()
        if not source_root.exists() or not source_root.is_dir():
            Logger.info("No local overrides directory found next to manifest.json")
            return

        Logger.info(f"Copying overrides from {source_root}")
        for entry in source_root.rglob("*"):
            if not entry.is_file():
                continue

            relative = entry.relative_to(source_root)
            target = game_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)

    def _extract_overrides_from_zip(self, archive_path: Path, overrides_dir: str, game_path: Path) -> None:
        prefix = f"{overrides_dir}/"
        with zipfile.ZipFile(archive_path, "r") as zf:
            members = [name for name in zf.namelist() if name.startswith(prefix)]
            if not members:
                Logger.info(f"No overrides found in archive: {overrides_dir}/")
                return

            Logger.info(f"Extracting {len(members)} override files")
            game_root = game_path.resolve()

            for member in members:
                if member.endswith("/"):
                    continue

                relative_name = member[len(prefix):]
                if not relative_name:
                    continue

                relative_path = Path(relative_name)
                if ".." in relative_path.parts:
                    Logger.warning(f"Skipping unsafe override path: {member}")
                    continue

                target = (game_path / relative_path).resolve()
                try:
                    target.relative_to(game_root)
                except ValueError:
                    Logger.warning(f"Skipping override outside target directory: {member}")
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _download_manifest_files(
        self,
        file_entries: List[dict],
        game_path: Path,
        *,
        operation: OperationHandle | None = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {"success": 0, "failed": 0, "skipped": 0, "errors": []}
        if not file_entries:
            Logger.info("CurseForge manifest has no files to download")
            return result

        mods_dir = game_path / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)

        tasks: List[DownloadTask] = []
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

            file_name, expected_size = self._resolve_file_metadata(project_id, file_id)
            download_url = self.DOWNLOAD_URL_TEMPLATE.format(project_id=project_id, file_id=file_id)
            destination = mods_dir / file_name
            task = DownloadTask(
                url=download_url,
                destination=destination,
                expected_size=expected_size,
                task_id=f"{project_id}:{file_id}",
                use_requests=True,
            )
            tasks.append(task)

        if not tasks:
            return result

        downloader = AsyncDownloader(max_workers=6)

        def progress_callback(completed: int, total: int, current_file: str) -> None:
            self._update_feedback_operation(
                status=current_file,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        download_result = downloader.download_files(
            tasks,
            progress_callback=progress_callback,
            skip_existing=True,
        )

        result["success"] += download_result["success"]
        result["failed"] += download_result["failed"]
        result["skipped"] += download_result["skipped"]
        result["errors"].extend(download_result["errors"])
        return result

    def _resolve_file_metadata(self, project_id: int, file_id: int) -> tuple[str, Optional[int]]:
        cache_key = (project_id, file_id)
        cached = self._file_meta_cache.get(cache_key)
        if cached:
            return cached

        fallback_name = f"{project_id}-{file_id}.jar"
        fallback_size: Optional[int] = None
        meta_url = self.FILE_META_URL_TEMPLATE.format(project_id=project_id, file_id=file_id)

        try:
            response = requests.get(meta_url, headers={"User-Agent": "launcher/3.0"}, timeout=20)
            if response.status_code != 200:
                Logger.warning(f"Failed to fetch CurseForge metadata for {project_id}/{file_id}: {response.status_code}")
                self._file_meta_cache[cache_key] = (fallback_name, fallback_size)
                return fallback_name, fallback_size

            payload = response.json().get("data") or {}
            file_name = str(payload.get("fileName") or fallback_name).strip() or fallback_name
            file_size = payload.get("fileLength")
            if not isinstance(file_size, int) or file_size <= 0:
                file_size = None

            resolved = (file_name, file_size)
            self._file_meta_cache[cache_key] = resolved
            return resolved
        except Exception as exc:
            Logger.warning(f"Could not read CurseForge metadata for {project_id}/{file_id}: {exc}")
            self._file_meta_cache[cache_key] = (fallback_name, fallback_size)
            return fallback_name, fallback_size

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
