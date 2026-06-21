from __future__ import annotations

from ..feedback.tooltip import Tooltip
from .text import Text


def TextWithTooltip(text: str, *, max_length: int = 20, tooltip_message: str | None = None):
    truncated = text[:max_length] + "..." if len(text) > max_length else text
    control = Text(truncated)
    control.tooltip = Tooltip(message=tooltip_message or text)
    return control


__all__ = ["TextWithTooltip"]
