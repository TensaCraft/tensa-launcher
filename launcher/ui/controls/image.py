from __future__ import annotations

import base64
from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs


def Image(
    src: str | None = None,
    *,
    src_base64: str | None = None,
    fit: ft.BoxFit | None = None,
    **kwargs: Any,
) -> ft.Image:
    merged = {"src": src or "", "fit": fit or ft.BoxFit.CONTAIN, **kwargs}
    if src_base64 is not None:
        try:
            merged["src"] = base64.b64decode(src_base64)
        except (TypeError, ValueError):
            merged["src"] = src_base64
    return ft.Image(**filter_control_kwargs(ft.Image, merged))


__all__ = ["Image"]
