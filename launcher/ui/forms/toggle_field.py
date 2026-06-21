from __future__ import annotations

import flet as ft

from ..controls.switch import Switch
from ..controls.text import Text
from ..layout.container import Container
from ..layout.row import Row
from ..theme import current_theme


def ToggleField(*, label: str, value: bool, on_change):
    theme = current_theme()
    return Container(
        height=theme.input_height,
        bgcolor=theme.overlay(theme.alpha_input, theme.bg_list),
        border=ft.Border.all(1, theme.border_color),
        border_radius=theme.radius(),
        padding=theme.field_shell_padding(),
        alignment=ft.Alignment.CENTER,
        content=Row(
            controls=[
                Container(
                    content=Text(
                        label,
                        size=theme.text_size_xs,
                        color=theme.text_color,
                        weight=theme.font_weight_medium,
                    ),
                    expand=True,
                ),
                Switch(value=value, on_change=on_change),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


__all__ = ["ToggleField"]
