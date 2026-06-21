from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def Icon(
    name: Any | None = None,
    *,
    color: str | None = None,
    size: int | float | None = None,
    **kwargs: Any,
) -> ft.Icon:
    theme = current_theme()
    merged = {
        "icon": name,
        "color": color or theme.text_color,
        "size": size or theme.icon_size,
        **kwargs,
    }
    return ft.Icon(**filter_control_kwargs(ft.Icon, merged))


__all__ = ["Icon"]
