from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from launcher import APP_NAME, __version__, ui  # noqa: E402
from launcher.application.catalog import ModrinthCatalogService  # noqa: E402
from launcher.application.feedback import FeedbackService  # noqa: E402
from launcher.application.modrinth_mods import ModrinthModsService  # noqa: E402
from launcher.application.version_content import VersionContentService  # noqa: E402
from launcher.application.version_options import VersionOptionsService  # noqa: E402
from launcher.application.world_backups import WorldBackupService  # noqa: E402


class DummyPage:
    def __init__(self) -> None:
        self.services: list[Any] = []
        self.overlay: list[Any] = []
        self.controls: list[Any] = []
        self.theme_colors: dict[str, str] = {}
        self.floating_action_button = None
        self.floating_action_button_location = None
        self.fonts = None
        self.theme = None
        self.dark_theme = None
        self.title = ""
        self.padding = 0
        self.spacing = 0
        self.bgcolor = None
        self.decoration = None
        self.theme_mode = None
        self.horizontal_alignment = None
        self.vertical_alignment = None
        self.window = SimpleNamespace(
            icon=None,
            resizable=False,
            maximizable=False,
            center=lambda: None,
            close=lambda: None,
            destroy=lambda: None,
        )

    def update(self) -> None:
        return None

    def schedule_update(self) -> None:
        return None

    def add(self, *controls: Any) -> None:
        self.controls.extend(controls)

    def show_dialog(self, dialog: Any) -> None:
        dialog.open = True

    def open(self, dialog: Any) -> None:
        dialog.open = True

    def pop_dialog(self) -> None:
        dialogs = getattr(self, "_dialogs", None)
        controls = getattr(dialogs, "controls", None)
        if isinstance(controls, list) and controls:
            dialog = controls.pop()
            dialog.open = False
            updater = getattr(dialogs, "update", None)
            if callable(updater):
                updater()

    def run_task(self, func, *args, **kwargs):
        return None

    def run_thread(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class DummyConfig:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)


class DummyLogger:
    def debug(self, *_args, **_kwargs) -> None:
        return None

    info = debug
    warning = debug
    error = debug


class DummyAlert:
    def show_alert(self, *_args, **_kwargs) -> None:
        return None

    def show_confirm(self, _title: str, _question: str, callback) -> None:
        callback(False)


@dataclass
class FakeVersion:
    name: str = "Vanilla 1.20.1"
    client: str = "vanilla"
    version: str = "1.20.1"
    version_id: str = "vanilla-1.20.1"
    path: str = "versions/vanilla-1.20.1"
    loader: str = ""
    image: str | None = None
    options: dict[str, Any] = field(
        default_factory=lambda: {
            "executablePath": "C:/Java/bin/javaw.exe",
            "server": {"host": "localhost", "port": 25565},
            "gpuMode": "dgpu",
        }
    )
    id: str = "vanilla-1.20.1"

    def get_image(self) -> str | None:
        return self.image

    def start(self, *, allow_duplicate: bool = False, profile_key: str | None = None):
        return None

    def jvm_args(self) -> list[str]:
        return ["-Xms2G", "-Xmx4G"]

    def is_tensacraft(self) -> bool:
        return False

    def is_home_pinned(self) -> bool:
        return False

    def install(self) -> None:
        return None


class FakeVersionsRepo:
    def __init__(self, versions: list[FakeVersion]) -> None:
        self._versions = list(versions)

    def all(self) -> list[FakeVersion]:
        return list(self._versions)

    def get(self, key: str) -> FakeVersion | None:
        for version in self._versions:
            if version.version_id == key or version.id == key:
                return version
        return None

    def get_by_name(self, name: str) -> FakeVersion | None:
        for version in self._versions:
            if version.name == name:
                return version
        return None

    def remove(self, key: str, *, delete_files: bool = True) -> None:
        if delete_files:
            for version in self._versions:
                if version.version_id == key or version.id == key:
                    path = Path(version.path)
                    if path.exists() and path.is_dir():
                        shutil.rmtree(path)
        self._versions = [version for version in self._versions if version.version_id != key and version.id != key]


class FakeProfilesRepo:
    def __init__(self) -> None:
        self._profiles = {
            "default": {
                "id": "player-1",
                "name": "PlayerOne",
                "access_token": "offline",
                "default": True,
            }
        }

    def get_all_profiles(self) -> dict[str, dict[str, Any]]:
        return dict(self._profiles)

    def get_default_profile(self) -> dict[str, Any] | None:
        for profile in self._profiles.values():
            if profile.get("default"):
                return dict(profile)
        return None

    def get_profile(self, key: str) -> dict[str, Any] | None:
        profile = self._profiles.get(key)
        return dict(profile) if profile else None

    def load(self) -> dict[str, dict[str, Any]]:
        return dict(self._profiles)

    def delete_profile(self, key: str) -> None:
        self._profiles.pop(key, None)

    def set_default_profile(self, key: str) -> None:
        for profile_key, profile in self._profiles.items():
            profile["default"] = profile_key == key

    def create_profile(self, name: str, payload: dict[str, Any]):
        self._profiles[name] = dict(payload)
        self.set_default_profile(name)
        return None


@pytest.fixture
def fake_app(tmp_path: Path):
    page = DummyPage()
    minecraft_dir = tmp_path / "minecraft"
    minecraft_dir.mkdir()

    util = SimpleNamespace(
        launcher_version=__version__,
        launcher_name=APP_NAME,
        minecraft_dir=minecraft_dir,
        app_dir=tmp_path,
        app_state_dir=tmp_path,
        get_skin_url=lambda _profile_id: "https://example.com/skin.png",
        get_cached_skin_url=lambda _profile_id, allow_stale=True: "https://example.com/skin.png",
        prefetch_skin=lambda _profile_id, on_ready=None: False,
        get_resource_path=lambda *_parts: None,
        get_background_path=lambda: None,
        set_minecraft_dir_override=lambda _value: None,
        open_mc_dir=lambda *_paths: None,
    )

    config = DummyConfig(
        {
            "lang": "uk_UA",
            "default_min_ram_gb": 2,
            "default_max_ram_gb": 4,
            "gpu_mode_default": "dgpu",
            "compact_sidebar": "yes",
            "show_tensacraft_versions": "yes",
            "java_versions": [{"Java 21": "C:/Java/bin/javaw.exe"}],
        }
    )

    app = SimpleNamespace()
    app.page = page
    app.util = util
    app.log = DummyLogger()
    app.config = config
    app.catalog = ModrinthCatalogService()
    app.modrinth_mods = ModrinthModsService()
    app.version_options = VersionOptionsService()
    app.content = VersionContentService(util.minecraft_dir, app.log)
    app.world_backups = WorldBackupService(util.minecraft_dir, config, app.log)
    app.trans = lambda key, **placeholders: (
        f"{key} ({', '.join(f'{name}={value}' for name, value in placeholders.items())})"
        if placeholders
        else key
    )
    app.theme = ui.set_current_theme(ui.UiTheme.build())
    page.theme = app.theme.flet_theme
    page.dark_theme = app.theme.flet_theme
    app.sleep = lambda _seconds: None
    app.profiles = FakeProfilesRepo()
    version = FakeVersion()
    app.versions = FakeVersionsRepo([version])
    app.auth = SimpleNamespace(verify=lambda _profile: True, authenticate_with_device_code=lambda: {"ok": True})
    app.java_versions = [{"Java 21": "C:/Java/bin/javaw.exe"}]
    app._refresh_java_versions = lambda: None

    app.header = ui.Header(app)
    app.footer = ui.Footer(app)
    app.navigation = ui.Sidebar(app)
    app.progressbar = ui.ProgressOverlay(app)
    app._alert_renderer = DummyAlert()
    app.feedback = FeedbackService(app, auto_close_delay=0)
    app.feedback.attach(progress_overlay=app.progressbar, alert_renderer=app._alert_renderer)
    app.version_card = ui.VersionCard()

    app.show_home_page = lambda: None
    app.show_settings_page = lambda: None
    app.show_versions_page = lambda: None
    app.show_version_create_page = lambda: None
    app.show_minecraft_components_page = lambda: None
    app.show_profiles_page = lambda initial_action=None: None
    app.show_modpacks_page = lambda: None
    app.show_activity_page = lambda: None
    app.show_mods_manager_page = lambda _version: None
    app.show_version_settings_page = lambda _version_key: None
    app.get_sidebar_width = lambda: app.navigation.current_width()
    app.refresh_sidebar = lambda: None
    app.refresh_shell = lambda: None
    app.set_sidebar_collapsed = lambda collapsed: app.config.set("compact_sidebar", "yes" if collapsed else "no")

    app.modpack_install_modal = lambda *_args, **_kwargs: SimpleNamespace(show=lambda: None)
    app.version_install_modal = lambda *_args, **_kwargs: SimpleNamespace(show=lambda: None)
    app.curseforge_import_modal = lambda *_args, **_kwargs: SimpleNamespace(show=lambda: None)
    app.form_modal = lambda *_args, **_kwargs: SimpleNamespace(open=lambda: None)

    app._content_area = None

    return app
