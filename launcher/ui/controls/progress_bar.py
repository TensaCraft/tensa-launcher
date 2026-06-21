from __future__ import annotations

from typing import Any

import flet as ft


def ProgressBar(value: float | None = None, **kwargs: Any) -> ft.ProgressBar:
    return ft.ProgressBar(value=value, **kwargs)


__all__ = ["ProgressBar"]
