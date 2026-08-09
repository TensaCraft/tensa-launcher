from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from launcher.application.file_sync_journal import SYNC_JOURNAL_FILE, FileSyncJournal


def test_file_sync_journal_removes_only_tmp_files_inside_sync_root(tmp_path: Path) -> None:
    root = tmp_path / "game"
    mods = root / "mods"
    mods.mkdir(parents=True)
    stale_tmp = mods / "sodium.jar.worker.tmp"
    stale_tmp.write_bytes(b"partial")
    real_file = mods / "sodium.jar"
    real_file.write_bytes(b"mod")

    removed = FileSyncJournal(root).cleanup_temporary_downloads([mods])

    assert removed == 1
    assert not stale_tmp.exists()
    assert real_file.exists()


def test_file_sync_journal_resolves_relative_managed_directories(tmp_path: Path) -> None:
    root = tmp_path / "game"
    stale_tmp = root / "mods" / "sodium.jar.worker.tmp"
    stale_tmp.parent.mkdir(parents=True)
    stale_tmp.write_bytes(b"partial")

    removed = FileSyncJournal(root).cleanup_temporary_downloads([Path("mods")])

    assert removed == 1
    assert not stale_tmp.exists()


def test_file_sync_journal_does_not_scan_directory_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    outside_tmp = tmp_path / "outside" / "keep.tmp"
    outside_tmp.parent.mkdir()
    outside_tmp.write_bytes(b"keep")

    removed = FileSyncJournal(root).cleanup_temporary_downloads([outside_tmp.parent])

    assert removed == 0
    assert outside_tmp.exists()


def test_file_sync_transaction_activates_verified_files_and_stale_deletions(tmp_path: Path) -> None:
    root = tmp_path / "game"
    current = root / "mods" / "current.jar"
    stale = root / "mods" / "stale.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    stale.write_bytes(b"stale")
    journal = FileSyncJournal(root)

    journal.begin_transaction(
        operation="sync",
        replacements=["mods/current.jar"],
        stale=["mods/stale.jar"],
    )
    journal.stage_path("mods/current.jar").parent.mkdir(parents=True, exist_ok=True)
    journal.stage_path("mods/current.jar").write_bytes(b"new")
    journal.mark_prepared()
    journal.activate()
    journal.complete()

    assert current.read_bytes() == b"new"
    assert not stale.exists()
    assert not (root / ".tensalauncher-sync").exists()
    assert journal.read()["status"] == "complete"


def test_file_sync_transaction_rolls_back_when_activation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "game"
    current = root / "mods" / "current.jar"
    stale = root / "mods" / "stale.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    stale.write_bytes(b"stale")
    journal = FileSyncJournal(root)
    journal.begin_transaction(
        operation="sync",
        replacements=["mods/current.jar"],
        stale=["mods/stale.jar"],
    )
    staged = journal.stage_path("mods/current.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    real_replace = os.replace

    def fail_activation(source, destination):
        if Path(source) == staged:
            raise PermissionError("target is locked")
        return real_replace(source, destination)

    monkeypatch.setattr("launcher.application.file_sync_journal.os.replace", fail_activation)

    with pytest.raises(RuntimeError, match="target is locked"):
        journal.activate()

    assert current.read_bytes() == b"old"
    assert stale.read_bytes() == b"stale"
    assert journal.read()["status"] == "rolled_back"


def test_file_sync_journal_recovers_interrupted_activation(tmp_path: Path) -> None:
    root = tmp_path / "game"
    current = root / "mods" / "current.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    journal = FileSyncJournal(root)
    journal.begin_transaction(
        operation="sync",
        replacements=["mods/current.jar"],
        stale=[],
    )
    staged = journal.stage_path("mods/current.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    payload = journal.read()
    transaction_root = root / ".tensalauncher-sync" / payload["transaction_id"]
    backup = transaction_root / "backup" / "mods" / "current.jar"
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(current, backup)
    os.replace(staged, current)
    payload["status"] = "applying"
    payload["entries"][0].update({"had_original": True, "state": "applied"})
    (root / SYNC_JOURNAL_FILE).write_text(json.dumps(payload), encoding="utf-8")

    assert FileSyncJournal(root).recover() is True

    assert current.read_bytes() == b"old"
    assert journal.read()["status"] == "rolled_back"
    assert not transaction_root.exists()


def test_file_sync_transaction_rejects_corrupted_journal(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    (root / SYNC_JOURNAL_FILE).write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupted"):
        FileSyncJournal(root).begin_transaction(
            operation="sync",
            replacements=["mods/current.jar"],
            stale=[],
        )


def test_file_sync_transaction_rejects_case_colliding_paths(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Duplicate managed file path"):
        FileSyncJournal(tmp_path).begin_transaction(
            operation="sync",
            replacements=["mods/Example.jar", "mods/example.jar"],
            stale=[],
        )


def test_file_sync_complete_keeps_committed_state_when_cleanup_is_temporarily_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "game"
    current = root / "mods" / "current.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    journal = FileSyncJournal(root)
    journal.begin_transaction(
        operation="sync",
        replacements=["mods/current.jar"],
        stale=[],
    )
    staged = journal.stage_path("mods/current.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    journal.activate()
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(
        "launcher.application.file_sync_journal.shutil.rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("scanner lock")),
    )

    journal.complete()

    assert current.read_bytes() == b"new"
    assert journal.read()["status"] == "complete"
    assert journal.read()["cleanup_pending"] is True

    monkeypatch.setattr("launcher.application.file_sync_journal.shutil.rmtree", real_rmtree)
    assert journal.recover() is False
    assert current.read_bytes() == b"new"
    assert not (root / ".tensalauncher-sync").exists()


def test_file_sync_repair_required_blocks_new_transaction_until_rollback_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "game"
    current = root / "mods" / "current.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    journal = FileSyncJournal(root)
    journal.begin_transaction(
        operation="sync",
        replacements=["mods/current.jar"],
        stale=[],
    )
    staged = journal.stage_path("mods/current.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    payload = journal.read()
    transaction_id = payload["transaction_id"]
    transaction_root = root / ".tensalauncher-sync" / transaction_id
    backup = transaction_root / "backup" / "mods" / "current.jar"
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(current, backup)
    os.replace(staged, current)
    payload["status"] = "applying"
    payload["entries"][0].update({"had_original": True, "state": "applied"})
    (root / SYNC_JOURNAL_FILE).write_text(json.dumps(payload), encoding="utf-8")
    real_copy2 = shutil.copy2
    monkeypatch.setattr(
        "launcher.application.file_sync_journal.shutil.copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("backup locked")),
    )

    with pytest.raises(RuntimeError, match="backup locked"):
        journal.recover()
    with pytest.raises(RuntimeError, match="backup locked"):
        journal.begin_transaction(
            operation="new-sync",
            replacements=["mods/other.jar"],
            stale=[],
        )

    assert journal.read()["transaction_id"] == transaction_id
    assert journal.read()["status"] == "repair_required"
    assert current.read_bytes() == b"new"
    assert backup.read_bytes() == b"old"

    monkeypatch.setattr("launcher.application.file_sync_journal.shutil.copy2", real_copy2)
    assert journal.recover() is True
    assert current.read_bytes() == b"old"
    assert journal.read()["status"] == "rolled_back"
