from __future__ import annotations

import inspect
import os
import threading
import time
from contextlib import suppress
from time import sleep

import flet as ft
import minecraft_launcher_lib

from launcher import ui
from launcher.application.error_reports import LauncherReportService
from launcher.application.java_preferences import JavaPreferencesService
from launcher.models.install_callback import InstallCallback
from launcher.models.logger import Logger
from launcher.models.translator import Translator
from launcher.pages.home import Home
from launcher.pages.modpacks import ModpacksPage
from launcher.pages.minecraft_components import MinecraftComponentsPage
from launcher.pages.profiles import ProfilesPage
from launcher.pages.settings import SettingsPage
from launcher.pages.setup_wizard import maybe_show_setup_wizard
from launcher.pages.version_create import VersionCreatePage
from launcher.pages.version_settings import VersionSettingsPage
from launcher.pages.versions import VersionsPage
from launcher.shared import AppContext
from launcher.state import StateStore


class App:
    instance: App | None = None
    JAVA_VERSIONS_TTL_SEC = 24 * 60 * 60

    def __init__(self, page: ft.Page):
        App.instance = self
        AppContext.set(self)
        self.page = page
        self.mll = minecraft_launcher_lib
        self.log = Logger()
        self.sleep = sleep
        self._terminating = False

        self._bootstrap_state()
        ui.set_current_theme(self.theme)
        self._configure_page()
        self._configure_window_lifecycle()
        self._center_window()
        self._build_ui_services()
        self._build_stateful_models()
        self._build_shell()
        self._warm_up_background_tasks()

    def _bootstrap_state(self) -> None:
        self.state = StateStore.build(self)
        self.util = self.state.util
        self.paths = self.state.paths
        self.config = self.state.config
        self.theme = self.state.theme
        self.feedback = self.state.feedback
        self.catalog = self.state.catalog
        self.modrinth_mods = self.state.modrinth_mods
        self.ui_sound = self.state.ui_sound
        self.version_options = self.state.version_options
        self.content = self.state.content
        self.world_backups = self.state.world_backups
        self.auth = self.state.auth
        self.profiles = self.state.profiles
        self.versions = self.state.versions
        self.updater = self.state.updater

    def _configure_page(self) -> None:
        self.page.fonts = ui.configured_page_fonts() or None
        self.page.theme = self.theme.flet_theme
        self.page.dark_theme = self.theme.flet_theme

        self.page.title = self.util.launcher_name
        icon_path = self.util.get_resource_path("logo.ico")
        if icon_path:
            self.page.window.icon = str(icon_path)
        self.page.theme_mode = ft.ThemeMode.DARK

        # Background image для всієї сторінки
        bg_path = self.util.get_background_path()
        if bg_path:
            self.page.bgcolor = ft.Colors.TRANSPARENT
            self.page.decoration = ft.BoxDecoration(
                image=ft.DecorationImage(
                    src=str(bg_path),
                    fit=ft.BoxFit.COVER,
                )
            )
        else:
            self.page.bgcolor = self.theme.bg_primary

        self.page.padding = 0
        self.page.spacing = 0
        self.page.window.resizable = True
        self.page.window.maximizable = True
        self.page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self.page.vertical_alignment = ft.MainAxisAlignment.CENTER

    def _build_ui_services(self) -> None:
        self.header = ui.Header(self)
        self.footer = ui.Footer(self)
        self.navigation = ui.Sidebar(self)
        self.reporter = LauncherReportService(self)
        self._alert_renderer = ui.Alert(self)
        self._main_layout: ft.Row | None = None
        self._content_area: ft.Column | None = None
        self._sidebar: ft.Control | None = None
        self.progressbar = ui.ProgressOverlay(self)
        self.feedback.attach(progress_overlay=self.progressbar, alert_renderer=self._alert_renderer)
        self.version_install_modal = ui.VersionInstallModal
        self.modpack_install_modal = ui.ModpackInstallModal
        self.curseforge_import_modal = ui.CurseForgeImportModal
        self.form_modal = ui.FormDialog
        self.version_card = ui.VersionCard()
        self.install_callback = InstallCallback

    def _build_stateful_models(self) -> None:
        self.java_versions = self.config.get(JavaPreferencesService.LAUNCHER_CACHE_KEY, [])
        self.initialize_app_variables()

    def _build_shell(self) -> None:
        self.setup_navigation()
        self.current_page = Home(self)
        self.show_page(self.current_page)

        minecraft_dir_error = getattr(self.util, "minecraft_dir_error", None)
        if minecraft_dir_error:
            self.feedback.warning(
                self.trans(
                    "minecraft_game_dir_unavailable",
                    path=self.util.minecraft_dir,
                    error=minecraft_dir_error,
                )
            )
        maybe_show_setup_wizard(self)

    def _warm_up_background_tasks(self) -> None:
        auth_refresh = getattr(self.auth, "refresh_all_online_profiles", self.auth.get_default_profile_data)
        threading.Thread(target=auth_refresh, daemon=True).start()
        check_updates = self.config.get("check_updates", self.config.get("auto_update", "yes"))
        if check_updates == "yes":
            self.page.run_task(self.updater.check_for_updates_async)

    def initialize_app_variables(self):
        self.java_versions = self.config.get(JavaPreferencesService.LAUNCHER_CACHE_KEY, []) or []

        last_scan = self.config.get(JavaPreferencesService.LAUNCHER_CACHE_TS_KEY, 0)
        try:
            last_scan_ts = float(last_scan) if last_scan is not None else 0.0
        except (TypeError, ValueError):
            last_scan_ts = 0.0

        # Refresh Java list in the background to avoid blocking UI startup.
        # The dropdown is used only in settings; launching MC doesn't require this scan.
        should_refresh = (
            (not self.java_versions)
            or JavaPreferencesService.has_raw_launcher_runtime_labels(self.java_versions)
            or (time.time() - last_scan_ts > self.JAVA_VERSIONS_TTL_SEC)
        )
        if should_refresh:
            threading.Thread(target=self._refresh_java_versions, daemon=True).start()

    def _refresh_java_versions(self) -> None:
        try:
            versions = self.util.get_all_java()
        except Exception as exc:
            self.log.debug(f"Java versions scan failed: {exc!r}")
            return
        self.config.update(
            {
                JavaPreferencesService.LAUNCHER_CACHE_KEY: versions,
                JavaPreferencesService.LAUNCHER_CACHE_TS_KEY: time.time(),
            }
        )
        self.java_versions = versions

    def _center_window(self) -> None:
        center = getattr(self.page.window, "center", None)
        if not callable(center):
            return
        if inspect.iscoroutinefunction(center):
            self.page.run_task(center)
            return
        center()

    def _configure_window_lifecycle(self) -> None:
        window = getattr(self.page, "window", None)
        if window is None:
            return
        with suppress(Exception):
            window.prevent_close = False
        with suppress(Exception):
            window.on_event = None
        with suppress(Exception):
            self.page.on_disconnect = lambda _e: self._handle_disconnect()

    def _clear_install_session_state(self) -> None:
        self.feedback.shutdown(update_ui=False)

    def _schedule_forced_exit(self, delay_sec: float) -> None:
        def _force_exit() -> None:
            time.sleep(delay_sec)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    async def _close_window_async(self) -> None:
        window = getattr(self.page, "window", None)
        if window is None:
            return

        with suppress(Exception):
            window.prevent_close = False

        for method_name in ("destroy", "close"):
            method = getattr(window, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
                return
            except Exception as exc:
                self.log.debug(f"Window {method_name} failed during shutdown: {exc!r}")

    def _request_window_close(self) -> bool:
        runner = getattr(self.page, "run_task", None)
        if not callable(runner):
            return False
        try:
            runner(self._close_window_async)
            return True
        except Exception as exc:
            self.log.debug(f"Unable to schedule graceful window close: {exc!r}")
            return False

    def _begin_shutdown(self) -> bool:
        if self._terminating:
            return False
        self._terminating = True
        try:
            self._clear_install_session_state()
        except Exception as exc:
            self.log.error(f"Shutdown cleanup failed: {exc}")
        return True

    def _shutdown_launcher(self) -> None:
        if not self._begin_shutdown():
            return
        self.log.info("Launcher shutdown requested, terminating background tasks")
        self._schedule_forced_exit(1.5)
        if not self._request_window_close():
            self._schedule_forced_exit(0.1)

    def _handle_disconnect(self) -> None:
        if not self._begin_shutdown():
            return
        self.log.info("Launcher disconnected, terminating background tasks")
        self._schedule_forced_exit(0.2)

    def setup_navigation(self):
        """Налаштування destinations для NavigationRail."""
        destinations = [
            {
                'icon': ft.Icons.HOME_OUTLINED,
                'selected_icon': ft.Icons.HOME,
                'label': self.trans('home_title'),
                'on_click': lambda e: self.show_home_page(),
                'section': 'main',
            },
            {
                'icon': ft.Icons.LAYERS_OUTLINED,
                'selected_icon': ft.Icons.LAYERS,
                'label': self.trans('builds_title'),
                'on_click': lambda e: self.show_versions_page(),
                'section': 'main',
            },
            {
                'icon': ft.Icons.WEBHOOK_OUTLINED,
                'selected_icon': ft.Icons.WEBHOOK,
                'label': self.trans('modpacks_title'),
                'on_click': lambda e: self.show_modpacks_page(),
                'section': 'main',
            },
            {
                'icon': ft.Icons.SETTINGS_OUTLINED,
                'selected_icon': ft.Icons.SETTINGS,
                'label': self.trans('settings_title'),
                'on_click': lambda e: self.show_settings_page(),
                'section': 'main',
            },
        ]
        self.navigation.set_destinations(destinations)

    def show_page(self, page):
        previous_page = getattr(self, "current_page", None)
        before_hide = getattr(previous_page, "before_hide", None)
        if callable(before_hide):
            before_hide()

        self.current_page = page

        # Створюємо новий контент для сторінки
        new_content = ui.PageContainer(controls=[self.current_page.view()])

        if self._main_layout is None:
            # Перший запуск - створюємо весь layout
            self._sidebar = self.navigation.view()

            # Обгортаємо header, PageContainer і footer в Column
            self._content_area = ui.Column(
                controls=[
                    self.header.view(),
                    new_content,
                    self.footer.view(),
                ],
                expand=True,
                spacing=0,
            )

            # Row з Sidebar ліворуч і контентом праворуч
            self._main_layout = ui.Row(
                controls=[self._sidebar, self._content_area],
                spacing=0,
                expand=True,
            )

            self.page.controls.clear()
            self.page.add(self._main_layout)
        else:
            # Оновлюємо sidebar, header, контент і footer
            assert self._content_area is not None
            self._main_layout.controls[0] = self.navigation.view()
            self._content_area.controls = [
                self.header.view(),
                new_content,
                self.footer.view(),
            ]

        self.page.update()
        after_show = getattr(self.current_page, "after_show", None)
        if callable(after_show):
            after_show()

    def get_sidebar_width(self) -> int:
        return self.navigation.current_width()

    def refresh_sidebar(self) -> None:
        if self._main_layout is None:
            return
        self._sidebar = self.navigation.view()
        self._main_layout.controls[0] = self._sidebar
        self.page.update()

    def refresh_shell(self) -> None:
        if self._main_layout is None or self._content_area is None:
            return
        self._sidebar = self.navigation.view()
        self._main_layout.controls[0] = self._sidebar
        if self._content_area.controls:
            self._content_area.controls[0] = self.header.view()
        if len(self._content_area.controls) >= 3:
            self._content_area.controls[-1] = self.footer.view()
        self.page.update()

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.config.set("compact_sidebar", "yes" if collapsed else "no")
        self.refresh_sidebar()

    def show_home_page(self):
        self.navigation.set_selected_index(0)
        self.show_page(Home(self))

    def show_settings_page(self, initial_tab: str = "launcher"):
        self.navigation.set_selected_index(3)
        self.show_page(SettingsPage(self, initial_tab=initial_tab))

    def show_versions_page(self):
        self.navigation.set_selected_index(1)
        self.show_page(VersionsPage(self))

    def show_version_create_page(self):
        self.navigation.set_selected_index(1)
        self.show_page(VersionCreatePage(self))

    def show_minecraft_components_page(self):
        self.navigation.set_selected_index(1)
        self.show_page(MinecraftComponentsPage(self))

    def show_version_settings_page(self, version_key: str):
        # Залишаємо вибраний індекс незмінним (версії залишаються виділеними)
        self.show_page(VersionSettingsPage(self, version_key))

    def show_profiles_page(self, initial_action: str | None = None):
        self.navigation.set_selected_index(-1)  # Не виділяємо жоден пункт у sidebar
        self.show_page(ProfilesPage(self, initial_action=initial_action))

    def show_modpacks_page(self):
        self.navigation.set_selected_index(2)
        self.show_page(ModpacksPage(self))

    def show_activity_page(self):
        self.show_settings_page(initial_tab="activity")

    def show_mods_manager_page(self, version):
        from launcher.pages.mods_manager import ModsManagerPage
        self.show_page(ModsManagerPage(self, version))

    def run(self):
        self.page.update()
        ui.show_window_when_ready(self.page)

    def restart(self):
        self.page.scroll = None
        self.__init__(self.page)
        self.run()

    def stop(self):
        self._shutdown_launcher()

    def trans(self, key, **placeholders):
        return Translator(self.config.get('lang', 'en_US')).get(key, **placeholders)
