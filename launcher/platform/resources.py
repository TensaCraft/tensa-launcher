from __future__ import annotations

import sys
from pathlib import Path

from .paths import is_frozen

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ASSETS_DIR = PACKAGE_ROOT / "assets"


class ResourceService:
    def __init__(self, path_service) -> None:
        self.path_service = path_service

    @staticmethod
    def _normalize_parts(*path_parts: str) -> tuple[str, ...]:
        parts = tuple(part for part in path_parts if part)
        if parts and parts[0] == "assets":
            return parts[1:]
        return parts

    def get_resource_path(self, *path_parts: str) -> Path | None:
        normalized_parts = self._normalize_parts(*path_parts)
        candidates: list[Path] = []
        if is_frozen() and hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "launcher" / "assets" / Path(*normalized_parts))
        candidates.append(PACKAGE_ASSETS_DIR / Path(*normalized_parts))

        for path in candidates:
            if path.exists():
                return path
        return None

    def get_background_path(self) -> Path | None:
        for location in (("img", "bg.png"), ("bg.png",)):
            path = self.get_resource_path(*location)
            if path:
                return path
        return None
