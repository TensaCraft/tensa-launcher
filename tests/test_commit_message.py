from __future__ import annotations

from pathlib import Path

from tools_import import load_tool_module

commit_validator = load_tool_module("validate_commit_message")


def test_commit_message_accepts_conventional_english_subject():
    errors = commit_validator.validate_subject("fix(updater): select stable release when newer than beta")

    assert errors == []


def test_commit_message_rejects_vague_subject():
    errors = commit_validator.validate_subject("fix: up")

    assert "Commit summary must be at least 15 characters" in errors
    assert "Commit summary is too vague for release notes" in errors


def test_commit_message_rejects_non_english_subject():
    errors = commit_validator.validate_subject("fix(updater): виправити оновлення beta")

    assert "Commit subject must be English ASCII text" in errors


def test_commit_message_reads_first_non_comment_line(tmp_path: Path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("\n# comment\nui(home): balance card title spacing\n\nbody\n", encoding="utf-8")

    assert commit_validator.read_commit_subject(message) == "ui(home): balance card title spacing"
