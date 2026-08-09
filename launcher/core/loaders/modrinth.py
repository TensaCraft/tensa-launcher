from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from launcher.application.feedback import OperationHandle
from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationLease,
)
from launcher.application.modrinth_pack import ModrinthPackService
from launcher.application.version_profile_state import (
    capture_version_profile,
    restore_version_profile,
)
from launcher.core.api import ModrinthAPI
from launcher.core.async_downloader import AsyncDownloader, DownloadTask

from .base import BaseLoader

if TYPE_CHECKING:
    from launcher.domain.version import Version


class ModrinthLoader(BaseLoader):
    def __init__(self, *, app: Any) -> None:
        super().__init__(app=app)
        self.pack_service = ModrinthPackService(
            downloader_factory=AsyncDownloader,
            logger=self.app.log,
        )

    def get_id(self) -> str:
        return "modrinth"

    def get_name(self) -> str:
        return "Modrinth"

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
            with self._instance_operation(game_path, "modrinth_pack_install", lease=lease):
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
        owns_operation = operation is None
        previous_operation = self._feedback_operation
        version_snapshot = capture_version_profile(version)
        profile_committed = False
        content_committed = False
        if operation is None:
            operation = self.begin_feedback_operation(status=self.app.trans("installation_started") if self.app else None)
        else:
            self._feedback_operation = operation
        try:
            game_path = self.get_game_path(version.version_id)
            game_path.mkdir(parents=True, exist_ok=True)

            # 1) Завантажити .mrpack файл
            data = ModrinthAPI.get_version(version.id, version.version)
            if not data or "files" not in data:
                raise ValueError(f"Could not retrieve Modrinth data for {version.id} / {version.version}")

            release_files = data.get("files")
            if not isinstance(release_files, list):
                raise ValueError("Modrinth version files have an invalid format")
            mrpack_file = next(
                (
                    item
                    for item in release_files
                    if isinstance(item, dict) and str(item.get("filename") or "").endswith(".mrpack")
                ),
                None,
            )
            if not isinstance(mrpack_file, dict) or not mrpack_file.get("url"):
                raise ValueError(f"No .mrpack file found in Modrinth data for {version.id} / {version.version}")

            file_mrpack_path = game_path / ".tensalauncher-pack.mrpack"
            hashes = mrpack_file.get("hashes") or {}
            if not isinstance(hashes, dict):
                hashes = {}
            hash_algorithm = next(
                (algorithm for algorithm in ("sha512", "sha256", "sha1") if hashes.get(algorithm)),
                None,
            )
            if hash_algorithm is None:
                raise ValueError("Modrinth pack archive is missing a supported hash")
            archive_size = self.pack_service.normalized_size(mrpack_file.get("size"))
            if archive_size is None:
                raise ValueError("Modrinth pack archive is missing a positive declared size")
            archive_result = AsyncDownloader(max_workers=1).download_files(
                [
                    DownloadTask(
                        url=self.pack_service.validated_download_url(mrpack_file["url"]),
                        destination=file_mrpack_path,
                        expected_size=archive_size,
                        expected_hash=str(hashes.get(hash_algorithm) or ""),
                        expected_hash_algorithm=hash_algorithm,
                        task_id="modrinth-pack",
                    )
                ],
                skip_existing=False,
            )
            if archive_result["failed"] or archive_result["errors"]:
                file_mrpack_path.unlink(missing_ok=True)
                details = "; ".join(archive_result["errors"][:3]) or "download failed"
                raise RuntimeError(f"Failed to download Modrinth pack: {details}")

            # 2) Прочитати індекс та визначити залежності
            self.app.log.info(f"Installing modpack to {game_path}")
            try:
                index = self.pack_service.read_index(str(file_mrpack_path))
                mc_ver, loader_id, loader_ver = self.pack_service.resolve_loader(index)

                launch_version = mc_ver

                def install_runtime() -> None:
                    nonlocal launch_version, loader_ver
                    if loader_id and loader_ver:
                        loader_ver = self._install_mod_loader_for_mrpack(
                            mc_ver,
                            loader_id,
                            loader_ver,
                            operation=operation,
                        )
                        launch_version = self.pack_service.build_launch_version(
                            loader_id,
                            mc_ver,
                            loader_ver,
                        )
                    else:
                        self._install_minecraft_if_needed(mc_ver, operation=operation)

                    self.app.log.info(
                        f"Modpack installed with launch version: {launch_version}"
                    )

                def commit_profile() -> None:
                    nonlocal profile_committed
                    self._update_version_entity(
                        version,
                        game_path,
                        mc_ver,
                        loader_id,
                        loader_ver,
                    )
                    profile_committed = True

                self.pack_service.install_content(
                    mrpack_path=file_mrpack_path,
                    index=index,
                    game_path=game_path,
                    progress_callback=lambda completed, total, current_file: (
                        self._update_feedback_operation(
                            status=current_file,
                            progress=completed,
                            max_progress=total,
                            operation=operation,
                        )
                    ),
                    before_activate=install_runtime,
                    commit_callback=commit_profile,
                )
                content_committed = True
            finally:
                file_mrpack_path.unlink(missing_ok=True)

            if callback:
                callback()
        except Exception as exc:
            if not content_committed:
                restore_version_profile(version, version_snapshot)
                if profile_committed:
                    try:
                        version.save()
                    except Exception as rollback_error:
                        raise RuntimeError(
                            f"{exc}; Modrinth profile rollback failed: {rollback_error}"
                        ) from rollback_error
            raise
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    def _install_mod_loader_for_mrpack(
            self,
            mc_ver: str,
            loader_id: str,
            loader_ver: str,
            operation=None,
    ) -> str:
        """
        Встановлює mod loader для Modrinth модпаку.

        Returns:
            str: Фактична версія лоадера яка була встановлена
        """
        mll_key = self.pack_service.loader_key(loader_id)
        if not mll_key:
            self.app.log.warning(f"Unknown loader: {loader_id}")
            return loader_ver

        # Використовуємо базовий метод
        _, actual_loader_version = self._install_mod_loader(
            mc_version=mc_ver,
            loader_name=mll_key,
            requested_loader_version=loader_ver,
            operation=operation,
        )

        return actual_loader_version

    def _update_version_entity(
            self,
            version: Version,
            game_path: Path,
            mc_ver: str,
            loader_id: Optional[str],
            loader_ver: Optional[str]
    ) -> None:
        """Оновлює Version entity з інформацією про встановлений модпак."""
        version.path = str(game_path)
        version.version = mc_ver

        if loader_id and loader_ver:
            version.loader = self.pack_service.build_launch_version(loader_id, mc_ver, loader_ver)
            version.loader_version = loader_ver

            mll_key = self.pack_service.loader_key(loader_id) or "minecraft"
            loader_obj = self.app.launcher.get_loader(mll_key)
            version.client = loader_obj.get_name()
        else:
            version.loader = mc_ver
            version.loader_version = None
            version.client = self.app.launcher.get_loader("minecraft").get_name()

        version.save()

    def versions(self) -> list[str]:
        return []
