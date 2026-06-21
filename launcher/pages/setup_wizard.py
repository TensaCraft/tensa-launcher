from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft

from launcher import ui
from launcher.application.setup_wizard import SUPPORTED_LANGUAGE_CODES, SetupWizardService
from launcher.platform.paths import PathPolicy
from launcher.ui.core.page_runtime import schedule_update

DEFAULT_LANGUAGE = "en_US"


def _app_state_dir(app: Any) -> Path:
    paths = getattr(app, "paths", None)
    value = getattr(paths, "app_state_dir", None)
    if value:
        return PathPolicy.select_app_state_dir(Path(value))
    return PathPolicy.select_app_state_dir(Path(str(app.util.app_state_dir)))


def _minecraft_dir(app: Any) -> Path:
    app_state_dir = _app_state_dir(app)
    paths = getattr(app, "paths", None)
    value = getattr(paths, "minecraft_dir", None)
    if value:
        selected, _accepted = PathPolicy.select_minecraft_dir(Path(value), app_state_dir)
        return selected
    selected, _accepted = PathPolicy.select_minecraft_dir(Path(str(app.util.minecraft_dir)), app_state_dir)
    return selected


class SetupWizardPage:
    def __init__(self, app: Any, storage_issues: list[str] | None = None) -> None:
        self.app = app
        self.page = app.page
        self.trans = app.trans
        self.service = SetupWizardService()
        self.storage_issues = list(storage_issues or [])
        self.storage_ready = False
        self.browse_buttons: list[Any] = []

        launcher_data_dir = _app_state_dir(app)

        self.language_select: Any = ui.build_field(
            app,
            ui.FieldSpec(
                type="dropdown",
                key="setup_language",
                label=self.trans("language"),
                value=app.config.get("lang", DEFAULT_LANGUAGE),
                options=[
                    {"text": "Українська", "key": "uk_UA"},
                    {"text": "English", "key": "en_US"},
                ],
                width=None,
                expand=True,
            ),
            on_change=self._on_language_change,
        )
        self.launcher_data_dir: Any = ui.build_field(
            app,
            ui.FieldSpec(
                type="textfield",
                key="setup_launcher_data_dir",
                label=self.trans("setup_wizard_launcher_data"),
                value=str(launcher_data_dir),
                width=None,
            ),
            on_change=self._on_path_field_change,
        )
        self.title_text = ui.Text(
            self.trans("setup_wizard_title"),
            color=self.app.theme.text_color,
            size=self.app.theme.text_size_xl,
            weight=self.app.theme.font_weight_bold,
        )
        self.use_defaults_button: Any = ui.Button(
            text=self.trans("setup_wizard_use_defaults"),
            icon=ft.Icons.RESTART_ALT,
            variant="outline",
            tone="neutral",
            on_click=lambda _e: self._use_defaults(),
        )
        self.create_paths_button: Any = ui.Button(
            text=self.trans("setup_wizard_create_paths"),
            icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
            variant="outline",
            tone="neutral",
            on_click=lambda _e: self._create_paths(),
        )
        self.save_button: Any = ui.Button(
            text=self.trans("setup_wizard_save"),
            icon=ft.Icons.CHECK,
            on_click=lambda _e: self._save_and_continue(),
        )
        self.launcher_data_picker = ui.FilePicker(page=self.page, on_result=self._on_launcher_data_dir_result)
        self._configure_shell()

    def view(self) -> ft.Control:
        return self._build_content()

    def _build_content(self) -> ft.Control:
        theme = self.app.theme
        self.browse_buttons.clear()
        self.description_text = ui.Text(
            self.trans("setup_wizard_desc"),
            color=theme.text_secondary,
            size=theme.text_size_sm,
        )
        self._refresh_storage_issues(schedule=False)
        self.storage_issues_box: Any = self._issues_box()
        controls: list[ft.Control] = [
            self.title_text,
            self.description_text,
            self.storage_issues_box,
            ui.Row(
                controls=[self.language_select],
                spacing=theme.spacing_md,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            self._path_picker_row(
                field=self.launcher_data_dir,
                on_browse=self._open_launcher_data_picker,
            ),
            ui.Row(
                controls=[
                    self.use_defaults_button,
                    self.create_paths_button,
                    self.save_button,
                ],
                spacing=theme.spacing_md,
                alignment=ft.MainAxisAlignment.END,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ]
        return ui.Container(
            expand=True,
            padding=theme.profile_content_padding,
            alignment=ft.Alignment(0, 0),
            content=ui.Column(
                controls=[
                    ui.Container(
                        width=720,
                        padding=ft.Padding.symmetric(vertical=theme.padding_xl),
                        content=ui.Column(
                            controls=controls,
                            spacing=theme.spacing_md,
                            tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    )
                ],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _on_launcher_data_dir_result(self, event: Any) -> None:
        selected = (getattr(event, "path", None) or "").strip()
        if not selected:
            return
        new_root = self.service.resolve_app_state_candidate(current_dir=_app_state_dir(self.app), raw_value=selected)
        self.launcher_data_dir.value = str(new_root)
        self.storage_ready = False
        self._refresh_storage_issues()

    def _open_launcher_data_picker(self, _event: Any = None) -> None:
        kwargs: dict[str, Any] = {"dialog_title": self.trans("select_directory")}
        initial_directory = self._picker_initial_directory(self.launcher_data_dir.value)
        if initial_directory is not None:
            kwargs["initial_directory"] = initial_directory
        self.launcher_data_picker.get_directory_path(**kwargs)

    def _picker_initial_directory(self, value: object) -> str | None:
        initial_directory = ui.initial_directory_from_path(value)
        if initial_directory is None:
            return None
        path = Path(initial_directory)
        if not PathPolicy.is_safe_storage_path(path):
            return None
        return initial_directory

    def _issues_box(self) -> ft.Control:
        theme = self.app.theme
        is_error = bool(self.storage_issues)
        color = theme.error if is_error else theme.success
        return ui.Container(
            visible=is_error or self.storage_ready,
            bgcolor=theme.overlay(0.14, color),
            border=ft.Border.all(1, theme.overlay(0.5, color)),
            border_radius=theme.radius(),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=self._issues_content(),
        )

    def _issues_content(self) -> ft.Control:
        theme = self.app.theme
        if not self.storage_issues and self.storage_ready:
            return ui.Row(
                controls=[
                    ui.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=theme.success, size=18),
                    ui.Text(
                        self.trans("setup_wizard_storage_ready"),
                        color=theme.text_color,
                        weight=theme.font_weight_semibold,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        return ui.Column(
            controls=[
                ui.Row(
                    controls=[
                        ui.Icon(ft.Icons.WARNING_AMBER, color=theme.error, size=18),
                        ui.Text(
                            self.trans("setup_wizard_storage_issue"),
                            color=theme.text_color,
                            weight=theme.font_weight_semibold,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                *[
                    ui.Text(f"- {issue}", color=theme.text_secondary, size=theme.text_size_xs)
                    for issue in self.storage_issues
                ],
            ],
            spacing=6,
            tight=True,
        )

    def _path_picker_row(self, *, field: Any, on_browse: Any) -> ft.Control:
        theme = self.app.theme
        field.width = None
        field.expand = True
        browse_button = ui.Button(
            text=self.trans("browse_directory"),
            icon=ft.Icons.FOLDER_OPEN,
            variant="outline",
            tone="neutral",
            on_click=on_browse,
            height=theme.input_height,
        )
        self.browse_buttons.append(browse_button)
        return ui.Row(
            controls=[
                field,
                browse_button,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _on_language_change(self, event: Any) -> None:
        selected = str(getattr(getattr(event, "control", None), "value", None) or DEFAULT_LANGUAGE)
        if selected not in SUPPORTED_LANGUAGE_CODES:
            selected = DEFAULT_LANGUAGE
            self.language_select.value = selected
        self.app.config.set("lang", selected)
        self.trans = self.app.trans
        self._refresh_texts()
        self._configure_shell()
        refresher = getattr(self.app, "refresh_shell", None)
        if callable(refresher):
            refresher()
        schedule_update(self.page)

    def _on_path_field_change(self, _event: Any) -> None:
        self.storage_ready = False
        self._refresh_storage_issues()

    def _refresh_texts(self) -> None:
        self.title_text.value = self.trans("setup_wizard_title")
        self.description_text.value = self.trans("setup_wizard_desc")
        self.language_select.label = self.trans("language")
        self.launcher_data_dir.label = self.trans("setup_wizard_launcher_data")
        self.use_defaults_button.content = self.trans("setup_wizard_use_defaults")
        self.create_paths_button.content = self.trans("setup_wizard_create_paths")
        self.save_button.content = self.trans("setup_wizard_save")
        for button in self.browse_buttons:
            button.content = self.trans("browse_directory")
        self._refresh_storage_issues(schedule=False)

    def _refresh_storage_issues(self, *, schedule: bool = True) -> None:
        self.storage_issues = self.service.storage_issues(
            app_state_dir=self._field_app_state_dir(),
            minecraft_dir=self._field_minecraft_dir(),
            launcher_label=self.trans("setup_wizard_launcher_data"),
            minecraft_label=self.trans("setup_wizard_minecraft_dir"),
        )
        if not hasattr(self, "storage_issues_box"):
            return
        status_color = self.app.theme.error if self.storage_issues else self.app.theme.success
        self.storage_issues_box.visible = bool(self.storage_issues) or self.storage_ready
        self.storage_issues_box.bgcolor = self.app.theme.overlay(0.14, status_color)
        self.storage_issues_box.border = ft.Border.all(1, self.app.theme.overlay(0.5, status_color))
        self.storage_issues_box.content = self._issues_content()
        if schedule:
            schedule_update(self.page)

    def _field_app_state_dir(self) -> Path:
        raw = str(self.launcher_data_dir.value or "").strip()
        current = _app_state_dir(self.app)
        return self.service.resolve_app_state_candidate(current_dir=current, raw_value=raw or str(current))

    def _field_minecraft_dir(self) -> Path:
        minecraft_dir, _backups_dir = self.service.derived_paths(self._field_app_state_dir())
        return minecraft_dir

    def _use_defaults(self) -> None:
        launcher_data_dir = _app_state_dir(self.app)
        self.launcher_data_dir.value = str(launcher_data_dir)
        self.storage_ready = False
        self._refresh_storage_issues()

    def _create_paths(self) -> None:
        try:
            root = self._field_app_state_dir()
            minecraft_dir, backups_dir = self.service.derived_paths(root)
            if not self._validate_storage_fields():
                return
            self.service.ensure_writable(root)
            self.service.ensure_writable(minecraft_dir)
            self.service.ensure_writable(backups_dir)
        except OSError as exc:
            self.app.feedback.warning(self.trans("setup_wizard_save_failed", error=exc))
            return
        self.launcher_data_dir.value = str(root)
        self.storage_ready = True
        self._refresh_storage_issues()
        self.app.feedback.info(self.trans("setup_wizard_paths_created"))

    def _save_and_continue(self) -> None:
        root = self._field_app_state_dir()
        minecraft_dir, backups_dir = self.service.derived_paths(root)
        if not self._validate_storage_fields():
            return
        try:
            self.service.apply_paths(
                self.app,
                app_state_dir=str(root),
                minecraft_dir=str(minecraft_dir),
                backups_dir=str(backups_dir),
                language=str(self.language_select.value or DEFAULT_LANGUAGE),
            )
        except OSError as exc:
            self.app.feedback.warning(self.trans("setup_wizard_save_failed", error=exc))
            return

        self.app.feedback.info(self.trans("setup_wizard_saved"))
        restarter = getattr(self.app, "restart", None)
        if callable(restarter):
            restarter()
        else:
            schedule_update(self.page)

    def _validate_storage_fields(self) -> bool:
        self._refresh_storage_issues()
        if not self.storage_issues:
            return True
        self.app.feedback.warning(self.trans("setup_wizard_save_failed", error="; ".join(self.storage_issues)))
        return False

    def _configure_shell(self) -> None:
        header = getattr(self.app, "header", None)
        set_header = getattr(header, "set_params", None)
        if callable(set_header):
            set_header(title=self.trans("setup_wizard_title"))
        footer = getattr(self.app, "footer", None)
        set_footer = getattr(footer, "set_params", None)
        if callable(set_footer):
            set_footer(center_btn=None, left_btn=False, right_btn=False)


SetupWizardDialog = SetupWizardPage


def maybe_show_setup_wizard(app: Any) -> bool:
    service = SetupWizardService()
    issues = service.storage_issues(
        app_state_dir=_app_state_dir(app),
        minecraft_dir=_minecraft_dir(app),
        launcher_label=app.trans("setup_wizard_launcher_data"),
        minecraft_label=app.trans("setup_wizard_minecraft_dir"),
    )
    if not service.should_open(app.config, issues):
        return False
    show_page = getattr(app, "show_page", None)
    if callable(show_page):
        navigation = getattr(app, "navigation", None)
        set_selected = getattr(navigation, "set_selected_index", None)
        if callable(set_selected):
            set_selected(-1)
        show_page(SetupWizardPage(app, issues))
    else:
        page = SetupWizardPage(app, issues)
        app.page.controls.clear()
        app.page.add(page.view())
        schedule_update(app.page)
    return True


__all__ = ["SetupWizardDialog", "SetupWizardPage", "maybe_show_setup_wizard"]
