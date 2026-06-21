from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def Checkbox(
    value: bool | None = None,
    *,
    label: str | ft.Control | None = None,
    label_style: ft.TextStyle | None = None,
    on_change: Any | None = None,
    **kwargs: Any,
) -> ft.Checkbox:
    theme = current_theme()
    merged = {
        "value": value,
        "label": label,
        "label_style": label_style or theme.text_style(
            size=theme.text_size_sm,
            color=theme.text_color,
            weight=theme.font_weight_medium,
        ),
        "on_change": on_change,
        **kwargs,
    }
    return ft.Checkbox(**filter_control_kwargs(ft.Checkbox, merged))


__all__ = ["Checkbox"]
