from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft

from launcher import ui
from launcher.application.installed_components import InstalledComponentsService
from launcher.application.tensacraft_catalog import TensaCraftCatalogService
from launcher.application.tensacraft_install_state import mark_pending, unmark_pending
from launcher.application.version_creation import (
    VersionCreateOption,
    VersionCreationCatalogService,
    unique_version_name,
)
from launcher.core.api import TensaCraftAPI
from launcher.core.versions import Version
from launcher.ui.core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from launcher.ui.patterns.loader_builds import (
    build_loader_build_dropdown,
    selected_loader_version,
    update_selected_loader_version,
)


class VersionCreatePage:
    BADGE_COLUMN_WIDTH = 96
    OPTION_RENDER_LIMIT = 80

    TABS = (
        ("tensacraft", "TensaCraft", ft.Icons.ROCKET_LAUNCH),
        ("minecraft", "Minecraft", ft.Icons.GRASS),
        ("fabric", "Fabric", ft.Icons.EXTENSION),
        ("forge", "Forge", ft.Icons.BUILD),
        ("neoforge", "NeoForge", ft.Icons.CONSTRUCTION),
        ("quilt", "Quilt", ft.Icons.GRID_VIEW),
    )

    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.trans = app.trans
        self.catalog = VersionCreationCatalogService()
        self.active_tab = "tensacraft"
        self.include_unstable_versions = False
        self.options_by_tab: dict[str, list[VersionCreateOption]] = {}
        self.loaded_state_by_tab: dict[str, tuple[str, bool, bool]] = {}
        self._options_cache: dict[tuple[str, bool, bool], list[VersionCreateOption]] = {}
        self.visible_options_by_state: dict[tuple[str, bool, bool], int] = {}
        self.load_generation = 0
        self.loading_tabs: set[str] = set()
        self.failed_tabs: dict[str, str] = {}
        self.install_pending = False
        self.install_dialog = None
        self.install_target: VersionCreateOption | None = None
        self.install_name: ft.TextField | None = None
        self.install_loader_build: ft.Dropdown | None = None
        self.selected_loader_builds: dict[str, str] = {}
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

        self.app.header.set_params(
            title=self.trans("create_version_title"),
            show_back_btn=True,
            back_action=self.app.show_versions_page,
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.tab_bar = self._build_tab_bar()
        self.filter_bar = ui.Container()
        self.options_list = ui.ListView(expand=True, spacing=8, padding=ft.Padding.only(top=8, bottom=18))
        self.content = self._build_content()
        self._rebuild_active_content()

    def view(self):
        self._load_tab(self.active_tab)
        return self.content

    def _build_content(self) -> ft.Control:
        return ui.Container(
            expand=True,
            padding=self.app.theme.profile_content_padding,
            content=ui.Column(
                controls=[self.tab_bar, self.filter_bar, self.options_list],
                spacing=12,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def _build_tab_bar(self) -> ft.Control:
        return ui.Row(
            controls=self._build_tab_bar_controls(),
            spacing=8,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_tab_bar_controls(self) -> list[ft.Control]:
        return [
            ui.Row(
                controls=self._build_tab_buttons(),
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            *self._build_filter_controls(),
        ]

    def _build_tab_buttons(self) -> list[ft.Control]:
        buttons: list[ft.Control] = []
        for key, label, icon in self.TABS:
            selected = key == self.active_tab
            buttons.append(
                ui.Button(
                    text=label,
                    icon=icon,
                    variant="filled" if selected else "ghost",
                    tone="primary" if selected else "neutral",
                    size="sm",
                    on_click=lambda _e, tab_key=key: self.show_tab(tab_key),
                )
            )
        return buttons

    def show_tab(self, tab_key: str) -> None:
        if tab_key == self.active_tab:
            return
        self.active_tab = tab_key
        self._rebuild_active_content()
        if self._load_tab(tab_key):
            self._rebuild_active_content()
        schedule_update(self.page)

    def _rebuild_active_content(self) -> None:
        self.tab_bar.controls = self._build_tab_bar_controls()
        self.filter_bar.content = None
        self.options_list.controls = self._build_option_controls()

    def _build_filter_bar(self) -> ft.Control:
        controls = self._build_filter_controls()
        if not controls:
            return ui.Container(height=0)
        return ui.Row(controls=controls, spacing=8, wrap=True)

    def _build_filter_controls(self) -> list[ft.Control]:
        return [
            self._filter_button(
                label=self.trans("version_create_filter_unstable_versions"),
                icon=ft.Icons.AUTO_AWESOME,
                selected=self.include_unstable_versions,
                on_click=lambda _e: self._set_unstable_versions_enabled(not self.include_unstable_versions),
            )
        ]

    def _filter_button(self, *, label: str, icon: str, selected: bool, on_click) -> ft.Control:
        return ui.Button(
            text=label,
            icon=icon,
            variant="filled" if selected else "outline",
            tone="primary" if selected else "neutral",
            size="sm",
            on_click=on_click,
        )

    def _on_snapshots_toggle(self, event) -> None:
        self._set_unstable_versions_enabled(bool(event.control.value))

    def _set_snapshots_enabled(self, enabled: bool) -> None:
        self._set_unstable_versions_enabled(enabled)

    def _on_unstable_loaders_toggle(self, event) -> None:
        self._set_unstable_versions_enabled(bool(event.control.value))

    def _set_unstable_loaders_enabled(self, enabled: bool) -> None:
        self._set_unstable_versions_enabled(enabled)

    def _set_unstable_versions_enabled(self, enabled: bool) -> None:
        if self.include_unstable_versions == enabled:
            return
        self.include_unstable_versions = enabled
        self.load_generation += 1
        self.options_by_tab.clear()
        self.loaded_state_by_tab.clear()
        self.failed_tabs.clear()
        self.loading_tabs.clear()
        self._reload_active_tab()

    def _reload_active_tab(self) -> None:
        self.options_by_tab.pop(self.active_tab, None)
        self.loaded_state_by_tab.pop(self.active_tab, None)
        self.failed_tabs.pop(self.active_tab, None)
        self.loading_tabs.discard(self.active_tab)
        self._rebuild_active_content()
        if self._load_tab(self.active_tab):
            self._rebuild_active_content()
        schedule_update(self.page)

    def _load_tab(self, tab_key: str, *, force: bool = False) -> bool:
        state_key = self._tab_state_key(tab_key)
        if not force and tab_key in self.options_by_tab and self.loaded_state_by_tab.get(tab_key) == state_key:
            return True
        if tab_key in self.options_by_tab:
            self.options_by_tab.pop(tab_key, None)
            self.loaded_state_by_tab.pop(tab_key, None)
        if state_key in self._options_cache:
            self.options_by_tab[tab_key] = list(self._options_cache[state_key])
            self.loaded_state_by_tab[tab_key] = state_key
            self.failed_tabs.pop(tab_key, None)
            self.loading_tabs.discard(tab_key)
            return True
        if tab_key in self.loading_tabs:
            return False
        self.loading_tabs.add(tab_key)
        self._rebuild_active_content()
        generation = self.load_generation
        try:
            run_task(
                self.page,
                self._load_tab_async,
                tab_key,
                self._include_snapshots(tab_key),
                self._include_unstable_loaders(tab_key),
                generation,
            )
        except Exception as exc:
            self.loading_tabs.discard(tab_key)
            self.failed_tabs[tab_key] = str(exc)
            self._rebuild_active_content()
        return False

    async def _load_tab_async(
        self,
        tab_key: str,
        include_snapshots: bool,
        include_unstable: bool,
        generation: int,
    ) -> None:
        try:
            options = await run_blocking(self._fetch_tab_options, tab_key, include_snapshots, include_unstable)
        except Exception as exc:
            if generation != self.load_generation:
                return
            self.failed_tabs[tab_key] = str(exc)
            self.app.log.error(f"Unable to load version create tab '{tab_key}': {exc!r}")
            options = []
        if generation != self.load_generation:
            return
        self.loading_tabs.discard(tab_key)
        if (
            self._include_snapshots(tab_key) == include_snapshots
            and self._include_unstable_loaders(tab_key) == include_unstable
        ):
            state_key = (tab_key, include_snapshots, include_unstable)
            self.options_by_tab[tab_key] = options
            self.loaded_state_by_tab[tab_key] = state_key
            self._options_cache[state_key] = list(options)
        if self.active_tab == tab_key:
            self._rebuild_active_content()
            schedule_update(self.page)

    def _tab_state_key(self, tab_key: str) -> tuple[str, bool, bool]:
        return (
            tab_key,
            self._include_snapshots(tab_key),
            self._include_unstable_loaders(tab_key),
        )

    def _include_snapshots(self, tab_key: str) -> bool:
        return bool(self.include_unstable_versions and self.catalog.supports_snapshots(tab_key))

    def _include_unstable_loaders(self, tab_key: str) -> bool:
        return bool(self.include_unstable_versions and self.catalog.supports_unstable_loaders(tab_key))

    def _fetch_tab_options(
        self,
        tab_key: str,
        include_snapshots: bool,
        include_unstable: bool,
    ) -> list[VersionCreateOption]:
        if tab_key == "tensacraft":
            return self._fetch_tensacraft_options()
        if tab_key == "minecraft":
            return self.catalog.minecraft_versions(
                include_snapshots=include_snapshots and self.catalog.supports_snapshots(tab_key)
            )
        return self.catalog.loader_versions(
            tab_key,
            include_snapshots=include_snapshots and self.catalog.supports_snapshots(tab_key),
            include_unstable_loaders=include_unstable and self.catalog.supports_unstable_loaders(tab_key),
        )

    def _fetch_tensacraft_options(self) -> list[VersionCreateOption]:
        packs = TensaCraftAPI().list_versions()
        options: list[VersionCreateOption] = []
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            pack_id = TensaCraftCatalogService.pack_id(pack)
            if not pack_id:
                continue
            client = pack.get("client") if isinstance(pack.get("client"), dict) else {}
            name = str(pack.get("title") or client.get("name") or pack.get("name") or pack_id).strip()
            minecraft_version = str(client.get("minecraft_version") or pack.get("minecraft_version") or "").strip()
            loader_id = str(client.get("loader_id") or client.get("loader") or pack.get("loader_id") or "").strip()
            loader_version = str(client.get("loader_version") or pack.get("loader_version") or "").strip()
            image = pack.get("image") or client.get("image")
            options.append(
                VersionCreateOption(
                    id=pack_id,
                    name=name,
                    minecraft_version=minecraft_version,
                    loader_id="tensacraft",
                    loader_name="TensaCraft",
                    description=TensaCraftCatalogService.pack_description(pack),
                    image=image if isinstance(image, str) and image.strip() else None,
                    loader_version=f"{loader_id} {loader_version}".strip() or None,
                    pack=pack,
                )
            )
        return options

    def _build_option_controls(self) -> list[ft.Control]:
        if self.active_tab in self.loading_tabs:
            return [self._state_row(self.trans("version_create_loading"), ft.Icons.HOURGLASS_TOP)]
        if self.active_tab in self.failed_tabs:
            return [self._state_row(self.failed_tabs[self.active_tab], ft.Icons.ERROR_OUTLINE)]
        options = self.options_by_tab.get(self.active_tab)
        if options is None:
            return [self._state_row(self.trans("version_create_loading"), ft.Icons.HOURGLASS_TOP)]
        if not options:
            return [self._state_row(self.trans("version_create_empty"), ft.Icons.INBOX_OUTLINED)]
        state_key = self._tab_state_key(self.active_tab)
        visible_count = self._visible_option_count(state_key, len(options))
        controls = [self._build_option_row(option) for option in options[:visible_count]]
        if visible_count < len(options):
            controls.append(self._load_more_row(lambda _e: self._load_more_options()))
        return controls

    def _visible_option_count(self, state_key: tuple[str, bool, bool], total: int) -> int:
        current = self.visible_options_by_state.get(state_key, self.OPTION_RENDER_LIMIT)
        return min(max(0, current), total)

    def _load_more_options(self) -> None:
        options = self.options_by_tab.get(self.active_tab) or []
        state_key = self._tab_state_key(self.active_tab)
        current = self._visible_option_count(state_key, len(options))
        self.visible_options_by_state[state_key] = min(len(options), current + self.OPTION_RENDER_LIMIT)
        self._rebuild_active_content()
        schedule_update(self.page)

    def _load_more_row(self, on_click) -> ft.Control:
        theme = self.app.theme
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Button(
                        text=self.trans("load_more"),
                        icon=ft.Icons.EXPAND_MORE,
                        variant="outline",
                        tone="primary",
                        size="sm",
                        on_click=on_click,
                    )
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=theme.spacing_sm),
        )

    def _state_row(self, text: str, icon: str) -> ft.Control:
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Icon(icon, color=self.app.theme.text_secondary),
                    ui.Text(text, color=self.app.theme.text_secondary, weight=self.app.theme.font_weight_medium),
                ],
                spacing=self.app.theme.spacing_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=self.app.theme.radius_sm,
            padding=self.app.theme.padding_md,
        )

    def _build_option_row(self, option: VersionCreateOption) -> ft.Control:
        theme = self.app.theme
        badges = self._build_badges(option)
        subtitle = self._option_subtitle(option)
        description = str(option.description or "").strip()
        install_button = ui.Button(
            text=self.trans("install"),
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            size="sm",
            disabled=self.install_pending,
            on_click=lambda _e, opt=option: self._handle_install_click(opt),
        )
        visual = self._option_visual(option)
        title = ui.Text(
            option.name,
            size=theme.text_size_medium,
            weight=theme.font_weight_semibold,
            color=theme.text_color,
        )
        text_controls: list[ft.Control] = [
            title,
            ui.Text(subtitle, size=theme.text_size_xs, color=theme.text_secondary),
        ]
        if description:
            text_controls.append(
                ui.Text(
                    description,
                    size=theme.text_size_xs,
                    color=theme.text_tertiary,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        row = ui.Row(
            controls=[
                ui.Row(
                    controls=[
                        visual,
                        ui.Column(
                            controls=text_controls,
                            spacing=2,
                            expand=True,
                        ),
                        self._badge_slot(badges),
                    ],
                    spacing=theme.spacing_sm,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                install_button,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ui.Container(
            content=row,
            bgcolor=theme.bg_list,
            border=ft.Border.all(1, theme.border_color),
            border_radius=theme.radius_sm,
            padding=theme.padding_md,
            margin=ft.Margin.only(left=6, right=6, bottom=4),
            on_click=lambda _e, opt=option: self._open_install_dialog(opt),
        )

    def _option_visual(self, option: VersionCreateOption) -> ft.Control:
        size = self.app.theme.version_image_size_compact
        if option.image:
            return ui.Image(
                src=option.image,
                width=size,
                height=size,
                fit=ft.BoxFit.COVER,
                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            )
        return ui.Container(
            content=ui.Icon(self._tab_icon(option.loader_id), color=self.app.theme.text_secondary),
            width=size,
            height=size,
            bgcolor=self.app.theme.bg_card,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            alignment=ft.Alignment.CENTER,
        )

    def _build_badges(self, option: VersionCreateOption) -> list[ft.Control]:
        badges: list[ft.Control] = []
        if option.snapshot:
            badges.append(self._badge(self.trans("version_create_snapshot_badge"), ft.Colors.AMBER_500))
        if option.unstable_loader:
            badges.append(self._badge(self.trans("version_create_unstable_loader_badge"), ft.Colors.DEEP_ORANGE_400))
        return badges

    def _badge_slot(self, badges: list[ft.Control]) -> ft.Control:
        return ui.Container(
            key="version-create-badge-slot",
            width=self.BADGE_COLUMN_WIDTH,
            alignment=ft.Alignment.CENTER,
            content=ui.Row(
                controls=badges,
                spacing=self.app.theme.spacing_xs,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _badge(self, label: str, color: str) -> ft.Control:
        text_size = max(9, int(self.app.theme.text_size_xs) - 1)
        return ui.Container(
            content=ui.Text(
                label,
                size=text_size,
                color=color,
                weight=self.app.theme.font_weight_semibold,
            ),
            bgcolor=self.app.theme.overlay(0.14, color),
            border=ft.Border.all(1, self.app.theme.overlay(0.36, color)),
            border_radius=ft.BorderRadius.all(999),
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
        )

    def _option_subtitle(self, option: VersionCreateOption) -> str:
        parts = [option.loader_name]
        if option.minecraft_version:
            parts.append(f"Minecraft {option.minecraft_version}")
        if option.loader_version:
            parts.append(self.trans("version_create_loader_build", version=self._selected_loader_version(option)))
        return " • ".join(parts)

    def _loader_build_selector(self, option: VersionCreateOption) -> ft.Control | None:
        return build_loader_build_dropdown(
            self.app,
            option,
            self.selected_loader_builds,
            self._set_loader_build,
            width=self.app.theme.modal_width,
        )

    def _set_loader_build(self, option: VersionCreateOption, event) -> None:
        update_selected_loader_version(option, self.selected_loader_builds, event)
        schedule_update(self.page)

    def _selected_loader_version(self, option: VersionCreateOption) -> str | None:
        return selected_loader_version(option, self.selected_loader_builds)

    def _tab_icon(self, tab_key: str) -> str:
        for key, _label, icon in self.TABS:
            if key == tab_key:
                return icon
        return ft.Icons.VIDEOGAME_ASSET

    def _handle_install_click(self, option: VersionCreateOption) -> None:
        self._open_install_dialog(option)

    def _confirm_tensacraft_install(self, option: VersionCreateOption) -> None:
        self._open_install_dialog(option)

    def _open_install_dialog(self, option: VersionCreateOption) -> None:
        self.install_target = option
        theme = self.app.theme
        self.install_name = ui.TextField(
            value=self._install_name(option),
            label=self.trans("version_name_label"),
            autofocus=True,
            width=theme.modal_width,
        )
        build_selector = self._loader_build_selector(option)
        self.install_loader_build = build_selector if isinstance(build_selector, ft.Dropdown) else None
        details: list[ft.Control] = [
            ui.Text(self.trans("version_create_install_confirm_message"), color=theme.text_secondary),
            self.install_name,
            ui.Text(self._option_subtitle(option), color=theme.text_secondary, size=theme.text_size_sm),
        ]
        if build_selector is not None:
            details.append(build_selector)
        if option.loader_id == "tensacraft":
            description = option.description or self.trans("tensacraft_description_unavailable")
            details.append(
                ui.Container(
                    content=ui.Text(description, color=theme.text_secondary, size=theme.text_size_sm),
                    width=theme.modal_width,
                    padding=theme.padding_md,
                    border=ft.Border.all(1, theme.border_color),
                    border_radius=theme.radius_md,
                    bgcolor=theme.overlay(theme.alpha_input, theme.bg_shell),
                )
            )
        self.install_dialog = ui.AlertDialog(
            title=ui.Text(
                self.trans("version_create_install_confirm_title", version=option.name),
                color=theme.text_color,
                weight=theme.font_weight_bold,
            ),
            modal=True,
            content=ui.Column(
                details,
                width=theme.modal_width,
                spacing=theme.spacing_md,
                tight=True,
            ),
            actions=[
                ui.Button(text=self.trans("install"), on_click=lambda _e: self._confirm_install_dialog()),
                ui.Button(text=self.trans("cancel"), variant="outline", tone="neutral", on_click=lambda _e: self._close_install_dialog()),
            ],
        )
        show_dialog(self.page, self.install_dialog)
        schedule_update(self.page)

    def _confirm_install_dialog(self) -> None:
        option = self.install_target
        name = str(getattr(self.install_name, "value", "") or "").strip()
        if not name:
            self.app.feedback.warning(self.trans("empty_version_name"))
            schedule_update(self.page)
            return
        existing_names = {version.name.casefold() for version in self.app.versions.all()}
        if name.casefold() in existing_names:
            self.app.feedback.warning(self.trans("version_exists", name=name))
            schedule_update(self.page)
            return
        self._close_install_dialog()
        if option is not None:
            self._start_install(option, name)

    def _close_install_dialog(self) -> None:
        if self.install_dialog is not None:
            close_dialog(self.page, self.install_dialog)
        self.install_dialog = None
        self.install_target = None
        self.install_name = None
        self.install_loader_build = None
        schedule_update(self.page)

    def _start_install(self, option: VersionCreateOption, name: str | None = None) -> None:
        if self.install_pending:
            return
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        name = str(name or self._install_name(option)).strip()
        if option.loader_id == "tensacraft":
            mark_pending(self.app, option.id)
        self.install_pending = True
        operation = self.app.feedback.begin_operation(
            self.trans("installation_started"),
            kind="install",
            status=self.trans("installation_started"),
        )
        try:
            run_task(self.page, self._install_option_async, option, name, operation)
        except Exception:
            if option.loader_id == "tensacraft":
                unmark_pending(self.app, option.id)
            self.install_pending = False
            operation.fail(self.trans("installation_failed"), notify=False)
            raise

    async def _install_option_async(self, option: VersionCreateOption, name: str, operation) -> None:
        final_message = self.trans("installation_complete")
        try:
            await run_blocking(self._install_option, option, name, operation=operation)
        except Exception as exc:
            self.app.log.error(f"Failed to install {option.loader_id} {option.minecraft_version or option.id}: {exc}")
            final_message = self.trans("version_install_error", client=option.loader_name, version=option.name, error=str(exc))
            self.app.feedback.warning(final_message)
            operation.fail(final_message, notify=False)
            return
        finally:
            if option.loader_id == "tensacraft":
                unmark_pending(self.app, option.id)
            self.install_pending = False
        operation.finish(final_message, show_success=False)
        await self.app.feedback.wait_until_progress_hidden()
        self.app.feedback.info(self.trans("version_install_success", version=name))
        self.app.show_versions_page()

    def _install_option(self, option: VersionCreateOption, name: str, *, operation=None) -> None:
        data: dict[str, Any] = {
            "name": name,
            "version": option.id if option.loader_id == "tensacraft" else option.minecraft_version,
            "client": option.loader_id,
        }
        selected_loader_version = self._selected_loader_version(option)
        if selected_loader_version and option.loader_id not in {"minecraft", "tensacraft"}:
            data["loader_version"] = selected_loader_version
        version = Version(name, data)
        if option.loader_id == "tensacraft":
            version.install()
            return
        self.components.install_game_build(version, operation=operation)

    def _install_name(self, option: VersionCreateOption) -> str:
        existing_names = [version.name for version in self.app.versions.all()]
        return unique_version_name(existing_names, option.name)


__all__ = ["VersionCreatePage"]
