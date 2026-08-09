from __future__ import annotations

from typing import Any, Dict, Iterable, List, Type

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

    def __init__(self, app: Any) -> None:
        self.app = app
        self._instances: Dict[str, BaseLoader] = {}

    def available_loader_ids(self) -> Iterable[str]:
        builtin = set(self.LOADERS.keys())
        dynamic = {
            name
            for name in minecraft_launcher_lib.mod_loader.list_mod_loader()
            if name not in builtin
        }
        return [*builtin, *sorted(dynamic)]

    def get_loader(self, loader_name: str) -> BaseLoader:
        name = loader_name.lower()
        loader_class = self.LOADERS.get(name)
        if not loader_class:
            Logger.error(f"No loader class defined for '{loader_name}'.")
            raise ValueError(f"No loader class defined for '{loader_name}'.")
        if name not in self._instances:
            self._instances[name] = loader_class(app=self.app)
        return self._instances[name]

    def loaders(self) -> List[BaseLoader]:
        return [
            self.get_loader(name)
            for name in self.available_loader_ids()
            if name in self.LOADERS
        ]

    def get_loader_versions(self, loader: str) -> List[str]:
        loader_instance = self.get_loader(loader)
        return loader_instance.versions()


__all__ = ["Launcher"]
