from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from launcher.application.catalog import ModrinthCatalogService
from launcher.application.feedback import FeedbackService
from launcher.application.modrinth_mods import ModrinthModsService
from launcher.application.ui_sound import UiSoundService
from launcher.application.version_options import VersionOptionsService
from launcher.application.version_content import VersionContentService
from launcher.application.world_backups import WorldBackupService
from launcher.core import util
from launcher.core.auth.auth import Auth
from launcher.core.updater import AutoUpdater
from launcher.platform.paths import StorageLayout
from launcher.storage import Config, Profiles, Versions
from launcher.ui.theme import UiTheme, set_current_theme


@dataclass(slots=True)
class AppState:
    util: Any
    paths: StorageLayout
    config: Config
    theme: UiTheme
    feedback: FeedbackService
    catalog: ModrinthCatalogService
    modrinth_mods: ModrinthModsService
    ui_sound: UiSoundService
    version_options: VersionOptionsService
    content: VersionContentService
    world_backups: WorldBackupService
    auth: Auth
    profiles: Profiles
    versions: Versions
    updater: AutoUpdater


class StateStore:
    _state: AppState | None = None

    @classmethod
    def build(cls, app: Any) -> AppState:
        util.init(create_minecraft_dirs=False)
        app.util = util

        initial_layout = util.paths
        config = Config(storage_dir=initial_layout.app_state_dir)
        saved_minecraft_dir = config.get("minecraft_game_dir")
        if saved_minecraft_dir:
            if not util.set_minecraft_dir_override(str(saved_minecraft_dir)):
                config.delete("minecraft_game_dir")
        util.init()
        app.util = util
        layout = util.paths
        config = Config(storage_dir=layout.app_state_dir)

        from launcher.core import Launcher

        Launcher._INSTANCE_CACHE.clear()
        configure_versions = getattr(Versions, "configure", None)
        if callable(configure_versions):
            configure_versions(
                storage_dir=layout.app_state_dir,
                minecraft_dir=layout.minecraft_dir,
            )
        Versions._instance = None

        state = AppState(
            util=util,
            paths=layout,
            config=config,
            theme=set_current_theme(UiTheme.build()),
            feedback=FeedbackService(app),
            catalog=ModrinthCatalogService(),
            modrinth_mods=ModrinthModsService(),
            ui_sound=UiSoundService(config, app.log, use_thread=True),
            version_options=VersionOptionsService(),
            content=VersionContentService(layout.minecraft_dir, app.log),
            world_backups=WorldBackupService(
                layout.minecraft_dir,
                config,
                app.log,
                translator=getattr(app, "trans", None),
            ),
            auth=Auth(app),
            profiles=Profiles(app, storage_dir=layout.app_state_dir),
            versions=Versions.instance(),
            updater=AutoUpdater(app),
        )
        cls._state = state
        return state

    @classmethod
    def current(cls) -> AppState:
        if cls._state is None:
            raise RuntimeError("App state has not been initialized.")
        return cls._state
