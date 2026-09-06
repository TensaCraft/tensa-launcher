from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from launcher.application.catalog import CatalogPage
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


@pytest.mark.parametrize("fails", [False, True])
def test_deferred_tab_scan_updates_visible_page_even_when_started_without_update(
    fake_app, monkeypatch, fails,
):
    updates = []

    async def complete_scan(*_args, **_kwargs):
        if fails:
            raise OSError("locked")
        return [_mod("ready.jar")]

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", complete_scan)
    monkeypatch.setattr("launcher.pages.mods_manager.schedule_update", lambda _page: updates.append(True))
    monkeypatch.setattr("launcher.pages.mods_manager_installed.schedule_update", lambda _page: updates.append(True))
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page._rebuild_installed_mods(update=False)
    scan = _installed_scan(pending, 1)
    updates.clear()

    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    assert page.is_loading is False
    assert updates == [True]


def test_obsolete_queued_scan_does_not_start_disk_or_metadata_work(fake_app, monkeypatch):
    scans = []

    async def complete_scan(*_args, **_kwargs):
        scans.append(True)
        return []

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", complete_scan)
    page, pending = _build_page(fake_app, monkeypatch)
    scan = _installed_scan(pending)
    page._rebuild_installed_mods(update=False)

    asyncio.run(scan.handler(*scan.args, **scan.kwargs))

    assert scans == []
    assert page.is_loading is True


def test_rejected_installed_scan_clears_loading(fake_app, monkeypatch):
    page, _pending = _build_page(fake_app, monkeypatch)

    def reject(*_args, **_kwargs):
        raise RuntimeError("Event loop is closed")

    fake_app.page.run_task = reject
    page._rebuild_installed_mods(update=False)

    assert page.is_loading is False
    assert not isinstance(page.installed_containers["mods"].controls[0].content, ft.ProgressRing)


def _search_page(fake_app, monkeypatch):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page.current_inner_tab = "modrinth"
    workers = []

    class QueuedThread:
        def __init__(self, *, target, args, **_kwargs):
            workers.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr("launcher.pages.mods_manager_search.threading.Thread", QueuedThread)
    return page, pending, workers


def test_search_status_refresh_preserves_inflight_loading_and_does_not_duplicate_results(fake_app, monkeypatch):
    page, _pending, _workers = _search_page(fake_app, monkeypatch)
    page._create_search_result_card = lambda item: ft.Text(item["title"])
    page.search_result_items = [{"title": "old"}]
    page._load_search_page(0)
    loading = page.search_results_container.controls[0]

    page._refresh_visible_modrinth_search_results()

    assert page.search_results_container.controls == [loading]
    result = CatalogPage(items=[{"title": "new"}], total_results=1)
    page._apply_search_results(page.search_state.token, loading, result)
    assert [control.value for control in page.search_results_container.controls] == ["new"]


def test_search_status_refresh_does_not_resurrect_results_after_error(fake_app, monkeypatch):
    page, _pending, _workers = _search_page(fake_app, monkeypatch)
    page._create_search_result_card = lambda item: ft.Text(item["title"])
    page.search_result_items = [{"title": "old"}]
    page._load_search_page(0)
    loading = page.search_results_container.controls[0]
    page._apply_search_error(page.search_state.token, loading, "offline")
    error_controls = list(page.search_results_container.controls)

    page._refresh_visible_modrinth_search_results()

    assert page.search_results_container.controls == error_controls
    assert page.search_result_items == []


def test_search_worker_uses_request_facets_and_limit_snapshot(fake_app, monkeypatch):
    page, _pending, workers = _search_page(fake_app, monkeypatch)
    requests = []
    fake_app.catalog.search_mods = lambda query, **kwargs: requests.append(kwargs) or CatalogPage()
    expected_facets = fake_app.modrinth_mods.build_search_facets(
        page.version, project_type="mod", game_version=page.selected_minecraft_version,
    )
    expected_limit = page.search_state.limit
    page._load_search_page(0)
    page.version = SimpleNamespace(loader="forge", client="forge", version="1.0")
    page.search_state.limit = 1

    worker, args = workers[0]
    worker(*args)

    assert requests == [{"facets": expected_facets, "offset": 0, "limit": expected_limit}]


def test_superseded_search_worker_does_not_call_api(fake_app, monkeypatch):
    page, _pending, workers = _search_page(fake_app, monkeypatch)
    requests = []
    fake_app.catalog.search_mods = lambda *args, **kwargs: requests.append(args) or CatalogPage()
    page._load_search_page(0)
    page._load_search_page(0)

    worker, args = workers[0]
    worker(*args)

    assert requests == []


def test_search_submit_invalidates_already_queued_debounce_callback(fake_app, monkeypatch):
    page, _pending, workers = _search_page(fake_app, monkeypatch)
    timers = []
    callbacks = []

    class QueuedTimer:
        def __init__(self, interval, function, args=None, kwargs=None):
            self.function = function
            self.args = args or ()
            self.kwargs = kwargs or {}
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(threading, "Timer", QueuedTimer)
    monkeypatch.setattr(
        "launcher.pages.mods_manager_search.invoke_on_ui",
        lambda _page, callback, *args: callbacks.append((callback, args)),
    )
    page.search_input.value = "sodium"
    page.on_search_change(SimpleNamespace(control=page.search_input))
    timer = timers[0]
    timer.function(*timer.args, **timer.kwargs)
    page._search_mods()

    for callback, args in callbacks:
        callback(*args)

    assert len(workers) == 1


def test_restore_worker_uses_validated_mod_directory_after_page_context_changes(fake_app, monkeypatch):
    page, _pending = _build_page(fake_app, monkeypatch)
    original_dir = page.mods_dir
    mod = _mod("old.jar") | {"path": str(original_dir / "old.jar")}
    calls = []
    fake_app.content.has_backup = lambda directory, filename: calls.append(("check", directory)) or True
    fake_app.content.restore_backup = lambda directory, item: calls.append(("restore", directory))
    page.mods_dir = original_dir.parent / "replacement" / "mods"

    page._restore_mod_backup_worker(mod)

    assert calls == [("check", original_dir), ("restore", original_dir)]


def test_update_confirmation_after_disposal_does_not_leave_busy_operation(fake_app, monkeypatch):
    page, _pending = _build_page(fake_app, monkeypatch)
    confirmations = []
    fake_app.feedback.confirm = lambda title, message, callback: confirmations.append(callback)
    page._update_mod(_mod("old.jar") | {"update_available": True, "latest_version": {}})
    page.before_hide()

    confirmations[0](True)

    assert not fake_app.feedback.is_busy()


@pytest.mark.parametrize("key", ["mods", "resourcepacks", "shaders"])
def test_completed_content_install_refreshes_visible_search_cards_once(fake_app, monkeypatch, key):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page.current_content_key = key
    page.current_inner_tab = "modrinth"
    refreshes = []
    page._refresh_visible_modrinth_search_results = lambda: refreshes.append(True)

    async def install(*_args, **_kwargs):
        return {}

    async def scan(*_args, **_kwargs):
        return []

    page._install_modrinth_candidates_transaction = install
    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", scan)
    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", scan)
    plan = SimpleNamespace(
        can_install=True,
        main=SimpleNamespace(title="Main", version_number="1"),
        install_order_with_optional=lambda _optional: [],
    )

    asyncio.run(page._install_modrinth_plan_async(plan, page._content_context()))
    if key != "mods":
        assert refreshes == []
        task = _pack_scan(pending, key)
        asyncio.run(task.handler(*task.args, **task.kwargs))

    assert refreshes == [True]


def test_install_worker_captures_version_before_thread_handoff(fake_app, monkeypatch):
    page, _pending = _build_page(fake_app, monkeypatch)
    original_version = page.version
    installed_versions = []

    async def replace_page_version_before_worker(fn, *args, **kwargs):
        page.version = SimpleNamespace(name="Replacement")
        return fn(*args, **kwargs)

    def install(_installer, version, candidates, **kwargs):
        installed_versions.append(version)
        return {}

    monkeypatch.setattr("launcher.pages.mods_manager_search.run_blocking", replace_page_version_before_worker)
    monkeypatch.setattr("launcher.pages.mods_manager_search.ModrinthContentInstaller.install", install)

    asyncio.run(page._install_modrinth_candidates_transaction([], page._content_context()))

    assert installed_versions == [original_version]


def test_search_thread_start_failure_clears_loading(fake_app, monkeypatch):
    page, _pending, _workers = _search_page(fake_app, monkeypatch)

    def reject(*args, **kwargs):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr("launcher.pages.mods_manager_search.threading.Thread", reject)
    page._load_search_page(0)

    assert page.search_state.loading is False
    assert page.search_result_items == []
    assert not isinstance(page.search_results_container.controls[0].content, ft.ProgressRing)


def _pack_scan(pending, key, index=0):
    return [
        task for task in pending
        if getattr(task.handler, "__name__", "") == "_load_installed_content_async"
        and task.args[0][2] == key
    ][index]


@pytest.mark.parametrize("key", ["resourcepacks", "shaders"])
def test_pack_scan_is_deferred_off_ui_and_reused_across_tab_switches(fake_app, monkeypatch, key):
    scans = []
    ui_thread = threading.get_ident()

    def scan(directory, **kwargs):
        scans.append((directory, threading.get_ident()))
        return []

    scanner = "scan_installed_resourcepacks" if key == "resourcepacks" else "scan_installed_shaderpacks"
    monkeypatch.setattr(fake_app.content, scanner, scan)
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    directory = getattr(page, page.content_configs[key]["directory_attr"])
    page._switch_content_tab(key)
    page._switch_content_tab("mods")
    page._switch_content_tab(key)

    assert scans == []
    assert isinstance(page.installed_containers[key].controls[0].content, ft.ProgressRing)
    task = _pack_scan(pending, key)
    assert len([item for item in pending if item.handler == task.handler]) == 1
    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert len(scans) == 1
    assert scans[0][0] == directory
    assert scans[0][1] != ui_thread
    assert key in page.loaded_content_keys
    assert not isinstance(page.installed_containers[key].controls[0].content, ft.ProgressRing)


@pytest.mark.parametrize("invalidation", ["hide", "version", "replace"])
def test_pack_scan_rejects_obsolete_callbacks(fake_app, monkeypatch, invalidation):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page._switch_content_tab("resourcepacks")
    task = _pack_scan(pending, "resourcepacks")
    scans = []

    async def scan(*args, **kwargs):
        scans.append(True)
        return []

    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", scan)
    if invalidation == "hide":
        page.before_hide()
    elif invalidation == "version":
        page._after_embedded_version_save(SimpleNamespace(
            name="Replacement", version_id="replacement", client="fabric", loader="fabric", path=page.version.path,
        ))
    else:
        page._rebuild_installed_content("resourcepacks")
    controls = list(page.installed_containers["resourcepacks"].controls)

    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert task.cancelled is True
    assert scans == []
    assert page.installed_containers["resourcepacks"].controls == controls
    assert "resourcepacks" not in page.loaded_content_keys


@pytest.mark.parametrize("fails", [False, True])
def test_pack_scan_completion_clears_spinner_and_updates_visible_tab(fake_app, monkeypatch, fails):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page._switch_content_tab("resourcepacks")
    task = _pack_scan(pending, "resourcepacks")
    updates = []

    async def scan(*args, **kwargs):
        if fails:
            raise OSError("locked")
        return []

    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", scan)
    monkeypatch.setattr("launcher.pages.mods_manager.schedule_update", lambda _page: updates.append(True))

    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert updates == [True]
    assert ("resourcepacks" in page.loaded_content_keys) is not fails
    assert not isinstance(page.installed_containers["resourcepacks"].controls[0].content, ft.ProgressRing)


def test_hidden_pack_scan_does_not_overwrite_or_refresh_current_tab(fake_app, monkeypatch):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page._switch_content_tab("resourcepacks")
    task = _pack_scan(pending, "resourcepacks")
    page._switch_content_tab("mods")
    controls = list(page.installed_containers["mods"].controls)
    updates = []

    async def scan(*args, **kwargs):
        return []

    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", scan)
    monkeypatch.setattr("launcher.pages.mods_manager.schedule_update", lambda _page: updates.append(True))

    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert "resourcepacks" in page.loaded_content_keys
    assert page.installed_containers["mods"].controls == controls
    assert updates == []


@pytest.mark.parametrize("invalidation", ["hide", "version", "replace"])
def test_inflight_pack_scan_ignores_late_completion(fake_app, monkeypatch, invalidation):
    page, pending = _build_page(fake_app, monkeypatch)
    page.after_show()
    page._switch_content_tab("resourcepacks")
    task = _pack_scan(pending, "resourcepacks")

    async def complete_after_invalidation(*args, **kwargs):
        if invalidation == "hide":
            page.before_hide()
        elif invalidation == "version":
            page._after_embedded_version_save(SimpleNamespace(
                name="Replacement", version_id="replacement", client="fabric", loader="fabric", path=page.version.path,
            ))
        else:
            page._rebuild_installed_content("resourcepacks", update=False)
        return []

    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", complete_after_invalidation)

    asyncio.run(task.handler(*task.args, **task.kwargs))

    assert "resourcepacks" not in page.loaded_content_keys
    assert isinstance(page.installed_containers["resourcepacks"].controls[0].content, ft.ProgressRing)
