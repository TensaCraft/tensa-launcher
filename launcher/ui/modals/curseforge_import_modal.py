from __future__ import annotations

from typing import Any

import flet as ft

from launcher.core.loaders.curseforge import CurseForgeLoader
from launcher.core.versions import Version

from ..controls.button import Button
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..forms.field_specs import FieldSpec, build_field
from ..forms.file_picker import FilePicker
from ..layout.column import Column


class CurseForgeImportModal:
    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.trans = app.trans
        self.selected_source_path: str | None = None
        self.source_kind: str | None = None
        self.manifest_data: dict | None = None
        self.content_width = self.app.theme.modal_width
        self.name_input = build_field(
            self.app,
            FieldSpec(
                type="textfield",
                key="cf_name",
                label=self.trans("enter_name"),
                value="",
                width=self.content_width,
            ),
            on_change=lambda _e: None,
        )
        self.file_label = Text(self.trans("curseforge_no_file_selected"), color=self.app.theme.text_secondary, size=self.app.theme.text_size_xs)
        self.file_picker = FilePicker(page=self.page, on_result=self.on_file_picker_result)
        self.pick_file_button = Button(
            text=self.trans("select_curseforge_file"),
            icon=ft.Icons.UPLOAD_FILE,
            on_click=lambda _e: self.file_picker.pick_files(
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["zip", "json"],
            ),
            width=None,
        )
        self.modal = AlertDialog(
            title=Text(self.trans("curseforge_import_title"), color=self.app.theme.text_color),
            modal=True,
            content=Column(
                controls=[self.name_input, self.pick_file_button, self.file_label],
                width=self.content_width,
                height=self.app.theme.modal_height,
                spacing=self.app.theme.spacing_md,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            actions=[
                Button(text=self.trans("import"), on_click=self.import_version),
                Button(text=self.trans("close"), variant="outline", tone="neutral", on_click=lambda _e: self.close()),
            ],
        )

    def on_file_picker_result(self, e: Any) -> None:
        if not e.files:
            return
        selected = e.files[0]
        selected_path = selected.path or ""
        if not selected_path:
            self.app.feedback.warning(self.trans("curseforge_source_missing"))
            return
        self.selected_source_path = selected_path
        self.file_label.value = selected_path
        try:
            manifest, source_kind = CurseForgeLoader.load_manifest(selected_path)
            self.manifest_data = manifest
            self.source_kind = source_kind
            if not (self.name_input.value or "").strip():
                self.name_input.value = CurseForgeLoader.suggest_version_name(manifest)
        except Exception as exc:
            self.manifest_data = None
            self.source_kind = None
            self.app.feedback.warning(self.trans("curseforge_manifest_invalid", error=str(exc)))
        schedule_update(self.page)

    def import_version(self, _):
        name = (self.name_input.value or "").strip()
        if not name:
            self.app.feedback.warning(self.trans("fill_all_fields"))
            return
        if self.app.versions.get_by_name(name):
            self.app.feedback.warning(self.trans("version_exists", name=name))
            return
        if not self.selected_source_path:
            self.app.feedback.warning(self.trans("curseforge_source_missing"))
            return
        try:
            manifest, source_kind = CurseForgeLoader.load_manifest(self.selected_source_path)
        except Exception as exc:
            self.app.feedback.warning(self.trans("curseforge_manifest_invalid", error=str(exc)))
            return
        selected_source_path = self.selected_source_path
        mc_version = str((manifest.get("minecraft") or {}).get("version") or "").strip()
        if not mc_version:
            self.app.feedback.warning(self.trans("curseforge_manifest_invalid", error="Missing minecraft.version"))
            return
        self.close()
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        operation = self.app.feedback.begin_operation(
            self.trans("curseforge_import_started"),
            kind="install",
            status=self.trans("curseforge_import_started"),
        )
        try:
            run_task(self.page, self._import_version_async, name, mc_version, selected_source_path, source_kind, operation)
        except Exception:
            operation.fail(self.trans("installation_failed"), notify=False)
            raise

    async def _import_version_async(self, name: str, mc_version: str, selected_source_path: str, source_kind: str, operation) -> None:
        final_message = self.trans("installation_complete")
        try:
            version = Version(
                name,
                {
                    "name": name,
                    "version": mc_version,
                    "client": "curseforge",
                    "options": {
                        "curseforge_source_path": selected_source_path,
                        "curseforge_source_type": source_kind,
                    },
                },
            )
            await run_blocking(version.install)
        except Exception as exc:
            self.app.log.error(f"Failed to import CurseForge pack '{name}': {exc}")
            final_message = self.trans("curseforge_import_error", error=str(exc))
            self.app.feedback.warning(final_message)
            operation.fail(final_message, notify=False)
            return
        finally:
            pass
        operation.finish(final_message, show_success=False)
        await self.app.feedback.wait_until_progress_hidden()
        self.app.feedback.info(self.trans("curseforge_import_success", name=name))
        self.app.show_versions_page()

    def show(self) -> None:
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        show_dialog(self.page, self.modal)
        schedule_update(self.page)

    def close(self) -> None:
        close_dialog(self.page, self.modal)
        schedule_update(self.page)


__all__ = ["CurseForgeImportModal"]
