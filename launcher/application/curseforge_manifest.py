from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CurseForgeManifest:
    data: dict[str, Any]
    source_kind: str
    minecraft_version: str
    loader_name: str
    loader_version: str | None


class CurseForgeManifestService:
    def load(self, source_path: str | Path) -> CurseForgeManifest:
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        suffix = path.suffix.lower()
        if suffix == ".zip":
            with zipfile.ZipFile(path, "r") as archive:
                manifest_name = next(
                    (name for name in archive.namelist() if name.lower().endswith("manifest.json")),
                    None,
                )
                if not manifest_name:
                    raise ValueError("manifest.json not found in the archive")
                with archive.open(manifest_name, "r") as manifest_file:
                    payload = json.load(manifest_file)
            data = self.validate(payload)
            source_kind = "zip"
        elif suffix == ".json":
            with open(path, "r", encoding="utf-8") as manifest_file:
                payload = json.load(manifest_file)
            data = self.validate(payload)
            source_kind = "manifest"
        else:
            raise ValueError("Only .zip archives and .json manifest files are supported")

        return CurseForgeManifest(
            data=data,
            source_kind=source_kind,
            minecraft_version=self.minecraft_version(data),
            loader_name=self.loader_info(data)[0],
            loader_version=self.loader_info(data)[1],
        )

    @staticmethod
    def suggest_version_name(manifest: dict[str, Any]) -> str:
        base_name = str(manifest.get("name") or "CurseForge Modpack").strip()
        pack_version = str(manifest.get("version") or "").strip()
        if pack_version and pack_version.lower() not in base_name.lower():
            return f"{base_name} {pack_version}"
        return base_name

    @staticmethod
    def validate(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("manifest.json has invalid format")

        minecraft = data.get("minecraft")
        if not isinstance(minecraft, dict):
            raise ValueError("manifest.json missing 'minecraft' section")

        mc_version = str(minecraft.get("version") or "").strip()
        if not mc_version:
            raise ValueError("manifest.json missing Minecraft version")

        files = data.get("files", [])
        if not isinstance(files, list):
            raise ValueError("manifest.json has invalid 'files' section")

        return data

    @staticmethod
    def minecraft_version(manifest: dict[str, Any]) -> str:
        minecraft = manifest.get("minecraft") or {}
        version = str(minecraft.get("version") or "").strip()
        if not version:
            raise ValueError("Minecraft version is missing in manifest")
        return version

    @staticmethod
    def loader_info(manifest: dict[str, Any]) -> tuple[str, str | None]:
        minecraft = manifest.get("minecraft") or {}
        mod_loaders = minecraft.get("modLoaders") or []
        if not isinstance(mod_loaders, list):
            return "minecraft", None

        entries = [entry for entry in mod_loaders if isinstance(entry, dict)]
        if not entries:
            return "minecraft", None

        selected = next((entry for entry in entries if entry.get("primary")), entries[0])
        loader_id = str(selected.get("id") or "").strip().lower()
        if not loader_id:
            return "minecraft", None

        mappings = [
            ("neoforge", "neoforge-"),
            ("forge", "forge-"),
            ("fabric", "fabric-loader-"),
            ("fabric", "fabric-"),
            ("quilt", "quilt-loader-"),
            ("quilt", "quilt-"),
        ]
        for loader_name, prefix in mappings:
            if loader_id.startswith(prefix):
                parsed_version = loader_id[len(prefix):].strip() or None
                return loader_name, parsed_version

        if loader_id in {"forge", "neoforge", "fabric", "quilt"}:
            return loader_id, None

        return "minecraft", None
