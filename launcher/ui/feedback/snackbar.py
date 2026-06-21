from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs


def SnackBar(content: ft.Control | None = None, *, action: str | None = None, action_color: str | None = None, **kwargs: Any) -> ft.SnackBar:
    merged = {"content": content, "action": action, **kwargs}
    if action_color is not None and isinstance(action, str):
        merged["action"] = ft.SnackBarAction(label=action, text_color=action_color)
    return ft.SnackBar(**filter_control_kwargs(ft.SnackBar, merged))


__all__ = ["SnackBar"]
