from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from ..controls.icon import Icon
from ..controls.text import Text
from ..layout.column import Column
from ..layout.container import Container
from ..layout.row import Row
from ..theme import current_theme


def TabButton(text: str, icon: ft.IconData, *, selected: bool, on_click: Callable[..., Any]) -> ft.Container:
    theme = current_theme()
    return Container(
        Column(
            controls=[
                Container(
                    content=Row(
                        [
                            Icon(icon, size=16, color=theme.primary if selected else theme.text_secondary),
                            Text(
                                text,
                                size=theme.text_size_xs,
                                text_align=ft.TextAlign.CENTER,
                                weight=ft.FontWeight.W_500,
                                color=theme.text_color if selected else theme.text_secondary,
                                expand=True,
                                expand_loose=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    expand=True,
                    alignment=ft.Alignment.CENTER,
                ),
                Container(
                    height=2,
                    border_radius=ft.BorderRadius.all(2),
                    bgcolor=theme.primary if selected else ft.Colors.TRANSPARENT,
                ),
            ],
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        ),
        alignment=ft.Alignment.CENTER,
        expand=1,
        height=theme.tab_height,
        padding=ft.Padding.only(left=12, top=5, right=12, bottom=4),
        bgcolor=ft.Colors.with_opacity(0.10, theme.primary) if selected else ft.Colors.TRANSPARENT,
        border_radius=ft.BorderRadius.all(theme.radius_sm - 2),
        on_click=on_click,
        tooltip=text,
        ink=False,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )


def TabBar(controls: list[ft.Control]) -> ft.Container:
    theme = current_theme()
    return Container(
        content=Row(controls, spacing=0, expand=True),
        padding=2,
        bgcolor=ft.Colors.with_opacity(0.08, theme.bg_header_footer),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.18, theme.text_tertiary)),
        border_radius=ft.BorderRadius.all(theme.radius_sm),
    )


__all__ = ["TabBar", "TabButton"]
