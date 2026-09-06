from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread

import pytest

from launcher.domain.version import Version
from launcher.storage.config_store import Config
from launcher.storage.version_store import Versions


@pytest.mark.parametrize("separate_store", [False, True])
@pytest.mark.parametrize("operation", ["save", "remove"])
def test_version_read_modify_write_is_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, separate_store: bool, operation: str,
) -> None:
    first = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    first.add(Version("existing", {"name": "Before"}))
    second = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft") if separate_store else first
    first_read = Event()
    second_read = Event()
    second_started = Event()
    resume_first = Event()
    original_read = Versions._read_file

    def paused_read(store):
        data = original_read(store)
        if current_thread().name.startswith("first-writer"):
            first_read.set()
            assert resume_first.wait(5)
        else:
            second_read.set()
        return data

    def write_first():
        if operation == "remove":
            first.remove("existing", delete_files=False)
        else:
            first.add(Version("first", {"name": "First"}))

    def write_second():
        second_started.set()
        second.add(Version("second", {"name": "Second"}))

    monkeypatch.setattr(Versions, "_read_file", paused_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-writer") as first_pool:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="second-writer") as second_pool:
            first_future = first_pool.submit(write_first)
            try:
                assert first_read.wait(5)
                second_future = second_pool.submit(write_second)
                assert second_started.wait(5)
                if second_read.wait(0.2):
                    second_future.result(timeout=5)
            finally:
                resume_first.set()
            first_future.result(timeout=5)
            second_future.result(timeout=5)

    saved = json.loads(first.filepath.read_text(encoding="utf-8"))
    assert set(saved) == ({"second"} if operation == "remove" else {"existing", "first", "second"})


def test_simultaneous_config_instances_preserve_all_keys(tmp_path: Path) -> None:
    stores = [Config(storage_dir=tmp_path) for _ in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store.set, f"key_{index}", index) for index, store in enumerate(stores)]
        for future in futures:
            future.result(timeout=5)

    assert dict(Config(storage_dir=tmp_path).items()) == {f"key_{index}": index for index in range(8)}


@pytest.mark.parametrize("store_type", [Config, Versions])
def test_stores_share_locks_only_for_the_same_resolved_path(tmp_path: Path, store_type) -> None:
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    kwargs = {"minecraft_dir": tmp_path / "minecraft"} if store_type is Versions else {}
    first = store_type(storage_dir=tmp_path, **kwargs)
    same = store_type(storage_dir=alias_dir / "..", **kwargs)
    separate = store_type(storage_dir=tmp_path / "other", **kwargs)

    assert first._lock is same._lock
    assert first._lock is not separate._lock
