from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme
from .text_field import calc_input_padding


def Dropdown(
    value: str | None = None,
    *,
    options: list[ft.dropdown.Option] | None = None,
    label: str | ft.Control | None = None,
    hint_text: str | None = None,
    variant: str = "filled",
    size: str = "md",
    dense: bool | None = None,
    icon: str | ft.Control | None = None,
    autofocus: bool | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
    color: str | None = None,
    bgcolor: str | None = None,
    border_color: str | None = None,
    focused_border_color: str | None = None,
    border_radius: int | ft.BorderRadius | None = None,
    border_width: int | float | None = None,
    filled: bool | None = None,
    disabled: bool | None = None,
    visible: bool | None = None,
    opacity: float | None = None,
    on_change: Any | None = None,
    on_focus: Any | None = None,
    on_blur: Any | None = None,
    **kwargs: Any,
) -> ft.Dropdown:
    theme = current_theme()
    actual_height = height if height is not None else theme.input_height + (6 if size == "lg" else -4 if size == "sm" else 0)
    actual_dense = True if dense is None else dense
    actual_text_size = float(theme.text_size_sm)
    background = bgcolor or theme.overlay(theme.alpha_input, theme.bg_list)
    merged: dict[str, Any] = {
        "value": value,
        "options": options,
        "label": label,
        "hint_text": hint_text,
        "leading_icon": icon,
        "autofocus": autofocus,
        "width": width,
        "height": actual_height,
        "text_size": actual_text_size,
        "text_style": theme.text_style(size=round(actual_text_size), color=color),
        "label_style": theme.text_style(size=theme.text_size_xs, color=theme.text_secondary, weight=theme.font_weight_medium),
        "hint_style": theme.text_style(size=theme.text_size_sm, color=theme.overlay(0.75, theme.text_tertiary)),
        "helper_style": theme.text_style(size=theme.text_size_xs, color=theme.text_secondary),
        "color": color or theme.text_color,
        "bgcolor": background,
        "fill_color": background,
        "border_color": border_color or theme.overlay(0.6, theme.text_tertiary),
        "focused_border_color": focused_border_color or theme.primary_light,
        "border_radius": border_radius or theme.radius_sm,
        "border_width": border_width,
        "filled": variant != "ghost" if filled is None else filled,
        "dense": actual_dense,
        "disabled": disabled,
        "visible": visible,
        "opacity": opacity,
        "on_select": on_change,
        "on_focus": on_focus,
        "on_blur": on_blur,
        "content_padding": calc_input_padding(float(actual_height), actual_text_size),
        **kwargs,
    }
    if variant == "ghost":
        merged["filled"] = False
        merged["bgcolor"] = ft.Colors.TRANSPARENT
        merged["fill_color"] = ft.Colors.TRANSPARENT
    return ft.Dropdown(**filter_control_kwargs(ft.Dropdown, merged))


__all__ = ["Dropdown"]
