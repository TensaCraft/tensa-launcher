from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import minecraft_launcher_lib


@dataclass(frozen=True)
class VersionCreateOption:
    id: str
    name: str
    minecraft_version: str
    loader_id: str
    loader_name: str
    description: str = ""
    image: str | None = None
    loader_version: str | None = None
    loader_versions: tuple[str, ...] = ()
    snapshot: bool = False
    unstable_loader: bool = False
    pack: dict[str, Any] | None = None


class VersionCreationCatalogService:
    SNAPSHOT_LOADER_IDS = {"minecraft", "fabric", "quilt"}
    UNSTABLE_LOADER_IDS = {"neoforge", "quilt"}
    COMPACT_BUILD_CHOICES_LOADER_IDS = {"fabric", "quilt"}
    _LOADER_PRERELEASE_RANKS = {
        "snapshot": 1,
        "alpha": 2,
        "beta": 3,
        "pre": 4,
        "rc": 5,
    }
    _LOADER_STABLE_RANK = 10

    def __init__(self) -> None:
        self._minecraft_versions_cache: list[dict[str, str]] | None = None
        self._minecraft_options_cache: dict[bool, list[VersionCreateOption]] = {}
        self._loader_options_cache: dict[tuple[str, bool, bool], list[VersionCreateOption]] = {}
        self._loader_minecraft_versions_cache: dict[tuple[str, bool], list[str]] = {}
        self._loader_builds_cache: dict[tuple[str, str, bool], list[str]] = {}

    def minecraft_versions(self, *, include_snapshots: bool = False) -> list[VersionCreateOption]:
        cache_key = bool(include_snapshots)
        if cache_key in self._minecraft_options_cache:
            return list(self._minecraft_options_cache[cache_key])

        allowed_version_ids = [
            item["id"]
            for item in self._minecraft_version_items()
            if item["type"] == "release" or (include_snapshots and item["type"] == "snapshot")
        ]
        version_types = self._minecraft_version_types()
        options: list[VersionCreateOption] = []
        for version_id in self._sort_minecraft_versions(allowed_version_ids):
            version_type = version_types.get(version_id, "")
            if not version_id:
                continue
            options.append(
                VersionCreateOption(
                    id=version_id,
                    name=f"Minecraft {version_id}",
                    minecraft_version=version_id,
                    loader_id="minecraft",
                    loader_name="Minecraft",
                    snapshot=version_type == "snapshot",
                )
            )
        self._minecraft_options_cache[cache_key] = list(options)
        return options

    def loader_versions(
        self,
        loader_id: str,
        *,
        include_snapshots: bool = False,
        include_unstable_loaders: bool = False,
    ) -> list[VersionCreateOption]:
        cache_key = (loader_id, bool(include_snapshots), bool(include_unstable_loaders))
        if cache_key in self._loader_options_cache:
            return list(self._loader_options_cache[cache_key])

        mod_loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_id)
        loader_name = mod_loader.get_name()
        valid_versions = self._valid_minecraft_versions(include_snapshots=include_snapshots)
        loader_versions = self._loader_minecraft_versions(
            loader_id,
            mod_loader,
            include_snapshots=include_snapshots,
        )
        if valid_versions:
            loader_versions = [version for version in loader_versions if version in valid_versions]
        minecraft_versions = self._sort_minecraft_versions(loader_versions)
        version_types = self._minecraft_version_types()
        options: list[VersionCreateOption] = []
        for minecraft_version in minecraft_versions:
            loader_builds, stable_builds = self._loader_build_choices(
                loader_id,
                mod_loader,
                minecraft_version,
                include_unstable=include_unstable_loaders,
            )
            loader_version = loader_builds[0] if loader_builds else None
            if not loader_version:
                continue
            unstable = loader_version not in stable_builds
            options.append(
                VersionCreateOption(
                    id=f"{loader_id}:{minecraft_version}:{loader_version}",
                    name=f"{loader_name} {minecraft_version}",
                    minecraft_version=minecraft_version,
                    loader_id=loader_id,
                    loader_name=loader_name,
                    loader_version=loader_version,
                    loader_versions=tuple(loader_builds),
                    snapshot=version_types.get(minecraft_version) == "snapshot",
                    unstable_loader=unstable,
                )
            )
        self._loader_options_cache[cache_key] = list(options)
        return options

    def supports_unstable_loaders(self, loader_id: str) -> bool:
        return loader_id in self.UNSTABLE_LOADER_IDS

    def supports_snapshots(self, loader_id: str) -> bool:
        return loader_id in self.SNAPSHOT_LOADER_IDS

    def _loader_minecraft_versions(self, loader_id: str, mod_loader: Any, *, include_snapshots: bool) -> list[str]:
        cache_key = (loader_id, bool(include_snapshots))
        if cache_key in self._loader_minecraft_versions_cache:
            return list(self._loader_minecraft_versions_cache[cache_key])

        stable_versions = self._load_loader_minecraft_versions(mod_loader, stable_only=True)
        if not include_snapshots:
            versions = stable_versions
        else:
            versions = self._merge_unique(
                self._load_loader_minecraft_versions(mod_loader, stable_only=False),
                stable_versions,
            )
        self._loader_minecraft_versions_cache[cache_key] = list(versions)
        return versions

    def _load_loader_minecraft_versions(self, mod_loader: Any, *, stable_only: bool) -> list[str]:
        try:
            return self._normalize_string_list(mod_loader.get_minecraft_versions(stable_only))
        except TypeError:
            return self._normalize_string_list(mod_loader.get_minecraft_versions(True))

    def _loader_build_choices(
        self,
        loader_id: str,
        mod_loader: Any,
        minecraft_version: str,
        *,
        include_unstable: bool,
    ) -> tuple[list[str], set[str]]:
        stable = self._loader_builds(
            loader_id,
            mod_loader,
            minecraft_version,
            stable_only=True,
        )
        stable_set = set(stable)
        if not include_unstable:
            builds = stable
            if stable and loader_id.lower() in self.COMPACT_BUILD_CHOICES_LOADER_IDS:
                builds = self._sort_loader_builds(
                    self._merge_unique(
                        self._loader_builds(
                            loader_id,
                            mod_loader,
                            minecraft_version,
                            stable_only=False,
                        ),
                        stable,
                    )
                )
            return self._compact_loader_builds(loader_id, builds, selected_build=stable[0] if stable else None), stable_set

        builds = self._sort_loader_builds(
            self._merge_unique(
                self._loader_builds(
                    loader_id,
                    mod_loader,
                    minecraft_version,
                    stable_only=False,
                ),
                stable,
            )
        )
        if stable and loader_id.lower() in self.COMPACT_BUILD_CHOICES_LOADER_IDS:
            builds = self._merge_unique(
                self._compact_loader_builds(loader_id, builds),
                self._compact_loader_builds(loader_id, builds, selected_build=stable[0]),
            )
            return builds, stable_set
        return self._compact_loader_builds(loader_id, builds), stable_set

    def _loader_builds(self, loader_id: str, mod_loader: Any, minecraft_version: str, *, stable_only: bool) -> list[str]:
        cache_key = (loader_id, minecraft_version, bool(stable_only))
        if cache_key in self._loader_builds_cache:
            return list(self._loader_builds_cache[cache_key])

        try:
            builds: list[str] = []
            seen: set[str] = set()
            for raw_build in mod_loader.get_loader_versions(minecraft_version, stable_only):
                build = str(raw_build or "").strip()
                if not build or build in seen:
                    continue
                seen.add(build)
                builds.append(build)
            sorted_builds = self._sort_loader_builds(builds)
            self._loader_builds_cache[cache_key] = list(sorted_builds)
            return sorted_builds
        except Exception:
            self._loader_builds_cache[cache_key] = []
            return []

    @classmethod
    def _compact_loader_builds(cls, loader_id: str, builds: list[str], *, selected_build: str | None = None) -> list[str]:
        if loader_id.lower() not in cls.COMPACT_BUILD_CHOICES_LOADER_IDS or not builds:
            return builds
        selected_family = cls._loader_build_family_key(selected_build or builds[0])
        return [
            build
            for build in builds
            if cls._loader_build_family_key(build) == selected_family
        ]

    @classmethod
    def _loader_build_family_key(cls, version: str) -> tuple[str, ...]:
        numeric_parts: list[str] = []
        for token in re.findall(r"\d+|[a-z]+", version.lower()):
            if token in cls._LOADER_PRERELEASE_RANKS:
                return ("unstable", *numeric_parts[:3], token)
            if token.isdigit():
                numeric_parts.append(token)
        return ("stable", *numeric_parts[:2])

    @classmethod
    def _sort_loader_builds(cls, builds: Iterable[str]) -> list[str]:
        return sorted(
            builds,
            key=cls._loader_build_sort_key,
            reverse=True,
        )

    @staticmethod
    def _merge_unique(*groups: Iterable[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for raw_item in group:
                item = str(raw_item or "").strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    @staticmethod
    def _normalize_string_list(values: Iterable[Any]) -> list[str]:
        items: list[str] = []
        for raw_item in values:
            item = str(raw_item or "").strip()
            if item:
                items.append(item)
        return items

    def _sort_minecraft_versions(self, versions: Iterable[str]) -> list[str]:
        ordered_versions = list(versions)
        order = self._minecraft_version_order()
        if not order:
            return ordered_versions
        return sorted(ordered_versions, key=lambda version: order.get(version, len(order)))

    def _minecraft_version_items(self) -> list[dict[str, str]]:
        if self._minecraft_versions_cache is None:
            try:
                self._minecraft_versions_cache = [
                    {
                        "id": version_id,
                        "type": str(item.get("type") or "").strip(),
                    }
                    for item in minecraft_launcher_lib.utils.get_version_list()
                    if (version_id := str(item.get("id") or "").strip())
                ]
            except Exception:
                self._minecraft_versions_cache = []
        return self._minecraft_versions_cache

    def _minecraft_version_order(self) -> dict[str, int]:
        return {
            item["id"]: index
            for index, item in enumerate(self._minecraft_version_items())
        }

    def _minecraft_version_types(self) -> dict[str, str]:
        return {
            item["id"]: item["type"]
            for item in self._minecraft_version_items()
        }

    def _valid_minecraft_versions(self, *, include_snapshots: bool) -> set[str]:
        return {
            item["id"]
            for item in self._minecraft_version_items()
            if item["type"] == "release" or (include_snapshots and item["type"] == "snapshot")
        }

    @classmethod
    def _loader_build_sort_key(cls, version: str) -> tuple[int, ...]:
        numeric_parts: list[int] = []
        prerelease_parts: list[int] = []
        prerelease_rank = cls._LOADER_STABLE_RANK
        in_prerelease = False
        for token in re.findall(r"\d+|[a-z]+", version.lower()):
            token_rank = cls._LOADER_PRERELEASE_RANKS.get(token)
            if token_rank is not None:
                in_prerelease = True
                prerelease_rank = min(prerelease_rank, token_rank)
                continue
            if not token.isdigit():
                continue
            if in_prerelease:
                prerelease_parts.append(int(token))
            else:
                numeric_parts.append(int(token))
        return (*cls._pad_version_parts(numeric_parts, 8), prerelease_rank, *cls._pad_version_parts(prerelease_parts, 4))

    @staticmethod
    def _pad_version_parts(parts: list[int], length: int) -> tuple[int, ...]:
        return tuple(parts[:length] + [0] * max(0, length - len(parts)))


def unique_version_name(existing_names: Iterable[str], base_name: str) -> str:
    base = str(base_name or "").strip() or "Minecraft"
    existing = {str(name).strip().casefold() for name in existing_names if str(name).strip()}
    if base.casefold() not in existing:
        return base
    index = 2
    while True:
        candidate = f"{base} ({index})"
        if candidate.casefold() not in existing:
            return candidate
        index += 1


__all__ = ["VersionCreateOption", "VersionCreationCatalogService", "unique_version_name"]
