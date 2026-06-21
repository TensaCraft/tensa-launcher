from __future__ import annotations

from typing import Any

import flet as ft

from ..theme import current_theme


def ProgressRing(value: float | None = None, **kwargs: Any) -> ft.ProgressRing:
    theme = current_theme()
    return ft.ProgressRing(
        value=value,
        width=kwargs.pop("width", theme.progress_ring_size),
        height=kwargs.pop("height", theme.progress_ring_size),
        stroke_width=kwargs.pop("stroke_width", 4),
        color=kwargs.pop("color", theme.primary),
        **kwargs,
    )


__all__ = ["ProgressRing"]
