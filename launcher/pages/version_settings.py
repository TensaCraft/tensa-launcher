from pathlib import Path
from typing import Any, Dict, List, Optional

import flet as ft
import minecraft_launcher_lib

from launcher import ui
from launcher.application.java_preferences import JavaPreferencesService
from launcher.application.java_runtime import JavaRuntimeService
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.application.version_options import VersionOptionsPayload
from launcher.core import util
from launcher.ui.core.page_runtime import schedule_update


class VersionSettingsPage:
    AUTO_JAVA_VALUE = "__launcher_auto__"
    TABS = (
        ("general", "version_section_general", ft.Icons.TUNE),
        ("runtime", "version_section_runtime", ft.Icons.COFFEE_OUTLINED),
        ("arguments", "version_section_arguments", ft.Icons.TERMINAL_OUTLINED),
    )

    def __init__(self, app, version_key: str, *, embedded: bool = False, on_saved=None, initial_tab: str = "general"):
        self.app = app
        self.page = app.page
        self.version = app.versions.get(version_key)
        self.layout = ui.FormSection(app)
        self.select_image: Optional[ft.FilePickerFile] = None
        self._preset_args: Dict[str, List[str]] = {}
        self.embedded = embedded
        self.on_saved = on_saved
        self.active_tab = self._normalize_tab(initial_tab)
        self.footer_save_button = self._build_save_button()

        title_value = (self.version.name if self.version else None) or version_key
        if not self.embedded:
            self.app.header.set_params(
                title=self.app.trans("version_settings_title", version=title_value),
                show_back_btn=True,
                back_action=self.app.show_versions_page,
            )
            self.app.footer.set_params(
                center_control=self.footer_save_button,
                left_btn=False,
                right_btn=False,
            )

        if not self.version:
            self.content = ui.Container(
                content=ui.Text(
                    self.app.trans("version_not_found"),
                    color=self.app.theme.text_color,
                    text_align=ft.TextAlign.CENTER,
                ),
                alignment=ft.Alignment.CENTER,
            )
            if not self.embedded:
                self.app.feedback.warning(self.app.trans("version_not_found"))
            return

        jvm_arguments = list(self.version.jvm_args())
        xmx, xms = self.app.version_options.parse_jvm_arguments(jvm_arguments)
        custom_args = self.app.version_options.extract_custom_arguments(jvm_arguments)
        self.memory_limits = MemoryPreferencesService.detect_limits()

        loader_items = [
            {"text": loader.get("id"), "key": loader.get("id")}
            for loader in minecraft_launcher_lib.utils.get_installed_versions(
                str(self._minecraft_dir())
            )
        ]

        self.name = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="version_name",
                label=self.app.trans("version_name_label"),
                value=self.version.name or "",
                width=None,
            ),
            on_change=lambda _e: None,
        )

        default_max_ram = MemoryPreferencesService.normalize_max_ram_gb(
            self.app.config.get("default_max_ram_gb"),
            limits=self.memory_limits,
        )
        max_ram_gb = MemoryPreferencesService.normalize_max_ram_gb(
            xmx,
            limits=self.memory_limits,
            default=default_max_ram,
        )
        self.max_ram_value = ui.Text(
            self._ram_value_text(max_ram_gb),
            color=self.app.theme.text_secondary,
            size=self.app.theme.text_size_sm,
        )
        self.max_ram_slider = ft.Slider(
            min=self.memory_limits.min_heap_gb,
            max=self.memory_limits.max_heap_gb,
            divisions=max(1, self.memory_limits.max_heap_gb - self.memory_limits.min_heap_gb),
            value=float(max_ram_gb),
            label="{value} GB",
            padding=0,
            on_change=self.on_max_ram_change,
        )
        self.max_ram = self._build_memory_slider(
            label=self.app.trans("max_ram_label"),
            value_label=self.max_ram_value,
            slider=self.max_ram_slider,
        )

        available_java_versions = JavaPreferencesService.normalize_entries(
            self.app.config.get(JavaPreferencesService.CUSTOM_CONFIG_KEY, [])
        )
        selected_java_path = self.version.options.get("executablePath", "")
        java_options = [{"text": self.app.trans("java_launcher_default"), "key": self.AUTO_JAVA_VALUE}] + [
            {"text": ver, "key": path}
            for java_ver in available_java_versions
            for ver, path in java_ver.items()
        ]
        option_keys = {str(option["key"]) for option in java_options}
        if selected_java_path and selected_java_path not in option_keys:
            java_options.append({"text": self.app.trans("java_saved_custom_path"), "key": selected_java_path})

        self.java_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="java",
                label=self.app.trans("java_label"),
                value=selected_java_path or self.AUTO_JAVA_VALUE,
                options=java_options,
                width=None,
            ),
            on_change=self.on_java_change,
        )
        self.java_path_display = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="java_path",
                label=self.app.trans("java_path_label"),
                value=self._java_path_display_value(),
                width=None,
                props={"read_only": True},
            ),
            on_change=lambda _e: None,
        )
        self.java_help_text = ui.Text(
            self.app.trans("java_selection_hint"),
            color=self.app.theme.text_secondary,
            size=self.app.theme.text_size_sm,
        )

        self.loaders_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="loader",
                label=self.app.trans("loaders_label"),
                value=self.version.loader or "",
                options=loader_items,
                width=None,
            ),
            on_change=lambda _e: None,
        )

        # ---------- NEW: server quick-start fields ----------
        server_cfg = (self.version.options or {}).get("server") or {}
        self.server_host = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="server_host",
                label=self.app.trans("server_host_label") or "Server host",
                value=str(server_cfg.get("host", "")),
                width=None,
            ),
            on_change=lambda _e: None,
        )
        self.server_port = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="server_port",
                label=self.app.trans("server_port_label") or "Port",
                value=str(server_cfg.get("port", "")) if server_cfg.get("port") is not None else "",
                width=None,
                props={"input_filter": ft.NumbersOnlyInputFilter()},
            ),
            on_change=lambda _e: None,
        )
        # ----------------------------------------------------

        # ---------- NEW: GPU mode selector ----------
        gpu_mode_value = (self.version.options or {}).get("gpuMode", "dgpu")
        gpu_mode_options = [
            {"text": self.app.trans("gpu_mode_auto") or "Auto", "key": "auto"},
            {"text": self.app.trans("gpu_mode_integrated") or "Integrated (iGPU)", "key": "igpu"},
            {"text": self.app.trans("gpu_mode_discrete") or "Discrete (dGPU)", "key": "dgpu"},
        ]
        self.gpu_mode_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="gpu_mode",
                label=self.app.trans("gpu_mode_label") or "GPU mode",
                value=gpu_mode_value,
                options=gpu_mode_options,
                width=None,
            ),
            on_change=lambda _e: None,
        )
        # --------------------------------------------

        preset_options, self._preset_args = self.app.version_options.build_preset_options(self.app.trans)
        self.jvm_preset_select = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="dropdown",
                key="jvm_preset",
                label=self.app.trans("recommended_parameters_label"),
                value="keep",
                options=preset_options,
                width=None,
            ),
            on_change=self.on_preset_change,
        )

        self.custom_args = ui.build_field(
            self.app,
            ui.FieldSpec(
                type="textfield",
                key="custom_args",
                label="",
                value="\n".join(custom_args),
                props={
                    "multiline": True,
                    "min_lines": self.app.theme.jvm_args_min_lines,
                    "max_lines": self.app.theme.jvm_args_max_lines,
                    "height": 200,
                    "hint_text": self.app.trans("custom_jvm_arguments_hint"),
                },
            ),
            on_change=lambda _e: None,
        )
        self.custom_args_group = ui.Column(
            controls=[
                ui.Text(
                    self.app.trans("custom_jvm_arguments_label"),
                    size=self.app.theme.text_size_sm,
                    weight=self.app.theme.font_weight_semibold,
                ),
                self.custom_args,
            ],
            spacing=8,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.file_picker = ui.FilePicker(page=self.page, on_result=self.on_file_picker_result)

        self.selected_file_text = self.app.trans("select_icon")
        self.file_picker_button = ui.FileInputTrigger(
            text=self.selected_file_text,
            icon=ft.Icons.IMAGE,
            on_click=lambda _: self.file_picker.pick_files(
                allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE
            ),
            width=None,
            height=self.app.theme.input_height,
        )

        self.open_instance_button = self._diagnostic_button(
            "open_version_folder",
            ft.Icons.FOLDER_OPEN,
            self._version_root,
        )
        self.open_logs_button = self._diagnostic_button(
            "open_version_logs",
            ft.Icons.ARTICLE_OUTLINED,
            lambda: self._version_root() / "logs",
        )
        self.open_latest_log_button = self._diagnostic_button(
            "open_latest_log",
            ft.Icons.DESCRIPTION_OUTLINED,
            lambda: self._version_root() / "logs" / "latest.log",
        )
        self.open_crash_reports_button = self._diagnostic_button(
            "open_crash_reports",
            ft.Icons.BUG_REPORT_OUTLINED,
            lambda: self._version_root() / "crash-reports",
        )
        self.open_latest_crash_button = self._diagnostic_button(
            "open_latest_crash",
            ft.Icons.REPORT_PROBLEM_OUTLINED,
            self._latest_crash_report,
        )
        self.open_launch_log_button = self._diagnostic_button(
            "open_launch_diagnostics",
            ft.Icons.TERMINAL_OUTLINED,
            lambda: self._version_root() / "logs" / "tensalauncher-launch.log",
        )
        self.send_version_report_button = ui.Button(
            text=self.app.trans("version_report_button"),
            icon=ft.Icons.BUG_REPORT_OUTLINED,
            variant="outline",
            tone="neutral",
            width=None,
            on_click=lambda _e: self._open_version_report_dialog(),
        )

        self.layout.expand_controls(
            self.name,
            self.java_select,
            self.java_path_display,
            self.loaders_select,
            self.server_host,  # NEW
            self.server_port,  # NEW
            self.gpu_mode_select,  # NEW
            self.jvm_preset_select,
            self.max_ram,
            self.custom_args,
            self.custom_args_group,
            self.file_picker_button,
            self.open_instance_button,
            self.open_logs_button,
            self.open_latest_log_button,
            self.open_crash_reports_button,
            self.open_latest_crash_button,
            self.open_launch_log_button,
            self.send_version_report_button,
        )

        self.content = self._build_content()

    def view(self):
        return self.content

    def _build_save_button(self) -> ft.Control:
        return ui.Button(
            text=self.app.trans("save"),
            icon=ft.Icons.SAVE,
            on_click=lambda _e: self.save(),
            size="sm",
        )

    def _build_content(self) -> ft.Control:
        self.version_tabs = self._build_tab_bar()
        self.tab_content = ui.Container(
            expand=True,
            content=self._build_active_tab_content(),
        )
        controls: list[ft.Control] = [self.version_tabs, self.tab_content]
        return ui.Container(
            padding=ft.Padding.all(0) if self.embedded else self.app.theme.version_content_padding,
            expand=True,
            content=ui.Column(
                controls=controls,
                spacing=16,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
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
                    text=self.app.trans(label_key),
                    icon=icon,
                    variant="filled" if selected else "ghost",
                    tone="primary" if selected else "neutral",
                    size="sm",
                    on_click=lambda _e, tab_key=key: self.show_tab(tab_key),
                )
            )
        return buttons

    def _build_active_tab_content(self) -> ft.Control:
        if self.active_tab == "runtime":
            return self._build_runtime_tab()
        if self.active_tab == "arguments":
            return self._build_arguments_tab()
        return self._build_general_tab()

    def _tab_body(self, *sections: ft.Control) -> ft.Control:
        return ui.Column(
            controls=list(sections),
            spacing=24,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _build_general_tab(self) -> ft.Control:
        return self._tab_body(
            self._section(
                title=self.app.trans("version_section_general"),
                description=self.app.trans("version_section_general_desc"),
                controls=[
                    self.layout.wrap_control(self.name, {"sm": 12, "md": 6, "lg": 6}),
                    self.layout.wrap_control(self.loaders_select, {"sm": 12, "md": 6, "lg": 6}),
                    self.layout.wrap_control(self._build_icon_picker(), {"sm": 12, "md": 6, "lg": 4}),
                ],
            ),
            self._section(
                title=self.app.trans("version_server_section"),
                description=self.app.trans("version_server_section_desc"),
                controls=[
                    self.layout.wrap_control(self.server_host, {"sm": 12, "md": 8, "lg": 8}),
                    self.layout.wrap_control(self.server_port, {"sm": 12, "md": 4, "lg": 4}),
                ],
            ),
        )

    def _build_runtime_tab(self) -> ft.Control:
        return self._tab_body(
            self._section(
                title=self.app.trans("version_section_runtime"),
                description=self.app.trans("version_section_runtime_desc"),
                controls=[
                    self.layout.wrap_control(self.java_select, {"sm": 12, "md": 4, "lg": 4}),
                    self.layout.wrap_control(self.gpu_mode_select, {"sm": 12, "md": 8, "lg": 8}),
                    self.layout.wrap_control(self.java_path_display, {"sm": 12}),
                    self.layout.wrap_control(self.java_help_text, {"sm": 12}),
                    self.layout.wrap_control(self.max_ram, {"sm": 12}),
                ],
            )
        )

    def _build_arguments_tab(self) -> ft.Control:
        return self._tab_body(
            self._section(
                title=self.app.trans("version_section_arguments"),
                description=self.app.trans("version_section_arguments_desc"),
                controls=[
                    self.layout.wrap_control(self.jvm_preset_select, {"sm": 12}),
                    self.layout.wrap_control(self.custom_args_group, {"sm": 12}),
                ],
            )
        )

    def _build_icon_picker(self) -> ft.Control:
        icon_column = ui.Column(
            controls=[
                self.file_picker_button,
                ui.Text(
                    self.app.trans("version_icon_hint"),
                    color=self.app.theme.text_color,
                    size=12,
                ),
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.START,
        )
        icon_column.expand = True
        return icon_column

    def _build_diagnostics_tab(self) -> ft.Control:
        return self._tab_body(
            self._section(
                title=self.app.trans("version_section_diagnostics"),
                description=self.app.trans("version_section_diagnostics_desc"),
                controls=[
                    self.layout.wrap_control(self.open_instance_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.open_logs_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.open_latest_log_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.open_crash_reports_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.open_latest_crash_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.open_launch_log_button, {"sm": 12, "md": 6, "lg": 4}),
                    self.layout.wrap_control(self.send_version_report_button, {"sm": 12, "md": 6, "lg": 4}),
                ],
            )
        )

    def diagnostics_view(self) -> ft.Control:
        return self._build_diagnostics_tab()

    def show_tab(self, key: str) -> None:
        self.active_tab = self._normalize_tab(key)
        self.version_tabs.controls = self._build_tab_buttons()
        self.tab_content.content = self._build_active_tab_content()
        schedule_update(self.page)

    def _normalize_tab(self, key: str) -> str:
        keys = {tab_key for tab_key, _label_key, _icon in self.TABS}
        return key if key in keys else "general"

    def on_cancel(self):
        self.app.show_versions_page()

    def _section(self, title: str, controls: List[ft.Control], description: Optional[str] = None):
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
                        self.app.trans(
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
        return self.app.trans("ram_slider_value", value=value, max=self.memory_limits.max_heap_gb)

    def _diagnostic_button(self, text_key: str, icon: str, target_factory):
        return ui.Button(
            text=self.app.trans(text_key),
            icon=icon,
            variant="outline",
            tone="neutral",
            width=None,
            on_click=lambda _e: self._open_diagnostic_path(target_factory()),
        )

    def _minecraft_dir(self) -> Path:
        paths = getattr(self.app, "paths", None)
        if paths is not None:
            minecraft_dir = getattr(paths, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        app_util = getattr(self.app, "util", None)
        if app_util is not None:
            minecraft_dir = getattr(app_util, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        return Path(util.minecraft_dir)

    def _version_root(self) -> Path:
        if not self.version:
            return self._minecraft_dir()
        raw = Path(self.version.path or self.version.version_id)
        if raw.is_absolute():
            return raw
        return self._minecraft_dir() / raw

    def _latest_crash_report(self) -> Optional[Path]:
        crash_dir = self._version_root() / "crash-reports"
        if not crash_dir.is_dir():
            return None
        try:
            files = [path for path in crash_dir.iterdir() if path.is_file()]
        except Exception:
            return None
        if not files:
            return None
        try:
            return max(files, key=lambda path: path.stat().st_mtime)
        except Exception:
            return None

    def _latest_hs_err_log(self) -> Optional[Path]:
        version_root = self._version_root()
        if not version_root.is_dir():
            return None
        try:
            files = [path for path in version_root.glob("hs_err_*.log") if path.is_file()]
        except Exception:
            return None
        if not files:
            return None
        try:
            return max(files, key=lambda path: path.stat().st_mtime)
        except Exception:
            return None

    def _open_diagnostic_path(self, target: Optional[Path]) -> None:
        if target is None or not target.exists():
            missing = str(target) if target is not None else self.app.trans("latest_crash_not_found")
            self.app.feedback.warning(self.app.trans("diagnostic_path_missing", path=missing))
            return
        response = self.app.util.open_mc_dir(str(target))
        if response:
            self.app.feedback.warning(response)

    def _selected_java_value(self) -> str:
        return str(getattr(self.java_select, "value", self.AUTO_JAVA_VALUE) or self.AUTO_JAVA_VALUE).strip()

    def _selected_custom_java_path(self) -> str:
        selected = self._selected_java_value()
        return "" if selected == self.AUTO_JAVA_VALUE else selected

    def _automatic_java_path(self) -> str:
        try:
            runtime = JavaRuntimeService(self._minecraft_dir(), self.app.log)
            runtime_name = runtime.get_runtime_name(self.version.version)
            if not runtime_name:
                return ""
            return runtime.get_executable_path(runtime_name) or ""
        except Exception:
            self.app.log.debug("Unable to resolve automatic Java path for %s", self.version.version, exc_info=True)
            return ""

    def _java_path_display_value(self) -> str:
        selected_path = self._selected_custom_java_path()
        if selected_path:
            return selected_path
        return self._automatic_java_path() or self.app.trans("java_path_auto_pending")

    def _update_java_path_display(self) -> None:
        self.java_path_display.value = self._java_path_display_value()

    def _open_version_report_dialog(self) -> None:
        self.version_report_contact = ui.TextField(
            label=self.app.trans("report_contact_label"),
            hint_text=self.app.trans("report_contact_hint"),
            value=str(self.app.config.get("report_contact", "")),
            width=560,
        )
        self.version_report_message = ui.TextField(
            label=self.app.trans("version_report_message_label"),
            hint_text=self.app.trans("version_report_message_hint"),
            multiline=True,
            min_lines=5,
            max_lines=8,
            height=180,
            width=560,
        )
        self.version_report_send_button = ui.Button(
            text=self.app.trans("version_report_submit"),
            icon=ft.Icons.SEND,
            on_click=lambda _e: self._submit_version_report(),
        )
        self.version_report_dialog = ui.AlertDialog(
            modal=True,
            title=ui.Text(
                self.app.trans("version_report_title", version=self.version.name or self.version.version_id),
                size=self.app.theme.text_size_xl,
                weight=self.app.theme.font_weight_semibold,
            ),
            content=ui.Container(
                width=600,
                content=ui.Column(
                    controls=[
                        ui.Text(
                            self.app.trans("version_report_description"),
                            color=self.app.theme.text_secondary,
                            size=self.app.theme.text_size_sm,
                        ),
                        self.version_report_contact,
                        self.version_report_message,
                    ],
                    spacing=12,
                    tight=True,
                ),
            ),
            actions=[
                self.version_report_send_button,
                ui.Button(
                    text=self.app.trans("close"),
                    variant="ghost",
                    tone="neutral",
                    on_click=lambda _e: self._close_version_report_dialog(),
                ),
            ],
        )
        ui.show_dialog(self.page, self.version_report_dialog)
        schedule_update(self.page)

    def _close_version_report_dialog(self) -> None:
        dialog = getattr(self, "version_report_dialog", None)
        if dialog is not None:
            ui.close_dialog(self.page, dialog)
        schedule_update(self.page)

    def _set_version_report_state(self, text_key: str, *, disabled: bool) -> None:
        button = getattr(self, "version_report_send_button", None)
        if button is None:
            return
        button.content = self.app.trans(text_key)
        button.disabled = disabled
        schedule_update(self.page)

    def _version_report_attachments(self) -> list[Path]:
        logs_dir = self._version_root() / "logs"
        candidates = [
            logs_dir / "latest.log",
            logs_dir / "tensalauncher-launch.log",
            self._latest_crash_report(),
            self._latest_hs_err_log(),
        ]
        attachments: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate is None or not candidate.exists() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            attachments.append(candidate)
        return attachments

    def _version_report_metadata(self, attachments: list[Path]) -> dict[str, Any]:
        return {
            "action": "manual_version_report",
            "version_id": self.version.version_id,
            "version_name": self.version.name,
            "client": self.version.client,
            "loader": self.loaders_select.value or self.version.loader,
            "minecraft": self.version.version,
            "path": str(self._version_root()),
            "java_path": self._java_path_display_value(),
            "java_mode": "custom" if self._selected_custom_java_path() else "launcher_auto",
            "gpu_mode": self.gpu_mode_select.value or "",
            "max_ram_gb": MemoryPreferencesService.normalize_max_ram_gb(
                self.max_ram_slider.value,
                limits=self.memory_limits,
            ),
            "server": {
                "host": self.server_host.value or "",
                "port": self.server_port.value or "",
            },
            "jvm_arguments": self.app.version_options.collect_custom_arguments(self.custom_args.value or ""),
            "force_update": bool(getattr(self.version, "force_update", False)),
            "is_tensacraft": bool(self.version.is_tensacraft()),
            "attachments": [str(path) for path in attachments],
            "contact": self._version_report_contact(),
        }

    def _version_report_contact(self) -> str:
        control = getattr(self, "version_report_contact", None)
        return str(getattr(control, "value", "") or self.app.config.get("report_contact", "") or "").strip()

    def _submit_version_report(self) -> None:
        message_control = getattr(self, "version_report_message", None)
        message = str(getattr(message_control, "value", "") or "").strip()
        if not message:
            self.app.feedback.warning(
                self.app.trans("version_report_message_required"),
                allow_report=False,
            )
            return

        reporter = getattr(self.app, "reporter", None)
        submit = getattr(reporter, "submit_report_async", None)
        if not callable(submit):
            self.app.feedback.warning(
                self.app.trans("error_report_failed", error="report service unavailable"),
                allow_report=False,
            )
            return

        attachments = self._version_report_attachments()
        contact = self._version_report_contact()
        if contact:
            self.app.config.set("report_contact", contact)
        else:
            self.app.config.delete("report_contact")
        self._set_version_report_state("error_report_sending", disabled=True)

        def on_success(result: dict[str, Any]) -> None:
            ui.invoke_on_ui(self.page, self._after_version_report_success, result)

        def on_error(exc: Exception) -> None:
            ui.invoke_on_ui(self.page, self._after_version_report_error, exc)

        try:
            submit(
                report_type="error",
                severity="error",
                title=self.app.trans("version_report_title", version=self.version.name or self.version.version_id),
                message=message,
                metadata=self._version_report_metadata(attachments),
                attachments=attachments,
                on_success=on_success,
                on_error=on_error,
            )
        except Exception as exc:
            self._after_version_report_error(exc)

    def _after_version_report_success(self, result: dict[str, Any]) -> None:
        self._set_version_report_state("error_report_sent_button", disabled=True)
        self._close_version_report_dialog()
        self.app.feedback.info(self.app.trans("version_report_sent", report_id=result.get("report_id", "")))

    def _after_version_report_error(self, exc: Exception) -> None:
        self._set_version_report_state("error_report_retry", disabled=False)
        self.app.feedback.warning(
            self.app.trans("error_report_failed", error=str(exc)),
            allow_report=False,
        )

    def save(self, *_):
        if not self.version:
            self.on_cancel()
            return

        name = (self.name.value or "").strip()
        payload = VersionOptionsPayload(
            name=name,
            java_path=self._selected_custom_java_path(),
            loader_id=self.loaders_select.value or "",
            min_ram="",
            max_ram=str(
                MemoryPreferencesService.normalize_max_ram_gb(
                    self.max_ram_slider.value,
                    limits=self.memory_limits,
                )
            ),
            custom_args_text=self.custom_args.value or "",
            server_host=self.server_host.value or "",
            server_port=self.server_port.value or "",
            gpu_mode=self.gpu_mode_select.value or "dgpu",
            image_path=getattr(self.select_image, "path", None),
        )
        try:
            self.app.version_options.apply(self.version, payload)
        except ValueError:
            msg = self.app.trans("invalid_port") or "Invalid port"
            self.app.feedback.warning(msg)
            return

        self.version.save()
        self.app.feedback.info(self.app.trans("version_updated"))
        if self.embedded:
            if callable(self.on_saved):
                self.on_saved(self.version)
            return
        self.on_cancel()

    def on_file_picker_result(self, e: Any):
        if e.files:
            self.select_image = e.files[0]
            self.selected_file_text = e.files[0].name
        else:
            self.select_image = None
            self.selected_file_text = self.app.trans("select_icon")
        self.file_picker_button.text = self.selected_file_text
        schedule_update(self.page)

    def on_java_change(self, _e):
        self._update_java_path_display()
        schedule_update(self.page)

    def on_max_ram_change(self, e):
        gb = MemoryPreferencesService.normalize_max_ram_gb(
            getattr(e.control, "value", None),
            limits=self.memory_limits,
        )
        self.max_ram_slider.value = float(gb)
        self.max_ram_value.value = self._ram_value_text(gb)
        schedule_update(self.page)

    def on_preset_change(self, e):
        selected = self.jvm_preset_select.value
        if not selected or selected == "keep":
            return
        if selected == "none":
            self.custom_args.value = ""
            schedule_update(self.page)
            return
        preset_args = self._preset_args.get(selected)
        if preset_args is None:
            return
        self.custom_args.value = "\n".join(preset_args)
        schedule_update(self.page)
