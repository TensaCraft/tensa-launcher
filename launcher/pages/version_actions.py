import subprocess
from pathlib import Path

import flet as ft

from launcher import ui
from launcher.core.game import Game
from launcher.pages.launch_feedback import handle_launch_response
from launcher.pages.launch_profiles import launch_start_kwargs, launch_task_args, show_launch_profile_selector
from launcher.platform.instance_shortcuts import create_desktop_shortcut
from launcher.ui.core.page_runtime import run_blocking, run_task, schedule_update
from launcher.ui.core.session_tasks import PageSessionTasks


class VersionActions:
    """Shared build actions and their page-scoped lifecycle, without shell setup."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.trans = app.trans
        self._closed = False
        self._shortcut_tasks = PageSessionTasks(self.page)
        self._pending_shortcuts: set[str] = set()

    def before_hide(self):
        self._closed = True
        self._shortcut_tasks.close()

    def _session_open(self) -> bool:
        return not self._closed and self._app_session_open()

    def _app_session_open(self) -> bool:
        if getattr(self.app, "_terminating", False):
            return False
        try:
            getattr(self.page, "session", None)
        except RuntimeError:
            return False
        return True

    def _invoke(self, callback):
        if self._session_open():
            callback()

    def _menu_item(self, key, label, icon, callback):
        async def select(_event):
            self._invoke(callback)

        return ft.PopupMenuItem(
            key=key,
            content=ui.Text(label),
            icon=icon,
            height=36,
            on_click=select,
        )

    def _context_menu(self, content, items, *, key=None, expand=False):
        return ui.ContextMenu(content, items, is_active=self._session_open, key=key, expand=expand)

    def _navigation_actions(self):
        return [
            ("add_version", ft.Icons.ADD, self.add_version_btn),
            ("minecraft_components_nav", ft.Icons.CONSTRUCTION, self.app.show_minecraft_components_page),
            ("modpacks_title", ft.Icons.WEBHOOK, self.app.show_modpacks_page),
            ("import_curseforge", ft.Icons.UPLOAD_FILE, self.import_curseforge_btn),
        ]

    def _build_header_actions(self):
        return [
            ui.Button(
                icon=icon,
                text=self.trans(key),
                size="sm",
                on_click=lambda _e, action=callback: self._invoke(action),
            )
            for key, icon, callback in self._navigation_actions()
        ]

    def _build_navigation_menu_items(self):
        return [
            self._menu_item(key, self.trans(key), icon, callback)
            for key, icon, callback in self._navigation_actions()
        ]

    def _build_version_menu_items(self, version):
        from launcher.pages.mods_manager import ModsManagerPage

        if getattr(version, "is_remote", False):
            return []
        items = [
            self._menu_item("play", self.trans("play"), ft.Icons.PLAY_ARROW, lambda: self.handle_play(version)),
            self._menu_item("copy", self.trans("copy"), ft.Icons.COPY, lambda: self.copy_version(version)),
            self._menu_item(
                "directory", self.trans("open_directory"), ft.Icons.FOLDER_OPEN, lambda: self.open_directory(version)
            ),
            self._menu_item(
                "shortcut", self.trans("create_desktop_shortcut"), ft.Icons.ADD_TO_HOME_SCREEN,
                lambda: self.create_shortcut(version),
            ),
            ft.PopupMenuItem(),
        ]
        for key, label_key, icon in ModsManagerPage.CONTENT_TABS:
            items.append(self._menu_item(
                f"tab:{key}", self.trans(label_key), icon,
                lambda tab=key: self.app.show_mods_manager_page(version, initial_tab=tab),
            ))
        return items

    def create_shortcut(self, version):
        if not self._session_open() or version.version_id in self._pending_shortcuts:
            return
        self._pending_shortcuts.add(version.version_id)
        try:
            self._shortcut_tasks.run(self._create_shortcut_async, version.version_id, version.name, version.get_image())
        except RuntimeError as exc:
            self._pending_shortcuts.discard(version.version_id)
            self.app.log.error(f"Unable to schedule desktop shortcut creation: {exc!r}")

    async def _create_shortcut_async(self, version_id: str, name: str, icon_source: str | None = None):
        try:
            if not self._session_open():
                return
            state_dir = getattr(getattr(self.app, "paths", None), "app_state_dir", None) or self.app.util.app_state_dir
            await run_blocking(
                create_desktop_shortcut, version_id, name,
                icon_source=icon_source,
                icon_directory=Path(state_dir) / "shortcut-icons",
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.app.log.error(f"Desktop shortcut creation failed for {version_id!r}: {exc!r}")
            if self._session_open():
                self.app.feedback.warning(self.trans("desktop_shortcut_failed"))
        else:
            if self._session_open():
                self.app.feedback.info(self.trans("desktop_shortcut_created"))
        finally:
            self._pending_shortcuts.discard(version_id)

    def add_version_btn(self):
        self._invoke(self.app.show_version_create_page)

    def import_curseforge_btn(self):
        if self._session_open():
            self.app.curseforge_import_modal(self.app).show()

    def copy_version(self, version):
        from launcher.ui import VersionCopyModal

        if self._session_open():
            VersionCopyModal(self.app, version).show()

    def manage_mods(self, version):
        if self._session_open():
            self.app.show_mods_manager_page(version)

    def open_directory(self, version):
        if not self._session_open():
            return
        response = self.app.util.open_mc_dir(version.path or version.version_id)
        if response is not None:
            self.app.feedback.info(response)

    def handle_play(self, version, *, allow_duplicate: bool = False, profile_key: str | None = None):
        if not self._session_open():
            return
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        if not allow_duplicate and self._confirm_duplicate_launch(version):
            return
        if profile_key is None and show_launch_profile_selector(
            self.app,
            version,
            lambda selected_key: self.handle_play(
                version,
                allow_duplicate=allow_duplicate,
                profile_key=selected_key,
            ),
        ):
            return
        try:
            args = launch_task_args(version, allow_duplicate, profile_key)
            run_task(self.page, self._handle_play_async, *args)
        except Exception:
            self.app.feedback.info(self.trans("installation_already_running"))
            raise

    def _confirm_duplicate_launch(self, version) -> bool:
        if not Game.is_game_dir_active(Game.version_game_dir(version)):
            return False

        def handle_response(response: bool) -> None:
            if response:
                self.handle_play(version, allow_duplicate=True)

        self.app.feedback.confirm(
            self.trans("version_already_running_confirm_title", version=version.name),
            self.trans("version_already_running_confirm_message", version=version.name),
            handle_response,
        )
        return True

    async def _handle_play_async(self, version, allow_duplicate: bool = False, profile_key: str | None = None):
        if not self._session_open():
            return
        response = await run_blocking(version.start, **launch_start_kwargs(allow_duplicate, profile_key))
        if not self._app_session_open():
            return
        handle_launch_response(self.app, response)
        if self._session_open():
            schedule_update(self.page)
