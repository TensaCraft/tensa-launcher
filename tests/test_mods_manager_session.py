from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from launcher.pages.mods_manager import ModsManagerPage


@dataclass
class PendingTask:
    handler: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    cancelled: bool = False
    callbacks: list[Any] = field(default_factory=list)

    def cancel(self) -> bool:
        self.cancelled = True
        return True

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)


class FakeTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _build_page(fake_app, monkeypatch) -> tuple[ModsManagerPage, list[PendingTask]]:
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    (version_root / "mods").mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    pending: list[PendingTask] = []

    def queue_task(handler, *args, **kwargs):
        task = PendingTask(handler, args, kwargs)
        pending.append(task)
        return task

    fake_app.page.run_task = queue_task
    return ModsManagerPage(fake_app, version), pending


def _installed_scan(pending: list[PendingTask], index: int = 0) -> PendingTask:
    scans = [task for task in pending if getattr(task.handler, "__name__", "") == "_load_installed_mods_async"]
    return scans[index]


def _mod(filename: str) -> dict[str, Any]:
    return {
        "filename": filename,
        "path": filename,
        "enabled": True,
        "size": 1024,
    }


def test_initial_installed_mod_scan_is_deferred_to_page_task(fake_app, monkeypatch) -> None:
    scans: list[bool] = []

    async def run_blocking_immediately(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    fake_app.content.scan_installed_mods = lambda _path: scans.append(True) or []
    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", run_blocking_immediately)

    page, pending = _build_page(fake_app, monkeypatch)

    assert scans == []
    assert isinstance(page.installed_containers["mods"].controls[0].content, ft.ProgressRing)

    task = _installed_scan(pending)
    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert scans == [True]
    assert "mods" in page.loaded_content_keys


def test_initial_installed_mod_scan_refreshes_page_when_it_finishes_after_show(
    fake_app,
    monkeypatch,
) -> None:
    updates: list[str] = []

    async def complete_scan(*_args, **_kwargs):
        return [_mod("ready.jar")]

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", complete_scan)
    monkeypatch.setattr(
        "launcher.pages.mods_manager_installed.schedule_update",
        lambda _page: updates.append("installed"),
    )
    monkeypatch.setattr(
        "launcher.pages.mods_manager.schedule_update",
        lambda _page: updates.append("page"),
    )

    page, pending = _build_page(fake_app, monkeypatch)
    scan = _installed_scan(pending)

    assert scan.args[1] is True
    assert page.is_loading is True
    page.after_show()
    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    assert updates == ["page"]
    assert [mod["filename"] for mod in page.installed_mods] == ["ready.jar"]


def test_installed_content_search_filters_local_items(fake_app, monkeypatch) -> None:
    page, _pending = _build_page(fake_app, monkeypatch)
    sodium = _mod("sodium-fabric.jar") | {"name": "Sodium"}
    iris = _mod("iris-fabric.jar") | {"name": "Iris Shaders"}
    page.installed_items["mods"] = [sodium, iris]

    page.installed_search_queries["mods"] = "sodium"

    assert page._filtered_installed_items("mods") == [sodium]


@pytest.mark.parametrize("invalidation", ["content-tab", "inner-tab", "version"])
def test_installed_mod_scan_ignores_stale_result_after_context_change(
    fake_app,
    monkeypatch,
    invalidation: str,
) -> None:
    async def complete_scan(*_args, **_kwargs):
        return [_mod("stale.jar")]

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", complete_scan)
    page, pending = _build_page(fake_app, monkeypatch)
    scan = _installed_scan(pending)
    loading_controls = list(page.installed_containers["mods"].controls)

    if invalidation == "content-tab":
        page._switch_content_tab("resourcepacks")
    elif invalidation == "inner-tab":
        page._switch_inner_tab("modrinth")
    else:
        page._after_embedded_version_save(
            SimpleNamespace(
                name="Fabric replacement",
                version_id="fabric-replacement",
                client="fabric",
                loader="fabric",
                path=page.version.path,
            )
        )

    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    assert scan.cancelled is True
    assert page.installed_containers["mods"].controls == loading_controls
    assert page.installed_mods == []
    assert "mods" not in page.loaded_content_keys


def test_mods_manager_dispose_cancels_tasks_and_timer_and_ignores_late_error(
    fake_app,
    monkeypatch,
) -> None:
    errors: list[str] = []

    async def fail_scan(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", fail_scan)
    fake_app.log.error = errors.append
    page, pending = _build_page(fake_app, monkeypatch)
    scan = _installed_scan(pending)
    loading_controls = list(page.installed_containers["mods"].controls)
    timer = FakeTimer()
    page._search_timer = timer

    page.before_hide()
    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    assert scan.cancelled is True
    assert timer.cancelled is True
    assert errors == []
    assert page.installed_containers["mods"].controls == loading_controls


def test_modrinth_install_task_is_owned_by_page_session(fake_app, monkeypatch) -> None:
    page, pending = _build_page(fake_app, monkeypatch)
    mod = {"project_id": "fabric-api", "title": "Fabric API"}

    page._install_mod(mod)

    install_tasks = [
        task
        for task in pending
        if getattr(task.handler, "__name__", "") == "_install_mod_async"
    ]
    assert len(install_tasks) == 1

    page.before_hide()

    assert install_tasks[0].cancelled is True


def test_modrinth_install_rejected_by_closed_session_resets_install_state(fake_app, monkeypatch) -> None:
    page, pending = _build_page(fake_app, monkeypatch)
    page.before_hide()

    page._install_mod({"project_id": "fabric-api", "title": "Fabric API"})

    assert page.content_installing is False
    assert all(getattr(task.handler, "__name__", "") != "_install_mod_async" for task in pending)


def test_overlapping_installed_mod_refresh_is_latest_wins(fake_app, monkeypatch) -> None:
    results = iter(
        [
            [_mod("latest.jar")],
            [_mod("stale.jar")],
        ]
    )

    async def complete_scan(*_args, **_kwargs):
        return next(results)

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", complete_scan)
    page, pending = _build_page(fake_app, monkeypatch)
    first_scan = _installed_scan(pending)

    page._rebuild_installed_mods(update=False)
    latest_scan = _installed_scan(pending, 1)

    asyncio.run(latest_scan.handler(*latest_scan.args, **latest_scan.kwargs))
    asyncio.run(first_scan.handler(*first_scan.args, **first_scan.kwargs))

    assert first_scan.cancelled is True
    assert [mod["filename"] for mod in page.installed_mods] == ["latest.jar"]
    assert "mods" in page.loaded_content_keys


def test_installed_mod_worker_exception_replaces_loading_state(fake_app, monkeypatch) -> None:
    errors: list[str] = []

    async def fail_scan(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", fail_scan)
    fake_app.log.error = errors.append
    page, pending = _build_page(fake_app, monkeypatch)
    scan = _installed_scan(pending)

    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    state = page.installed_containers["mods"].controls[0]
    assert isinstance(state, ft.Container)
    assert isinstance(state.content, ft.Row)
    assert state.content.controls[1].value == "unknown_error"
    assert page.is_loading is False
    assert "mods" not in page.loaded_content_keys
    assert errors == ["Failed to scan installed mods: OSError('locked')"]
