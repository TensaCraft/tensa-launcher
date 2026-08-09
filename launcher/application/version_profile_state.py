from __future__ import annotations

from copy import deepcopy
from typing import Any

_PROFILE_FIELDS = (
    "id",
    "name",
    "version",
    "loader",
    "client",
    "path",
    "loader_version",
    "force_update",
    "options",
    "image",
    "is_remote",
    "remote_pack_id",
    "description",
)


def capture_version_profile(version: Any) -> dict[str, Any]:
    return {
        field: deepcopy(getattr(version, field))
        for field in _PROFILE_FIELDS
        if hasattr(version, field)
    }


def restore_version_profile(version: Any, state: dict[str, Any]) -> None:
    for field, value in state.items():
        setattr(version, field, deepcopy(value))


__all__ = ["capture_version_profile", "restore_version_profile"]
