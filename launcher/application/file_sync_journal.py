from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


SYNC_JOURNAL_FILE = ".tensalauncher-sync.json"


class FileSyncJournal:
    def __init__(self, root: Path, *, filename: str = SYNC_JOURNAL_FILE) -> None:
        self.root = Path(root)
        self.path = self.root / filename

    def begin(self, *, operation: str, downloads: int, stale: int) -> None:
        self._write(
            {
                "schema_version": 1,
                "status": "running",
                "operation": operation,
                "downloads": downloads,
                "stale": stale,
                "started_at": self._now(),
            }
        )

    def complete(self) -> None:
        self._write({"schema_version": 1, "status": "complete", "completed_at": self._now()})

    def fail(self, error: Exception | str) -> None:
        self._write(
            {
                "schema_version": 1,
                "status": "failed",
                "error": str(error),
                "failed_at": self._now(),
            }
        )

    def cleanup_temporary_downloads(self, directories: Iterable[Path]) -> int:
        removed = 0
        root_resolved = self.root.resolve()
        for directory in directories:
            candidate = Path(directory)
            if not candidate.exists():
                continue
            try:
                files = list(candidate.rglob("*.tmp"))
            except OSError:
                continue
            for path in files:
                try:
                    if path.is_file() and path.resolve().is_relative_to(root_resolved):
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def _write(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["FileSyncJournal", "SYNC_JOURNAL_FILE"]
