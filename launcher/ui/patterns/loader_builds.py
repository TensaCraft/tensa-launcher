from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Callable

import flet as ft

from launcher.application.version_creation import VersionCreateOption

from ..controls.dropdown import Dropdown


LoaderBuildSelection = MutableMapping[str, str]
LoaderBuildChangeHandler = Callable[[VersionCreateOption, Any], None]


def selected_loader_version(option: VersionCreateOption, selected_builds: LoaderBuildSelection) -> str | None:
    return selected_builds.get(option.id) or option.loader_version


def update_selected_loader_version(
    option: VersionCreateOption,
    selected_builds: LoaderBuildSelection,
    event: Any,
) -> bool:
    value = str(getattr(getattr(event, "control", None), "value", "") or "").strip()
    if not value:
        return False
    selected_builds[option.id] = value
    return True


def build_loader_build_dropdown(
    app: Any,
    option: VersionCreateOption,
    selected_builds: LoaderBuildSelection,
    on_change: LoaderBuildChangeHandler,
    *,
    width: int = 220,
) -> ft.Control | None:
    if option.loader_id in {"minecraft", "tensacraft"} or not option.loader_versions:
        return None
    return Dropdown(
        value=selected_loader_version(option, selected_builds),
        options=[ft.dropdown.Option(build) for build in option.loader_versions],
        label=app.trans("minecraft_components_loader_build_label"),
        width=width,
        size="sm",
        on_change=lambda event, selected=option: on_change(selected, event),
    )


__all__ = [
    "build_loader_build_dropdown",
    "selected_loader_version",
    "update_selected_loader_version",
]
