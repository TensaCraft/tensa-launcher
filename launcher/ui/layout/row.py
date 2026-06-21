from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import flet as ft


def Row(controls: Sequence[ft.Control] | None = None, **kwargs: Any) -> ft.Row:
    return ft.Row(controls=list(controls or []), **kwargs)


__all__ = ["Row"]
