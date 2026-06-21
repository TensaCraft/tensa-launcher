from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import flet as ft

from ..controls.text_field import TextField
from ..layout.row import Row
from ..theme import current_theme


@dataclass(slots=True)
class SearchFieldParts:
    field: ft.TextField
    row: ft.Row


def build_search_field(
    app,
    *,
    label: str,
    value: str = "",
    on_submit: Any | None = None,
    on_change: Any | None = None,
    autofocus: bool = False,
    height: int | float | None = None,
) -> SearchFieldParts:
    theme = current_theme()
    field = TextField(
        hint_text=label,
        value=value,
        on_submit=on_submit,
        on_change=on_change,
        autofocus=autofocus,
        height=height or theme.search_input_height,
        expand=1,
        width=None,
        fit_parent_size=False,
        border_color=theme.overlay(0.92, theme.border_light_color),
        focused_border_color=theme.primary,
        bgcolor=theme.overlay(0.98, theme.bg_list),
        content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
        prefix_icon=ft.Icons.SEARCH,
        prefix_icon_size_constraints=ft.BoxConstraints(min_width=28, min_height=28),
    )
    field.width = None
    return SearchFieldParts(field=field, row=Row([field], spacing=0))


__all__ = ["SearchFieldParts", "build_search_field"]
