from __future__ import annotations

import os

from launcher.application.diagnostics.artifacts import read_artifact


def test_artifact_reader_ignores_file_from_previous_launch(tmp_path) -> None:
    path = tmp_path / "latest.log"
    path.write_text("old failure", encoding="utf-8")
    os.utime(path, (100, 100))

    artifact = read_artifact(path, started_at=200)

    assert artifact.fresh is False
    assert artifact.text == ""


def test_artifact_reader_keeps_head_and_tail_with_a_hard_size_bound(tmp_path) -> None:
    path = tmp_path / "latest.log"
    path.write_bytes(b"HEAD" + (b"x" * 200) + b"TAIL")

    artifact = read_artifact(path, max_bytes=100)

    encoded = artifact.text.encode()
    assert b"HEAD" in encoded
    assert b"TAIL" in encoded
    assert b"truncated by TensaLauncher" in encoded
    assert len(encoded) <= 100
