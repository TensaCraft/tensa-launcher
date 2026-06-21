from __future__ import annotations

from datetime import datetime

import flet as ft

from launcher import ui
from launcher.application.installed_components import InstalledComponent, InstalledComponentsService
from launcher.application.version_creation import VersionCreateOption, VersionCreationCatalogService
from launcher.ui.core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog
from launcher.ui.patterns.loader_builds import (
    build_loader_build_dropdown,
    selected_loader_version,
    update_selected_loader_version,
)


class MinecraftComponentsPage:
    BADGE_COLUMN_WIDTH = 96
    OPTION_RENDER_LIMIT = 80

    INSTALL_TABS = (
        ("minecraft", "Minecraft", ft.Icons.GRASS),
        ("fabric", "Fabric", ft.Icons.EXTENSION),
        ("forge", "Forge", ft.Icons.BUILD),
        ("neoforge", "NeoForge", ft.Icons.CONSTRUCTION),
        ("quilt", "Quilt", ft.Icons.GRID_VIEW),
    )

    MODES = (
        ("installed", "minecraft_components_installed_tab", ft.Icons.INVENTORY_2_OUTLINED),
        ("install", "minecraft_components_install_tab", ft.Icons.DOWNLOAD_ROUNDED),
    )

    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.trans = app.trans
        minecraft_dir = getattr(getattr(app, "paths", None), "minecraft_dir", None) or app.util.minecraft_dir
        self.service = InstalledComponentsService(minecraft_dir, versions_provider=app.versions.all)
        self.catalog = VersionCreationCatalogService()
        self.active_mode = "installed"
        self.active_install_tab = "minecraft"
        self.include_unstable_versions = False
        self.installed_components: list[InstalledComponent] = []
        self.options_by_tab: dict[str, list[VersionCreateOption]] = {}
        self.loaded_state_by_tab: dict[str, tuple[str, bool, bool]] = {}
        self.selected_loader_builds: dict[str, str] = {}
        self.visible_options_by_state: dict[tuple[str, bool, bool], int] = {}
        self.load_generation = 0
        self.loading_tabs: set[str] = set()
        self.failed_tabs: dict[str, str] = {}
        self._options_cache: dict[tuple[str, bool, bool], list[VersionCreateOption]] = {}
        self.operation_pending = False
        self.install_dialog = None
        self.install_target: VersionCreateOption | None = None

        self.app.header.set_params(
            title=self.trans("minecraft_components_title"),
            show_back_btn=True,
            back_action=self.app.show_versions_page,
            actions=[
                ui.Button(
                    text=self.trans("refresh"),
                    icon=ft.Icons.REFRESH,
                    size="sm",
                    variant="outline",
                    tone="neutral",
                    on_click=lambda _e: self.refresh_installed(),
                )
            ],
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.mode_tabs = ui.Row(controls=self._build_mode_tabs(), spacing=8, scroll=ft.ScrollMode.AUTO)
        self.install_tabs = ui.Container()
        self.filter_bar = ui.Container()
        self.content_list = ui.ListView(expand=True, spacing=8, padding=ft.Padding.only(top=8, bottom=18))
        self.content = self._build_content()
        self.refresh_installed(update=False)
        self._rebuild_content()

    def view(self):
        return self.content

    def refresh_installed(self, *, update: bool = True) -> None:
        self.installed_components = self.service.list_installed()
        self._rebuild_content()
        if update:
            schedule_update(self.page)

    def _build_content(self) -> ft.Control:
        return ui.Container(
            expand=True,
            padding=self.app.theme.profile_content_padding,
            content=ui.Column(
                controls=[
                    self.mode_tabs,
                    self.install_tabs,
                    self.filter_bar,
                    self.content_list,
                ],
                spacing=12,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def _build_mode_tabs(self) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for mode, label_key, icon in self.MODES:
            selected = mode == self.active_mode
            controls.append(
                ui.Button(
                    text=self.trans(label_key),
                    icon=icon,
                    variant="filled" if selected else "ghost",
                    tone="primary" if selected else "neutral",
                    size="sm",
                    on_click=lambda _e, selected_mode=mode: self.show_mode(selected_mode),
                )
            )
        return controls

    def _build_install_tabs(self) -> ft.Control:
        return ui.Row(
            controls=[
                ui.Row(
                    controls=self._build_install_tab_buttons(),
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                *self._build_filter_controls(),
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_install_tab_buttons(self) -> list[ft.Control]:
        return [
            ui.Button(
                text=label,
                icon=icon,
                variant="filled" if key == self.active_install_tab else "ghost",
                tone="primary" if key == self.active_install_tab else "neutral",
                size="sm",
                on_click=lambda _e, tab_key=key: self.show_install_tab(tab_key),
            )
            for key, label, icon in self.INSTALL_TABS
        ]

    def show_mode(self, mode: str) -> None:
        if mode == self.active_mode:
            return
        self.active_mode = mode
        self.mode_tabs.controls = self._build_mode_tabs()
        self._rebuild_content()
        if mode == "install":
            self._load_install_tab(self.active_install_tab)
        schedule_update(self.page)

    def show_install_tab(self, tab_key: str) -> None:
        if tab_key == self.active_install_tab:
            return
        self.active_install_tab = tab_key
        self._rebuild_content()
        self._load_install_tab(tab_key)
        schedule_update(self.page)

    def _rebuild_content(self) -> None:
        if self.active_mode == "installed":
            self.install_tabs.content = None
            self.filter_bar.content = None
            self.content_list.controls = self._build_installed_controls()
            return

        self.install_tabs.content = self._build_install_tabs()
        self.filter_bar.content = None
        self.content_list.controls = self._build_install_controls()

    def _build_installed_controls(self) -> list[ft.Control]:
        if not self.installed_components:
            return [self._state_row(self.trans("minecraft_components_empty"), ft.Icons.INBOX_OUTLINED)]
        return [self._build_component_row(component) for component in self.installed_components]

    def _build_component_row(self, component: InstalledComponent) -> ft.Control:
        theme = self.app.theme
        title = ui.Text(
            component.version_id,
            size=theme.text_size_medium,
            weight=theme.font_weight_semibold,
            color=theme.text_color,
        )
        subtitle = ui.Text(
            self._component_summary(component),
            size=theme.text_size_xs,
            color=theme.text_secondary,
        )
        meta = ui.Text(
            self._component_meta(component),
            size=theme.text_size_xs,
            color=theme.text_tertiary,
        )
        actions = ui.Row(
            controls=[
                ui.IconButton(
                    ft.Icons.FACT_CHECK,
                    tooltip=self.trans("minecraft_components_verify"),
                    on_click=lambda _e, selected=component: self.verify_component(selected),
                ),
                ui.IconButton(
                    ft.Icons.RESTART_ALT,
                    tooltip=self.trans("minecraft_components_reinstall"),
                    icon_color=theme.primary,
                    on_click=lambda _e, selected=component: self.confirm_reinstall(selected),
                ),
                ui.IconButton(
                    ft.Icons.FOLDER_OPEN,
                    tooltip=self.trans("open_directory"),
                    on_click=lambda _e, selected=component: self.open_component_directory(selected),
                ),
                ui.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    tooltip=self.trans("delete"),
                    icon_color=theme.error,
                    on_click=lambda _e, selected=component: self.confirm_delete(selected),
                ),
            ],
            spacing=theme.spacing_xs,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Row(
                        controls=[
                            self._component_visual(component),
                            ui.Column(controls=[title, subtitle, meta], spacing=2, expand=True),
                        ],
                        spacing=theme.spacing_sm,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    actions,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=theme.bg_list,
            border=ft.Border.all(1, theme.border_color),
            border_radius=theme.radius_sm,
            padding=theme.padding_md,
            margin=ft.Margin.only(left=6, right=6, bottom=4),
        )

    def _component_visual(self, component: InstalledComponent) -> ft.Control:
        return ui.Container(
            content=ui.Icon(self._loader_icon(component.kind), color=self.app.theme.text_secondary),
            width=self.app.theme.version_image_size_compact,
            height=self.app.theme.version_image_size_compact,
            bgcolor=self.app.theme.bg_card,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            alignment=ft.Alignment.CENTER,
        )

    def _component_summary(self, component: InstalledComponent) -> str:
        parts = [component.loader_name]
        if component.minecraft_version:
            parts.append(f"Minecraft {component.minecraft_version}")
        if component.loader_version:
            parts.append(self.trans("version_create_loader_build", version=component.loader_version))
        return " / ".join(parts)

    def _component_meta(self, component: InstalledComponent) -> str:
        parts = [self._format_bytes(component.size_bytes)]
        if component.modified_at:
            parts.append(self.trans("minecraft_components_modified", date=self._format_timestamp(component.modified_at)))
        if component.used_by:
            parts.append(self.trans("minecraft_components_used_by", versions=", ".join(component.used_by)))
        elif component.dependent_components:
            parts.append(
                self.trans(
                    "minecraft_components_base_for",
                    count=len(component.dependent_components),
                )
            )
        else:
            parts.append(self.trans("minecraft_components_unused"))
        return " / ".join(parts)

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
                on_click=lambda _e: self.set_unstable_versions_enabled(not self.include_unstable_versions),
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

    def set_snapshots_enabled(self, enabled: bool) -> None:
        self.set_unstable_versions_enabled(enabled)

    def set_unstable_loaders_enabled(self, enabled: bool) -> None:
        self.set_unstable_versions_enabled(enabled)

    def set_unstable_versions_enabled(self, enabled: bool) -> None:
        if self.include_unstable_versions == enabled:
            return
        self.include_unstable_versions = enabled
        self.load_generation += 1
        self.options_by_tab.clear()
        self.loaded_state_by_tab.clear()
        self.failed_tabs.clear()
        self.loading_tabs.clear()
        self.reload_install_tab()

    def reload_install_tab(self) -> None:
        self.options_by_tab.pop(self.active_install_tab, None)
        self.loaded_state_by_tab.pop(self.active_install_tab, None)
        self.failed_tabs.pop(self.active_install_tab, None)
        self.loading_tabs.discard(self.active_install_tab)
        self._rebuild_content()
        self._load_install_tab(self.active_install_tab)
        schedule_update(self.page)

    def _build_install_controls(self) -> list[ft.Control]:
        tab_key = self.active_install_tab
        if tab_key in self.loading_tabs:
            return [self._state_row(self.trans("version_create_loading"), ft.Icons.HOURGLASS_TOP)]
        if tab_key in self.failed_tabs:
            return [self._state_row(self.failed_tabs[tab_key], ft.Icons.ERROR_OUTLINE)]
        options = self.options_by_tab.get(tab_key)
        if options is None:
            return [self._state_row(self.trans("version_create_loading"), ft.Icons.HOURGLASS_TOP)]
        if not options:
            return [self._state_row(self.trans("version_create_empty"), ft.Icons.INBOX_OUTLINED)]
        installed_ids = {component.version_id for component in self.installed_components}
        state_key = self._tab_state_key(tab_key)
        visible_count = self._visible_option_count(state_key, len(options))
        controls = [self._build_install_option_row(option, installed_ids) for option in options[:visible_count]]
        if visible_count < len(options):
            controls.append(self._load_more_row(lambda _e: self._load_more_install_options()))
        return controls

    def _visible_option_count(self, state_key: tuple[str, bool, bool], total: int) -> int:
        current = self.visible_options_by_state.get(state_key, self.OPTION_RENDER_LIMIT)
        return min(max(0, current), total)

    def _load_more_install_options(self) -> None:
        options = self.options_by_tab.get(self.active_install_tab) or []
        state_key = self._tab_state_key(self.active_install_tab)
        current = self._visible_option_count(state_key, len(options))
        self.visible_options_by_state[state_key] = min(len(options), current + self.OPTION_RENDER_LIMIT)
        self._rebuild_content()
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

    def _build_install_option_row(self, option: VersionCreateOption, installed_ids: set[str]) -> ft.Control:
        theme = self.app.theme
        badges = self._build_badges(option)
        expected_id = InstalledComponentsService.expected_component_id(
            option.loader_id,
            option.minecraft_version,
            self._selected_loader_version(option),
        )
        installed = bool(expected_id and expected_id in installed_ids)
        action = ui.Button(
            text=self.trans("minecraft_components_installed") if installed else self.trans("minecraft_components_install_action"),
            icon=ft.Icons.CHECK if installed else ft.Icons.DOWNLOAD_ROUNDED,
            size="sm",
            disabled=installed or self.operation_pending,
            on_click=lambda _e, selected=option: self.confirm_install_option(selected),
        )
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Row(
                        controls=[
                            self._option_visual(option),
                            ui.Column(
                                controls=[
                                    ui.Text(
                                        option.name,
                                        size=theme.text_size_medium,
                                        weight=theme.font_weight_semibold,
                                        color=theme.text_color,
                                    ),
                                    ui.Text(
                                        self._option_subtitle(option),
                                        size=theme.text_size_xs,
                                        color=theme.text_secondary,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            self._badge_slot(badges),
                        ],
                        spacing=theme.spacing_sm,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    action,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=theme.bg_list,
            border=ft.Border.all(1, theme.border_color),
            border_radius=theme.radius_sm,
            padding=theme.padding_md,
            margin=ft.Margin.only(left=6, right=6, bottom=4),
            on_click=None if installed else lambda _e, selected=option: self.confirm_install_option(selected),
        )

    def _option_visual(self, option: VersionCreateOption) -> ft.Control:
        return ui.Container(
            content=ui.Icon(self._loader_icon(option.loader_id), color=self.app.theme.text_secondary),
            width=self.app.theme.version_image_size_compact,
            height=self.app.theme.version_image_size_compact,
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
            key="minecraft-components-badge-slot",
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
        return " / ".join(parts)

    def _loader_build_selector(self, option: VersionCreateOption, *, width: int | float | None = None) -> ft.Control | None:
        return build_loader_build_dropdown(
            self.app,
            option,
            self.selected_loader_builds,
            self._set_loader_build,
            width=width or self.app.theme.modal_width,
        )

    def _set_loader_build(self, option: VersionCreateOption, event) -> None:
        update_selected_loader_version(option, self.selected_loader_builds, event)
        schedule_update(self.page)

    def _selected_loader_version(self, option: VersionCreateOption) -> str | None:
        return selected_loader_version(option, self.selected_loader_builds)

    def _load_install_tab(self, tab_key: str) -> bool:
        state_key = self._tab_state_key(tab_key)
        if tab_key in self.options_by_tab and self.loaded_state_by_tab.get(tab_key) == state_key:
            return True
        if tab_key in self.options_by_tab:
            self.options_by_tab.pop(tab_key, None)
            self.loaded_state_by_tab.pop(tab_key, None)
        if state_key in self._options_cache:
            self.options_by_tab[tab_key] = list(self._options_cache[state_key])
            self.loaded_state_by_tab[tab_key] = state_key
            self.loading_tabs.discard(tab_key)
            self.failed_tabs.pop(tab_key, None)
            self._rebuild_content()
            return True
        if tab_key in self.loading_tabs:
            return False
        self.loading_tabs.add(tab_key)
        self._rebuild_content()
        generation = self.load_generation
        try:
            run_task(
                self.page,
                self._load_install_tab_async,
                tab_key,
                self._include_snapshots(tab_key),
                self._include_unstable_loaders(tab_key),
                generation,
            )
        except Exception as exc:
            self.loading_tabs.discard(tab_key)
            self.failed_tabs[tab_key] = str(exc)
            self._rebuild_content()
        return False

    async def _load_install_tab_async(
        self,
        tab_key: str,
        include_snapshots: bool,
        include_unstable: bool,
        generation: int,
    ) -> None:
        try:
            options = await run_blocking(self._fetch_install_options, tab_key, include_snapshots, include_unstable)
        except Exception as exc:
            if generation != self.load_generation:
                return
            self.failed_tabs[tab_key] = str(exc)
            self.app.log.error(f"Unable to load component install tab '{tab_key}': {exc!r}")
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
        if self.active_mode == "install" and self.active_install_tab == tab_key:
            self._rebuild_content()
            schedule_update(self.page)

    def _fetch_install_options(self, tab_key: str, include_snapshots: bool, include_unstable: bool) -> list[VersionCreateOption]:
        if tab_key == "minecraft":
            return self.catalog.minecraft_versions(
                include_snapshots=include_snapshots and self.catalog.supports_snapshots(tab_key)
            )
        return self.catalog.loader_versions(
            tab_key,
            include_snapshots=include_snapshots and self.catalog.supports_snapshots(tab_key),
            include_unstable_loaders=include_unstable and self.catalog.supports_unstable_loaders(tab_key),
        )

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

    def verify_component(self, component: InstalledComponent) -> None:
        if self.operation_pending:
            return
        run_task(self.page, self._verify_component_async, component)

    async def _verify_component_async(self, component: InstalledComponent) -> None:
        result = await run_blocking(self.service.verify_component, component)
        if result.get("valid"):
            self.app.feedback.info(self.trans("minecraft_components_verify_ok", version=component.version_id))
            return
        repair_message = self.trans("minecraft_components_repairing", version=component.version_id)
        operation = self.app.feedback.begin_operation(repair_message, kind="install", status=repair_message)
        self.operation_pending = True
        try:
            await run_blocking(self.service.reinstall_component, component, operation)
        except Exception as exc:
            message = self.trans("minecraft_components_repair_failed", version=component.version_id, error=str(exc))
            operation.fail(message, notify=False)
            self.app.feedback.warning(message)
            return
        finally:
            self.operation_pending = False
        operation.finish(self.trans("minecraft_components_repair_complete", version=component.version_id), show_success=False)
        self.refresh_installed(update=False)
        self.app.feedback.info(self.trans("minecraft_components_repair_complete", version=component.version_id))
        schedule_update(self.page)

    def confirm_reinstall(self, component: InstalledComponent) -> None:
        self.app.feedback.confirm(
            self.trans("minecraft_components_reinstall_confirm_title", version=component.version_id),
            self.trans("minecraft_components_reinstall_confirm_message"),
            lambda confirmed, selected=component: self.start_reinstall(selected) if confirmed else None,
        )

    def confirm_install_option(self, option: VersionCreateOption) -> None:
        self.install_target = option
        theme = self.app.theme
        build_selector = self._loader_build_selector(option)
        details: list[ft.Control] = [
            ui.Text(self.trans("minecraft_components_install_confirm_message"), color=theme.text_secondary),
            ui.Text(self._option_subtitle(option), color=theme.text_secondary, size=theme.text_size_sm),
        ]
        if build_selector is not None:
            details.append(build_selector)

        self.install_dialog = ui.AlertDialog(
            title=ui.Text(
                self.trans("minecraft_components_install_confirm_title", version=option.name),
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
                ui.Button(
                    text=self.trans("minecraft_components_install_action"),
                    on_click=lambda _e: self._confirm_install_dialog(),
                ),
                ui.Button(
                    text=self.trans("cancel"),
                    variant="outline",
                    tone="neutral",
                    on_click=lambda _e: self._close_install_dialog(),
                ),
            ],
        )
        show_dialog(self.page, self.install_dialog)
        schedule_update(self.page)

    def _confirm_install_dialog(self) -> None:
        option = self.install_target
        self._close_install_dialog()
        if option is not None:
            self.start_install_option(option)

    def _close_install_dialog(self) -> None:
        if self.install_dialog is not None:
            close_dialog(self.page, self.install_dialog)
        self.install_dialog = None
        self.install_target = None
        schedule_update(self.page)

    def confirm_delete(self, component: InstalledComponent) -> None:
        message = self._delete_message(component)
        self.app.feedback.confirm(
            self.trans("minecraft_components_delete_confirm_title", version=component.version_id),
            message,
            lambda confirmed, selected=component: self.delete_component(selected) if confirmed else None,
        )

    def _delete_message(self, component: InstalledComponent) -> str:
        if component.used_by:
            return self.trans("minecraft_components_delete_used_message", versions=", ".join(component.used_by))
        if component.dependent_components:
            return self.trans(
                "minecraft_components_delete_base_message",
                count=len(component.dependent_components),
            )
        return self.trans("minecraft_components_delete_message")

    def start_reinstall(self, component: InstalledComponent) -> None:
        if self._guard_operation():
            return
        operation = self.app.feedback.begin_operation(
            self.trans("minecraft_components_reinstalling", version=component.version_id),
            kind="install",
            status=self.trans("minecraft_components_reinstalling", version=component.version_id),
        )
        self.operation_pending = True
        run_task(self.page, self._reinstall_component_async, component, operation)

    async def _reinstall_component_async(self, component: InstalledComponent, operation) -> None:
        try:
            await run_blocking(self.service.reinstall_component, component, operation)
        except Exception as exc:
            message = self.trans("minecraft_components_reinstall_failed", version=component.version_id, error=str(exc))
            operation.fail(message, notify=False)
            self.app.feedback.warning(message)
            return
        finally:
            self.operation_pending = False
        operation.finish(self.trans("minecraft_components_reinstall_complete", version=component.version_id), show_success=False)
        self.refresh_installed(update=False)
        self.app.feedback.info(self.trans("minecraft_components_reinstall_complete", version=component.version_id))
        schedule_update(self.page)

    def start_install_option(self, option: VersionCreateOption) -> None:
        if self._guard_operation():
            return
        operation = self.app.feedback.begin_operation(
            self.trans("minecraft_components_installing", version=option.name),
            kind="install",
            status=self.trans("minecraft_components_installing", version=option.name),
        )
        self.operation_pending = True
        run_task(self.page, self._install_option_async, option, operation)

    async def _install_option_async(self, option: VersionCreateOption, operation) -> None:
        try:
            await run_blocking(
                self.service.install_component,
                option.loader_id,
                option.minecraft_version,
                loader_version=self._selected_loader_version(option),
                operation=operation,
            )
        except Exception as exc:
            message = self.trans("minecraft_components_install_failed", version=option.name, error=str(exc))
            operation.fail(message, notify=False)
            self.app.feedback.warning(message)
            return
        finally:
            self.operation_pending = False
        operation.finish(self.trans("minecraft_components_install_complete", version=option.name), show_success=False)
        self.options_by_tab.pop(self.active_install_tab, None)
        self.refresh_installed(update=False)
        self.app.feedback.info(self.trans("minecraft_components_install_complete", version=option.name))
        schedule_update(self.page)

    def delete_component(self, component: InstalledComponent) -> None:
        try:
            self.service.delete_component(component.version_id)
        except Exception as exc:
            self.app.feedback.warning(self.trans("minecraft_components_delete_failed", version=component.version_id, error=str(exc)))
            return
        self.refresh_installed(update=False)
        self.app.feedback.info(self.trans("minecraft_components_delete_complete", version=component.version_id))
        schedule_update(self.page)

    def open_component_directory(self, component: InstalledComponent) -> None:
        response = self.app.util.open_mc_dir(str(component.path))
        if response is not None:
            self.app.feedback.info(response)

    def _guard_operation(self) -> bool:
        if self.operation_pending or self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return True
        return False

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

    @staticmethod
    def _loader_icon(kind: str) -> str:
        return {
            "minecraft": ft.Icons.GRASS,
            "fabric": ft.Icons.EXTENSION,
            "forge": ft.Icons.BUILD,
            "neoforge": ft.Icons.CONSTRUCTION,
            "quilt": ft.Icons.GRID_VIEW,
        }.get(kind, ft.Icons.VIDEOGAME_ASSET)

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

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


__all__ = ["MinecraftComponentsPage"]
