from __future__ import annotations

from typing import Any

import flet as ft

from launcher import ui


class ModsManagerCards:
    def __init__(self, app) -> None:
        self.app = app
        self.trans = app.trans

    def _action_button(
        self,
        *,
        icon: str,
        on_click,
        tooltip: str,
        color: str,
    ) -> ft.FloatingActionButton:
        return ui.FloatingActionButton(
            icon=icon,
            on_click=on_click,
            tooltip=tooltip,
            mini=True,
            bgcolor=self.app.theme.bg_primary,
            foreground_color=color,
        )

    def _toggle_action_color(self, enabled: bool) -> str:
        return self.app.theme.success if enabled else self.app.theme.text_disabled

    def tab_button(self, tab_data: dict[str, Any], *, is_active: bool, on_click) -> ui.Container:
        indicator = ui.Container(
            height=2,
            border_radius=ft.BorderRadius.all(2),
            bgcolor=self.app.theme.primary if is_active else ft.Colors.TRANSPARENT,
        )
        return ui.Container(
            ui.Column(
                controls=[
                    ui.Container(
                        content=ui.Row(
                            [
                                ui.Icon(
                                    tab_data["icon"],
                                    size=16,
                                    color=self.app.theme.primary if is_active else self.app.theme.text_secondary,
                                ),
                                ui.Text(
                                    tab_data["text"],
                                    size=self.app.theme.text_size_xs,
                                    text_align=ft.TextAlign.CENTER,
                                    weight=ft.FontWeight.W_500,
                                    color=self.app.theme.text_color if is_active else self.app.theme.text_secondary,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                    ),
                    indicator,
                ],
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
            alignment=ft.Alignment.CENTER,
            expand=1,
            height=self.app.theme.tab_height,
            padding=ft.Padding.only(left=12, top=5, right=12, bottom=4),
            bgcolor=ft.Colors.with_opacity(0.10, self.app.theme.primary) if is_active else ft.Colors.TRANSPARENT,
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm - 2),
            on_click=on_click,
            ink=False,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )

    def resourcepack_card(self, resourcepack: dict[str, Any], *, on_toggle, on_open_folder, on_delete) -> ui.Container:
        pack_name = resourcepack["filename"]
        pack_type = "📁 Папка" if resourcepack["type"] == "resourcepack_folder" else "📦 ZIP"
        status_icon = ft.Icons.CHECK_CIRCLE if resourcepack.get("enabled", True) else ft.Icons.CANCEL
        status_color = self.app.theme.success if resourcepack.get("enabled", True) else self.app.theme.text_disabled
        size_mb = resourcepack["size"] / (1024 * 1024)

        return ui.Container(
            ui.Row(
                [
                    ui.Column(
                        [
                            ui.Row(
                                [
                                    ui.Icon(status_icon, size=16, color=status_color),
                                    ui.Icon(ft.Icons.PALETTE, size=16, color=self.app.theme.primary),
                                    ui.Text(
                                        pack_name,
                                        size=self.app.theme.text_size_medium,
                                        weight=ft.FontWeight.W_600,
                                        color=self.app.theme.text_color,
                                    ),
                                ],
                                spacing=8,
                            ),
                            ui.Text(
                                f"{pack_type} • {size_mb:.1f} MB",
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_disabled,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ui.Row(
                        [
                            self._action_button(
                                icon=ft.Icons.POWER_SETTINGS_NEW if resourcepack.get("enabled", True) else ft.Icons.PLAY_ARROW,
                                on_click=on_toggle,
                                tooltip=(
                                    self.trans("disable_resourcepack")
                                    if resourcepack.get("enabled", True)
                                    else self.trans("enable_resourcepack")
                                ),
                                color=self._toggle_action_color(resourcepack.get("enabled", True)),
                            ),
                            self._action_button(
                                icon=ft.Icons.FOLDER,
                                on_click=on_open_folder,
                                tooltip=self.trans("open_directory"),
                                color=self.app.theme.text_secondary,
                            ),
                            self._action_button(
                                icon=ft.Icons.DELETE,
                                on_click=on_delete,
                                tooltip=self.trans("delete"),
                                color=self.app.theme.error,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def installed_content_card(
        self,
        item: dict[str, Any],
        *,
        icon: str,
        enable_tooltip: str,
        disable_tooltip: str,
        show_toggle: bool = True,
        on_toggle,
        on_delete,
    ) -> ui.Container:
        item_name = item.get("name") or item["filename"]
        status_icon = ft.Icons.CHECK_CIRCLE if item.get("enabled", True) else ft.Icons.CANCEL
        status_color = self.app.theme.success if item.get("enabled", True) else self.app.theme.text_disabled
        size_mb = item["size"] / (1024 * 1024)
        actions: list[ft.Control] = []
        if show_toggle:
            actions.append(
                self._action_button(
                    icon=ft.Icons.POWER_SETTINGS_NEW if item.get("enabled", True) else ft.Icons.PLAY_ARROW,
                    on_click=on_toggle,
                    tooltip=disable_tooltip if item.get("enabled", True) else enable_tooltip,
                    color=self._toggle_action_color(item.get("enabled", True)),
                )
            )
        actions.append(
            self._action_button(
                icon=ft.Icons.DELETE,
                on_click=on_delete,
                tooltip=self.trans("delete"),
                color=self.app.theme.error,
            )
        )

        return ui.Container(
            ui.Row(
                [
                    ui.Column(
                        [
                            ui.Row(
                                [
                                    ui.Icon(status_icon, size=16, color=status_color),
                                    ui.Icon(icon, size=16, color=self.app.theme.primary),
                                    ui.Text(
                                        item_name,
                                        size=self.app.theme.text_size_medium,
                                        weight=ft.FontWeight.W_600,
                                        color=self.app.theme.text_color,
                                    ),
                                ],
                                spacing=8,
                            ),
                            ui.Text(
                                f"{item['filename']} • {size_mb:.1f} MB",
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_disabled,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ui.Row(actions, spacing=8),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def installed_mod_card(
        self,
        mod: dict[str, Any],
        *,
        has_backup: bool,
        on_update,
        on_restore,
        on_toggle,
        on_delete,
    ) -> ui.Container:
        mod_name = mod.get("name") or mod["filename"]
        mod_version = mod.get("version", "")
        mod_description = mod.get("description", "")
        status_icon = ft.Icons.CHECK_CIRCLE if mod["enabled"] else ft.Icons.CANCEL
        status_color = self.app.theme.success if mod["enabled"] else self.app.theme.text_disabled
        size_mb = mod["size"] / (1024 * 1024)

        version_row = [
            ui.Icon(status_icon, size=16, color=status_color),
            ui.Text(
                mod_name,
                size=self.app.theme.text_size_medium,
                weight=ft.FontWeight.W_600,
                color=self.app.theme.text_color,
            ),
        ]
        if mod_version:
            version_row.append(
                ui.Text(
                    mod_version,
                    size=self.app.theme.text_size_xs,
                    color=self.app.theme.text_secondary,
                )
            )

        text_column = [ui.Row(version_row, spacing=8)]
        if mod_description:
            text_column.append(
                ui.Text(
                    mod_description[:100] + ("..." if len(mod_description) > 100 else ""),
                    size=self.app.theme.text_size_xs,
                    color=self.app.theme.text_tertiary,
                )
            )
        text_column.append(
            ui.Text(
                f"{mod['filename']} • {size_mb:.1f} MB",
                size=self.app.theme.text_size_xs,
                color=self.app.theme.text_disabled,
            )
        )

        actions = [
            self._action_button(
                icon=ft.Icons.UPDATE,
                on_click=on_update,
                tooltip=self.trans("update_mod"),
                color=self.app.theme.info,
            ) if mod.get("update_available") else None,
            self._action_button(
                icon=ft.Icons.RESTORE,
                on_click=on_restore,
                tooltip=self.trans("restore_backup"),
                color=self.app.theme.primary,
            ) if has_backup else None,
            self._action_button(
                icon=ft.Icons.POWER_SETTINGS_NEW if mod["enabled"] else ft.Icons.PLAY_ARROW,
                on_click=on_toggle,
                tooltip=self.trans("disable_mod") if mod["enabled"] else self.trans("enable_mod"),
                color=self._toggle_action_color(mod["enabled"]),
            ),
            self._action_button(
                icon=ft.Icons.DELETE,
                on_click=on_delete,
                tooltip=self.trans("delete"),
                color=self.app.theme.error,
            ),
        ]

        return ui.Container(
            ui.Row(
                [
                    ui.Column(text_column, spacing=4, expand=True),
                    ui.Row([action for action in actions if action is not None], spacing=8),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def search_result_card(
        self,
        mod: dict[str, Any],
        *,
        installed: bool,
        update_available: bool = False,
        on_install,
        on_open_site=None,
    ) -> ui.Container:
        mod_name = mod.get("title", "Unknown")
        mod_author = mod.get("author", "Unknown")
        mod_description = mod.get("description", "")
        mod_downloads = mod.get("downloads", 0)
        mod_icon = mod.get("icon_url", "")

        return ui.Container(
            ui.Row(
                [
                    ui.Image(
                        src=mod_icon if mod_icon else None,
                        width=48,
                        height=48,
                        fit=ft.BoxFit.COVER,
                        border_radius=ft.BorderRadius.all(8),
                    ) if mod_icon else ui.Icon(ft.Icons.EXTENSION, size=48),
                    ui.Column(
                        [
                            ui.Text(
                                mod_name,
                                size=self.app.theme.text_size_medium,
                                weight=ft.FontWeight.W_600,
                                color=self.app.theme.text_color,
                            ),
                            ui.Text(
                                f"by {mod_author} • {mod_downloads:,} downloads",
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_secondary,
                            ),
                        ] + (
                            [
                                ui.Text(
                                    mod_description[:150] + ("..." if len(mod_description) > 150 else ""),
                                    size=self.app.theme.text_size_xs,
                                    color=self.app.theme.text_tertiary,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                )
                            ]
                            if mod_description
                            else []
                        ),
                        spacing=4,
                        expand=True,
                    ),
                    ui.Row(
                        [
                            ui.IconButton(
                                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                tooltip=self.trans("open_on_site"),
                                width=self.app.theme.button_height_for_size("sm"),
                                height=self.app.theme.button_height_for_size("sm"),
                                icon_size=self.app.theme.icon_size_sm,
                                on_click=on_open_site,
                            ),
                            ui.Button(
                                text=(
                                    self.trans("update_modrinth_content")
                                    if update_available
                                    else self.trans("installed") if installed else self.trans("install")
                                ),
                                on_click=on_install if not installed or update_available else None,
                                icon=ft.Icons.UPDATE if update_available else ft.Icons.CHECK if installed else ft.Icons.DOWNLOAD,
                                disabled=installed and not update_available,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )
