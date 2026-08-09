from __future__ import annotations

from pathlib import Path

import pytest

import launcher.application.file_transaction as transaction_module
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.file_transaction import FileTransaction, FileTransactionPlan


def test_file_transaction_executes_full_sequence(tmp_path: Path):
    destination = tmp_path / "mods" / "example.jar"
    destination.parent.mkdir()
    destination.write_bytes(b"old")
    plan = FileTransactionPlan(
        operation="test-install",
        replacements=("mods/example.jar",),
        staged_bytes=3,
    )
    transaction = FileTransaction(tmp_path, plan)
    calls: list[str] = []

    transaction.execute(
        lambda current: current.stage_path("mods/example.jar").write_bytes(b"new"),
        validate=lambda: calls.append("validate"),
        before_activate=lambda: calls.append("before"),
        commit=lambda: calls.append("commit"),
        commit_key="test:full-sequence",
    )

    assert destination.read_bytes() == b"new"
    assert calls == ["validate", "before", "validate", "commit"]
    assert FileSyncJournal(tmp_path).read()["status"] == "complete"


def test_file_transaction_rolls_back_when_commit_fails(tmp_path: Path):
    destination = tmp_path / "mods" / "example.jar"
    destination.parent.mkdir()
    destination.write_bytes(b"old")
    transaction = FileTransaction(
        tmp_path,
        FileTransactionPlan(
            operation="test-install",
            replacements=("mods/example.jar",),
        ),
    )

    def fail_commit() -> None:
        raise RuntimeError("save failed")

    with pytest.raises(RuntimeError, match="save failed"):
        transaction.execute(
            lambda current: current.stage_path("mods/example.jar").write_bytes(b"new"),
            commit=fail_commit,
            commit_key="test:failed-commit",
        )

    assert destination.read_bytes() == b"old"
    assert FileSyncJournal(tmp_path).read()["status"] == "rolled_back"


def test_file_transaction_preflights_staging_and_rollback_space(
    monkeypatch,
    tmp_path: Path,
):
    destination = tmp_path / "mods" / "example.jar"
    destination.parent.mkdir()
    destination.write_bytes(b"old")
    observed = []

    monkeypatch.setattr(
        transaction_module,
        "ensure_storage_available",
        lambda requests: observed.extend(requests),
    )
    transaction = FileTransaction(
        tmp_path,
        FileTransactionPlan(
            operation="test-install",
            replacements=("mods/example.jar",),
            staged_bytes=7,
        ),
    )

    transaction.execute(
        lambda current: current.stage_path("mods/example.jar").write_bytes(b"new")
    )

    assert observed[0].required_bytes == 10


def test_file_transaction_recovers_interrupted_committed_profile_without_rollback(
    monkeypatch,
    tmp_path: Path,
):
    class SimulatedCrash(BaseException):
        pass

    destination = tmp_path / "mods" / "example.jar"
    destination.parent.mkdir()
    destination.write_bytes(b"old")
    profile = {"version": "old"}
    transaction = FileTransaction(
        tmp_path,
        FileTransactionPlan(
            operation="test-install",
            replacements=("mods/example.jar",),
        ),
    )
    real_complete = transaction.journal.complete

    def commit() -> None:
        profile["version"] = "new"

    monkeypatch.setattr(
        transaction.journal,
        "complete",
        lambda: (_ for _ in ()).throw(SimulatedCrash()),
    )

    with pytest.raises(SimulatedCrash):
        transaction.execute(
            lambda current: current.stage_path("mods/example.jar").write_bytes(b"new"),
            commit=commit,
            commit_key="test:recoverable-profile",
        )

    assert destination.read_bytes() == b"new"
    assert profile == {"version": "new"}
    assert transaction.journal.read()["status"] == "committing"

    monkeypatch.setattr(transaction.journal, "complete", real_complete)
    assert transaction.journal.recover(
        commit_recovery=commit,
        commit_key="test:recoverable-profile",
    ) is True

    assert destination.read_bytes() == b"new"
    assert profile == {"version": "new"}
    assert transaction.journal.read()["status"] == "complete"


def test_file_transaction_refuses_wrong_commit_recovery_identity(tmp_path: Path):
    destination = tmp_path / "mods" / "example.jar"
    destination.parent.mkdir()
    destination.write_bytes(b"old")
    journal = FileSyncJournal(tmp_path)
    journal.begin_transaction(
        operation="test-install",
        replacements=("mods/example.jar",),
        stale=(),
        commit_key="test:original",
    )
    staged = journal.stage_path("mods/example.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    journal.activate()
    journal.mark_committing()

    with pytest.raises(RuntimeError, match="does not match"):
        journal.recover(
            commit_recovery=lambda: None,
            commit_key="test:different",
        )

    assert destination.read_bytes() == b"new"
    assert journal.read()["status"] == "committing"
