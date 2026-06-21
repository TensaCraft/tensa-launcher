from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def GridView(
    controls: list[ft.Control] | None = None,
    *,
    on_scroll_interval: int | float | None = None,
    **kwargs: Any,
) -> ft.GridView:
    theme = current_theme()
    merged = {
        "controls": controls or [],
        "runs_count": kwargs.pop("runs_count", 10),
        "child_aspect_ratio": kwargs.pop("child_aspect_ratio", 0.9),
        "spacing": kwargs.pop("spacing", theme.spacing_sm),
        "run_spacing": kwargs.pop("run_spacing", theme.spacing_sm),
        "padding": kwargs.pop("padding", theme.padding_sm),
        **kwargs,
    }
    if on_scroll_interval is not None:
        merged["scroll_interval"] = on_scroll_interval
    return ft.GridView(**filter_control_kwargs(ft.GridView, merged))


__all__ = ["GridView"]
