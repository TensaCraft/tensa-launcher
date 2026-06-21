from __future__ import annotations

import importlib
from typing import Dict, List, Optional

import launcher.core.util as util


def _versions_store_class():
    return importlib.import_module("launcher.storage.version_store").Versions


def _launcher_class():
    return importlib.import_module("launcher.core.launcher").Launcher


def _game_class():
    return importlib.import_module("launcher.core.game").Game


class Version:
    _default_image_cache: Optional[str] = None
    HOME_PINNED_KEY = "homePinned"
    LAST_INTEGRITY_CHECK_KEY = "lastIntegrityCheck"

    def __init__(self, version_id: str, data: Dict):
        self.version_id = util.normalize_string(version_id)
        self.id = data.get("id", self.version_id)
        self.name = data.get("name", self.version_id)
        self.version = data.get("version")
        self.loader = data.get("loader")
        self.client = data.get("client") or self.loader
        self.path = data.get("path")
        self.loader_version = data.get("loader_version", None)
        self.force_update = data.get("force_update", False)
        self.options = data.get("options", {}) or {}
        self.image = data.get("image", None)

        if "gpuMode" not in self.options:
            self.options["gpuMode"] = "dgpu"

    def jvm_args(self) -> List[str]:
        return self.options.get("jvmArguments", [])

    def executable_path(self) -> Optional[str]:
        return self.options.get("executablePath")

    def is_tensacraft(self) -> bool:
        client = (self.client or "").lower()
        return "tensacraft" in client or "tensa" in client

    def is_home_pinned(self) -> bool:
        return bool((self.options or {}).get(self.HOME_PINNED_KEY))

    def mark_home_pinned(self) -> None:
        self.options = self.options or {}
        self.options[self.HOME_PINNED_KEY] = True
        self.save()

    def last_integrity_check(self) -> Optional[float]:
        val = (self.options or {}).get(self.LAST_INTEGRITY_CHECK_KEY)
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def mark_integrity_check(self, timestamp: Optional[float] = None) -> None:
        from time import time

        self.options = self.options or {}
        self.options[self.LAST_INTEGRITY_CHECK_KEY] = timestamp or time()
        self.save()

    def get_image(self) -> Optional[str]:
        if self.image:
            return self.image
        if Version._default_image_cache is None:
            default_icon = util.get_resource_path("img", "grass_block.png")
            if default_icon:
                Version._default_image_cache = str(default_icon)
        return Version._default_image_cache

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "loader": self.loader,
            "client": self.client,
            "path": self.path,
            "loader_version": self.loader_version,
            "force_update": self.force_update,
            "options": self.options,
            "image": self.image,
        }

    def save(self) -> None:
        _versions_store_class().instance().add(self)

    def __str__(self) -> str:
        return f"{self.name or self.version_id} ({self.version})"

    def install(self) -> None:
        loader = _launcher_class().get_loader(self.client)
        loader.install(self, loader_version=self.loader_version)

    def sync_update(self) -> None:
        loader = _launcher_class().get_loader(self.client)
        loader.sync_update(self)

    def start(self, *, allow_duplicate: bool = False, profile_key: str | None = None):
        return _game_class()().start(self, allow_duplicate=allow_duplicate, profile_key=profile_key)
