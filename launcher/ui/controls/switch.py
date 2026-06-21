from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def Switch(
    value: bool | None = None,
    *,
    label: str | ft.Control | None = None,
    label_style: ft.TextStyle | None = None,
    scale: float | None = None,
    on_change: Any | None = None,
    **kwargs: Any,
) -> ft.Switch:
    theme = current_theme()
    merged = {
        "value": bool(value),
        "label": label,
        "label_text_style": label_style or theme.text_style(size=theme.text_size_sm, color=theme.text_color),
        "scale": scale if scale is not None else theme.switch_scale(),
        "on_change": on_change,
        **kwargs,
    }
    return ft.Switch(**filter_control_kwargs(ft.Switch, merged))


__all__ = ["Switch"]
