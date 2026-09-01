from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from launcher.application.catalog import ModrinthCatalogService
from launcher.application.feedback import FeedbackService
from launcher.application.instance_operations import InstanceOperationCoordinator
from launcher.application.modrinth_mods import ModrinthModsService
from launcher.application.shared_resources import SharedResourceCoordinator
from launcher.application.tensacraft_profile_identity import TensaCraftProfileIdentity
from launcher.application.ui_sound import UiSoundService
from launcher.application.version_content import VersionContentService
from launcher.application.version_options import VersionOptionsService
from launcher.application.world_backups import WorldBackupService
from launcher.core import util
from launcher.core.auth.auth import Auth
from launcher.core.updater import AutoUpdater
from launcher.platform.paths import LauncherPaths
from launcher.storage import Config, Profiles, Versions
from launcher.ui.theme import UiTheme, set_current_theme


@dataclass(slots=True)
class AppState:
    util: Any
    paths: LauncherPaths
    config: Config
    theme: UiTheme
    feedback: FeedbackService
    instance_operations: InstanceOperationCoordinator
    shared_resources: SharedResourceCoordinator
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
    @staticmethod
    def build(app: Any) -> AppState:
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

        instance_operations = InstanceOperationCoordinator()
        shared_resources = SharedResourceCoordinator()
        versions = Versions(
            storage_dir=layout.app_state_dir,
            minecraft_dir=layout.minecraft_dir,
        )
        for version in versions.all():
            try:
                repaired = TensaCraftProfileIdentity.repair(
                    version,
                    minecraft_dir=layout.minecraft_dir,
                    persist=True,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                app.log.warning(
                    f"Unable to repair TensaCraft profile identity for "
                    f"{version.version_id}: {exc}"
                )
            else:
                if repaired:
                    app.log.info(
                        f"Repaired TensaCraft profile identity for {version.version_id}"
                    )
        state = AppState(
            util=util,
            paths=layout,
            config=config,
            theme=set_current_theme(UiTheme.build()),
            feedback=FeedbackService(app),
            instance_operations=instance_operations,
            shared_resources=shared_resources,
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
                instance_operations=instance_operations,
            ),
            auth=Auth(app),
            profiles=Profiles(app, storage_dir=layout.app_state_dir),
            versions=versions,
            updater=AutoUpdater(app),
        )
        return state
