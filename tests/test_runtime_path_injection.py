from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from launcher.core.game import Game
from launcher.core.loaders.base import BaseLoader
from launcher.platform.paths import StorageLayout
from launcher.shared.app_context import AppContext


class _ConcreteLoader(BaseLoader):
    def get_id(self) -> str:
        return "test"

    def get_name(self) -> str:
        return "Test"

    def install(self, *args: Any, **kwargs: Any) -> None:
        return None

    def versions(self) -> list[str]:
        return []


def test_game_uses_app_storage_layout_for_relative_version_paths(tmp_path: Path) -> None:
    layout = StorageLayout(
        app_state_dir=tmp_path / "state",
        minecraft_dir=tmp_path / "minecraft",
        games_dir=tmp_path / "minecraft" / "games",
    )
    AppContext.set(
        SimpleNamespace(
            paths=layout,
            util=SimpleNamespace(minecraft_dir=tmp_path / "legacy" / "minecraft"),
        )
    )
    version = SimpleNamespace(path="games/demo", version_id="demo", name="Demo")

    assert Game.version_game_dir(version) == layout.minecraft_dir / "games" / "demo"
    assert Game()._version_game_dir(version) == layout.minecraft_dir / "games" / "demo"


def test_base_loader_uses_app_storage_layout_paths(tmp_path: Path) -> None:
    layout = StorageLayout(
        app_state_dir=tmp_path / "state",
        minecraft_dir=tmp_path / "minecraft",
        games_dir=tmp_path / "minecraft" / "games",
    )
    AppContext.set(
        SimpleNamespace(
            paths=layout,
            util=SimpleNamespace(
                minecraft_dir=tmp_path / "legacy" / "minecraft",
                games_path=tmp_path / "legacy" / "minecraft" / "games",
            ),
        )
    )

    loader = _ConcreteLoader()

    assert loader.minecraft_dir == layout.minecraft_dir
    assert loader.install_dir == layout.games_dir
