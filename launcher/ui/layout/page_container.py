from __future__ import annotations

from typing import Any, Iterable

import flet as ft

from .column import Column
from .container import Container


def PageContainer(
    *,
    controls: Iterable[ft.Control] | None = None,
    content_padding: int | float | ft.Padding | None = None,
    **kwargs: Any,
) -> ft.Stack:
    items: list[ft.Control] = []
    if controls:
        items.append(
            Container(
                content=Column(list(controls), expand=True, spacing=0),
                padding=content_padding,
                expand=True,
            )
        )
    return ft.Stack(controls=items, expand=kwargs.pop("expand", True), **kwargs)


__all__ = ["PageContainer"]
