from __future__ import annotations

from typing import Any

from ..controls.button import Button


def FileInputTrigger(*, text: str, icon=None, on_click=None, **kwargs: Any):
    return Button(text=text, icon=icon, on_click=on_click, **kwargs)


__all__ = ["FileInputTrigger"]
