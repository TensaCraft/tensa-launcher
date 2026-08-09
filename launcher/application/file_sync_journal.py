from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

from launcher.storage.atomic import atomic_write_json

SYNC_JOURNAL_FILE = ".tensalauncher-sync.json"
SYNC_WORK_DIRECTORY = ".tensalauncher-sync"


class FileSyncJournal:
    REPAIR_STATUSES = {
        "running",
        "staging",
        "prepared",
        "applying",
        "files_applied",
        "committing",
        "failed",
        "repair_required",
    }

    def __init__(self, root: Path, *, filename: str = SYNC_JOURNAL_FILE) -> None:
        self.root = Path(root)
        self.path = self.root / filename

    def complete(self) -> None:
        payload = self.read() or {"schema_version": 1}
        payload.update({"status": "complete", "completed_at": self._now()})
        self._write(payload)
        self._cleanup_transaction_best_effort(payload)

    def mark_repair_required(self, *, reason: str, details: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "status": "repair_required",
            "reason": str(reason or "unknown"),
            "marked_at": self._now(),
        }
        if details:
            payload["details"] = details
        self._write(payload)

    def read(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def needs_repair(self) -> bool:
        if self.path.exists() and self.read() is None:
            return True
        payload = self.read()
        status = str((payload or {}).get("status") or "").strip().lower()
        return status in self.REPAIR_STATUSES

    def begin_transaction(
        self,
        *,
        operation: str,
        replacements: Sequence[str | PurePosixPath],
        stale: Sequence[str | PurePosixPath],
        commit_recovery: Callable[[], None] | None = None,
        commit_key: str | None = None,
    ) -> Path:
        self.recover(
            commit_recovery=commit_recovery,
            commit_key=commit_key,
        )
        replacement_paths = self._normalized_paths(replacements)
        stale_paths = self._normalized_paths(stale)
        replacement_keys = {path.as_posix().casefold() for path in replacement_paths}
        stale_paths = [path for path in stale_paths if path.as_posix().casefold() not in replacement_keys]

        transaction_id = uuid.uuid4().hex
        transaction_root = self.root / SYNC_WORK_DIRECTORY / transaction_id
        (transaction_root / "stage").mkdir(parents=True, exist_ok=False)
        (transaction_root / "backup").mkdir(parents=True, exist_ok=False)
        entries = [
            {"path": path.as_posix(), "kind": "replace", "state": "planned"}
            for path in replacement_paths
        ]
        entries.extend(
            {"path": path.as_posix(), "kind": "delete", "state": "planned"}
            for path in stale_paths
        )
        self._write(
            {
                "schema_version": 2,
                "status": "staging",
                "operation": operation,
                "commit_key": commit_key,
                "transaction_id": transaction_id,
                "entries": entries,
                "started_at": self._now(),
            }
        )
        return transaction_root / "stage"

    def stage_path(self, relative_path: str | PurePosixPath) -> Path:
        payload = self._read_strict()
        transaction_root = self._transaction_root(payload)
        return self._contained_path(transaction_root / "stage", self._normalized_path(relative_path))

    def mark_prepared(self) -> None:
        payload = self._read_strict()
        if payload.get("schema_version") != 2 or payload.get("status") != "staging":
            raise RuntimeError("File synchronization transaction is not staging")
        payload.update({"status": "prepared", "prepared_at": self._now()})
        self._write(payload)

    def activate(self) -> None:
        payload = self._read_strict()
        if payload.get("schema_version") != 2 or payload.get("status") != "prepared":
            raise RuntimeError("File synchronization transaction is not prepared")

        transaction_root = self._transaction_root(payload)
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise RuntimeError("File synchronization journal entries are invalid")

        payload["status"] = "applying"
        self._write(payload)
        current_path = "<unknown>"
        try:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise RuntimeError("File synchronization journal entry is invalid")
                relative_path = self._normalized_path(entry.get("path"))
                current_path = relative_path.as_posix()
                destination = self._contained_path(self.root, relative_path)
                backup = self._contained_path(transaction_root / "backup", relative_path)
                staged = self._contained_path(transaction_root / "stage", relative_path)
                kind = str(entry.get("kind") or "")
                if kind not in {"replace", "delete"}:
                    raise RuntimeError(f"Unsupported synchronization action: {kind}")
                if kind == "replace" and not staged.is_file():
                    raise RuntimeError(f"Verified staged file is missing: {relative_path.as_posix()}")

                entry["had_original"] = destination.is_file()
                entry["state"] = "backing_up"
                self._write(payload)

                if entry["had_original"]:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, backup)

                entry["state"] = "backed_up"
                self._write(payload)

                if kind == "replace":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, destination)

                entry["state"] = "applied"
                self._write(payload)
        except Exception as exc:
            payload["status"] = "failed"
            payload["error"] = str(exc)
            payload["failed_at"] = self._now()
            self._write(payload)
            self._rollback(payload)
            raise RuntimeError(f"Failed to activate managed file {current_path}: {exc}") from exc

        payload.update({"status": "files_applied", "applied_at": self._now()})
        self._write(payload)

    def mark_committing(self) -> None:
        payload = self._read_strict()
        if payload.get("schema_version") != 2 or payload.get("status") != "files_applied":
            raise RuntimeError("File synchronization transaction has not applied its files")
        payload.update({"status": "committing", "commit_started_at": self._now()})
        self._write(payload)

    def recover(
        self,
        *,
        commit_recovery: Callable[[], None] | None = None,
        commit_key: str | None = None,
    ) -> bool:
        if not self.path.exists():
            return False
        payload = self._read_strict()
        status = str(payload.get("status") or "").strip().lower()
        schema_version = payload.get("schema_version")

        if schema_version != 2:
            return status in self.REPAIR_STATUSES
        if status == "complete":
            self._cleanup_transaction_best_effort(payload)
            return False
        if status in {"rolled_back", "recovered"}:
            self._cleanup_transaction(payload)
            return False
        if status in {"staging", "prepared"}:
            self._cleanup_transaction(payload)
            payload.update({"status": "recovered", "recovered_at": self._now()})
            self._write(payload)
            return True
        if status in {"applying", "files_applied", "failed"}:
            self._rollback(payload)
            return True
        if status == "committing":
            if commit_recovery is None:
                raise RuntimeError(
                    "File synchronization commit was interrupted and requires the original operation to resume"
                )
            stored_commit_key = str(payload.get("commit_key") or "")
            if not stored_commit_key or stored_commit_key != str(commit_key or ""):
                raise RuntimeError(
                    "Interrupted file synchronization commit does not match the requested operation"
                )
            try:
                commit_recovery()
            except Exception as exc:
                payload.update(
                    {
                        "commit_recovery_error": str(exc),
                        "commit_recovery_failed_at": self._now(),
                    }
                )
                self._write(payload)
                raise RuntimeError(f"Could not recover file synchronization commit: {exc}") from exc
            self.complete()
            return True
        if status == "repair_required":
            self._rollback(payload)
            return True
        raise RuntimeError(f"Unknown file synchronization journal state: {status or 'missing'}")

    def rollback(self) -> None:
        payload = self._read_strict()
        if payload.get("schema_version") != 2:
            raise RuntimeError("Legacy file synchronization journal cannot be rolled back")
        self._rollback(payload)

    def cleanup_temporary_downloads(self, directories: Iterable[Path]) -> int:
        removed = 0
        root_resolved = self.root.resolve()
        for directory in directories:
            candidate = Path(directory)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            if not candidate.exists():
                continue
            try:
                candidate_resolved = candidate.resolve()
                if not candidate_resolved.is_relative_to(root_resolved):
                    continue
                files = list(candidate_resolved.rglob("*.tmp"))
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
        atomic_write_json(self.path, payload, ensure_ascii=False, indent=2, sort_keys=True)

    def _read_strict(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError("File synchronization journal is missing") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("File synchronization journal is corrupted") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("File synchronization journal is invalid")
        return payload

    def _rollback(self, payload: dict[str, Any]) -> None:
        transaction_root = self._transaction_root(payload)
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise RuntimeError("File synchronization journal entries are invalid")

        errors: list[str] = []
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            try:
                if entry.get("state") in {"planned", "rolled_back"}:
                    continue
                relative_path = self._normalized_path(entry.get("path"))
                destination = self._contained_path(self.root, relative_path)
                backup = self._contained_path(transaction_root / "backup", relative_path)
                state = str(entry.get("state") or "planned")
                had_original = entry.get("had_original")

                if backup.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    rollback_copy = destination.with_name(
                        f".{destination.name}.rollback.{uuid.uuid4().hex}.tmp"
                    )
                    try:
                        shutil.copy2(backup, rollback_copy, follow_symlinks=False)
                        os.replace(rollback_copy, destination)
                    finally:
                        rollback_copy.unlink(missing_ok=True)
                elif had_original is False and state in {"backed_up", "applied"}:
                    destination.unlink(missing_ok=True)
                elif had_original is True and state in {"backed_up", "applied"}:
                    raise RuntimeError("required rollback backup is missing")
                elif had_original is True and state == "backing_up" and not destination.is_file():
                    raise RuntimeError("original file and rollback backup are both missing")
                entry["state"] = "rolled_back"
                self._write(payload)
            except Exception as exc:
                errors.append(f"{entry.get('path', '?')}: {exc}")

        if errors:
            payload.update(
                {
                    "status": "repair_required",
                    "reason": "rollback_failed",
                    "details": {"errors": errors[:10]},
                    "marked_at": self._now(),
                }
            )
            self._write(payload)
            raise RuntimeError(f"Could not roll back managed files: {'; '.join(errors[:5])}")

        self._cleanup_transaction(payload)
        payload.update({"status": "rolled_back", "rolled_back_at": self._now()})
        self._write(payload)

    def _transaction_root(self, payload: dict[str, Any]) -> Path:
        transaction_id = str(payload.get("transaction_id") or "").strip()
        if not transaction_id or any(character not in "0123456789abcdef" for character in transaction_id.lower()):
            raise RuntimeError("File synchronization transaction ID is invalid")
        work_root = (self.root / SYNC_WORK_DIRECTORY).resolve()
        transaction_root = (work_root / transaction_id).resolve()
        if not transaction_root.is_relative_to(work_root):
            raise RuntimeError("File synchronization transaction path is unsafe")
        return transaction_root

    def _cleanup_transaction(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != 2 or not payload.get("transaction_id"):
            return
        transaction_root = self._transaction_root(payload)
        if transaction_root.exists():
            shutil.rmtree(transaction_root)
        work_root = transaction_root.parent
        try:
            work_root.rmdir()
        except OSError:
            pass

    def _cleanup_transaction_best_effort(self, payload: dict[str, Any]) -> None:
        try:
            self._cleanup_transaction(payload)
        except OSError as exc:
            payload["cleanup_pending"] = True
            payload["cleanup_error"] = str(exc)
            try:
                self._write(payload)
            except OSError:
                pass

    @classmethod
    def _normalized_paths(
        cls,
        values: Sequence[str | PurePosixPath],
    ) -> list[PurePosixPath]:
        normalized: list[PurePosixPath] = []
        seen: set[str] = set()
        for value in values:
            path = cls._normalized_path(value)
            key = path.as_posix().casefold()
            if key in seen:
                raise RuntimeError(f"Duplicate managed file path: {path.as_posix()}")
            seen.add(key)
            normalized.append(path)
        return normalized

    @staticmethod
    def _normalized_path(value: Any) -> PurePosixPath:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
            raise RuntimeError(f"Unsafe managed file path: {text or '<empty>'}")
        path = PurePosixPath(text)
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise RuntimeError(f"Unsafe managed file path: {text}")
        return path

    @staticmethod
    def _contained_path(root: Path, relative_path: PurePosixPath) -> Path:
        resolved_root = root.resolve()
        candidate = (resolved_root / Path(relative_path.as_posix())).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise RuntimeError(f"Managed file path escapes synchronization root: {relative_path.as_posix()}")
        return candidate

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["FileSyncJournal", "SYNC_JOURNAL_FILE", "SYNC_WORK_DIRECTORY"]
