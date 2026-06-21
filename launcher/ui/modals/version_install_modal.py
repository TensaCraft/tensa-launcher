from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from launcher.application.installed_components import InstalledComponentsService
from launcher.application.tensacraft_catalog import TensaCraftCatalogService
from launcher.application.tensacraft_install_state import mark_pending, unmark_pending
from launcher.application.version_creation import VersionCreateOption, VersionCreationCatalogService
from launcher.core import Launcher
from launcher.core.api import TensaCraftAPI
from launcher.core.versions import Version

from ..controls.button import Button
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..forms.field_specs import FieldSpec, build_field
from ..layout.column import Column
from ..layout.container import Container
from ..patterns.loader_builds import selected_loader_version, update_selected_loader_version


class VersionInstallModal:
    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self._install_pending = False
        self.content_width = self.app.theme.modal_width
        self.tensacraft_packs: dict[str, dict] = {}
        self.catalog = VersionCreationCatalogService()
        self.loader_options_by_version: dict[str, VersionCreateOption] = {}
        self.selected_loader_builds: dict[str, str] = {}
        self._pending_loader_version: str | None = None
        minecraft_dir = getattr(getattr(app, "paths", None), "minecraft_dir", None) or app.util.minecraft_dir
        games_dir = (
            getattr(getattr(app, "paths", None), "games_dir", None)
            or getattr(app.util, "games_path", None)
            or (Path(minecraft_dir) / "games")
        )
        self.components = InstalledComponentsService(
            minecraft_dir,
            games_dir=games_dir,
            versions_provider=app.versions.all,
        )

        self.version_name = build_field(
            self.app,
            FieldSpec(
                type="textfield",
                key="version_name",
                label=self.app.trans("enter_name"),
                value="",
                width=self.content_width,
            ),
            on_change=lambda _e: None,
        )
        self.type_select = build_field(
            self.app,
            FieldSpec(
                type="dropdown",
                key="client",
                label=self.app.trans("select_client"),
                value="tensacraft",
                options=self._build_loader_options(),
                width=self.content_width,
            ),
            on_change=self.on_type_change,
        )
        self.version_select = build_field(
            self.app,
            FieldSpec(
                type="dropdown",
                key="version",
                label=self.app.trans("select_version"),
                value="",
                options=[],
                width=self.content_width,
            ),
            on_change=self.on_version_change,
        )
        self.loader_build_select = build_field(
            self.app,
            FieldSpec(
                type="dropdown",
                key="loader_version",
                label=self.app.trans("minecraft_components_loader_build_label"),
                value="",
                options=[],
                width=self.content_width,
                props={"visible": False},
            ),
            on_change=self.on_loader_build_change,
        )
        self.tensacraft_description_text = Text(
            self.app.trans("tensacraft_description_unavailable"),
            color=self.app.theme.text_secondary,
            size=self.app.theme.text_size_sm,
        )
        self.tensacraft_description_panel = Container(
            content=Column(
                [
                    Text(
                        self.app.trans("tensacraft_description_title"),
                        color=self.app.theme.text_color,
                        weight=self.app.theme.font_weight_semibold,
                    ),
                    self.tensacraft_description_text,
                ],
                spacing=self.app.theme.spacing_sm,
                tight=True,
            ),
            width=self.content_width,
            height=max(108, self.app.theme.input_height * 3),
            padding=self.app.theme.padding_md,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=self.app.theme.radius_md,
            bgcolor=self.app.theme.overlay(self.app.theme.alpha_input, self.app.theme.bg_shell),
        )
        self.modal = AlertDialog(
            title=Text(self.app.trans("install_new_version"), color=self.app.theme.text_color),
            modal=True,
            content=Column(
                [
                    self.version_name,
                    self.type_select,
                    self.version_select,
                    self.loader_build_select,
                    self.tensacraft_description_panel,
                ],
                width=self.content_width,
                height=self.app.theme.modal_height,
                spacing=self.app.theme.spacing_md,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            actions=[
                Button(text=self.app.trans("install"), on_click=self.create_version),
                Button(text=self.app.trans("close"), variant="outline", tone="neutral", on_click=lambda _e: self.close()),
            ],
        )
        self.on_type_change(None)

    @staticmethod
    def _build_loader_options() -> list[dict]:
        seen = set()
        options = []
        for loader in Launcher().loaders():
            loader_id = loader.get_id()
            if loader_id in {"modrinth", "curseforge"} or loader_id in seen:
                continue
            seen.add(loader_id)
            options.append({"text": loader.get_name(), "key": loader_id})
        return options

    def on_type_change(self, _):
        selected = self.type_select.value or "tensacraft"
        try:
            versions = self._loader_minecraft_versions(selected)
        except Exception as exc:
            self.app.log.error(f"Unable to fetch versions for loader '{selected}': {exc}")
            self.version_select.options = []
            self.version_select.value = ""
            self._update_loader_build_options()
            if selected in {"fabric", "forge", "neoforge", "quilt"}:
                self.app.feedback.warning(self.app.trans("missing_mod_loader_support"))
            schedule_update(self.page)
            return
        self.version_select.options = [ft.dropdown.Option(text=value, key=value) for value in versions]
        self.version_select.value = versions[0] if versions else ""
        self._update_loader_build_options()
        self._update_tensacraft_metadata()
        schedule_update(self.page)

    def on_version_change(self, _):
        self._update_loader_build_options()
        self._update_tensacraft_description()
        schedule_update(self.page)

    def on_loader_build_change(self, event) -> None:
        option = self._current_loader_option()
        if option is not None:
            update_selected_loader_version(option, self.selected_loader_builds, event)
        schedule_update(self.page)

    def _loader_minecraft_versions(self, loader_id: str) -> list[str]:
        self.loader_options_by_version = {}
        if loader_id == "tensacraft":
            return Launcher().get_loader_versions(loader_id)
        if loader_id == "minecraft":
            options = self.catalog.minecraft_versions()
        else:
            options = self.catalog.loader_versions(loader_id)
        self.loader_options_by_version = {option.minecraft_version: option for option in options}
        return [option.minecraft_version for option in options]

    def _current_loader_option(self) -> VersionCreateOption | None:
        return self.loader_options_by_version.get(str(self.version_select.value or ""))

    def _update_loader_build_options(self) -> None:
        option = self._current_loader_option()
        if option is None or option.loader_id in {"minecraft", "tensacraft"} or not option.loader_versions:
            self.loader_build_select.visible = False
            self.loader_build_select.options = []
            self.loader_build_select.value = ""
            return

        selected = selected_loader_version(option, self.selected_loader_builds)
        self.loader_build_select.options = [ft.dropdown.Option(build) for build in option.loader_versions]
        self.loader_build_select.value = selected or option.loader_versions[0]
        self.loader_build_select.visible = True

    def _selected_loader_version(self, loader_id: str, minecraft_version: str) -> str | None:
        if loader_id in {"minecraft", "tensacraft"}:
            return None
        option = self.loader_options_by_version.get(minecraft_version)
        if option is None:
            return str(self.loader_build_select.value or "").strip() or None
        return selected_loader_version(option, self.selected_loader_builds)

    def _update_tensacraft_metadata(self) -> None:
        selected = self.type_select.value or "tensacraft"
        if selected != "tensacraft":
            self.tensacraft_packs = {}
            self.tensacraft_description_panel.visible = False
            self.tensacraft_description_text.value = ""
            return

        try:
            packs = TensaCraftAPI().list_versions()
        except Exception as exc:
            self.app.log.error(f"Unable to fetch TensaCraft descriptions: {exc}")
            packs = []

        self.tensacraft_packs = {
            pack_id: pack
            for pack in packs
            if isinstance(pack, dict)
            for pack_id in [TensaCraftCatalogService.pack_id(pack)]
            if pack_id
        }
        self.tensacraft_description_panel.visible = True
        self._update_tensacraft_description()

    def _update_tensacraft_description(self) -> None:
        if (self.type_select.value or "tensacraft") != "tensacraft":
            self.tensacraft_description_panel.visible = False
            self.tensacraft_description_text.value = ""
            return

        pack = self.tensacraft_packs.get(self.version_select.value or "")
        description = TensaCraftCatalogService.pack_description(pack) if isinstance(pack, dict) else ""
        self.tensacraft_description_panel.visible = True
        self.tensacraft_description_text.value = description or self.app.trans("tensacraft_description_unavailable")

    def create_version(self, _):
        if self._install_pending:
            return None
        name = self.version_name.value.strip()
        loader = self.type_select.value
        version = self.version_select.value
        if not name or not loader or not version:
            self.app.feedback.info(self.app.trans("fill_all_fields"))
            return self.close()
        if self.app.versions.get_by_name(name):
            self.app.feedback.info(self.app.trans("version_exists", name=name))
            return self.close()
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return self.close()
        if loader == "tensacraft":
            self._mark_tensacraft_pending(version)
        self._pending_loader_version = self._selected_loader_version(loader, version)
        self._install_pending = True
        self.close()
        try:
            run_task(self.page, self._start_install_after_close, name, loader, version)
        except Exception:
            if loader == "tensacraft":
                unmark_pending(self.app, version)
            self._install_pending = False
            raise
        return None

    def _mark_tensacraft_pending(self, pack_id: str) -> None:
        mark_pending(self.app, pack_id)
        current_page = getattr(self.app, "current_page", None)
        hide_pending = getattr(current_page, "hide_pending_tensacraft_pack", None)
        if callable(hide_pending):
            hide_pending(pack_id)

    async def _start_install_after_close(self, name: str, loader: str, version: str):
        await asyncio.sleep(0)
        if self.app.feedback.is_busy():
            if loader == "tensacraft":
                unmark_pending(self.app, version)
            self._install_pending = False
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        operation = self.app.feedback.begin_operation(
            self.app.trans("installation_started"),
            kind="install",
            status=self.app.trans("installation_started"),
        )
        try:
            await self._install_version_async(name, loader, version, operation)
        except Exception:
            operation.fail(self.app.trans("installation_failed"), notify=False)
            raise

    async def _install_version_async(self, name: str, loader: str, version: str, operation):
        final_message = self.app.trans("installation_complete")
        try:
            data = {"name": name, "version": version, "client": loader}
            if self._pending_loader_version and loader != "tensacraft":
                data["loader_version"] = self._pending_loader_version
            installed_version = Version(name, data)
            if loader == "tensacraft":
                await run_blocking(installed_version.install)
            else:
                await run_blocking(self.components.install_game_build, installed_version, operation=operation)
        except Exception as exc:
            self.app.log.error(f"Failed to install {loader} {version}: {exc}")
            final_message = self.app.trans("version_install_error", client=loader, version=version, error=str(exc))
            self.app.feedback.warning(final_message)
            operation.fail(final_message, notify=False)
            return
        finally:
            if loader == "tensacraft":
                unmark_pending(self.app, version)
            self._install_pending = False
            self._pending_loader_version = None
        operation.finish(final_message, show_success=False)
        await self.app.feedback.wait_until_progress_hidden()
        self.app.feedback.info(self.app.trans("version_install_success", version=name))
        self.app.show_versions_page()

    def show(self):
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        show_dialog(self.page, self.modal)
        schedule_update(self.page)

    def close(self):
        close_dialog(self.page, self.modal)
        schedule_update(self.page)


__all__ = ["VersionInstallModal"]
