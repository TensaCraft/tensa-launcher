from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


COPY_SNAPSHOT_FILE = "tensalauncher-copy.json"
COPY_SYNC_MODE = "manual"


def mark_manual_copy_options(options: dict[str, Any], *, source_version_id: str) -> dict[str, Any]:
    updated = dict(options or {})
    updated["syncMode"] = COPY_SYNC_MODE
    updated["managedByApi"] = False
    updated["sourceVersionId"] = source_version_id
    return updated


def write_copy_snapshot(source_version, copied_version, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_path = destination / COPY_SNAPSHOT_FILE
    payload = {
        "schema_version": 1,
        "managed": False,
        "sync_mode": COPY_SYNC_MODE,
        "copied_at": datetime.now(timezone.utc).isoformat(),
        "source": _version_summary(source_version),
        "copy": _version_summary(copied_version),
    }
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return snapshot_path


def _version_summary(version) -> dict[str, Any]:
    return {
        "version_id": getattr(version, "version_id", None) or getattr(version, "id", None),
        "id": getattr(version, "id", None),
        "name": getattr(version, "name", None),
        "minecraft": getattr(version, "version", None),
        "client": getattr(version, "client", None),
        "loader": getattr(version, "loader", None),
        "loader_version": getattr(version, "loader_version", None),
        "path": str(getattr(version, "path", "") or ""),
    }


__all__ = ["COPY_SNAPSHOT_FILE", "COPY_SYNC_MODE", "mark_manual_copy_options", "write_copy_snapshot"]
