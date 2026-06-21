from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..core.click_sound import wrap_click_handler
from ..theme import current_theme


def IconButton(
    icon: str | ft.Control | None = None,
    *,
    selected: bool | None = None,
    selected_icon: str | ft.Control | None = None,
    selected_icon_color: str | None = None,
    icon_size: int | float | None = None,
    icon_color: str | None = None,
    variant: str = "ghost",
    active: bool | None = None,
    style: ft.ButtonStyle | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
    tooltip: str | None = None,
    on_click: Any | None = None,
    on_long_press: Any | None = None,
    on_hover: Any | None = None,
    **kwargs: Any,
) -> ft.IconButton:
    theme = current_theme()
    is_active = bool(active if active is not None else selected)
    button_style = style or theme.icon_button_style(active=is_active)
    if variant == "solid":
        button_style.bgcolor = {
            ft.ControlState.DEFAULT: theme.overlay(0.12, theme.primary),
            ft.ControlState.HOVERED: theme.overlay(0.2, theme.primary),
        }
    merged: dict[str, Any] = {
        "icon": icon,
        "selected": selected,
        "selected_icon": selected_icon,
        "selected_icon_color": selected_icon_color,
        "icon_size": icon_size or theme.icon_size,
        "icon_color": icon_color,
        "style": button_style,
        "width": width,
        "height": height,
        "tooltip": tooltip,
        "on_click": wrap_click_handler(on_click),
        "on_long_press": on_long_press,
        "on_hover": on_hover,
        **kwargs,
    }
    return ft.IconButton(**filter_control_kwargs(ft.IconButton, merged))


__all__ = ["IconButton"]
