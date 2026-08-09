from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from launcher.application.installed_components import InstalledComponent, InstalledComponentsService
from launcher.application.version_creation import VersionCreateOption, VersionCreationCatalogService

from ..controls.button import Button
from ..controls.checkbox import Checkbox
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..forms.field_specs import FieldSpec, build_field
from ..layout.column import Column


class VersionComponentModal:
    SUPPORTED_LOADERS = {"minecraft", "fabric", "forge", "neoforge", "quilt"}

    def __init__(
        self,
        app: Any,
        version: Any,
        service: InstalledComponentsService,
        *,
        on_installed: Callable[[InstalledComponent], None] | None = None,
    ) -> None:
        self.app = app
        self.page = app.page
        self.version = version
        self.service = service
        self.on_installed = on_installed
        self.catalog = VersionCreationCatalogService()
        self.options_by_version: dict[str, VersionCreateOption] = {}
        self._load_generation = 0
        self._install_pending = False
        self._closed = True
        self._disposed = False
        width = self.app.theme.modal_width

        self.loader_select = build_field(
            app,
            FieldSpec(
                type="dropdown",
                key="component_loader",
                label=app.trans("version_component_loader_label"),
                value=self._current_loader_id(),
                options=self._loader_options(),
                width=width,
            ),
            on_change=self._on_loader_change,
        )
        self.version_select = build_field(
            app,
            FieldSpec(
                type="dropdown",
                key="component_minecraft_version",
                label=app.trans("version_component_minecraft_label"),
                value="",
                options=[],
                width=width,
            ),
            on_change=self._on_version_change,
        )
        self.loader_build_select = build_field(
            app,
            FieldSpec(
                type="dropdown",
                key="component_loader_build",
                label=app.trans("minecraft_components_loader_build_label"),
                value="",
                options=[],
                width=width,
                props={"visible": False},
            ),
            on_change=lambda _event: None,
        )
        self.include_unstable = Checkbox(
            label=app.trans("version_component_include_unstable"),
            value=False,
            on_change=self._on_unstable_change,
        )
        self.status_text = Text(
            app.trans("version_component_loading"),
            color=app.theme.text_secondary,
            size=app.theme.text_size_sm,
        )
        self.install_button = Button(
            text=app.trans("version_component_install_action"),
            icon=ft.Icons.DOWNLOAD_OUTLINED,
            disabled=True,
            on_click=lambda _event: self.install(),
        )
        self.modal = AlertDialog(
            title=Text(
                app.trans("version_component_title"),
                color=app.theme.text_color,
                weight=app.theme.font_weight_bold,
            ),
            modal=True,
            content=Column(
                [
                    Text(
                        app.trans("version_component_description"),
                        color=app.theme.text_secondary,
                        size=app.theme.text_size_sm,
                    ),
                    self.loader_select,
                    self.version_select,
                    self.loader_build_select,
                    self.include_unstable,
                    self.status_text,
                ],
                width=width,
                spacing=app.theme.spacing_md,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            actions=[
                self.install_button,
                Button(
                    text=app.trans("cancel"),
                    variant="outline",
                    tone="neutral",
                    on_click=lambda _event: self.close(),
                ),
            ],
        )

    def show(self) -> None:
        if self._disposed:
            return
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        self._closed = False
        show_dialog(self.page, self.modal)
        self._queue_options_load()

    def close(self) -> None:
        self._closed = True
        self._load_generation += 1
        close_dialog(self.page, self.modal)
        schedule_update(self.page)

    def dispose(self) -> None:
        self._disposed = True
        self._closed = True
        self._load_generation += 1

    def install(self) -> None:
        if self._install_pending or self.app.feedback.is_busy():
            self.app.feedback.info(self.app.trans("installation_already_running"))
            return
        option = self.options_by_version.get(str(self.version_select.value or ""))
        if option is None:
            self.app.feedback.warning(self.app.trans("version_component_selection_required"))
            return
        loader_version = str(self.loader_build_select.value or "").strip() or option.loader_version
        message = self.app.trans(
            "version_component_installing",
            loader=option.loader_name,
            version=option.minecraft_version,
        )
        operation = self.app.feedback.begin_operation(message, kind="install", status=message)
        self._install_pending = True
        self.close()
        try:
            run_task(self.page, self._install_async, option, loader_version, operation)
        except Exception:
            self._install_pending = False
            operation.fail(self.app.trans("installation_failed"), notify=False)
            raise

    async def _install_async(self, option: VersionCreateOption, loader_version: str | None, operation: Any) -> None:
        try:
            component = await run_blocking(
                self.service.install_profile_component,
                self.version,
                option.loader_id,
                option.minecraft_version,
                loader_version=loader_version,
                operation=operation,
            )
        except Exception as exc:
            message = self.app.trans("version_component_install_failed", error=str(exc))
            self.app.log.error(
                f"Failed to switch profile '{self.version.version_id}' to "
                f"{option.loader_id} {option.minecraft_version}: {exc}"
            )
            operation.fail(message, notify=False)
            self.app.feedback.warning(message)
            return
        finally:
            self._install_pending = False

        message = self.app.trans(
            "version_component_install_complete",
            loader=component.loader_name,
            version=component.minecraft_version or component.version_id,
        )
        operation.finish(message, show_success=False)
        if not self._disposed and self.on_installed is not None:
            self.on_installed(component)
        self.app.feedback.info(message)
        schedule_update(self.page)

    def _on_loader_change(self, _event: Any) -> None:
        self._queue_options_load()

    def _on_version_change(self, _event: Any) -> None:
        self._update_loader_builds()
        schedule_update(self.page)

    def _on_unstable_change(self, _event: Any) -> None:
        self._queue_options_load()

    def _queue_options_load(self) -> None:
        loader_id = str(self.loader_select.value or "minecraft")
        include_unstable = bool(self.include_unstable.value)
        self._load_generation += 1
        generation = self._load_generation
        self._set_loading(True)
        try:
            run_task(self.page, self._load_options_async, loader_id, include_unstable, generation)
        except Exception as exc:
            self._apply_load_error(exc, generation)

    async def _load_options_async(self, loader_id: str, include_unstable: bool, generation: int) -> None:
        try:
            options = await run_blocking(self._fetch_options, loader_id, include_unstable)
        except Exception as exc:
            self._apply_load_error(exc, generation)
            return
        if self._disposed or self._closed or generation != self._load_generation:
            return
        self.options_by_version = {option.minecraft_version: option for option in options}
        self.version_select.options = [ft.dropdown.Option(option.minecraft_version) for option in options]
        current_version = str(getattr(self.version, "version", "") or "")
        self.version_select.value = current_version if current_version in self.options_by_version else (
            options[0].minecraft_version if options else ""
        )
        self._update_loader_builds()
        self._set_loading(False)

    def _fetch_options(self, loader_id: str, include_unstable: bool) -> list[VersionCreateOption]:
        if loader_id == "minecraft":
            return self.catalog.minecraft_versions(
                include_snapshots=include_unstable and self.catalog.supports_snapshots(loader_id)
            )
        return self.catalog.loader_versions(
            loader_id,
            include_snapshots=include_unstable and self.catalog.supports_snapshots(loader_id),
            include_unstable_loaders=(
                include_unstable and self.catalog.supports_unstable_loaders(loader_id)
            ),
        )

    def _update_loader_builds(self) -> None:
        option = self.options_by_version.get(str(self.version_select.value or ""))
        if option is None or option.loader_id == "minecraft" or not option.loader_versions:
            self.loader_build_select.options = []
            self.loader_build_select.value = ""
            self.loader_build_select.visible = False
            return
        builds = list(option.loader_versions)
        current_loader_version = str(getattr(self.version, "loader_version", "") or "")
        selected = current_loader_version if current_loader_version in builds else option.loader_version or builds[0]
        self.loader_build_select.options = [ft.dropdown.Option(build) for build in builds]
        self.loader_build_select.value = selected
        self.loader_build_select.visible = True

    def _set_loading(self, loading: bool) -> None:
        self.loader_select.disabled = loading
        self.version_select.disabled = loading
        self.loader_build_select.disabled = loading
        self.install_button.disabled = loading or not bool(self.version_select.value)
        self.status_text.value = self.app.trans(
            "version_component_loading" if loading else (
                "version_component_ready" if self.options_by_version else "version_component_no_versions"
            )
        )
        self.status_text.color = (
            self.app.theme.text_secondary if loading or self.options_by_version else self.app.theme.error
        )
        schedule_update(self.page)

    def _apply_load_error(self, exc: Exception, generation: int) -> None:
        if self._disposed or self._closed or generation != self._load_generation:
            return
        self.options_by_version = {}
        self.version_select.options = []
        self.version_select.value = ""
        self._update_loader_builds()
        self._set_loading(False)
        self.status_text.value = self.app.trans("version_component_load_failed", error=str(exc))
        self.status_text.color = self.app.theme.error
        schedule_update(self.page)

    def _loader_options(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for loader in self.app.launcher.loaders():
            loader_id = str(loader.get_id() or "").strip().lower()
            if loader_id not in self.SUPPORTED_LOADERS or loader_id in seen:
                continue
            seen.add(loader_id)
            options.append({"text": loader.get_name(), "key": loader_id})
        return options

    def _current_loader_id(self) -> str:
        value = " ".join(
            (
                str(getattr(self.version, "client", "") or ""),
                str(getattr(self.version, "loader", "") or ""),
            )
        ).strip().lower().replace(" ", "")
        if "neoforge" in value:
            return "neoforge"
        for loader_id in ("fabric", "forge", "quilt", "minecraft"):
            if loader_id in value:
                return loader_id
        return "minecraft"


__all__ = ["VersionComponentModal"]
