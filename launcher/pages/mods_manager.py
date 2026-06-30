from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import flet as ft

from launcher import ui
from launcher.application.catalog import CatalogState
from launcher.application.world_backups import WorldBackupInfo, WorldInfo
from launcher.core.game import Game
from launcher.pages.launch_feedback import handle_launch_response
from launcher.pages.launch_profiles import launch_start_kwargs, launch_task_args, show_launch_profile_selector
from launcher.presentation import ModsManagerCards
from launcher.ui.core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog

from .mods_manager_installed import ModsManagerInstalledMixin
from .mods_manager_search import ModsManagerSearchMixin
from .version_settings import VersionSettingsPage


class ModsManagerPage(
    ModsManagerSearchMixin,
    ModsManagerInstalledMixin,
):
    CONTENT_TABS = (
        ("mods", "mods_content_tab", ft.Icons.EXTENSION),
        ("resourcepacks", "resourcepacks", ft.Icons.PALETTE),
        ("shaders", "shaders_content_tab", ft.Icons.WB_SUNNY_OUTLINED),
        ("backups", "world_backups_content_tab", ft.Icons.BACKUP_OUTLINED),
        ("screenshots", "version_screenshots_content_tab", ft.Icons.PHOTO_LIBRARY_OUTLINED),
        ("settings", "version_settings_content_tab", ft.Icons.TUNE),
        ("diagnostics", "version_section_diagnostics", ft.Icons.BUG_REPORT_OUTLINED),
        ("delete", "version_delete_content_tab", ft.Icons.DELETE_OUTLINE),
    )
    INNER_TABS = (
        ("installed", "installed_content_tab", ft.Icons.INVENTORY_2_OUTLINED),
        ("modrinth", "modrinth_content_tab", ft.Icons.SEARCH),
    )

    def __init__(self, app, version):
        self.app = app
        self.page = app.page
        self.version = version
        self.trans = self.app.trans
        self.cards = ModsManagerCards(app)

        self.mods_supported = self._check_mods_support()
        self.mods_dir = self._get_mods_directory() if self.mods_supported else None
        self.resourcepacks_dir = self._get_resourcepacks_directory()
        self.shaderpacks_dir = self._get_shaderpacks_directory()

        self.current_content_key = "mods"
        self.current_inner_tab = "installed"
        self.content_configs = self._build_content_configs()
        self.content_tabs_data = [
            {"key": key, "icon": icon, "text": self.trans(label_key), "index": index}
            for index, (key, label_key, icon) in enumerate(self.CONTENT_TABS)
        ]

        self.installed_items: dict[str, list[Dict]] = {key: [] for key, *_rest in self.CONTENT_TABS}
        self.installed_mods: list[Dict] = []
        self.installed_resourcepacks: list[Dict] = []
        self.installed_shaderpacks: list[Dict] = []
        self.worlds: list[WorldInfo] = []
        self.selected_backup_world_path: Path | None = None
        self.installed_containers: dict[str, ft.ListView] = {}
        self.loaded_content_keys: set[str] = set()
        self.world_backups_loaded = False
        self.screenshots_loaded = False
        self.screenshots_container: ft.ListView | None = None
        self.screenshot_preview_dialog: ft.AlertDialog | None = None
        self.modrinth_dependency_dialog: ft.AlertDialog | None = None
        self.version_settings_page: VersionSettingsPage | None = None
        self.delete_directory_toggle: ft.Container | None = None
        self.delete_backups_toggle: ft.Container | None = None

        self.search_field = None
        self.search_bar = None
        self.search_results_container = None
        self.search_prev_button = None
        self.search_next_button = None
        self.search_page_label = None
        self.search_pagination_container = None
        self.tab_buttons = None
        self.inner_tab_buttons = None
        self.filter_info_container = None
        self.minecraft_version_select = None
        self.search_result_items: list[Dict] = []

        self.search_state = CatalogState(limit=self.app.theme.modpacks_per_page)
        self._is_active = False
        self._search_timer: threading.Timer | None = None
        self.content_installing = False
        self.is_loading = True

        self._setup_header()
        self._setup_ui()

    def _build_content_configs(self) -> dict[str, dict]:
        return {
            "mods": {
                "project_type": "mod",
                "directory_attr": "mods_dir",
                "empty_key": "no_mods_installed",
                "empty_icon": ft.Icons.EXTENSION_OFF,
                "icon": ft.Icons.EXTENSION,
                "installing_key": "installing_mod",
                "installed_key": "mod_installed",
                "deleted_key": "mod_deleted",
                "confirm_delete_key": "confirm_delete_mod",
                "enable_key": "enable_mod",
                "disable_key": "disable_mod",
                "enabled_key": "mod_enabled",
                "disabled_key": "mod_disabled",
                "search_key": "search_mods",
            },
            "resourcepacks": {
                "project_type": "resourcepack",
                "directory_attr": "resourcepacks_dir",
                "empty_key": "no_resourcepacks_installed",
                "empty_icon": ft.Icons.PALETTE,
                "icon": ft.Icons.PALETTE,
                "installing_key": "installing_resourcepack",
                "installed_key": "resourcepack_installed",
                "deleted_key": "resourcepack_deleted",
                "confirm_delete_key": "confirm_delete_resourcepack",
                "enable_key": "enable_resourcepack",
                "disable_key": "disable_resourcepack",
                "enabled_key": "resourcepack_enabled",
                "disabled_key": "resourcepack_disabled",
                "search_key": "search_resourcepacks",
            },
            "shaders": {
                "project_type": "shader",
                "directory_attr": "shaderpacks_dir",
                "empty_key": "no_shaderpacks_installed",
                "empty_icon": ft.Icons.WB_SUNNY_OUTLINED,
                "icon": ft.Icons.WB_SUNNY_OUTLINED,
                "installing_key": "installing_shaderpack",
                "installed_key": "shaderpack_installed",
                "deleted_key": "shaderpack_deleted",
                "confirm_delete_key": "confirm_delete_shaderpack",
                "enable_key": "enable_shaderpack",
                "disable_key": "disable_shaderpack",
                "enabled_key": "shaderpack_enabled",
                "disabled_key": "shaderpack_disabled",
                "search_key": "search_shaders",
            },
        }

    def _check_mods_support(self) -> bool:
        return self.app.content.mods_supported(self.version)

    def _get_mods_directory(self) -> Optional[Path]:
        return self.app.content.get_mods_directory(self.version)

    def _get_backup_directory(self) -> Optional[Path]:
        return self.app.content.get_backup_directory(self.mods_dir)

    def _get_resourcepacks_directory(self) -> Optional[Path]:
        return self.app.content.get_resourcepacks_directory(self.version)

    def _get_shaderpacks_directory(self) -> Optional[Path]:
        return self.app.content.get_shaderpacks_directory(self.version)

    def _scan_installed_resourcepacks(self) -> List[Dict]:
        return self.app.content.scan_installed_resourcepacks(self.resourcepacks_dir)

    def _scan_installed_shaderpacks(self) -> List[Dict]:
        return self.app.content.scan_installed_shaderpacks(self.shaderpacks_dir, mods_dir=self.mods_dir)

    def _scan_installed_mods(self) -> List[Dict]:
        if not self.mods_supported:
            return []
        return self.app.content.scan_installed_mods(self.mods_dir)

    def _setup_header(self):
        self.app.header.set_params(
            title=self.trans("mods_manager_title", version=self.version.name),
            actions=[
                ui.Button(
                    text=self.trans("play"),
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    size="sm",
                    on_click=lambda _e: self._handle_play(),
                )
            ],
        )
        self.app.footer.set_params(center_btn=False, left_btn=False, right_btn=False)

    def _handle_play(self, *, allow_duplicate: bool = False, profile_key: str | None = None) -> None:
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        if not allow_duplicate and self._confirm_duplicate_launch():
            return
        if profile_key is None and show_launch_profile_selector(
            self.app,
            self.version,
            lambda selected_key: self._handle_play(
                allow_duplicate=allow_duplicate,
                profile_key=selected_key,
            ),
        ):
            return
        try:
            args = launch_task_args(self.version, allow_duplicate, profile_key)
            run_task(self.page, self._handle_play_async, *args)
        except Exception:
            self.app.feedback.info(self.trans("installation_already_running"))
            raise

    def _confirm_duplicate_launch(self) -> bool:
        if not Game.is_game_dir_active(Game.version_game_dir(self.version)):
            return False

        def handle_response(response: bool) -> None:
            if response:
                self._handle_play(allow_duplicate=True)

        self.app.feedback.confirm(
            self.trans("version_already_running_confirm_title", version=self.version.name),
            self.trans("version_already_running_confirm_message", version=self.version.name),
            handle_response,
        )
        return True

    async def _handle_play_async(self, version, allow_duplicate: bool = False, profile_key: str | None = None) -> None:
        response = await run_blocking(version.start, **launch_start_kwargs(allow_duplicate, profile_key))
        handle_launch_response(self.app, response)
        schedule_update(self.app.page)

    def _build_unavailable_content(self):
        return ui.Container(
            ui.Column(
                [
                    ui.Icon(ft.Icons.BLOCK, size=64, color=self.app.theme.text_tertiary),
                    ui.Text(
                        self.trans("mods_not_supported"),
                        size=self.app.theme.text_size_large,
                        color=self.app.theme.text_color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ui.Text(
                        self.trans("mods_not_supported_desc"),
                        size=self.app.theme.text_size_sm,
                        color=self.app.theme.text_secondary,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    def _build_filters_info_content(self):
        loader = self._get_loader_name() or "Unknown"
        game_version = self.selected_minecraft_version
        if self.current_content_key == "mods":
            label = self.trans("mods_filtered_by", loader=loader.capitalize(), version=game_version)
        else:
            label = self.trans("content_filtered_by_version", version=game_version)
        return ui.Row(
            [
                ui.Icon(ft.Icons.FILTER_ALT, size=16, color=self.app.theme.text_secondary),
                ui.Text(
                    label,
                    size=self.app.theme.text_size_xs,
                    color=self.app.theme.text_secondary,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_filters_info(self):
        return ui.Container(
            content=self._build_filters_info_content(),
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.16, self.app.theme.text_tertiary)),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            bgcolor=ft.Colors.with_opacity(0.06, self.app.theme.bg_header_footer),
        )

    def _refresh_filters_info(self) -> None:
        if self.filter_info_container is not None:
            self.filter_info_container.content = self._build_filters_info_content()

    @property
    def selected_minecraft_version(self) -> str:
        return str(self.version.version or "")

    def _build_search_controls(self):
        config = self._current_config()
        search_parts = ui.build_search_field(
            self.app,
            label=self.trans(config["search_key"]),
            value=self.search_state.query,
            on_submit=lambda _e: self._search_mods(),
            on_change=self.on_search_change,
        )
        self.search_input = search_parts.field
        self.search_bar = search_parts.row
        self.search_results_container = ui.ListView(
            [],
            spacing=8,
            expand=True,
            padding=0,
            auto_scroll=False,
            scroll=ft.ScrollMode.AUTO,
            build_controls_on_demand=True,
            item_extent=104,
            cache_extent=700,
        )
        self.search_prev_button = ui.Button(
            text=self.trans("previous_page"),
            on_click=lambda _e: self._go_previous_search_page(),
            size="sm",
            height=self.app.theme.shell_action_height,
        )
        self.search_next_button = ui.Button(
            text=self.trans("next_page"),
            on_click=lambda _e: self._go_next_search_page(),
            size="sm",
            height=self.app.theme.shell_action_height,
        )
        self.search_page_label = ui.Text(
            self.trans("page_indicator", current_page=1, total_pages=1),
            size=self.app.theme.text_size_xs,
            color=self.app.theme.text_secondary,
        )
        self.search_pagination_container = ui.Container(
            content=ui.Row(
                [self.search_prev_button, self.search_page_label, self.search_next_button],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            padding=ft.Padding.only(
                top=self.app.theme.padding_xs,
                bottom=self.app.theme.padding_xs,
            ),
            visible=False,
        )
        self.app.footer.set_params(
            center_control=self.search_pagination_container,
            left_btn=False,
            right_btn=False,
        )

    def _build_tab_bar(self):
        self.tab_buttons = ui.Container(
            content=ui.Row(
                [self._create_tab_button(tab_data) for tab_data in self.content_tabs_data],
                spacing=0,
                expand=True,
            ),
            padding=2,
            bgcolor=ft.Colors.with_opacity(0.08, self.app.theme.bg_header_footer),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.18, self.app.theme.text_tertiary)),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
        )

    def _build_inner_tabs(self):
        self.inner_tab_buttons = ui.Row(
            controls=self._build_inner_tab_buttons(),
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

    def _set_footer_for_current_tab(self) -> None:
        center_control = self.search_pagination_container if self._uses_inner_tabs() else None
        if self._is_settings_tab() and self.version_settings_page is not None:
            center_control = self.version_settings_page.footer_save_button
        self.app.footer.set_params(
            center_control=center_control,
            left_btn=False,
            right_btn=False,
        )

    def _setup_ui(self):
        self._build_search_controls()
        self.installed_containers = {
            key: ui.ListView(
                [],
                spacing=8,
                expand=True,
                padding=0,
                auto_scroll=False,
                scroll=ft.ScrollMode.AUTO,
                build_controls_on_demand=True,
                cache_extent=700,
            )
            for key, *_rest in self.CONTENT_TABS
        }
        self.screenshots_container = self.installed_containers["screenshots"]
        self._ensure_tab_loaded(self.current_content_key, update=False)

        self.tab_content = ui.Container(expand=True)
        self._build_tab_bar()
        self._build_inner_tabs()
        self.filter_info_container = self._build_filters_info()
        self._update_tab_content()
        self._update_search_pagination()

    def _create_tab_button(self, tab_data: dict) -> ui.Container:
        key = tab_data["key"]
        return self.cards.tab_button(
            tab_data,
            is_active=self.current_content_key == key,
            on_click=lambda e, content_key=key: self._switch_content_tab(content_key),
        )

    def _build_inner_tab_buttons(self) -> list[ft.Control]:
        buttons: list[ft.Control] = []
        for key, label_key, icon in self._inner_tabs_for_current_content():
            selected = key == self.current_inner_tab
            buttons.append(
                ui.Button(
                    text=self.trans(label_key),
                    icon=icon,
                    variant="filled" if selected else "ghost",
                    tone="primary" if selected else "neutral",
                    size="sm",
                    on_click=lambda _e, tab_key=key: self._switch_inner_tab(tab_key),
                )
            )
        return buttons

    def view(self):
        return ui.Column(
            [
                ui.Container(
                    content=self.tab_buttons,
                    padding=ft.Padding.only(
                        left=self.app.theme.shell_padding,
                        right=self.app.theme.shell_padding,
                        top=self.app.theme.padding_sm,
                    ),
                ),
                ui.Container(
                    content=ui.Row(
                        [self.inner_tab_buttons, self.filter_info_container],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.only(
                        left=self.app.theme.shell_padding,
                        right=self.app.theme.shell_padding,
                        top=self.app.theme.padding_sm,
                    ),
                ),
                self.tab_content,
            ],
            spacing=0,
            expand=True,
        )

    def after_show(self):
        self._is_active = True

    def before_hide(self):
        self._is_active = False
        self.search_state.cancel()
        if self._search_timer is not None:
            self._search_timer.cancel()
            self._search_timer = None

    def _switch_content_tab(self, key: str):
        if key == self.current_content_key:
            return

        self.current_content_key = key
        self._normalize_inner_tab_for_content()
        if self._is_world_backups_tab():
            self.selected_backup_world_path = None
        self._ensure_tab_loaded(key, update=False)
        self.tab_buttons.content.controls.clear()
        self.tab_buttons.content.controls.extend(
            [self._create_tab_button(tab_data) for tab_data in self.content_tabs_data]
        )
        self.inner_tab_buttons.controls = self._build_inner_tab_buttons()
        self._reset_search_results(clear_query=True)
        if self._uses_inner_tabs():
            self._refresh_search_label()
            self._refresh_filters_info()
        self._update_tab_content()
        self._update_search_pagination()
        if self._is_modrinth_tab_active():
            self._search_mods()
        schedule_update(self.page)

    def _switch_inner_tab(self, key: str):
        if key == self.current_inner_tab:
            return
        if not self._inner_tab_available(key):
            return

        self.current_inner_tab = key
        self.inner_tab_buttons.controls = self._build_inner_tab_buttons()
        if self.current_inner_tab == "installed":
            self._ensure_tab_loaded(self.current_content_key, update=False)
        self._update_tab_content()
        self._update_search_pagination()
        if self._is_modrinth_tab_active() and not self.search_results_container.controls:
            self._search_mods()
        schedule_update(self.page)

    def _update_tab_content(self):
        self._normalize_inner_tab_for_content()
        self._set_footer_for_current_tab()
        padding = ft.Padding.only(
            left=self.app.theme.shell_padding,
            right=self.app.theme.shell_padding,
            top=self.app.theme.padding_sm,
            bottom=self.app.theme.padding_md,
        )
        if self._is_world_backups_tab():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            self.tab_content.content = ui.Container(
                self.installed_containers["backups"],
                padding=padding,
                expand=True,
            )
            return
        if self._is_screenshots_tab():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            self.tab_content.content = ui.Container(
                self.installed_containers["screenshots"],
                padding=padding,
                expand=True,
            )
            return
        if self._is_settings_tab():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            settings_panel = self._build_version_settings_panel()
            self._set_footer_for_current_tab()
            self.tab_content.content = ui.Container(
                settings_panel,
                padding=padding,
                expand=True,
            )
            return
        if self._is_diagnostics_tab():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            self.tab_content.content = ui.Container(
                self._build_version_diagnostics_panel(),
                padding=padding,
                expand=True,
            )
            return
        if self._is_delete_tab():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            self.tab_content.content = ui.Container(
                self._build_delete_version_panel(),
                padding=padding,
                expand=True,
            )
            return

        if not self._uses_inner_tabs():
            self.inner_tab_buttons.visible = False
            self.filter_info_container.visible = False
            self.tab_content.content = ui.Container(
                self._build_installed_panel(),
                padding=padding,
                expand=True,
            )
            return

        self.inner_tab_buttons.visible = True
        self.filter_info_container.visible = True
        if self.current_inner_tab == "installed":
            self.tab_content.content = ui.Container(
                self._build_installed_panel(),
                padding=padding,
                expand=True,
            )
            return

        self.tab_content.content = ui.Container(
            self._build_modrinth_panel(),
            padding=padding,
            expand=True,
        )

    def _build_installed_panel(self):
        if self.current_content_key == "mods" and not self.mods_supported:
            return self._build_unavailable_content()
        self._ensure_tab_loaded(self.current_content_key, update=False)
        return self.installed_containers[self.current_content_key]

    def _build_modrinth_panel(self):
        if self.current_content_key == "mods" and not self.mods_supported:
            return self._build_unavailable_content()
        return ui.Column(
            [self.search_bar, self.search_results_container],
            spacing=8,
            expand=True,
        )

    def _current_config(self) -> dict:
        return self.content_configs[self.current_content_key]

    def _current_directory(self) -> Path | None:
        return getattr(self, self._current_config()["directory_attr"])

    def _current_project_type(self) -> str:
        return self._current_config()["project_type"]

    def _content_context(self, key: str | None = None) -> dict:
        content_key = key or self.current_content_key
        config = self.content_configs[content_key]
        return {
            "key": content_key,
            "config": config,
            "project_type": config["project_type"],
            "directory": getattr(self, config["directory_attr"]),
            "game_version": self.selected_minecraft_version,
            "loader": self._get_loader_name(),
            "installed_items": list(self.installed_items.get(content_key, [])),
        }

    def _is_modrinth_tab_active(self) -> bool:
        return self.current_inner_tab == "modrinth" and self._content_supports_modrinth(self.current_content_key)

    def _uses_inner_tabs(self) -> bool:
        return self.current_content_key in self.content_configs and len(self._inner_tabs_for_current_content()) > 1

    def _inner_tabs_for_current_content(self) -> tuple[tuple[str, str, str], ...]:
        if self.current_content_key not in self.content_configs:
            return ()
        return tuple(tab for tab in self.INNER_TABS if tab[0] != "modrinth" or self._content_supports_modrinth())

    def _inner_tab_available(self, key: str) -> bool:
        return any(tab_key == key for tab_key, *_rest in self._inner_tabs_for_current_content())

    def _content_supports_modrinth(self, key: str | None = None) -> bool:
        content_key = key or self.current_content_key
        if content_key not in self.content_configs:
            return False
        if content_key == "mods" and not self.mods_supported:
            return False
        if content_key == "shaders" and not self.mods_supported:
            return False
        return True

    def _normalize_inner_tab_for_content(self) -> None:
        if not self._inner_tab_available(self.current_inner_tab):
            self.current_inner_tab = "installed"

    def _is_world_backups_tab(self) -> bool:
        return self.current_content_key == "backups"

    def _is_screenshots_tab(self) -> bool:
        return self.current_content_key == "screenshots"

    def _is_settings_tab(self) -> bool:
        return self.current_content_key == "settings"

    def _is_diagnostics_tab(self) -> bool:
        return self.current_content_key == "diagnostics"

    def _is_delete_tab(self) -> bool:
        return self.current_content_key == "delete"

    def _ensure_tab_loaded(self, key: str, *, update: bool = False) -> None:
        if key in self.content_configs:
            if key not in self.loaded_content_keys:
                self._rebuild_installed_content(key, update=update)
            return
        if key == "backups":
            if not self.world_backups_loaded:
                self._rebuild_world_backups(update=update)
            return
        if key == "screenshots" and not self.screenshots_loaded:
            self._rebuild_screenshots(update=update)

    def _reset_search_results(self, *, clear_query: bool = False) -> None:
        self.search_state.cancel()
        self.search_result_items = []
        if clear_query:
            self.search_state.query = ""
            self.search_state.offset = 0
            self.search_state.total_results = 0
            self.search_state.current_page_size = 0
            if self.search_input is not None:
                self.search_input.value = ""
        if self.search_results_container is not None:
            self.search_results_container.controls.clear()
        self._update_search_pagination()

    def _refresh_search_label(self) -> None:
        if self.search_input is not None and not self._is_world_backups_tab():
            self.search_input.label = self.trans(self._current_config()["search_key"])

    def _scan_installed_content(self, key: str) -> list[Dict]:
        if key == "mods":
            return self.app.content.apply_modrinth_metadata(self.version, self._scan_installed_mods())
        if key == "resourcepacks":
            return self.app.content.apply_modrinth_metadata(self.version, self._scan_installed_resourcepacks())
        if key == "shaders":
            return self.app.content.apply_modrinth_metadata(self.version, self._scan_installed_shaderpacks())
        return []

    def _rebuild_installed_content(self, key: str, *, update: bool = True) -> None:
        if key == "backups":
            self._rebuild_world_backups(update=update)
            return
        if key not in self.content_configs:
            return

        items = self._scan_installed_content(key)
        self.installed_items[key] = items
        if key == "mods":
            self.installed_mods = items
        elif key == "resourcepacks":
            self.installed_resourcepacks = items
        elif key == "shaders":
            self.installed_shaderpacks = items

        container = self.installed_containers[key]
        container.controls.clear()

        if not items:
            config = self.content_configs[key]
            container.controls.append(
                ui.Container(
                    ui.Column(
                        [
                            ui.Icon(config["empty_icon"], size=48, color=self.app.theme.text_tertiary),
                            ui.Text(
                                self.trans(config["empty_key"]),
                                size=self.app.theme.text_size_medium,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.Alignment.CENTER,
                    expand=True,
                )
            )
        else:
            for item in items:
                container.controls.append(self._create_installed_content_card(key, item))

        self.loaded_content_keys.add(key)
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _rebuild_installed_mods(self):
        self._rebuild_installed_content("mods")

    def _rebuild_installed_resourcepacks(self):
        self._rebuild_installed_content("resourcepacks")

    def _rebuild_installed_shaderpacks(self):
        self._rebuild_installed_content("shaders")

    def _create_installed_content_card(self, key: str, item: Dict) -> ui.Container:
        if key == "mods":
            return self._create_installed_mod_card(item)

        config = self.content_configs[key]
        return self.cards.installed_content_card(
            item,
            icon=config["icon"],
            enable_tooltip=self.trans(config["enable_key"]),
            disable_tooltip=self.trans(config["disable_key"]),
            show_toggle=item.get("toggle_supported", True),
            on_toggle=lambda e, content_key=key, content_item=item: self._toggle_pack(content_key, content_item),
            on_delete=lambda e, content_key=key, content_item=item: self._delete_pack(content_key, content_item),
        )

    def _toggle_pack(self, key: str, item: Dict):
        config = self.content_configs[key]
        try:
            if key == "resourcepacks":
                enabled = self.app.content.toggle_resourcepack(self.resourcepacks_dir, item)
            elif key == "shaders":
                enabled = self.app.content.toggle_shaderpack(self.shaderpacks_dir, item)
            else:
                return

            message_key = config["enabled_key"] if enabled else config["disabled_key"]
            self.app.feedback.info(self.trans(message_key, name=item["filename"]))
            self.loaded_content_keys.discard(key)
            self._rebuild_installed_content(key)
        except Exception as exc:
            self.app.log.error(f"Failed to toggle {key}: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _delete_pack(self, key: str, item: Dict):
        config = self.content_configs[key]

        def handle_confirm(confirmed):
            if not confirmed:
                return
            try:
                if key == "resourcepacks":
                    self.app.content.delete_resourcepack(self.resourcepacks_dir, item)
                elif key == "shaders":
                    self.app.content.delete_shaderpack(self.shaderpacks_dir, item)
                else:
                    return

                self.app.feedback.info(self.trans(config["deleted_key"], name=item["filename"]))
                self.loaded_content_keys.discard(key)
                self._rebuild_installed_content(key)
            except Exception as exc:
                self.app.log.error(f"Failed to delete {key}: {exc}")
                self.app.feedback.warning(f"Error: {exc}")

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans(config["confirm_delete_key"], name=item["filename"]),
            handle_confirm,
        )

    def _build_version_settings_panel(self) -> ft.Control:
        if self.version_settings_page is None:
            self.version_settings_page = VersionSettingsPage(
                self.app,
                self.version.version_id,
                embedded=True,
                on_saved=self._after_embedded_version_save,
            )
        return self.version_settings_page.view()

    def _build_version_diagnostics_panel(self) -> ft.Control:
        if self.version_settings_page is None:
            self.version_settings_page = VersionSettingsPage(
                self.app,
                self.version.version_id,
                embedded=True,
                on_saved=self._after_embedded_version_save,
            )
        return self.version_settings_page.diagnostics_view()

    def _after_embedded_version_save(self, version) -> None:
        self.version = version
        self.app.header.set_params(title=self.trans("mods_manager_title", version=self.version.name))

    def _build_delete_version_panel(self) -> ft.Control:
        self._ensure_delete_controls()
        directory_toggle = self.delete_directory_toggle
        backups_toggle = self.delete_backups_toggle
        if directory_toggle is None or backups_toggle is None:
            raise RuntimeError("Delete controls were not initialised")
        warning_color = "#FFC107"
        return ui.ListView(
            [
                ui.Container(
                    ui.Row(
                        [
                            ui.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=24, color=warning_color),
                            ui.Column(
                                [
                                    ui.Text(
                                        self.trans("version_delete_warning_title"),
                                        size=self.app.theme.text_size_medium,
                                        weight=ft.FontWeight.W_600,
                                        color=self.app.theme.text_color,
                                    ),
                                    ui.Text(
                                        self.trans("version_delete_warning_desc"),
                                        size=self.app.theme.text_size_sm,
                                        color=self.app.theme.text_secondary,
                                    ),
                                ],
                                spacing=4,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.08, warning_color),
                    border=ft.Border.all(1, ft.Colors.with_opacity(0.35, warning_color)),
                    border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
                    padding=self.app.theme.padding_md,
                ),
                directory_toggle,
                backups_toggle,
                ui.Container(
                    content=ui.Row(
                        [
                            ui.Button(
                                text=self.trans("delete_version_action"),
                                icon=ft.Icons.DELETE_OUTLINE,
                                size="sm",
                                bgcolor=self.app.theme.error,
                                color=self.app.theme.color_white,
                                icon_color=self.app.theme.color_white,
                                on_click=self._delete_version,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                    padding=ft.Padding.only(top=self.app.theme.padding_sm),
                ),
            ],
            spacing=8,
            expand=True,
            padding=0,
            auto_scroll=False,
            scroll=ft.ScrollMode.AUTO,
        )

    def _ensure_delete_controls(self) -> None:
        if self.delete_directory_toggle is None:
            self.delete_directory_toggle = self._delete_option_toggle(
                "version_delete_directory",
                "version_delete_directory_desc",
                value=True,
            )
        if self.delete_backups_toggle is None:
            self.delete_backups_toggle = self._delete_option_toggle(
                "version_delete_backups",
                "version_delete_backups_desc",
                value=False,
            )

    def _delete_option_toggle(self, label_key: str, description_key: str, *, value: bool) -> ft.Container:
        return ui.Container(
            content=ui.Row(
                [
                    ui.Column(
                        [
                            ui.Text(
                                self.trans(label_key),
                                size=self.app.theme.text_size_medium,
                                weight=ft.FontWeight.W_600,
                                color=self.app.theme.text_color,
                            ),
                            ui.Text(
                                self.trans(description_key),
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        expand=True,
                    ),
                    ui.Switch(value=value),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def _delete_version(self, _e=None) -> None:
        self._ensure_delete_controls()
        delete_directory = self._delete_toggle_value(self.delete_directory_toggle)
        delete_backups = self._delete_toggle_value(self.delete_backups_toggle)

        def handle_confirm(confirmed):
            if not confirmed:
                return
            if delete_directory and self._game_dir_active():
                self.app.feedback.warning(self.trans("version_delete_close_game_first"))
                return
            try:
                run_task(self.page, self._delete_version_async, delete_directory, delete_backups)
            except Exception as exc:
                self.app.log.error(f"Unable to schedule version deletion for '{self.version.version_id}': {exc}")
                self.app.feedback.warning(self.trans("version_delete_failed"))

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_delete_version", version=self.version.name),
            handle_confirm,
        )

    async def _delete_version_async(self, delete_directory: bool, delete_backups: bool) -> None:
        try:
            await run_blocking(
                self._delete_version_worker,
                delete_directory=delete_directory,
                delete_backups=delete_backups,
            )
        except Exception as exc:
            self.app.log.error(f"Version deletion failed for '{self.version.version_id}': {exc}")
            self.app.feedback.warning(self.trans("version_delete_failed"))
            return

        self.app.feedback.info(self.trans("version_deleted"))
        self.app.show_versions_page()
        schedule_update(self.page)

    def _delete_version_worker(self, *, delete_directory: bool, delete_backups: bool) -> None:
        if delete_backups:
            service = self._world_backup_service()
            if service is not None:
                service.delete_version_backups(self.version)
        self.app.versions.remove(self.version.version_id, delete_files=delete_directory)

    @staticmethod
    def _delete_toggle_value(toggle: ft.Container | None) -> bool:
        row = getattr(toggle, "content", None)
        controls = getattr(row, "controls", None) or []
        if len(controls) < 2:
            return False
        return bool(getattr(controls[1], "value", False))

    def _version_root(self) -> Path:
        raw = Path(str(self.version.path or self.version.version_id))
        if raw.is_absolute():
            return raw
        return Path(getattr(self.app.util, "minecraft_dir")) / raw

    def _screenshots_dir(self) -> Path:
        return self._version_root() / "screenshots"

    def _scan_screenshots(self) -> list[Path]:
        screenshots_dir = self._screenshots_dir()
        if not screenshots_dir.is_dir():
            return []
        supported = {".png", ".jpg", ".jpeg"}
        try:
            screenshots = [path for path in screenshots_dir.iterdir() if path.is_file() and path.suffix.lower() in supported]
        except OSError:
            return []
        return sorted(screenshots, key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)

    def _rebuild_screenshots(self, *, update: bool = True) -> None:
        container = self.installed_containers["screenshots"]
        container.controls.clear()
        screenshots = self._scan_screenshots()

        if not screenshots:
            container.controls.append(self._empty_backups_panel("screenshots_empty"))
        else:
            for screenshot in screenshots:
                container.controls.append(self._create_screenshot_card(screenshot))

        self.screenshots_loaded = True
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _create_screenshot_card(self, screenshot: Path) -> ui.Container:
        try:
            stat = screenshot.stat()
            size = self._format_bytes(stat.st_size)
            modified = self._format_timestamp(stat.st_mtime)
        except OSError:
            size = "0 B"
            modified = "-"

        thumbnail = ui.Container(
            content=ui.Image(
                src=str(screenshot),
                width=96,
                height=54,
                fit=ft.BoxFit.COVER,
                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            ),
            width=96,
            height=54,
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            on_click=lambda _e, path=screenshot: self._open_screenshot_preview(path),
            tooltip=self.trans("open_screenshot"),
        )

        return ui.Container(
            ui.Row(
                [
                    thumbnail,
                    ui.Column(
                        [
                            ui.Text(
                                screenshot.name,
                                size=self.app.theme.text_size_medium,
                                weight=ft.FontWeight.W_600,
                                color=self.app.theme.text_color,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ui.Text(
                                f"{size} • {modified}",
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        expand=True,
                    ),
                    ui.Row(
                        [
                            ui.FloatingActionButton(
                                icon=ft.Icons.OPEN_IN_NEW,
                                on_click=lambda _e, path=screenshot: self._open_path(path),
                                tooltip=self.trans("open_screenshot"),
                                mini=True,
                            ),
                            ui.FloatingActionButton(
                                icon=ft.Icons.DELETE,
                                on_click=lambda _e, path=screenshot: self._delete_screenshot(path),
                                tooltip=self.trans("delete"),
                                foreground_color=self.app.theme.error,
                                mini=True,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def _open_screenshot_preview(self, screenshot: Path) -> None:
        theme = self.app.theme
        preview_width = theme.modal_width_md
        preview_height = 460
        self.screenshot_preview_dialog = ui.AlertDialog(
            title=ui.Text(
                screenshot.name,
                color=theme.text_color,
                weight=theme.font_weight_bold,
            ),
            modal=False,
            on_dismiss=lambda _e: self._dismiss_screenshot_preview(),
            content=ui.Container(
                content=ui.Image(
                    src=str(screenshot),
                    width=preview_width,
                    height=preview_height,
                    fit=ft.BoxFit.CONTAIN,
                ),
                width=preview_width,
                height=preview_height,
                bgcolor=theme.overlay(0.18, theme.bg_shell),
                border=ft.Border.all(1, theme.border_color),
                border_radius=ft.BorderRadius.all(theme.radius_sm),
                alignment=ft.Alignment.CENTER,
                padding=theme.padding_sm,
            ),
            actions=[
                ui.Button(
                    text=self.trans("open_screenshot"),
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda _e, path=screenshot: self._open_path(path),
                ),
                ui.Button(
                    text=self.trans("close"),
                    variant="outline",
                    tone="neutral",
                    on_click=lambda _e: self._close_screenshot_preview(),
                ),
            ],
        )
        show_dialog(self.page, self.screenshot_preview_dialog)
        schedule_update(self.page)

    def _dismiss_screenshot_preview(self) -> None:
        self.screenshot_preview_dialog = None
        schedule_update(self.page)

    def _close_screenshot_preview(self) -> None:
        if self.screenshot_preview_dialog is not None:
            close_dialog(self.page, self.screenshot_preview_dialog)
        self.screenshot_preview_dialog = None
        schedule_update(self.page)

    def _open_path(self, path: Path) -> None:
        response = self.app.util.open_mc_dir(str(path))
        if response:
            self.app.feedback.warning(response)

    def _delete_screenshot(self, screenshot: Path) -> None:
        def handle_confirm(confirmed):
            if not confirmed:
                return
            try:
                screenshot.unlink(missing_ok=True)
                self.app.feedback.info(self.trans("screenshot_deleted", name=screenshot.name))
                self.screenshots_loaded = False
                self._rebuild_screenshots()
            except OSError as exc:
                self.app.log.error(f"Failed to delete screenshot {screenshot}: {exc}")
                self.app.feedback.warning(self.trans("screenshot_delete_failed", name=screenshot.name))

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_delete_screenshot", name=screenshot.name),
            handle_confirm,
        )

    def _world_backup_service(self):
        return getattr(self.app, "world_backups", None)

    def _rebuild_world_backups(self, *, update: bool = True) -> None:
        container = self.installed_containers["backups"]
        container.controls.clear()

        service = self._world_backup_service()
        if service is None:
            container.controls.append(self._empty_backups_panel("world_backups_unavailable"))
            return

        self.worlds = service.scan_worlds(self.version)
        if not self.worlds:
            self.selected_backup_world_path = None
            container.controls.append(self._empty_backups_panel("world_backups_no_worlds"))
        else:
            selected_world = self._selected_backup_world()
            if selected_world is None:
                for world in self.worlds:
                    container.controls.append(self._create_world_backup_card(world))
            else:
                container.controls.append(self._create_world_backup_detail(selected_world))

        self.world_backups_loaded = True
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _empty_backups_panel(self, key: str) -> ui.Container:
        return ui.Container(
            ui.Column(
                [
                    ui.Icon(ft.Icons.BACKUP_OUTLINED, size=48, color=self.app.theme.text_tertiary),
                    ui.Text(
                        self.trans(key),
                        size=self.app.theme.text_size_medium,
                        color=self.app.theme.text_secondary,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    def _create_world_backup_card(self, world: WorldInfo) -> ui.Container:
        return ui.Container(
            ui.Row(
                [
                    ui.Column(
                        [
                            ui.Row(
                                [
                                    ui.Icon(ft.Icons.PUBLIC, size=18, color=self.app.theme.primary),
                                    ui.Text(
                                        world.name,
                                        size=self.app.theme.text_size_medium,
                                        weight=ft.FontWeight.W_600,
                                        color=self.app.theme.text_color,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ui.Text(
                                str(world.path),
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_disabled,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ui.Text(
                                self.trans(
                                    "world_backups_world_details",
                                    size=self._format_bytes(world.size),
                                    backups=world.backup_count,
                                    modified=self._format_timestamp(world.modified_at),
                                ),
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        spacing=3,
                        tight=True,
                        expand=True,
                    ),
                    ui.Row(
                        [
                            ui.FloatingActionButton(
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=lambda _e, backup_world=world: self._open_world_folder(backup_world),
                                tooltip=self.trans("open_directory"),
                                mini=True,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
            on_click=lambda _e, backup_world=world: self._open_world_backups(backup_world),
        )

    def _create_world_backup_detail(self, world: WorldInfo) -> ui.Column:
        service = self._world_backup_service()
        backups = service.scan_backups(self.version, world.path) if service is not None else []
        backup_rows = [self._create_backup_row(backup) for backup in backups]
        if not backup_rows:
            backup_rows.append(
                ui.Container(
                    ui.Column(
                        [
                            ui.Icon(ft.Icons.BACKUP_OUTLINED, size=44, color=self.app.theme.text_tertiary),
                            ui.Text(
                                self.trans("world_backups_empty"),
                                size=self.app.theme.text_size_medium,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.Alignment.CENTER,
                    padding=self.app.theme.padding_lg,
                )
            )

        return ui.Column(
            [
                ui.Container(
                    ui.Row(
                        [
                            ui.Button(
                                text=self.trans("world_backups_back_to_worlds"),
                                icon=ft.Icons.ARROW_BACK,
                                size="sm",
                                on_click=lambda _e: self._back_to_backup_worlds(),
                                tooltip=self.trans("world_backups_back_to_worlds"),
                            ),
                            ui.Button(
                                text=self.trans("world_backups_create_now"),
                                icon=ft.Icons.ADD,
                                size="sm",
                                on_click=lambda _e, backup_world=world: self._create_world_backup(backup_world),
                            ),
                        ],
                        spacing=12,
                        alignment=ft.MainAxisAlignment.START,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                ),
                ui.Column(backup_rows, spacing=8, tight=True),
            ],
            spacing=8,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _selected_backup_world(self) -> WorldInfo | None:
        if self.selected_backup_world_path is None:
            return None
        selected_path = self.selected_backup_world_path
        for world in self.worlds:
            try:
                if world.path == selected_path or world.path.resolve() == selected_path.resolve():
                    return world
            except OSError:
                if world.path == selected_path:
                    return world
        self.selected_backup_world_path = None
        return None

    def _open_world_backups(self, world: WorldInfo) -> None:
        self.selected_backup_world_path = world.path
        self._rebuild_world_backups()

    def _back_to_backup_worlds(self) -> None:
        self.selected_backup_world_path = None
        self._rebuild_world_backups()

    def _open_world_folder(self, world: WorldInfo) -> None:
        response = self.app.util.open_mc_dir(str(world.path))
        if response:
            self.app.feedback.warning(response)

    def _create_backup_row(self, backup: WorldBackupInfo) -> ui.Container:
        kind_key = "world_backup_kind_auto" if backup.kind == "auto" else "world_backup_kind_manual"
        return ui.Container(
            ui.Row(
                [
                    ui.Row(
                        [
                            ui.Icon(ft.Icons.BACKUP_OUTLINED, size=18, color=self.app.theme.primary),
                            ui.Column(
                                [
                                    ui.Text(
                                        self.trans(
                                            "world_backup_row",
                                            kind=self.trans(kind_key),
                                            date=self._format_timestamp(backup.created_timestamp),
                                            size=self._format_bytes(backup.size),
                                        ),
                                        size=self.app.theme.text_size_medium,
                                        weight=ft.FontWeight.W_600,
                                        color=self.app.theme.text_color,
                                    ),
                                    ui.Text(
                                        backup.path.name,
                                        size=self.app.theme.text_size_xs,
                                        color=self.app.theme.text_secondary,
                                    ),
                                ],
                                spacing=3,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=8,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ui.Row(
                        [
                            ui.FloatingActionButton(
                                icon=ft.Icons.RESTORE,
                                on_click=lambda _e, world_backup=backup: self._restore_world_backup(world_backup),
                                tooltip=self.trans("restore"),
                                foreground_color=self.app.theme.primary,
                                mini=True,
                            ),
                            ui.FloatingActionButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                on_click=lambda _e, world_backup=backup: self._delete_world_backup(world_backup),
                                tooltip=self.trans("delete"),
                                foreground_color=self.app.theme.error,
                                mini=True,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def _create_world_backup(self, world: WorldInfo) -> None:
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        operation = self.app.feedback.begin_operation(
            self.trans("world_backup_creating", world=world.name),
            kind="backup",
            status=self.trans("world_backup_creating", world=world.name),
        )
        try:
            run_task(self.page, self._create_world_backup_async, world, operation)
        except Exception:
            operation.fail(self.trans("world_backup_create_failed", world=world.name), notify=False)
            raise

    async def _create_world_backup_async(self, world: WorldInfo, operation=None) -> None:
        final_message = self.trans("world_backup_create_failed", world=world.name)
        finish_level = "warning"
        try:
            service = self._world_backup_service()
            if service is None:
                raise RuntimeError("World backup service is unavailable")
            await run_blocking(service.create_backup, self.version, world.path, kind="manual")
            self.selected_backup_world_path = world.path
            self.app.feedback.info(self.trans("world_backup_created", world=world.name))
            final_message = self.trans("world_backup_created", world=world.name)
            finish_level = "success"
            self._rebuild_world_backups()
        except Exception as exc:
            self.app.log.error(f"Failed to create world backup: {exc}")
            self.app.feedback.warning(self.trans("world_backup_create_failed", world=world.name))
        finally:
            if operation is not None:
                operation.finish(final_message, show_success=False, level=finish_level)

    def _restore_world_backup(self, backup: WorldBackupInfo) -> None:
        def handle_confirm(confirmed):
            if not confirmed:
                return
            if self._game_dir_active():
                self.app.feedback.warning(self.trans("world_backup_close_game_first"))
                return
            operation = self.app.feedback.begin_operation(
                self.trans("world_backup_restoring", world=backup.world_name),
                kind="backup",
                status=self.trans("world_backup_restoring", world=backup.world_name),
            )
            run_task(self.page, self._restore_world_backup_async, backup, operation)

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("world_backup_restore_confirm", world=backup.world_name),
            handle_confirm,
        )

    async def _restore_world_backup_async(self, backup: WorldBackupInfo, operation) -> None:
        final_message = self.trans("world_backup_restore_failed", world=backup.world_name)
        finish_level = "warning"
        try:
            service = self._world_backup_service()
            if service is None:
                raise RuntimeError("World backup service is unavailable")
            await run_blocking(service.restore_backup, backup)
            self.app.feedback.info(self.trans("world_backup_restored", world=backup.world_name))
            final_message = self.trans("world_backup_restored", world=backup.world_name)
            finish_level = "success"
            self._rebuild_world_backups()
        except Exception as exc:
            self.app.log.error(f"Failed to restore world backup: {exc}")
            self.app.feedback.warning(self.trans("world_backup_restore_failed", world=backup.world_name))
        finally:
            operation.finish(final_message, show_success=False, level=finish_level)

    def _delete_world_backup(self, backup: WorldBackupInfo) -> None:
        def handle_confirm(confirmed):
            if not confirmed:
                return
            try:
                service = self._world_backup_service()
                if service is None:
                    raise RuntimeError("World backup service is unavailable")
                service.delete_backup(backup)
                self.app.feedback.info(self.trans("world_backup_deleted", world=backup.world_name))
                self._rebuild_world_backups()
            except Exception as exc:
                self.app.log.error(f"Failed to delete world backup: {exc}")
                self.app.feedback.warning(self.trans("world_backup_delete_failed", world=backup.world_name))

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("world_backup_delete_confirm", world=backup.world_name),
            handle_confirm,
        )

    def _game_dir_active(self) -> bool:
        try:
            from launcher.core.game import Game

            service = self._world_backup_service()
            game_dir = service.version_game_dir(self.version) if service is not None else self.version.path
            return Game.is_game_dir_active(game_dir)
        except Exception:
            return False

    @staticmethod
    def _format_bytes(size: int | float) -> str:
        value = float(size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        if not timestamp:
            return "-"
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


__all__ = ["ModsManagerPage"]
