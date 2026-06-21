from __future__ import annotations

import json
import zipfile
from pathlib import Path

from launcher.application.modrinth_pack import ModrinthPackService


class DummyLog:
    def info(self, *_args, **_kwargs) -> None:
        return None


def test_modrinth_pack_reads_index_and_resolves_loader(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr(
            "modrinth.index.json",
            json.dumps(
                {
                    "dependencies": {
                        "minecraft": "1.20.1",
                        "fabric-loader": "0.16.0",
                    },
                    "files": [],
                }
            ),
        )

    service = ModrinthPackService()
    index = service.read_index(mrpack_path)
    mc_version, loader_id, loader_version = service.resolve_loader(index)

    assert mc_version == "1.20.1"
    assert loader_id == "fabric-loader"
    assert loader_version == "0.16.0"


def test_modrinth_pack_builds_download_tasks(tmp_path: Path):
    service = ModrinthPackService()
    tasks = service.build_download_tasks(
        {
            "files": [
                {
                    "path": "mods/example.jar",
                    "downloads": ["https://example.com/mod.jar"],
                    "hashes": {"sha1": "abc"},
                    "fileSize": 123,
                }
            ]
        },
        tmp_path,
    )

    assert len(tasks) == 1
    assert tasks[0].destination == tmp_path / "mods" / "example.jar"


def test_modrinth_pack_extracts_overrides(tmp_path: Path):
    mrpack_path = tmp_path / "pack.mrpack"
    game_path = tmp_path / "game"
    with zipfile.ZipFile(mrpack_path, "w") as archive:
        archive.writestr("overrides/config/example.txt", "value")

    ModrinthPackService.extract_overrides(mrpack_path, game_path, DummyLog())

    assert (game_path / "config" / "example.txt").read_text(encoding="utf-8") == "value"
