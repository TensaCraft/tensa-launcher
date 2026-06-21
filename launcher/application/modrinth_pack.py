from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Optional, Union

from minecraft_launcher_lib._internal_types.mrpack_types import MrpackIndex

from launcher.core.async_downloader import DownloadTask


class ModrinthPackService:
    @staticmethod
    def read_index(path: Union[str, os.PathLike]) -> MrpackIndex:
        abs_path = os.fspath(path)
        with zipfile.ZipFile(abs_path, "r") as archive:
            with archive.open("modrinth.index.json", "r") as index_file:
                return json.load(index_file)  # type: ignore[return-value]

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

    def resolve_loader(self, index: MrpackIndex) -> tuple[str, Optional[str], Optional[str]]:
        dependencies = index.get("dependencies", {})
        mc_version = dependencies["minecraft"]
        order = ["neoforge", "forge", "fabric-loader", "quilt-loader"]
        loader_id = next((key for key in order if key in dependencies), None)
        loader_version = dependencies.get(loader_id) if loader_id else None
        return mc_version, loader_id, loader_version

    @staticmethod
    def extract_overrides(mrpack_path: Path, game_path: Path, logger) -> None:
        with zipfile.ZipFile(str(mrpack_path), "r") as archive:
            override_files = [name for name in archive.namelist() if name.startswith("overrides/")]
            if override_files:
                logger.info(f"Extracting {len(override_files)} override files")
            for file_name in override_files:
                target_name = file_name.replace("overrides/", "", 1)
                if not target_name:
                    continue

                target_path = game_path / target_name
                if file_name.endswith("/"):
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(file_name) as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

    @staticmethod
    def build_download_tasks(index: MrpackIndex, game_path: Path) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for file_info in index.get("files", []):
            downloads = file_info.get("downloads", [])
            if not downloads:
                continue

            tasks.append(
                DownloadTask(
                    url=downloads[0],
                    destination=game_path / file_info["path"],
                    expected_size=file_info.get("fileSize"),
                    expected_sha1=(file_info.get("hashes") or {}).get("sha1"),
                    task_id=file_info["path"],
                )
            )
        return tasks
