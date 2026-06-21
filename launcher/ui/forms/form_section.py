from __future__ import annotations

import flet as ft

from ..controls.text import Text
from ..layout.column import Column
from ..layout.container import Container
from ..layout.responsive_row import ResponsiveRow
from ..theme import current_theme
from .toggle_field import ToggleField


class FormSection:
    def __init__(self, app) -> None:
        self.app = app

    def expand_controls(self, *controls) -> None:
        for control in controls:
            if hasattr(control, "width"):
                control.width = None
            if hasattr(control, "expand"):
                control.expand = True

    def wrap_control(self, control, col: dict[str, int] | None = None):
        return Container(
            content=control,
            col=col or {"sm": 12},
            padding=ft.Padding.only(top=4, bottom=4),
        )

    def section(self, *, title: str, controls: list[ft.Control], description: str | None = None) -> ft.Container:
        theme = current_theme()
        items: list[ft.Control] = [
            Text(title, size=theme.text_size_xl, weight=theme.font_weight_bold, color=theme.text_color)
        ]
        if description:
            items.append(Text(description, size=theme.text_size_sm, color=theme.text_secondary))
        items.append(
            ResponsiveRow(
                controls=controls,
                columns=12,
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        )
        return Container(
            bgcolor=theme.bg_list,
            border=ft.Border.all(1, theme.border_color),
            border_radius=theme.radius(md=True),
            padding=ft.Padding.all(theme.section_padding),
            content=Column(items, spacing=theme.spacing_md, tight=True),
        )

    def toggle_field(self, *, label: str, value: bool, on_change) -> ft.Control:
        return ToggleField(label=label, value=value, on_change=on_change)


__all__ = ["FormSection"]
