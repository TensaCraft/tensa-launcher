from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from launcher.core.api.modrinth import ModrinthAPI


@dataclass(slots=True)
class ModInstallFile:
    url: str
    filename: str
    version_number: str = ""


class ModrinthModsService:
    LOADER_SCOPED_PROJECT_TYPES = {"mod"}

    @staticmethod
    def get_loader_name(version) -> str | None:
        loader = (getattr(version, "loader", "") or "").lower()
        if not loader:
            loader = (getattr(version, "client", "") or "").lower()

        for loader_name in ("fabric", "neoforge", "forge", "quilt"):
            if loader_name in loader:
                return loader_name
        return None

    def build_search_facets(
        self,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> str:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        facets: list[list[str]] = [[f"project_type:{project_type}"]]

        if loader:
            facets.append([f"categories:{loader}"])
        if target_game_version:
            facets.append([f"versions:{target_game_version}"])

        return json.dumps(facets)

    def get_compatible_versions(
        self,
        project_id: str,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> list[dict[str, Any]]:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        loaders = [loader] if loader else None
        game_versions = [target_game_version] if target_game_version else None
        versions = ModrinthAPI.get_mod_versions(project_id, game_versions, loaders)
        return self.filter_compatible_versions(
            versions,
            version,
            project_type=project_type,
            game_version=target_game_version,
        )

    def find_latest_version(
        self,
        project_id: str,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> dict[str, Any] | None:
        versions = self.get_compatible_versions(
            project_id,
            version,
            project_type=project_type,
            game_version=game_version,
        )
        return versions[0] if versions else None

    def find_update(self, installed_mod: dict[str, Any], version) -> dict[str, Any] | None:
        mod_id = installed_mod.get("id")
        if not mod_id or not installed_mod.get("enabled", True):
            return None

        latest_version = self.find_latest_version(mod_id, version)
        if latest_version is None:
            return None

        current_version = installed_mod.get("version", "")
        latest_version_number = latest_version.get("version_number", "")
        if current_version and latest_version_number and current_version != latest_version_number:
            return latest_version
        return None

    @staticmethod
    def select_primary_file(version_data: dict[str, Any] | None) -> ModInstallFile | None:
        if not version_data:
            return None

        files = version_data.get("files", [])
        if not files:
            return None

        mod_file = next((file for file in files if file.get("primary", False)), files[0])
        return ModInstallFile(
            url=mod_file["url"],
            filename=mod_file["filename"],
            version_number=version_data.get("version_number", ""),
        )

    @staticmethod
    def is_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> bool:
        return ModrinthModsService.find_installed(installed_mods, project) is not None

    @staticmethod
    def find_installed(installed_mods: list[dict[str, Any]], project: dict[str, Any]) -> dict[str, Any] | None:
        project_id = project.get("project_id")
        project_slug = ModrinthModsService._normalize_identifier(project.get("slug"))
        project_name = ModrinthModsService._normalize_identifier(project.get("title"))
        candidates = {value for value in (project_slug, project_name) if value}

        for mod in installed_mods:
            if project_id and project_id in {mod.get("id"), mod.get("modrinth_project_id")}:
                return mod

            installed_values = {
                ModrinthModsService._normalize_identifier(mod.get("name")),
                ModrinthModsService._normalize_identifier(mod.get("filename")),
            }
            for installed_value in installed_values:
                if not installed_value:
                    continue
                if any(
                    installed_value == candidate or installed_value.startswith(f"{candidate}-")
                    for candidate in candidates
                ):
                    return mod

        return None

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text.endswith(".disabled"):
            text = text[:-9]
        for suffix in (".jar", ".zip"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return "-".join(part for part in text.replace("_", "-").split() if part)

    def filter_compatible_versions(
        self,
        versions: list[dict[str, Any]] | None,
        version,
        *,
        project_type: str = "mod",
        game_version: str | None = None,
    ) -> list[dict[str, Any]]:
        loader = self.get_loader_name(version) if project_type in self.LOADER_SCOPED_PROJECT_TYPES else None
        target_game_version = game_version or getattr(version, "version", None)
        compatible_versions: list[dict[str, Any]] = []

        for version_data in versions or []:
            version_game_versions = version_data.get("game_versions", [])
            version_loaders = version_data.get("loaders", [])
            game_version_match = not target_game_version or target_game_version in version_game_versions
            loader_match = not loader or loader in version_loaders
            if game_version_match and loader_match:
                compatible_versions.append(version_data)

        return compatible_versions
