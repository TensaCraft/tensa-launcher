from __future__ import annotations

from typing import Any

import flet as ft

from ..theme import current_theme


def Text(
    value: str | None = None,
    *,
    size: int | float | None = None,
    color: str | None = None,
    weight: ft.FontWeight | None = None,
    font_family: str | None = None,
    **kwargs: Any,
) -> ft.Text:
    theme = current_theme()
    return ft.Text(
        value=value,
        size=size or theme.text_size_sm,
        color=color or theme.text_color,
        weight=weight,
        font_family=font_family or theme.font_family,
        **kwargs,
    )


__all__ = ["Text"]
