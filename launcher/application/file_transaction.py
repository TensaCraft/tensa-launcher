from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.storage_preflight import (
    StorageRequest,
    ensure_storage_available,
)

TransactionPath = str | PurePosixPath


@dataclass(frozen=True, slots=True)
class FileTransactionPlan:
    operation: str
    replacements: Sequence[TransactionPath]
    stale: Sequence[TransactionPath] = ()
    staged_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("File transaction operation cannot be empty")
        if self.staged_bytes < 0:
            raise ValueError("staged_bytes cannot be negative")
        object.__setattr__(self, "replacements", tuple(self.replacements))
        object.__setattr__(self, "stale", tuple(self.stale))


class FileTransaction:
    """Own the durable stage, validate, activate, commit, and rollback sequence."""

    def __init__(
        self,
        root: Path,
        plan: FileTransactionPlan,
        *,
        journal_filename: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.plan = plan
        self.journal = (
            FileSyncJournal(self.root, filename=journal_filename)
            if journal_filename
            else FileSyncJournal(self.root)
        )
        self._started = False

    def stage_path(self, relative_path: TransactionPath) -> Path:
        if not self._started:
            raise RuntimeError("File transaction has not started")
        path = self.journal.stage_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def storage_request(self) -> StorageRequest:
        return StorageRequest(
            target=self.root,
            required_bytes=self.plan.staged_bytes + self._rollback_bytes(),
            label=self.plan.operation,
        )

    def execute(
        self,
        stage: Callable[[FileTransaction], None],
        *,
        validate: Callable[[], None] | None = None,
        before_activate: Callable[[], None] | None = None,
        commit: Callable[[], None] | None = None,
        commit_key: str | None = None,
    ) -> None:
        if commit is not None and not str(commit_key or "").strip():
            raise ValueError("A stable commit_key is required for recoverable commits")
        if commit is None and commit_key is not None:
            raise ValueError("commit_key cannot be used without a commit callback")
        ensure_storage_available([self.storage_request()])
        self.journal.begin_transaction(
            operation=self.plan.operation,
            replacements=self.plan.replacements,
            stale=self.plan.stale,
            commit_recovery=commit,
            commit_key=commit_key,
        )
        self._started = True
        try:
            stage(self)
            if validate is not None:
                validate()
            self.journal.mark_prepared()
            if before_activate is not None:
                before_activate()
            if validate is not None:
                validate()
            self.journal.activate()
            if commit is not None:
                self.journal.mark_committing()
                commit()
            self.journal.complete()
        except Exception as exc:
            self._rollback(exc)
            raise
        finally:
            self._started = False

    def _rollback_bytes(self) -> int:
        total = 0
        for raw_path in (*self.plan.replacements, *self.plan.stale):
            relative = FileSyncJournal._normalized_path(raw_path)
            candidate = FileSyncJournal._contained_path(self.root, relative)
            try:
                if candidate.is_file():
                    total += candidate.stat().st_size
            except OSError:
                continue
        return total

    def _rollback(self, original_error: Exception) -> None:
        payload = self.journal.read() or {}
        if str(payload.get("status") or "") in {
            "complete",
            "rolled_back",
            "recovered",
        }:
            return
        try:
            self.journal.rollback()
        except Exception as rollback_error:
            raise RuntimeError(
                f"{original_error}; {self.plan.operation} rollback failed: "
                f"{rollback_error}"
            ) from rollback_error


def build_commit_key(namespace: str, payload: object) -> str:
    prefix = str(namespace or "").strip()
    if not prefix:
        raise ValueError("Commit key namespace cannot be empty")
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(serialized).hexdigest()}"


__all__ = [
    "FileTransaction",
    "FileTransactionPlan",
    "TransactionPath",
    "build_commit_key",
]
