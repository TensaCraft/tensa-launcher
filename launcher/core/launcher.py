from __future__ import annotations

from typing import Dict, Iterable, List, Type

import minecraft_launcher_lib

from launcher.core.loaders import (
    CurseForgeLoader,
    FabricLoader,
    ForgeLoader,
    MinecraftLoader,
    ModrinthLoader,
    NeoForgeLoader,
    QuiltLoader,
    TensaCraftLoader,
)
from launcher.core.loaders.base import BaseLoader
from launcher.models.logger import Logger


class Launcher:
    """Factory and registry for loader implementations."""

    LOADERS: Dict[str, Type[BaseLoader]] = {
        "tensacraft": TensaCraftLoader,
        "minecraft": MinecraftLoader,
        "curseforge": CurseForgeLoader,
        "modrinth": ModrinthLoader,
        "fabric": FabricLoader,
        "forge": ForgeLoader,
        "neoforge": NeoForgeLoader,
        "quilt": QuiltLoader,
    }

    @classmethod
    def available_loader_ids(cls) -> Iterable[str]:
        builtin = set(cls.LOADERS.keys())
        dynamic = {
            name
            for name in minecraft_launcher_lib.mod_loader.list_mod_loader()
            if name not in builtin
        }
        return [*builtin, *sorted(dynamic)]

    _INSTANCE_CACHE: Dict[str, BaseLoader] = {}

    @classmethod
    def get_loader(cls, loader_name: str) -> BaseLoader:
        name = loader_name.lower()
        loader_class = cls.LOADERS.get(name)
        if not loader_class:
            Logger.error(f"No loader class defined for '{loader_name}'.")
            raise ValueError(f"No loader class defined for '{loader_name}'.")
        if name not in cls._INSTANCE_CACHE:
            cls._INSTANCE_CACHE[name] = loader_class()
        return cls._INSTANCE_CACHE[name]

    @classmethod
    def loaders(cls) -> List[BaseLoader]:
        return [cls.get_loader(name) for name in cls.available_loader_ids() if name in cls.LOADERS]

    @classmethod
    def get_loader_versions(cls, loader: str) -> List[str]:
        loader_instance = cls.get_loader(loader)
        return loader_instance.versions()


__all__ = ["Launcher"]
