from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UiScale:
    ui_scale: float = 1.0

    def px(self, value: int | float) -> int:
        return max(1, round(float(value) * self.ui_scale))

    def raw(self, value: int | float) -> float:
        return float(value) * self.ui_scale
