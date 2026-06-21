from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from launcher.core.api.modrinth import ModrinthAPI
from launcher.core.versions import Version

from ..controls.button import Button
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..forms.field_specs import FieldSpec, build_field
from ..layout.column import Column
from ..layout.container import Container


class ModpackInstallModal:
    def __init__(self, app, modpack_slug: str, title: str | None = None) -> None:
        self.app = app
        self.modpack_slug = modpack_slug
        self.page = app.page
        self.trans = self.app.trans
        self.modpack_data: dict[str, Any] | None = None
        self.versions: list[dict[str, Any]] = []
        self.selected_version: str | None = None
        self.version_name: str | None = None
        self._load_scheduled = False
        self._closed = False
        self._install_pending = False
        self.content_width = self.app.theme.modal_width
        self.modal = AlertDialog(
            title=Text(title or self.trans("modpack_details_title"), color=self.app.theme.text_color),
            modal=True,
            actions=[
                Button(text=self.trans("install"), on_click=lambda _e: self.install_modpack()),
                Button(text=self.trans("close"), variant="outline", tone="neutral", on_click=lambda _e: self.close()),
            ],
        )
        self.name_input = build_field(
            self.app,
            FieldSpec(
                type="textfield",
                key="name_input",
                value="",
                label=self.trans("enter_name"),
                width=self.content_width,
                props={"autofocus": True},
            ),
            on_change=self.on_change_name,
        )
        self.modal.content = Column(
            [
                Container(
                    content=Text(self.trans("loading_modpack_details"), size=self.app.theme.text_size_2xl, weight=self.app.theme.font_weight_bold),
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.all(self.app.theme.padding_xl),
                )
            ],
            width=self.content_width,
            height=self.app.theme.modal_height,
            scroll=ft.ScrollMode.ALWAYS,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def view(self):
        self._schedule_load()
        return self.modal

    def _schedule_load(self) -> None:
        if self._load_scheduled:
            return
        self._load_scheduled = True
        run_task = getattr(self.page, "run_task", None)
        if not callable(run_task):
            raise RuntimeError("Page.run_task is required to load modpack details.")
        run_task(self.load_modpack_details)

    async def load_modpack_details(self):
        self.modpack_data = await self.fetch_modpack_details()
        self.versions = await self.fetch_modpack_versions()
        self.update_view()

    async def fetch_modpack_details(self):
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, ModrinthAPI.get_modpack, self.modpack_slug)
        except Exception as exc:
            self.app.log.error(f"Error fetching modpack details: {exc}")
            return None

    async def fetch_modpack_versions(self):
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, ModrinthAPI.get_versions, self.modpack_slug)
        except Exception as exc:
            self.app.log.error(f"Error fetching modpack versions: {exc}")
            return []

    def update_view(self) -> None:
        if self._closed or self._install_pending:
            return
        if not self.modpack_data:
            self.modal.content.controls = [
                Container(
                    content=Text(self.trans("modpack_details_not_found"), size=self.app.theme.text_size_2xl, weight=self.app.theme.font_weight_bold),
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.all(self.app.theme.padding_xl),
                )
            ]
        else:
            options = [
                {"text": f"{version['version_number']} ({', '.join(version['game_versions'])})", "key": version["id"]}
                for version in self.versions
            ]
            self.selected_version = options[0]["key"] if options else None
            self.version_dropdown = build_field(
                self.app,
                FieldSpec(
                    type="dropdown",
                    key="version_dropdown",
                    options=options,
                    value=self.selected_version,
                    label=self.trans("select_version"),
                    width=self.content_width,
                ),
                on_change=self.update_selected_version_details,
            )
            self.modal.content.controls = [
                self.name_input,
                self.version_dropdown,
            ]
        schedule_update(self.page)

    def update_selected_version_details(self, _e) -> None:
        self.selected_version = self.version_dropdown.value.split(" ")[0]

    def on_change_name(self, _e) -> None:
        self.version_name = self.name_input.value
        self.name_input.autofocus = True

    def install_modpack(self):
        if self._install_pending:
            return None
        version_name = (self.version_name or "").strip()
        if not version_name:
            self.app.feedback.info(self.app.trans("fill_all_fields"))
            return None
        if self.app.versions.get_by_name(version_name):
            self.app.feedback.info(self.app.trans("version_exists", name=version_name))
            return self.close()
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return None
        version_data = next((version for version in self.versions if version["id"] == self.selected_version), None)
        if not version_data:
            self.app.feedback.warning(self.app.trans("version_not_found"))
            return None
        project_id = version_data.get("project_id")
        version_id = version_data.get("id")
        self._install_pending = True
        self.close()
        try:
            run_task(
                self.page,
                self._start_install_after_close,
                project_id,
                version_name,
                version_id,
                self.modpack_data.get("icon_url", None) if self.modpack_data else None,
            )
        except Exception:
            self._install_pending = False
            raise
        return None

    async def _start_install_after_close(self, project_id: str, version_name: str, version_id: str, icon_url: str | None) -> None:
        await asyncio.sleep(0)
        if self.app.feedback.is_busy():
            self._install_pending = False
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        operation = self.app.feedback.begin_operation(
            self.trans("installation_started"),
            kind="install",
            status=self.trans("installation_started"),
        )
        try:
            await self._install_modpack_async(project_id, version_name, version_id, icon_url, operation)
        except Exception:
            operation.fail(self.trans("installation_failed"), notify=False)
            raise

    async def _install_modpack_async(self, project_id: str, version_name: str, version_id: str, icon_url: str | None, operation) -> None:
        final_message = self.trans("installation_complete")
        try:
            await run_blocking(
                Version(
                    project_id,
                    {
                        "id": project_id,
                        "name": version_name,
                        "version": version_id,
                        "client": "modrinth",
                        "image": icon_url,
                    },
                ).install
            )
        except Exception as exc:
            self.app.log.error(f"Failed to install modpack '{version_name}': {exc}")
            final_message = self.trans("version_install_error", client="modrinth", version=version_name, error=str(exc))
            self.app.feedback.warning(final_message)
            operation.fail(final_message, notify=False)
            return
        finally:
            self._install_pending = False
        operation.finish(final_message, show_success=False)
        await self.app.feedback.wait_until_progress_hidden()
        self.app.feedback.info(self.trans("version_install_success", version=version_name))
        self.app.show_versions_page()

    def show(self):
        self._closed = False
        self._load_scheduled = False
        self._install_pending = False
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
        else:
            show_dialog(self.page, self.modal)
            self._schedule_load()
            schedule_update(self.page)

    def close(self):
        self._closed = True
        close_dialog(self.page, self.modal)
        schedule_update(self.page)


__all__ = ["ModpackInstallModal"]
