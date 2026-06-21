from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def calc_input_padding(total_height: float, text_size: float = 14) -> ft.Padding:
    line_height = text_size * 1.3
    free_vertical = max(total_height - line_height, 0)
    pad_vertical = free_vertical / 2
    return ft.Padding(12, pad_vertical, 12, pad_vertical)


def TextField(
    value: str | None = None,
    *,
    label: str | ft.Control | None = None,
    hint_text: str | None = None,
    variant: str = "filled",
    size: str = "md",
    dense: bool | None = None,
    password: bool | None = None,
    can_reveal_password: bool | None = None,
    multiline: bool | None = None,
    min_lines: int | None = None,
    max_lines: int | None = None,
    max_length: int | None = None,
    read_only: bool | None = None,
    disabled: bool | None = None,
    autofocus: bool | None = None,
    keyboard_type: ft.KeyboardType | None = None,
    text_align: ft.TextAlign | None = None,
    text_size: int | float | None = None,
    text_style: ft.TextStyle | None = None,
    label_style: ft.TextStyle | None = None,
    hint_style: ft.TextStyle | None = None,
    color: str | None = None,
    bgcolor: str | None = None,
    border_color: str | None = None,
    focused_border_color: str | None = None,
    border_radius: int | ft.BorderRadius | None = None,
    border_width: int | float | None = None,
    filled: bool | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
    prefix: str | ft.Control | None = None,
    suffix: str | ft.Control | None = None,
    prefix_icon: str | ft.Control | None = None,
    suffix_icon: str | ft.Control | None = None,
    on_change: Any | None = None,
    on_submit: Any | None = None,
    on_focus: Any | None = None,
    on_blur: Any | None = None,
    **kwargs: Any,
) -> ft.TextField:
    theme = current_theme()
    prefix_text = kwargs.pop("prefix_text", None)
    suffix_text = kwargs.pop("suffix_text", None)
    helper_text = kwargs.pop("helper_text", None)
    error_text = kwargs.pop("error_text", None)
    counter_text = kwargs.pop("counter_text", None)
    content_padding = kwargs.pop("content_padding", None)
    actual_dense = True if dense is None else dense
    actual_height = float(height if height is not None else theme.input_height + (6 if size == "lg" else -4 if size == "sm" else 0))
    actual_text_size = float(text_size if text_size is not None else theme.text_size_sm)
    background = bgcolor or theme.overlay(theme.alpha_input, theme.bg_list)
    merged: dict[str, Any] = {
        "value": value or "",
        "label": label,
        "hint_text": hint_text,
        "password": password,
        "can_reveal_password": can_reveal_password,
        "multiline": multiline,
        "min_lines": min_lines,
        "max_lines": max_lines,
        "max_length": max_length,
        "read_only": read_only,
        "disabled": disabled,
        "autofocus": autofocus,
        "keyboard_type": keyboard_type,
        "text_align": text_align,
        "text_size": actual_text_size,
        "text_style": text_style or theme.text_style(size=round(actual_text_size), color=color),
        "label_style": label_style or theme.text_style(size=theme.text_size_xs, color=theme.text_secondary, weight=theme.font_weight_medium),
        "hint_style": hint_style or theme.text_style(size=theme.text_size_sm, color=theme.overlay(0.75, theme.text_tertiary)),
        "helper_style": theme.text_style(size=theme.text_size_xs, color=theme.text_secondary),
        "counter_style": theme.text_style(size=theme.text_size_xs, color=theme.text_secondary),
        "prefix_style": theme.text_style(size=theme.text_size_sm, color=color),
        "suffix_style": theme.text_style(size=theme.text_size_sm, color=color),
        "color": color or theme.text_color,
        "bgcolor": background,
        "fill_color": background,
        "border_color": border_color or theme.overlay(0.6, theme.text_tertiary),
        "focused_border_color": focused_border_color or theme.primary_light,
        "focused_color": theme.text_color,
        "border_radius": border_radius or theme.radius_sm,
        "border_width": border_width,
        "filled": variant != "ghost" if filled is None else filled,
        "dense": actual_dense,
        "width": width,
        "height": actual_height,
        "prefix": prefix,
        "suffix": suffix,
        "prefix_icon": prefix_icon,
        "suffix_icon": suffix_icon,
        "on_change": on_change,
        "on_submit": on_submit,
        "on_focus": on_focus,
        "on_blur": on_blur,
        "text_vertical_align": ft.VerticalAlignment.CENTER,
        "fit_parent_size": kwargs.pop("fit_parent_size", True),
        **kwargs,
    }
    if variant == "ghost":
        merged["filled"] = False
        merged["bgcolor"] = ft.Colors.TRANSPARENT
        merged["fill_color"] = ft.Colors.TRANSPARENT
    if merged.get("prefix") is None and prefix_text is not None:
        merged["prefix"] = prefix_text
    if merged.get("suffix") is None and suffix_text is not None:
        merged["suffix"] = suffix_text
    if merged.get("helper") is None and helper_text is not None:
        merged["helper"] = helper_text
    if merged.get("error") is None and error_text is not None:
        merged["error"] = error_text
    if merged.get("counter") is None and counter_text is not None:
        merged["counter"] = counter_text
    if content_padding is not None:
        merged["content_padding"] = content_padding
    elif not merged.get("multiline"):
        merged["content_padding"] = calc_input_padding(actual_height, actual_text_size)
    return ft.TextField(**filter_control_kwargs(ft.TextField, merged))


__all__ = ["TextField", "calc_input_padding"]
