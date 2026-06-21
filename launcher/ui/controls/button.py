from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..core.click_sound import wrap_click_handler
from ..theme import current_theme


def _override_style(style: ft.ButtonStyle, *, bgcolor: str | None, color: str | None, icon_color: str | None) -> ft.ButtonStyle:
    theme = current_theme()
    if bgcolor is not None:
        style.bgcolor = {
            ft.ControlState.DEFAULT: bgcolor,
            ft.ControlState.HOVERED: bgcolor,
            ft.ControlState.DISABLED: ft.Colors.with_opacity(0.25, bgcolor),
        }
    if color is not None:
        style.color = {ft.ControlState.DEFAULT: color}
        style.text_style = {ft.ControlState.DEFAULT: ft.TextStyle(color=color, font_family=theme.font_family)}
    if icon_color is not None:
        style.icon_color = {ft.ControlState.DEFAULT: icon_color}
    return style


def Button(
    text: str | None = None,
    *,
    content: str | ft.Control | None = None,
    icon: Any | None = None,
    variant: str = "filled",
    tone: str = "primary",
    size: str = "md",
    style: ft.ButtonStyle | None = None,
    color: str | None = None,
    icon_color: str | None = None,
    bgcolor: str | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
    disabled: bool | None = None,
    visible: bool | None = None,
    opacity: float | None = None,
    tooltip: str | None = None,
    on_click: Any | None = None,
    on_long_press: Any | None = None,
    on_hover: Any | None = None,
    **kwargs: Any,
) -> ft.Button:
    theme = current_theme()
    button_style = style or theme.button_style(variant=variant, tone=tone, size=size)
    button_style = _override_style(button_style, bgcolor=bgcolor, color=color, icon_color=icon_color)
    merged: dict[str, Any] = {
        "content": content if content is not None else text,
        "icon": icon,
        "style": button_style,
        "height": height if height is not None else theme.button_height_for_size(size),
        "width": width,
        "disabled": disabled,
        "visible": visible,
        "opacity": opacity,
        "tooltip": tooltip,
        "on_click": wrap_click_handler(on_click),
        "on_long_press": on_long_press,
        "on_hover": on_hover,
        **kwargs,
    }
    return ft.Button(**filter_control_kwargs(ft.Button, merged))


__all__ = ["Button"]
