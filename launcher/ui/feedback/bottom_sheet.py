from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs


def BottomSheet(content: ft.Control | None = None, **kwargs: Any) -> ft.BottomSheet:
    return ft.BottomSheet(content=content, **filter_control_kwargs(ft.BottomSheet, kwargs))


__all__ = ["BottomSheet"]
