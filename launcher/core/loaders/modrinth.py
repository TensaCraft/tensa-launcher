from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import minecraft_launcher_lib
from minecraft_launcher_lib._internal_types.mrpack_types import MrpackIndex

from launcher.application.feedback import OperationHandle
from launcher.application.modrinth_pack import ModrinthPackService
from launcher.core.api import ModrinthAPI
from launcher.core.async_downloader import AsyncDownloader
from .base import BaseLoader

if TYPE_CHECKING:
    from launcher.domain.version import Version


class ModrinthLoader(BaseLoader):
    def __init__(self) -> None:
        super().__init__()
        self.pack_service = ModrinthPackService()

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
    ) -> None:
        owns_operation = operation is None
        previous_operation = self._feedback_operation
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

            mrpack_url = next((f["url"] for f in data["files"] if f.get("filename", "").endswith(".mrpack")), None)
            if not mrpack_url:
                raise ValueError(f"No .mrpack file found in Modrinth data for {version.id} / {version.version}")

            file_mrpack_path = game_path / f"{version.version}.mrpack"
            minecraft_launcher_lib.mrpack.download_file(mrpack_url, str(file_mrpack_path))

            # 2) Прочитати індекс та визначити залежності
            self.app.log.info(f"Installing modpack to {game_path}")
            index = self.pack_service.read_index(str(file_mrpack_path))
            mc_ver, loader_id, loader_ver = self.pack_service.resolve_loader(index)

            # 3) Розпакувати overrides
            self._extract_overrides(file_mrpack_path, game_path)

            # 4) Завантажити файли модів та ресурспаків
            self._download_modpack_files(index, game_path, operation=operation)

            # 5) Видалити .mrpack файл
            file_mrpack_path.unlink(missing_ok=True)

            # 6) Встановити loader
            launch_version = mc_ver
            if loader_id and loader_ver:
                loader_ver = self._install_mod_loader_for_mrpack(mc_ver, loader_id, loader_ver, operation=operation)
                launch_version = self.pack_service.build_launch_version(loader_id, mc_ver, loader_ver)
            else:
                # Vanilla Minecraft - встановити якщо потрібно
                self._install_minecraft_if_needed(mc_ver, operation=operation)

            self.app.log.info(f"Modpack installed with launch version: {launch_version}")

            # 7) Оновити Version entity
            self._update_version_entity(version, game_path, mc_ver, loader_id, loader_ver)

            if callback:
                callback()
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

    def _extract_overrides(self, mrpack_path: Path, game_path: Path) -> None:
        """Розпаковує overrides з .mrpack файлу в game directory."""
        self.pack_service.extract_overrides(mrpack_path, game_path, self.app.log)

    def _download_modpack_files(
        self,
        index: MrpackIndex,
        game_path: Path,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        """Завантажує моди та ресурспаки з mrpack індексу паралельно."""
        files = index.get("files", [])
        if not files:
            self.app.log.info("No files to download")
            return

        total_files = len(files)
        self.app.log.info(f"Preparing to download {total_files} modpack files")

        download_tasks = self.pack_service.build_download_tasks(index, game_path)

        if not download_tasks:
            self.app.log.info("No files need to be downloaded")
            return

        # Асинхронне завантаження
        self.app.log.info(f"Starting parallel download of {len(download_tasks)} files")
        downloader = AsyncDownloader(max_workers=6)  # Більше потоків для modpack

        def progress_callback(completed, total, current_file):
            self._update_feedback_operation(
                status=current_file,
                progress=completed,
                max_progress=total,
                operation=operation,
            )

        result = downloader.download_files(
            download_tasks,
            progress_callback=progress_callback,
            skip_existing=True
        )

        self.app.log.info(
            f"Downloaded {result['success']} files, "
            f"skipped {result['skipped']} existing files, "
            f"{result['failed']} failed"
        )

        if result['errors']:
            for error in result['errors'][:5]:
                self.app.log.error(f"Download error: {error}")

    def _update_version_entity(
            self,
            version: Version,
            game_path: Path,
            mc_ver: str,
            loader_id: Optional[str],
            loader_ver: Optional[str]
    ) -> None:
        """Оновлює Version entity з інформацією про встановлений модпак."""
        from launcher.core import Launcher

        version.path = str(game_path)
        version.version = mc_ver

        if loader_id and loader_ver:
            version.loader = self.pack_service.build_launch_version(loader_id, mc_ver, loader_ver)
            version.loader_version = loader_ver

            mll_key = self.pack_service.loader_key(loader_id) or "minecraft"
            loader_obj = Launcher.get_loader(mll_key)
            version.client = loader_obj.get_name()
        else:
            version.loader = mc_ver
            version.loader_version = None
            version.client = Launcher.get_loader("minecraft").get_name()

        version.save()

    def versions(self) -> list[str]:
        return []
