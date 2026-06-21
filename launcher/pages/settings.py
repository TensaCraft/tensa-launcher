from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import flet as ft

from launcher import ui
from launcher.application.java_preferences import JavaPreferencesService
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.application.setup_wizard import SetupWizardService
from launcher.application.ui_sound import UiSoundService
from launcher.pages.activity import ActivityPanel
from launcher.pages.launch_profiles import ASK_PROFILE_ON_LAUNCH_KEY
from launcher.ui.core.page_runtime import run_blocking, run_task, schedule_update


class SettingsPage:
    TABS = (
        ("launcher", "settings_tab_launcher", ft.Icons.TUNE),
        ("backups", "settings_tab_backups", ft.Icons.BACKUP_OUTLINED),
        ("java_performance", "settings_tab_java_performance", ft.Icons.SPEED),
        ("activity", "activity_center", ft.Icons.HISTORY),
    )

    def __init__(self, app, initial_tab: str = "launcher"):
        self.app = app
        self.page = app.page
        self.trans = self.app.trans
        self.layout = ui.FormSection(app)
        self.active_tab = self._normalize_tab(initial_tab)
        self.launcher_update_status_label = self.trans("launcher_update_status_unknown")
        self.setup_wizard = SetupWizardService()
        self.memory_limits = MemoryPreferencesService.detect_limits()

        # Header/footer
        self._update_settings_header()
        self.app.footer.set_params(
            center_control=ui.Button(
                text=self.trans("save"),
                icon=ft.Icons.SAVE,
                on_click=lambda _e: self.on_save_settings(),
                size="sm",
            ),
            left_btn=False,
            right_btn=False,
        )

        self._build_controls()
        self.activity_panel = ActivityPanel(app)
        self.content = self._build_content()

    def view(self):
        return self.content or ui.Container(content=ui.Text(self.trans("error_loading_page")))

    def _app_state_dir(self) -> Path:
        paths = getattr(self.app, "paths", None)
        value = getattr(paths, "app_state_dir", None)
        if value:
            return Path(value)
        return Path(self.app.util.app_state_dir)

    def _minecraft_dir(self) -> Path:
        paths = getattr(self.app, "paths", None)
        value = getattr(paths, "minecraft_dir", None)
        if value:
            return Path(value)
        return Path(self.app.util.minecraft_dir)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def _build_controls(self) -> None:
        self.language_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="language",
                label=self.trans("language"),
                value=self.app.config.get("lang", "en_US"),
                options=[
                    {"text": "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430", "key": "uk_UA"},
                    {"text": "English", "key": "en_US"},
                ],
                width=None,
            ),
            on_change=self.on_language_change,
        )

        self.auto_update_toggle = self._yesno_toggle(
            key="auto_update",
            label=self.trans("autoupdate"),
            default="yes",
        )
        self.close_on_game_toggle = self._yesno_toggle(
            key="close_launcher_on_game",
            label=self.trans("close_launcher_on_game"),
            default="no",
        )
        self.ask_profile_on_launch_toggle = self._yesno_toggle(
            key=ASK_PROFILE_ON_LAUNCH_KEY,
            label=self.trans("ask_profile_on_launch"),
            default="no",
        )
        self.show_tensacraft_toggle = self._yesno_toggle(
            key="show_tensacraft_versions",
            label=self.trans("show_tensacraft_versions"),
            default="yes",
        )
        self.compact_sidebar_toggle = self.layout.toggle_field(
            label=self.trans("compact_sidebar"),
            value=self.app.config.get("compact_sidebar", "yes") == "yes",
            on_change=self.on_compact_sidebar_change,
        )
        self.ui_click_sound_toggle = self._yesno_toggle(
            key=UiSoundService.CONFIG_KEY,
            label=self.trans("ui_click_sound_enabled"),
            default="yes",
        )
        self.ui_click_sound_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key=UiSoundService.SOUND_CONFIG_KEY,
                label=self.trans("ui_click_sound_variant"),
                value=UiSoundService.selected_sound_key(self.app.config),
                options=[
                    {"text": self.trans(choice.label), "key": choice.key}
                    for choice in UiSoundService.click_sound_choices()
                ],
                width=None,
            ),
            on_change=self.on_click_sound_change,
        )
        self.beta_updates_toggle = self._yesno_toggle(
            key="include_beta_updates",
            label=self.trans("include_beta_updates"),
            default="no",
            after_change=lambda _e: self._update_settings_header(refresh=True),
        )
        self.check_updates_button = ui.Button(
            text=self.trans("check_updates_now"),
            icon=ft.Icons.SYSTEM_UPDATE_ALT,
            on_click=self.on_check_updates_click,
            width=None,
            height=self.app.theme.input_height,
        )

        default_ram_gb = MemoryPreferencesService.normalize_max_ram_gb(
            self.app.config.get("default_max_ram_gb"),
            limits=self.memory_limits,
        )
        self.default_max_ram_value = ui.Text(
            self._ram_value_text(default_ram_gb),
            color=self.app.theme.text_secondary,
            size=self.app.theme.text_size_sm,
        )
        self.default_max_ram_slider = ft.Slider(
            min=self.memory_limits.min_heap_gb,
            max=self.memory_limits.max_heap_gb,
            divisions=max(1, self.memory_limits.max_heap_gb - self.memory_limits.min_heap_gb),
            value=float(default_ram_gb),
            label="{value} GB",
            padding=0,
            on_change=self.on_default_ram_change,
        )
        self.default_max_ram = self._build_memory_slider(
            label=self.trans("default_max_ram_label"),
            value_label=self.default_max_ram_value,
            slider=self.default_max_ram_slider,
        )

        self.gpu_mode_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="gpu_mode_default",
                label=self.trans("gpu_mode_label") or "GPU mode",
                value=self.app.config.get("gpu_mode_default", "dgpu"),
                options=[
                    {"text": self.trans("gpu_mode_auto") or "Auto", "key": "auto"},
                    {"text": self.trans("gpu_mode_integrated") or "Integrated (iGPU)", "key": "igpu"},
                    {"text": self.trans("gpu_mode_discrete") or "Discrete (dGPU)", "key": "dgpu"},
                ],
                width=None,
            ),
            on_change=self.on_global_gpu_change,
        )
        self.report_contact = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="report_contact",
                label=self.trans("report_contact_label"),
                value=str(self.app.config.get("report_contact", "")),
                width=None,
                props={"hint_text": self.trans("report_contact_hint")},
            ),
            on_change=self.on_report_contact_change,
        )

        self.minecraft_game_dir = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="minecraft_game_dir",
                label=self.trans("minecraft_game_dir_label"),
                value=str(self._minecraft_dir()),
                width=None,
            ),
            on_change=lambda _e: None,
        )
        self.minecraft_dir_picker = ui.FilePicker(page=self.page, on_result=self.on_game_dir_pick_result)
        self.minecraft_game_dir_browse = ui.FileInputTrigger(
            icon=ft.Icons.FOLDER_OPEN,
            text=self.trans("browse_directory"),
            on_click=self._browse_minecraft_dir,
            width=None,
            height=self.app.theme.input_height,
        )

        default_backup_dir = str(self._default_world_backups_dir())
        self.world_backups_toggle = self._yesno_toggle(
            key="world_backups_enabled",
            label=self.trans("world_backups_enabled"),
            default="no",
        )
        self.world_backups_keep_count = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="world_backups_keep_count",
                label=self.trans("world_backups_keep_count"),
                value=str(self.app.config.get("world_backups_keep_count", 3)),
                props={
                    "input_filter": ft.NumbersOnlyInputFilter(),
                    "width": None,
                },
            ),
            on_change=self.on_world_backups_keep_count_change,
        )
        self.world_backups_dir = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="world_backups_dir",
                label=self.trans("world_backups_dir"),
                value=str(self.app.config.get("world_backups_dir", default_backup_dir)),
                width=None,
            ),
            on_change=self.on_world_backups_dir_change,
        )
        self.world_backups_dir_picker = ui.FilePicker(page=self.page, on_result=self.on_world_backups_dir_pick_result)
        self.world_backups_dir_browse = ui.FileInputTrigger(
            icon=ft.Icons.FOLDER_OPEN,
            text=self.trans("browse_directory"),
            on_click=self._browse_world_backups_dir,
            width=None,
            height=self.app.theme.input_height,
        )

        self.custom_java_name = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="custom_java_name",
                label=self.trans("custom_java_name_label"),
                value="",
                width=None,
            ),
            on_change=lambda _e: None,
        )
        self.custom_java_path = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="custom_java_path",
                label=self.trans("custom_java_path_label"),
                value="",
                width=None,
            ),
            on_change=lambda _e: None,
        )
        self.custom_java_picker = ui.FilePicker(page=self.page, on_result=self.on_custom_java_pick_result)
        self.custom_java_browse = ui.IconButton(
            icon=ft.Icons.FOLDER_OPEN,
            variant="solid",
            on_click=self._browse_custom_java,
            width=self.app.theme.input_height,
            height=self.app.theme.input_height,
        )
        self.custom_java_add = ui.Button(
            text=self.trans("custom_java_add"),
            icon=ft.Icons.ADD,
            on_click=self.on_add_custom_java,
            width=None,
            height=self.app.theme.input_height,
        )
        self.custom_java_scan = ui.Button(
            text=self.trans("custom_java_scan"),
            icon=ft.Icons.SEARCH,
            on_click=self.on_scan_custom_java,
            width=None,
            height=self.app.theme.input_height,
        )
        self.custom_java_list = ui.Column(controls=self._build_custom_java_rows(), spacing=8, tight=True)

        self.layout.expand_controls(
            self.language_select,
            self.auto_update_toggle,
            self.close_on_game_toggle,
            self.ask_profile_on_launch_toggle,
            self.show_tensacraft_toggle,
            self.default_max_ram,
            self.gpu_mode_select,
            self.report_contact,
            self.beta_updates_toggle,
            self.compact_sidebar_toggle,
            self.ui_click_sound_toggle,
            self.ui_click_sound_select,
            self.minecraft_game_dir,
            self.minecraft_game_dir_browse,
            self.world_backups_toggle,
            self.world_backups_keep_count,
            self.world_backups_dir,
            self.world_backups_dir_browse,
            self.custom_java_name,
            self.custom_java_path,
            self.custom_java_add,
            self.custom_java_scan,
        )

    def _build_content(self) -> ft.Control:
        self.settings_tabs = self._build_tab_bar()
        self.tab_content = ui.Container(
            expand=True,
            content=self._build_active_tab_content(),
        )

        body = ui.Column(
            controls=[self.settings_tabs, self.tab_content],
            spacing=16,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            scroll=ft.ScrollMode.AUTO,
        )

        return ui.Container(
            expand=True,
            padding=self.app.theme.profile_content_padding,
            content=body,
        )

    def _build_tab_bar(self) -> ft.Control:
        return ui.Row(
            controls=self._build_tab_buttons(),
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

    def _build_tab_buttons(self) -> list[ft.Control]:
        buttons: list[ft.Control] = []
        for key, label_key, icon in self.TABS:
            selected = key == self.active_tab
            buttons.append(
                ui.Button(
                    text=self.trans(label_key),
                    icon=icon,
                    variant="filled" if selected else "ghost",
                    tone="primary" if selected else "neutral",
                    size="sm",
                    on_click=lambda _e, tab_key=key: self.show_tab(tab_key),
                )
            )
        return buttons

    def _build_active_tab_content(self) -> ft.Control:
        if self.active_tab == "backups":
            return self._build_backups_tab()
        if self.active_tab == "java_performance":
            return self._build_java_performance_tab()
        if self.active_tab == "activity":
            return self.activity_panel.view()
        return self._build_launcher_tab()

    def _tab_body(self, *sections: ft.Control) -> ft.Control:
        return ui.Column(
            controls=list(sections),
            spacing=24,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _build_launcher_tab(self) -> ft.Control:
        update_controls = [
            self.layout.wrap_control(self.auto_update_toggle, {"sm": 12, "md": 6, "lg": 6}),
            self.layout.wrap_control(self.beta_updates_toggle, {"sm": 12, "md": 6, "lg": 6}),
            self.layout.wrap_control(self.check_updates_button, {"sm": 12, "md": 6, "lg": 6}),
        ]

        launcher_controls = [
            self.layout.wrap_control(self.language_select, {"sm": 12}),
            self.layout.wrap_control(self.close_on_game_toggle, {"sm": 12, "md": 6, "lg": 6}),
            self.layout.wrap_control(self.ask_profile_on_launch_toggle, {"sm": 12, "md": 6, "lg": 6}),
            self.layout.wrap_control(self.report_contact, {"sm": 12}),
            *update_controls,
        ]

        sections = [
            self._section(
                title=self.trans("launcher_behavior"),
                description=self.trans("launcher_behavior_desc"),
                controls=launcher_controls,
            ),
            self._build_interface_section(),
        ]
        if self._is_macos():
            sections.append(self._build_macos_microphone_section())
        sections.append(self._build_storage_section())
        return self._tab_body(*sections)

    def _build_interface_section(self) -> ft.Control:
        return self._section(
            title=self.trans("interface"),
            description=self.trans("interface_desc"),
            controls=[
                self.layout.wrap_control(self.show_tensacraft_toggle, {"sm": 12, "md": 6, "lg": 6}),
                self.layout.wrap_control(self.compact_sidebar_toggle, {"sm": 12, "md": 6, "lg": 6}),
                self.layout.wrap_control(self.ui_click_sound_toggle, {"sm": 12, "md": 6, "lg": 6}),
                self.layout.wrap_control(self.ui_click_sound_select, {"sm": 12, "md": 6, "lg": 6}),
            ],
        )

    def _build_storage_section(self) -> ft.Control:
        return self._section(
            title=self.trans("minecraft_storage"),
            description=self.trans("minecraft_storage_desc"),
            controls=[
                self.layout.wrap_control(self.minecraft_game_dir, {"sm": 12, "md": 8, "lg": 8}),
                self.layout.wrap_control(self.minecraft_game_dir_browse, {"sm": 12, "md": 4, "lg": 4}),
            ],
        )

    def _build_macos_microphone_section(self) -> ft.Control:
        return self._section(
            title=self.trans("macos_microphone_permissions"),
            description=self.trans("macos_microphone_permissions_desc"),
            controls=[
                self.layout.wrap_control(
                    ui.Button(
                        text=self.trans("request_macos_microphone_access"),
                        icon=ft.Icons.MIC,
                        on_click=self.on_open_macos_microphone_settings,
                        width=None,
                        height=self.app.theme.input_height,
                    ),
                    {"sm": 12, "md": 6, "lg": 4},
                ),
                self.layout.wrap_control(
                    ui.Button(
                        text=self.trans("reset_macos_microphone_access"),
                        icon=ft.Icons.RESTART_ALT,
                        on_click=self.on_reset_macos_microphone_access,
                        width=None,
                        height=self.app.theme.input_height,
                    ),
                    {"sm": 12, "md": 6, "lg": 4},
                ),
            ],
        )

    def _build_backups_tab(self) -> ft.Control:
        return self._tab_body(self._section(
            title=self.trans("world_backups"),
            description=self.trans("world_backups_desc"),
            controls=[
                self.layout.wrap_control(self.world_backups_toggle, {"sm": 12, "md": 6, "lg": 4}),
                self.layout.wrap_control(self.world_backups_keep_count, {"sm": 12, "md": 6, "lg": 4}),
                self.layout.wrap_control(self.world_backups_dir, {"sm": 12, "md": 8, "lg": 8}),
                self.layout.wrap_control(self.world_backups_dir_browse, {"sm": 12, "md": 4, "lg": 4}),
            ],
        ))

    def _build_java_performance_tab(self) -> ft.Control:
        return self._tab_body(
            self._section(
                title=self.trans("custom_java_section"),
                description=self.trans("custom_java_section_desc"),
                controls=[
                    self.layout.wrap_control(self.custom_java_name, {"sm": 12, "md": 4, "lg": 4}),
                    self.layout.wrap_control(self.custom_java_path, {"sm": 12, "md": 8, "lg": 5}),
                    self.layout.wrap_control(self.custom_java_browse, {"sm": 12, "md": 6, "lg": 1}),
                    self.layout.wrap_control(self.custom_java_add, {"sm": 12, "md": 6, "lg": 2}),
                    self.layout.wrap_control(self.custom_java_scan, {"sm": 12}),
                    self.layout.wrap_control(self.custom_java_list, {"sm": 12}),
                ],
            ),
            self._section(
                title=self.trans("java_performance_section"),
                description=self.trans("java_performance_desc"),
                controls=[
                    self.layout.wrap_control(self.default_max_ram, {"sm": 12}),
                    self.layout.wrap_control(self.gpu_mode_select, {"sm": 12}),
                ],
            ),
        )

    def show_tab(self, key: str) -> None:
        next_tab = self._normalize_tab(key)
        if next_tab == self.active_tab:
            return
        if self.active_tab == "activity" and next_tab != "activity":
            self.activity_panel.before_hide()
        self.active_tab = next_tab
        self.settings_tabs.controls = self._build_tab_buttons()
        self.tab_content.content = self._build_active_tab_content()
        if self.active_tab == "activity":
            self.activity_panel.after_show()
        schedule_update(self.page)

    def after_show(self) -> None:
        if self.active_tab == "activity":
            self.activity_panel.after_show()

    def before_hide(self) -> None:
        self.activity_panel.before_hide()

    def _normalize_tab(self, key: str) -> str:
        if key in {"storage", "interface"}:
            return "launcher"
        if key in {"java", "performance"}:
            return "java_performance"
        keys = {tab_key for tab_key, _label_key, _icon in self.TABS}
        return key if key in keys else "launcher"

    def _is_macos(self) -> bool:
        checker = getattr(self.app.util, "is_macos", None)
        return bool(checker()) if callable(checker) else False

    def _section(
        self,
        *,
        title: str,
        controls: list[ft.Control],
        description: str | None = None,
    ) -> ft.Container:
        return self.layout.section(
            title=title,
            controls=controls,
            description=description,
        )

    def _build_memory_slider(self, *, label: str, value_label: ft.Text, slider: ft.Slider) -> ft.Control:
        return ui.Container(
            content=ui.Column(
                controls=[
                    ui.Row(
                        controls=[
                            ui.Text(
                                label,
                                size=self.app.theme.text_size_sm,
                                weight=self.app.theme.font_weight_semibold,
                            ),
                            value_label,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    slider,
                    ui.Text(
                        self.trans(
                            "ram_slider_limit_hint",
                            total=self.memory_limits.total_gb,
                            max=self.memory_limits.max_heap_gb,
                        ),
                        color=self.app.theme.text_secondary,
                        size=self.app.theme.text_size_sm,
                    ),
                ],
                spacing=6,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def _ram_value_text(self, value: int) -> str:
        return self.trans("ram_slider_value", value=value, max=self.memory_limits.max_heap_gb)

    def _yesno_toggle(self, *, key: str, label: str, default: str = "no", after_change=None) -> ft.Control:
        def on_change(e):
            self.app.config.set(key, "yes" if e.control.value else "no")
            if callable(after_change):
                after_change(e)

        return self.layout.toggle_field(label=label, value=self.app.config.get(key, default) == "yes", on_change=on_change)

    def _browse_minecraft_dir(self, _event: Any = None) -> None:
        kwargs: dict[str, Any] = {"dialog_title": self.trans("select_directory")}
        initial_directory = ui.initial_directory_from_path(self.minecraft_game_dir.value)
        if initial_directory is not None:
            kwargs["initial_directory"] = initial_directory
        self.minecraft_dir_picker.get_directory_path(**kwargs)

    def _browse_world_backups_dir(self, _event: Any = None) -> None:
        kwargs: dict[str, Any] = {"dialog_title": self.trans("select_directory")}
        initial_directory = ui.initial_directory_from_path(self.world_backups_dir.value)
        if initial_directory is not None:
            kwargs["initial_directory"] = initial_directory
        self.world_backups_dir_picker.get_directory_path(**kwargs)

    def _browse_custom_java(self, _event: Any = None) -> None:
        kwargs: dict[str, Any] = {"allow_multiple": False}
        initial_directory = ui.initial_directory_from_path(self.custom_java_path.value)
        if initial_directory is not None:
            kwargs["initial_directory"] = initial_directory
        self.custom_java_picker.pick_files(**kwargs)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def on_save_settings(self):
        if not self._save_game_dir_setting():
            return
        self.app.feedback.info(self.trans("settings_saved"))
        self.app.restart()

    def on_game_dir_pick_result(self, e: Any):
        selected = (getattr(e, "path", None) or "").strip()
        if not selected:
            return
        self.minecraft_game_dir.value = selected
        schedule_update(self.page)

    def on_world_backups_dir_pick_result(self, e: Any):
        selected = (getattr(e, "path", None) or "").strip()
        if not selected:
            return
        self.world_backups_dir.value = selected
        self.app.config.set("world_backups_dir", selected)
        schedule_update(self.page)

    def on_custom_java_pick_result(self, e: Any):
        files = getattr(e, "files", None) or []
        selected = getattr(files[0], "path", "") if files else ""
        if not selected:
            return
        self.custom_java_path.value = selected
        schedule_update(self.page)

    def on_add_custom_java(self, _e):
        try:
            entries = JavaPreferencesService.add_custom_java(
                self.app.config.get(JavaPreferencesService.CUSTOM_CONFIG_KEY, []),
                self.custom_java_name.value or "",
                self.custom_java_path.value or "",
            )
        except ValueError:
            self.app.feedback.warning(self.trans("custom_java_invalid"))
            return

        self.app.config.set(JavaPreferencesService.CUSTOM_CONFIG_KEY, entries)
        self.custom_java_name.value = ""
        self.custom_java_path.value = ""
        self._refresh_custom_java_list()
        self.app.feedback.info(self.trans("custom_java_added"))

    def on_scan_custom_java(self, _e):
        if self.custom_java_scan.disabled:
            return
        self.custom_java_scan.disabled = True
        self.app.feedback.info(self.trans("custom_java_scan_started"))
        schedule_update(self.page)
        run_task(self.page, self._scan_custom_java_async)

    async def _scan_custom_java_async(self):
        try:
            discovered = await run_blocking(self.app.util.get_all_java)
            entries, added_count = JavaPreferencesService.import_discovered_java(
                self.app.config.get(JavaPreferencesService.CUSTOM_CONFIG_KEY, []),
                discovered,
            )
            self.app.config.set(JavaPreferencesService.CUSTOM_CONFIG_KEY, entries)
            self.app.config.update(
                {
                    JavaPreferencesService.LAUNCHER_CACHE_KEY: discovered,
                    JavaPreferencesService.LAUNCHER_CACHE_TS_KEY: time.time(),
                }
            )
            self.app.java_versions = discovered
            self._refresh_custom_java_list()
            if added_count:
                self.app.feedback.info(self.trans("custom_java_scan_added", count=added_count))
            else:
                self.app.feedback.info(self.trans("custom_java_scan_no_new"))
        except Exception as exc:
            self.app.log.debug("Custom Java scan failed", exc_info=True)
            self.app.feedback.warning(self.trans("custom_java_scan_failed", error=exc))
        finally:
            self.custom_java_scan.disabled = False
            schedule_update(self.page)

    def on_remove_custom_java(self, path: str):
        entries = JavaPreferencesService.remove_custom_java(
            self.app.config.get(JavaPreferencesService.CUSTOM_CONFIG_KEY, []),
            path,
        )
        self.app.config.set(JavaPreferencesService.CUSTOM_CONFIG_KEY, entries)
        self._refresh_custom_java_list()

    def _build_custom_java_rows(self) -> list[ft.Control]:
        entries = JavaPreferencesService.normalize_entries(
            self.app.config.get(JavaPreferencesService.CUSTOM_CONFIG_KEY, [])
        )
        if not entries:
            return [
                ui.Text(
                    self.trans("custom_java_empty"),
                    color=self.app.theme.text_secondary,
                    size=self.app.theme.text_size_sm,
                )
            ]

        rows: list[ft.Control] = []
        for entry in entries:
            label, path = next(iter(entry.items()))
            rows.append(
                ui.Container(
                    bgcolor=self.app.theme.bg_card,
                    border=ft.Border.all(1, self.app.theme.border_color),
                    border_radius=self.app.theme.radius(),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                    content=ui.Row(
                        controls=[
                            ui.Column(
                                controls=[
                                    ui.Text(label, color=self.app.theme.text_color, weight=ft.FontWeight.W_600),
                                    ui.Text(path, color=self.app.theme.text_secondary, size=self.app.theme.text_size_sm),
                                ],
                                spacing=2,
                                tight=True,
                                expand=True,
                            ),
                            ui.Button(
                                text=self.trans("delete"),
                                icon=ft.Icons.DELETE_OUTLINE,
                                variant="outline",
                                tone="neutral",
                                icon_color=self.app.theme.error,
                                size="sm",
                                on_click=lambda _e, java_path=path: self.on_remove_custom_java(java_path),
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        return rows

    def _refresh_custom_java_list(self) -> None:
        self.custom_java_list.controls = self._build_custom_java_rows()
        schedule_update(self.page)

    def _save_game_dir_setting(self) -> bool:
        raw_value = (self.minecraft_game_dir.value or "").strip()
        normalized = self.setup_wizard.normalize_minecraft_dir(
            app_dir=self._app_state_dir(),
            raw_value=raw_value,
        )
        try:
            self.setup_wizard.ensure_writable(normalized.path)
        except OSError:
            self.app.feedback.warning(self.trans("directory_create_failed"))
            return False

        if normalized.stored_value is None:
            self.app.util.set_minecraft_dir_override(None)
            self.app.config.delete("minecraft_game_dir")
        else:
            self.app.util.set_minecraft_dir_override(normalized.stored_value)
            self.app.config.set("minecraft_game_dir", normalized.stored_value)

        self.minecraft_game_dir.value = str(normalized.path)
        return True

    def on_language_change(self, e):
        self.app.config.set("lang", e.control.value)

    def on_default_ram_change(self, e):
        gb = MemoryPreferencesService.normalize_max_ram_gb(
            getattr(e.control, "value", None),
            limits=self.memory_limits,
        )
        self.default_max_ram_slider.value = float(gb)
        self.default_max_ram_value.value = self._ram_value_text(gb)
        self.app.config.delete("default_min_ram_gb")
        self.app.config.set("default_max_ram_gb", gb)
        schedule_update(self.page)

    def on_report_contact_change(self, e):
        value = str(getattr(e.control, "value", "") or "").strip()
        if value:
            self.app.config.set("report_contact", value)
        else:
            self.app.config.delete("report_contact")

    def on_world_backups_keep_count_change(self, e):
        value = (e.control.value or "").strip()
        if not value:
            return
        try:
            count = int(value)
            if count <= 0:
                raise ValueError
        except ValueError:
            self.app.feedback.warning(self.trans("world_backups_keep_count_invalid"))
            return
        self.app.config.set("world_backups_keep_count", count)

    def on_world_backups_dir_change(self, e):
        value = (e.control.value or "").strip()
        if value:
            self.app.config.set("world_backups_dir", value)
        else:
            self.app.config.delete("world_backups_dir")

    def on_global_gpu_change(self, e):
        mode = (e.control.value or "dgpu").lower()
        self.app.config.set("gpu_mode_default", mode)

    def on_compact_sidebar_change(self, e):
        compact = bool(e.control.value)
        setter = getattr(self.app, "set_sidebar_collapsed", None)
        if callable(setter):
            setter(compact)
            return
        self.app.config.set("compact_sidebar", "yes" if compact else "no")

    def on_click_sound_change(self, e):
        selected = str(getattr(e.control, "value", "") or UiSoundService.DEFAULT_SOUND_KEY)
        if UiSoundService.sound_asset_path(selected) is not None or selected == UiSoundService.DEFAULT_SOUND_KEY:
            self.app.config.set(UiSoundService.SOUND_CONFIG_KEY, selected)
            return
        e.control.value = UiSoundService.DEFAULT_SOUND_KEY
        self.app.config.set(UiSoundService.SOUND_CONFIG_KEY, UiSoundService.DEFAULT_SOUND_KEY)

    def on_check_updates_click(self, _e):
        self.check_updates_button.disabled = True
        self._set_launcher_update_status(self.trans("launcher_update_status_checking"))
        schedule_update(self.page)
        run_task(self.page, self._check_updates_now_async)

    def on_open_macos_microphone_settings(self, _e):
        run_task(self.page, self._request_macos_microphone_access_async)

    def on_reset_macos_microphone_access(self, _e):
        run_task(self.page, self._reset_macos_microphone_access_async)

    async def _reset_macos_microphone_access_async(self):
        resetter = getattr(self.app.util, "reset_macos_microphone_access", None)
        if not callable(resetter) or not await run_blocking(resetter):
            self.app.feedback.warning(self.trans("macos_microphone_permission_reset_failed"))
            return
        self.app.feedback.info(self.trans("macos_microphone_permission_reset"))
        await self._request_macos_microphone_access_async()

    async def _request_macos_microphone_access_async(self):
        requester = getattr(self.app.util, "request_macos_microphone_access", None)
        if not callable(requester):
            opener = getattr(self.app.util, "open_macos_microphone_settings", None)
            if callable(opener) and await run_blocking(opener):
                self.app.feedback.info(self.trans("macos_microphone_settings_opened"))
                return
            self.app.feedback.warning(self.trans("macos_microphone_settings_unavailable"))
            return

        status = await run_blocking(requester)
        if status == "authorized":
            self.app.feedback.info(self.trans("macos_microphone_permission_authorized"))
        elif status == "denied":
            self.app.feedback.warning(self.trans("macos_microphone_permission_denied"))
        elif status == "restricted":
            self.app.feedback.warning(self.trans("macos_microphone_permission_restricted"))
        elif status == "timeout":
            self.app.feedback.warning(self.trans("macos_microphone_permission_timeout"))
        else:
            self.app.feedback.warning(self.trans("macos_microphone_settings_unavailable"))

    async def _check_updates_now_async(self):
        try:
            updater = getattr(self.app, "updater", None)
            if updater is None:
                raise RuntimeError("Updater service is unavailable")
            update_info = await run_blocking(updater.check_for_updates)
            if update_info:
                version = update_info.get("version", "")
                self._set_launcher_update_status(self.trans("launcher_update_status_update_available", version=version))
                updater.show_update_dialog(update_info)
            else:
                self._set_launcher_update_status(self.trans("launcher_update_status_latest"))
                self.app.feedback.info(self.trans("update_check_no_updates"))
        except Exception as exc:
            self._set_launcher_update_status(self.trans("launcher_update_status_failed"))
            self.app.feedback.warning(self.trans("update_check_failed", error=exc))
        finally:
            self.check_updates_button.disabled = False
            schedule_update(self.page)

    def _set_launcher_update_status(self, value: str) -> None:
        self.launcher_update_status_label = value
        self._update_settings_header()

    def _update_settings_header(self, *, refresh: bool = False) -> None:
        version_label = self.trans(
            "launcher_version_header",
            launcher=self.app.util.launcher_name,
            version=self.app.util.launcher_version,
        )
        self.app.header.set_params(
            title=self.trans("settings_title"),
            actions=[
                ui.Text(
                    version_label,
                    size=self.app.theme.text_size_sm,
                    weight=self.app.theme.font_weight_medium,
                    color=self.app.theme.text_secondary,
                )
            ],
        )
        if refresh:
            schedule_update(self.page)

    def _default_world_backups_dir(self) -> Path:
        return self._minecraft_dir() / "backups" / "worlds"
