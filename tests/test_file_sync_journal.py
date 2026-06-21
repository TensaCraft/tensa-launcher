from __future__ import annotations

import json
from pathlib import Path

from launcher.application.file_sync_journal import FileSyncJournal, SYNC_JOURNAL_FILE


def test_file_sync_journal_tracks_running_complete_and_failed_states(tmp_path: Path) -> None:
    journal = FileSyncJournal(tmp_path)

    journal.begin(operation="sync", downloads=3, stale=1)
    running = json.loads((tmp_path / SYNC_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert running["status"] == "running"
    assert running["downloads"] == 3
    assert running["stale"] == 1

    journal.complete()
    complete = json.loads((tmp_path / SYNC_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert complete["status"] == "complete"

    journal.fail("network down")
    failed = json.loads((tmp_path / SYNC_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["error"] == "network down"


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
