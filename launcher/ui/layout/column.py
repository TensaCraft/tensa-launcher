from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import flet as ft


def Column(controls: Sequence[ft.Control] | None = None, **kwargs: Any) -> ft.Column:
    return ft.Column(controls=list(controls or []), **kwargs)


__all__ = ["Column"]
