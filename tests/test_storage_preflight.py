from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import pytest

from launcher.application import storage_preflight
from launcher.application.storage_preflight import (
    StoragePreflightError,
    StorageRequest,
    ensure_storage_available,
)


def test_storage_preflight_creates_parent_and_removes_probe(tmp_path: Path):
    destination = tmp_path / "new" / "nested" / "client.jar"

    ensure_storage_available(
        [StorageRequest(destination, required_bytes=1)],
        reserve_bytes=0,
    )

    assert destination.parent.is_dir()
    assert list(destination.parent.iterdir()) == []


def test_storage_preflight_coalesces_requirements_for_one_volume(
    monkeypatch,
    tmp_path: Path,
):
    usage = namedtuple("usage", "total used free")
    observed: list[Path] = []

    def disk_usage(path):
        observed.append(Path(path))
        return usage(1000, 500, 499)

    monkeypatch.setattr(storage_preflight.shutil, "disk_usage", disk_usage)

    with pytest.raises(StoragePreflightError, match="requires 500.0 B"):
        ensure_storage_available(
            [
                StorageRequest(tmp_path / "one.bin", required_bytes=200, label="files"),
                StorageRequest(tmp_path / "two.bin", required_bytes=300, label="files"),
            ],
            reserve_bytes=0,
        )

    assert observed == [tmp_path.resolve()]


def test_storage_preflight_reports_unwritable_target(monkeypatch, tmp_path: Path):
    def fail_probe(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(storage_preflight.tempfile, "mkstemp", fail_probe)

    with pytest.raises(StoragePreflightError, match="not writable"):
        ensure_storage_available(
            [StorageRequest(tmp_path / "client.jar")],
            reserve_bytes=0,
        )
