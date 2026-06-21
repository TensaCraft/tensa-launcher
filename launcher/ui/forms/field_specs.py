from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ..controls.checkbox import Checkbox
from ..controls.dropdown import Dropdown
from ..controls.text_field import TextField
from ..theme import current_theme


@dataclass(slots=True)
class FieldSpec:
    type: str
    key: str
    label: str
    value: Any = None
    options: list[Any] = field(default_factory=list)
    width: int | float | None = None
    height: int | float | None = None
    expand: bool | int | None = None
    props: dict[str, Any] = field(default_factory=dict)


def _dropdown_options(raw_options: list[Any]) -> list[ft.dropdown.Option]:
    options: list[ft.dropdown.Option] = []
    for option in raw_options:
        if isinstance(option, ft.dropdown.Option):
            options.append(option)
        elif isinstance(option, dict):
            text = str(option.get("text", ""))
            key = str(option.get("key", text))
            options.append(ft.dropdown.Option(text=text, key=key))
        else:
            value = str(option)
            options.append(ft.dropdown.Option(text=value, key=value))
    return options


def build_field(app, spec: FieldSpec, *, on_change=None):
    theme = current_theme()
    width = spec.width
    height = spec.height
    common = {
        "key": spec.key,
        "width": width,
        "height": height if height is not None else theme.input_height,
        "expand": spec.expand,
        **spec.props,
    }
    if spec.type == "textfield":
        return TextField(
            label=spec.label,
            value="" if spec.value is None else str(spec.value),
            on_change=on_change,
            **common,
        )
    if spec.type == "dropdown":
        return Dropdown(
            label=spec.label,
            value=spec.value,
            options=_dropdown_options(spec.options),
            on_change=on_change,
            **common,
        )
    if spec.type == "checkbox":
        return Checkbox(
            label=spec.label,
            value=bool(spec.value),
            on_change=on_change,
            **spec.props,
        )
    raise ValueError(f"Unsupported field spec type: {spec.type}")


def apply_field_width(control: ft.Control, width: int | float | None):
    if width is not None and isinstance(control, (ft.TextField, ft.Dropdown)):
        control.width = width
    return control


__all__ = ["FieldSpec", "apply_field_width", "build_field"]
