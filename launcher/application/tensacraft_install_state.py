from __future__ import annotations

from collections.abc import MutableSet
from typing import Any


PENDING_TENSACRAFT_PACK_IDS_ATTR = "pending_tensacraft_pack_ids"


def _normalize_pack_id(pack_id: str | None) -> str:
    return str(pack_id or "").strip()


def pending_pack_ids(app: Any) -> MutableSet[str]:
    pending = getattr(app, PENDING_TENSACRAFT_PACK_IDS_ATTR, None)
    if not isinstance(pending, set):
        pending = set(pending or [])
        setattr(app, PENDING_TENSACRAFT_PACK_IDS_ATTR, pending)
    return pending


def mark_pending(app: Any, pack_id: str | None) -> None:
    normalized = _normalize_pack_id(pack_id)
    if normalized:
        pending_pack_ids(app).add(normalized)


def unmark_pending(app: Any, pack_id: str | None) -> None:
    normalized = _normalize_pack_id(pack_id)
    if normalized:
        pending_pack_ids(app).discard(normalized)


def is_pending(app: Any, pack_id: str | None) -> bool:
    normalized = _normalize_pack_id(pack_id)
    return bool(normalized and normalized in pending_pack_ids(app))


__all__ = ["is_pending", "mark_pending", "pending_pack_ids", "unmark_pending"]
