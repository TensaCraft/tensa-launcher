from __future__ import annotations

from typing import Any

import flet as ft

from ..theme import current_theme


def ResponsiveRow(controls: list[ft.Control] | None = None, **kwargs: Any) -> ft.ResponsiveRow:
    theme = current_theme()
    return ft.ResponsiveRow(
        controls=controls or [],
        spacing=kwargs.pop("spacing", theme.spacing_lg),
        run_spacing=kwargs.pop("run_spacing", theme.spacing_lg),
        **kwargs,
    )


__all__ = ["ResponsiveRow"]
