from __future__ import annotations

import json
from pathlib import Path

import pytest

import launcher.storage.atomic as atomic_module
from launcher.storage.atomic import atomic_copy_file, atomic_write_json


def test_atomic_write_json_replaces_complete_document(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}', encoding="utf-8")

    atomic_write_json(target, {"new": True}, indent=4)

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_atomic_write_json_preserves_original_when_replace_fails(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(_source, _target) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(PermissionError, match="locked"):
        atomic_write_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_atomic_copy_file_preserves_original_when_replace_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jar"
    source.write_bytes(b"new")
    target = tmp_path / "target.jar"
    target.write_bytes(b"old")

    def fail_replace(_source, _target) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_copy_file(source, target)

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".target.jar.*.tmp")) == []
