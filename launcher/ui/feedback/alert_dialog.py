from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs


def AlertDialog(title: ft.Control | None = None, **kwargs: Any) -> ft.AlertDialog:
    return ft.AlertDialog(title=title, **filter_control_kwargs(ft.AlertDialog, kwargs))


__all__ = ["AlertDialog"]
