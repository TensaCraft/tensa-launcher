import json
import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationCoordinator,
)
from launcher.application.shared_resources import SharedResourceBusy, SharedResourceCoordinator


def _hold_operation_in_child(
    coordinator_kind: str,
    path: str,
    ready,
    release,
    stale_pid: int | None,
):
    try:
        if coordinator_kind == "instance":
            coordinator = InstanceOperationCoordinator()
            lease = coordinator.try_acquire(path, "child_operation")
            if stale_pid is not None:
                lease._file_lock._write_owner_metadata(
                    replace(lease._file_lock.owner, pid=stale_pid),
                )
            ready.put(("acquired", str(lease._file_lock.path), os.getpid()))
            if not release.wait(20):
                raise TimeoutError("Parent did not release the child instance lock")
            lease.release()
        else:
            coordinator = SharedResourceCoordinator()
            with coordinator.operation(path, "child_operation"):
                operation = next(iter(coordinator._active.values()))
                ready.put(("acquired", str(operation.file_lock.path), os.getpid()))
                if not release.wait(20):
                    raise TimeoutError("Parent did not release the child shared-resource lock")
    except BaseException as error:
        ready.put(("error", f"{type(error).__name__}: {error}"))
        raise


@contextmanager
def _operation_held_by_child(
    coordinator_kind: str,
    path: Path,
    *,
    stale_pid: int | None = None,
):
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    process = context.Process(
        target=_hold_operation_in_child,
        args=(coordinator_kind, str(path), ready, release, stale_pid),
    )
    process.start()
    try:
        status, *details = ready.get(timeout=20)
        assert status == "acquired", details
        yield process, release, Path(details[0]), details[1]
    finally:
        release.set()
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        ready.close()
        ready.join_thread()
    assert process.exitcode == 0


def test_instance_operation_rejects_path_alias_conflict(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    instance = tmp_path / "games" / "demo"
    alias = instance / ".." / "demo"

    with coordinator.operation(instance, "sync"):
        with pytest.raises(InstanceOperationBusy) as error:
            coordinator.try_acquire(alias, "launch")

    assert error.value.active_kind == "sync"
    assert coordinator.active_kind(instance) is None


def test_instance_operation_releases_after_exception(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    instance = tmp_path / "game"

    with pytest.raises(RuntimeError, match="failure"):
        with coordinator.operation(instance, "install"):
            raise RuntimeError("failure")

    with coordinator.operation(instance, "repair") as lease:
        assert lease.kind == "repair"


def test_instance_operation_accepts_explicit_borrowed_lease(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    instance = tmp_path / "game"

    with coordinator.operation(instance, "launch") as lease:
        with coordinator.operation(instance, "sync", lease=lease) as borrowed:
            assert borrowed is lease
            assert coordinator.active_kind(instance) == "launch"

    assert coordinator.active_kind(instance) is None


def test_instance_operation_rejects_borrowed_lease_for_another_path(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()

    with coordinator.operation(tmp_path / "first", "launch") as lease:
        with pytest.raises(ValueError, match="invalid"):
            with coordinator.operation(tmp_path / "second", "sync", lease=lease):
                pass


def test_instance_operations_allow_different_paths(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    first = tmp_path / "first"
    second = tmp_path / "second"

    with coordinator.operation(first, "sync"):
        with coordinator.operation(second, "sync"):
            assert coordinator.active_kind(first) == "sync"
            assert coordinator.active_kind(second) == "sync"


def test_instance_operation_lease_can_be_released_from_another_thread(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    instance = tmp_path / "game"
    lease = coordinator.try_acquire(instance, "sync")

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(lease.release).result(timeout=5)

    assert coordinator.active_kind(instance) is None
    lease.release()


def test_borrowed_lease_stays_registered_until_borrower_exits(tmp_path: Path):
    coordinator = InstanceOperationCoordinator()
    instance = tmp_path / "game"
    lease = coordinator.try_acquire(instance, "launch")

    with coordinator.operation(instance, "sync", lease=lease):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(lease.release).result(timeout=5)
        assert lease.active is True
        with pytest.raises(InstanceOperationBusy):
            coordinator.try_acquire(instance, "delete")

    assert lease.active is False
    with coordinator.operation(instance, "delete"):
        assert coordinator.active_kind(instance) == "delete"


def test_shared_resource_operation_is_reentrant_on_owner_thread(tmp_path: Path):
    coordinator = SharedResourceCoordinator()
    shared_root = tmp_path / "minecraft"

    with coordinator.operation(shared_root, "loader_install"):
        with coordinator.operation(shared_root, "java_runtime"):
            assert coordinator.active_kind(shared_root) == "loader_install"

    assert coordinator.active_kind(shared_root) is None


def test_shared_resource_operation_rejects_another_thread(tmp_path: Path):
    coordinator = SharedResourceCoordinator()
    shared_root = tmp_path / "minecraft"

    def attempt():
        with pytest.raises(SharedResourceBusy) as error:
            with coordinator.operation(shared_root, "java_runtime"):
                pass
        return error.value.active_kind

    with coordinator.operation(shared_root, "loader_install"):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(attempt).result(timeout=5) == "loader_install"


@pytest.mark.parametrize(
    ("coordinator_kind", "busy_error"),
    [
        pytest.param("instance", InstanceOperationBusy, id="instance"),
        pytest.param("shared", SharedResourceBusy, id="shared"),
    ],
)
def test_os_lock_rejects_second_process_then_allows_after_normal_release(
    tmp_path: Path,
    coordinator_kind: str,
    busy_error: type[InstanceOperationBusy] | type[SharedResourceBusy],
):
    root = tmp_path / coordinator_kind / "minecraft"

    with _operation_held_by_child(coordinator_kind, root) as (_, _, lock_path, child_pid):
        if coordinator_kind == "instance":
            coordinator = InstanceOperationCoordinator()
            with pytest.raises(busy_error) as error:
                coordinator.try_acquire(root, "parent_operation")
        else:
            coordinator = SharedResourceCoordinator()
            with pytest.raises(busy_error) as error:
                with coordinator.operation(root, "parent_operation"):
                    pass

        assert error.value.active_kind == "child_operation"
        assert error.value.owner is not None
        assert error.value.owner.pid == child_pid
        assert lock_path.is_file()

    assert lock_path.is_file()
    with coordinator.operation(root, "parent_operation"):
        assert coordinator.active_kind(root) == "parent_operation"


def test_os_lock_is_released_when_holder_process_terminates(tmp_path: Path):
    root = tmp_path / "crash" / "minecraft"
    child_code = """
import sys
from launcher.application.instance_operations import InstanceOperationCoordinator

lease = InstanceOperationCoordinator().try_acquire(sys.argv[1], "child_operation")
print("acquired", flush=True)
sys.stdin.read()
lease.release()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "acquired"
        coordinator = InstanceOperationCoordinator()
        with pytest.raises(InstanceOperationBusy):
            coordinator.try_acquire(root, "parent_operation")

        process.terminate()
        process.wait(timeout=20)

        with coordinator.operation(root, "parent_operation"):
            assert coordinator.active_kind(root) == "parent_operation"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_stale_pid_metadata_does_not_bypass_active_os_lock(tmp_path: Path):
    root = tmp_path / "stale-metadata" / "minecraft"
    stale_pid = 2_147_483_647
    coordinator = InstanceOperationCoordinator()

    with _operation_held_by_child("instance", root, stale_pid=stale_pid) as (_, _, lock_path, _):
        with pytest.raises(InstanceOperationBusy) as error:
            coordinator.try_acquire(root, "parent_operation")

        assert error.value.active_kind == "child_operation"
        assert error.value.owner is not None
        assert error.value.owner.pid == stale_pid

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["pid"] == stale_pid
    with coordinator.operation(root, "parent_operation"):
        assert coordinator.active_kind(root) == "parent_operation"
