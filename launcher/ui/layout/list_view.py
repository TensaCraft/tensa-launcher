from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..theme import current_theme


def ListView(
    controls: list[ft.Control] | None = None,
    *,
    on_scroll_interval: int | float | None = None,
    **kwargs: Any,
) -> ft.ListView:
    theme = current_theme()
    merged = {
        "controls": controls or [],
        "spacing": kwargs.pop("spacing", theme.spacing_xs),
        "padding": kwargs.pop("padding", theme.padding_sm),
        **kwargs,
    }
    if on_scroll_interval is not None:
        merged["scroll_interval"] = on_scroll_interval
    return ft.ListView(**filter_control_kwargs(ft.ListView, merged))


__all__ = ["ListView"]
