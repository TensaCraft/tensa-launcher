from __future__ import annotations

from typing import Any

import flet as ft

from ..core.flet_compat import filter_control_kwargs
from ..core.click_sound import wrap_click_handler


def Container(
    content: ft.Control | None = None,
    *,
    image_src: str | None = None,
    image_fit: ft.BoxFit | None = None,
    image_opacity: float | None = None,
    **kwargs: Any,
) -> ft.Container:
    merged = {"content": content, **kwargs}
    if "on_click" in merged:
        merged["on_click"] = wrap_click_handler(merged.get("on_click"))
    decoration_image = merged.get("image")
    if image_src is not None or decoration_image is not None:
        image = decoration_image or ft.DecorationImage(src=image_src, fit=image_fit, opacity=image_opacity)
        if image_src is not None:
            image.src = image_src
        if image_fit is not None:
            image.fit = image_fit
        if image_opacity is not None:
            image.opacity = image_opacity
        merged["image"] = image
    merged.pop("image_src", None)
    merged.pop("image_fit", None)
    merged.pop("image_opacity", None)
    return ft.Container(**filter_control_kwargs(ft.Container, merged))


__all__ = ["Container"]
