from __future__ import annotations

import json
import zipfile
from pathlib import Path

from launcher.application.curseforge_manifest import CurseForgeManifestService


def test_curseforge_manifest_loads_json_manifest(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "Pack",
                "version": "1.0.0",
                "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-loader-0.16.0", "primary": True}]},
                "files": [],
            }
        ),
        encoding="utf-8",
    )

    manifest = CurseForgeManifestService().load(manifest_path)

    assert manifest.source_kind == "manifest"
    assert manifest.minecraft_version == "1.20.1"
    assert manifest.loader_name == "fabric"
    assert manifest.loader_version == "0.16.0"


def test_curseforge_manifest_loads_zip_manifest(tmp_path: Path):
    archive_path = tmp_path / "modpack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "Pack",
                    "minecraft": {"version": "1.20.1", "modLoaders": []},
                    "files": [],
                }
            ),
        )

    manifest = CurseForgeManifestService().load(archive_path)

    assert manifest.source_kind == "zip"
    assert manifest.loader_name == "minecraft"
    assert manifest.loader_version is None


def test_curseforge_manifest_suggests_version_name():
    suggested = CurseForgeManifestService.suggest_version_name({"name": "Pack", "version": "2.0.0"})

    assert suggested == "Pack 2.0.0"
