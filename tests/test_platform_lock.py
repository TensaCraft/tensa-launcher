from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Barrier

import pytest

from launcher.application import platform_lock
from launcher.application.platform_lock import OSFileLock, OSFileLockBusy, lock_file_path


def test_opening_new_lock_file_does_not_write_before_locking(tmp_path):
    path = tmp_path / "empty.lock"

    with platform_lock._open_lock_file(path) as handle:
        assert os.fstat(handle.fileno()).st_size == 0
        assert handle.tell() == 0
        assert not os.get_inheritable(handle.fileno())


def test_contender_of_locked_empty_file_reports_busy_without_writing(tmp_path):
    resource = tmp_path / "owner"
    path = lock_file_path(resource, "shared")
    path.parent.mkdir()

    # Hold byte zero while the first owner has not written its metadata yet.
    with path.open("w+b", buffering=0) as owner_handle:
        platform_lock._lock_nonblocking(owner_handle)
        try:
            with pytest.raises(OSFileLockBusy) as error:
                OSFileLock.try_acquire(resource, "shared", "contender")
            assert error.value.path == path
            assert error.value.owner is None
            assert os.fstat(owner_handle.fileno()).st_size == 0
        finally:
            platform_lock._unlock(owner_handle)

    with OSFileLock.try_acquire(resource, "shared", "next-owner") as acquired:
        assert acquired.owner.kind == "next-owner"


@pytest.mark.parametrize("original", [b"", b"\nstale owner metadata\n"])
def test_metadata_is_written_only_after_lock_acquisition(tmp_path, monkeypatch, original):
    resource = tmp_path / "owner"
    path = lock_file_path(resource, "shared")
    path.parent.mkdir()
    path.write_bytes(original)
    events = []
    lock = platform_lock._lock_nonblocking
    write_metadata = OSFileLock._write_owner_metadata

    def tracked_lock(handle):
        assert os.fstat(handle.fileno()).st_size == len(original)
        lock(handle)
        events.append("locked")

    def tracked_write(self, owner):
        assert events == ["locked"]
        events.append("metadata")
        write_metadata(self, owner)

    monkeypatch.setattr(platform_lock, "_lock_nonblocking", tracked_lock)
    monkeypatch.setattr(OSFileLock, "_write_owner_metadata", tracked_write)

    with OSFileLock.try_acquire(resource, "shared", "launcher", token="test-owner") as acquired:
        assert events == ["locked", "metadata"]
        with path.open("rb") as reader:
            assert platform_lock._read_owner(reader) == acquired.owner

    raw = path.read_bytes()
    assert raw.startswith(b"\n{")
    assert json.loads(raw)["token"] == "test-owner"


def test_simultaneous_first_acquisition_has_exactly_one_owner(tmp_path):
    workers = 8
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for iteration in range(30):
            resource = tmp_path / f"owner-{iteration}"
            start = Barrier(workers)
            acquired = []

            def contend(index):
                start.wait(timeout=10)
                try:
                    lock = OSFileLock.try_acquire(resource, "shared", "launcher", token=str(index))
                except OSFileLockBusy:
                    return False
                acquired.append(lock)
                return True

            futures = [executor.submit(contend, index) for index in range(workers)]
            try:
                outcomes = [future.result(timeout=10) for future in futures]
                assert outcomes.count(True) == 1
                assert len(acquired) == 1
                with lock_file_path(resource, "shared").open("rb") as reader:
                    assert platform_lock._read_owner(reader) == acquired[0].owner
            finally:
                wait(futures)
                for lock in acquired:
                    lock.release()

            with OSFileLock.try_acquire(resource, "shared", "restart") as restarted:
                assert restarted.owner.kind == "restart"
