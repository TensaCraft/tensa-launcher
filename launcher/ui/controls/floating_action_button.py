from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..core.click_sound import wrap_click_handler
from ..theme import current_theme


def FloatingActionButton(
    content: str | ft.Control | None = None,
    *,
    text: str | None = None,
    icon: str | ft.Control | None = None,
    tone: str = "neutral",
    mini: bool = False,
    bgcolor: str | None = None,
    foreground_color: str | None = None,
    tooltip: str | None = None,
    on_click: Any | None = None,
    **kwargs: Any,
) -> ft.FloatingActionButton:
    theme = current_theme()
    merged: dict[str, Any] = {
        "content": content if content is not None else text,
        "icon": icon,
        "mini": mini,
        "bgcolor": bgcolor or (theme.primary if tone == "primary" else theme.bg_primary),
        "foreground_color": foreground_color or (theme.bg_primary if tone == "primary" else theme.text_color),
        "tooltip": tooltip,
        "on_click": wrap_click_handler(on_click),
        **kwargs,
    }
    return ft.FloatingActionButton(**filter_control_kwargs(ft.FloatingActionButton, merged))


__all__ = ["FloatingActionButton"]
