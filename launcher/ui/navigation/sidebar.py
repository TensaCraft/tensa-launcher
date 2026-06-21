from __future__ import annotations

import flet as ft

from launcher import APP_NAME

from ..controls.icon import Icon
from ..controls.image import Image
from ..controls.text import Text
from ..core.page_runtime import invoke_on_ui
from ..layout.column import Column
from ..layout.container import Container
from ..layout.row import Row
from ..theme import current_theme


class Sidebar:
    def __init__(self, app) -> None:
        self.app = app
        self.selected_index = 0
        self.destinations = []
        self.main_items = []
        self.bottom_items = []

    def set_destinations(self, destinations: list[dict]):
        self.destinations = destinations
        self.main_items = []
        self.bottom_items = []
        for index, dest in enumerate(destinations):
            if dest.get("section") == "bottom":
                self.bottom_items.append((index, dest))
            else:
                self.main_items.append((index, dest))

    def set_selected_index(self, index: int):
        self.selected_index = index

    def is_collapsed(self) -> bool:
        return self.app.config.get("compact_sidebar", "yes") == "yes"

    def current_width(self) -> int:
        theme = current_theme()
        if not self.is_collapsed():
            return theme.sidebar_width
        return max(theme.icon_size_lg + theme.padding_2xl * 2, theme.header_height + theme.padding_xs)

    def _create_nav_button(self, index: int, dest: dict, is_selected: bool):
        theme = current_theme()
        collapsed = self.is_collapsed()
        label = dest.get("label", "")
        icon = dest.get("selected_icon" if is_selected else "icon", ft.Icons.HOME)
        text_color = theme.primary if is_selected else theme.text_color
        bg_color = theme.overlay(0.08, theme.primary) if is_selected else ft.Colors.TRANSPARENT
        controls: list[ft.Control] = [Icon(icon, size=24, color=text_color)]
        if not collapsed:
            controls.append(
                Text(
                    label,
                    size=theme.text_size_sm,
                    weight=theme.font_weight_semibold if is_selected else ft.FontWeight.W_400,
                    color=text_color,
                )
            )
        return Container(
            content=Container(
                content=Row(
                    controls=controls,
                    spacing=12 if not collapsed else 0,
                    alignment=ft.MainAxisAlignment.CENTER if collapsed else ft.MainAxisAlignment.START,
                ),
                padding=ft.Padding.symmetric(horizontal=theme.padding_md if not collapsed else theme.padding_sm, vertical=12),
                border_radius=theme.radius(),
                bgcolor=bg_color,
                ink=True,
                tooltip=label if collapsed else None,
                on_click=lambda e, i=index: self._on_button_click(e, i),
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=4),
        )

    def _on_button_click(self, e, index: int):
        self.selected_index = index
        if self.destinations and 0 <= index < len(self.destinations):
            on_click = self.destinations[index].get("on_click")
            if on_click:
                on_click(e)

    def _create_header(self):
        theme = current_theme()
        collapsed = self.is_collapsed()
        logo_path = self.app.util.get_resource_path("logo.ico")
        logo_widget = (
            Image(src=str(logo_path), width=40, height=40, fit=ft.BoxFit.CONTAIN)
            if logo_path
            else Icon(ft.Icons.ROCKET_LAUNCH, size=32, color=theme.primary)
        )
        launcher_name = self.app.util.launcher_name or APP_NAME
        if collapsed:
            return Container(
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding.all(16),
                content=Container(
                    content=logo_widget,
                    alignment=ft.Alignment.CENTER,
                    tooltip=launcher_name,
                ),
            )
        return Container(
            content=Row(
                controls=[
                    logo_widget,
                    Text(
                        launcher_name,
                        size=theme.text_size_xl,
                        weight=theme.font_weight_bold,
                        color=theme.text_color,
                    ),
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.START,
            ),
            padding=ft.Padding.all(16),
        )

    def _create_user_block(self):
        theme = current_theme()
        collapsed = self.is_collapsed()
        profile = self.app.profiles.get_default_profile()
        if profile is not None:
            avatar_id = profile.get("id") or profile.get("name")
            avatar_src = self.app.util.get_cached_skin_url(avatar_id)
            self.app.util.prefetch_skin(
                avatar_id,
                on_ready=lambda _src: invoke_on_ui(self.app.page, self.app.refresh_shell),
            )
            avatar = Image(
                src=avatar_src,
                width=24,
                height=24,
                fit=ft.BoxFit.COVER,
                border_radius=4,
                error_content=Icon(ft.Icons.ACCOUNT_CIRCLE, size=24, color=theme.text_color),
            )
            username = profile.get("name", "Unknown")
        else:
            avatar = Icon(ft.Icons.ACCOUNT_CIRCLE, size=24, color=theme.text_color)
            username = self.app.trans("profile_empty")
        if collapsed:
            return Container(
                alignment=ft.Alignment.CENTER,
                content=Container(
                    content=avatar,
                    padding=ft.Padding.all(theme.padding_sm),
                    border_radius=theme.radius(),
                    tooltip=username,
                    ink=True,
                    on_click=lambda _e: self.app.show_profiles_page(),
                ),
            )
        return Container(
            content=Row(
                controls=[
                    avatar,
                    Text(username, size=theme.text_size_sm, color=theme.text_color, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=12,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=12),
            border_radius=theme.radius(),
            ink=True,
            on_click=lambda _e: self.app.show_profiles_page(),
        )

    def view(self):
        theme = current_theme()
        all_controls: list[ft.Control] = [self._create_header()]
        if self.main_items:
            all_controls.append(
                Container(
                    content=Column(
                        controls=[self._create_nav_button(index, dest, index == self.selected_index) for index, dest in self.main_items],
                        spacing=0,
                    ),
                    padding=ft.Padding.only(top=8),
                )
            )
        all_controls.append(Container(expand=True))
        all_controls.append(
            Container(
                content=self._create_user_block(),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )
        )
        return Container(
            content=Column(
                controls=all_controls,
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            width=self.current_width(),
            bgcolor=theme.overlay(theme.alpha_header_footer, theme.bg_header_footer),
        )


__all__ = ["Sidebar"]
