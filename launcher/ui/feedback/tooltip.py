from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def Tooltip(
    message: str | None = None,
    *,
    content: ft.Control | None = None,
    bgcolor: str | None = None,
    text_style: ft.TextStyle | None = None,
    border: ft.Border | None = None,
    border_radius: int | ft.BorderRadius | None = None,
    **kwargs: Any,
) -> ft.Tooltip | ft.Container:
    theme = current_theme()
    params = {
        "message": message,
        "decoration": ft.BoxDecoration(
            bgcolor=bgcolor or theme.bg_tooltip,
            border=border,
            border_radius=border_radius or theme.radius(md=True),
        ),
        "text_style": text_style or theme.text_style(size=theme.text_size_small, color=theme.text_color),
        **kwargs,
    }
    tooltip = ft.Tooltip(**filter_control_kwargs(ft.Tooltip, params))
    if content is None:
        return tooltip
    return ft.Container(content=content, tooltip=tooltip)


__all__ = ["Tooltip"]
