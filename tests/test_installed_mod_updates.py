from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from launcher.pages.mods_manager import ModsManagerPage


@dataclass
class PendingTask:
    handler: Any
    args: tuple
    kwargs: dict
    cancelled: bool = False

    def cancel(self):
        self.cancelled = True

    async def run(self):
        return await self.handler(*self.args, **self.kwargs)


def _mod(**changes):
    return {
        "name": "Example",
        "filename": "example.jar",
        "path": "example.jar",
        "enabled": True,
        "size": 10,
        "version": "local-version",
        "modrinth_project_id": "project-id",
        "modrinth_version_number": "1.0",
        "modrinth_provenance_authoritative": True,
        "modrinth_hash_algorithm": "sha512",
        "modrinth_file_hash": "a" * 128,
        **changes,
    }


@pytest.fixture
def installed_page(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = version.loader = "fabric"
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    (version_root / "mods").mkdir(parents=True)
    version.path = str(version_root)
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))
    page = ModsManagerPage(fake_app, version)
    page.after_show()
    page._apply_installed_content("mods", [_mod()], update=False)
    page.build_installed_updates_toolbar()
    pending = []

    def queue_task(handler, *args, **kwargs):
        task = PendingTask(handler, args, kwargs)
        pending.append(task)
        return task

    fake_app.page.run_task = queue_task
    return page, pending


def _checks(pending):
    return [task for task in pending if task.handler.__name__ == "_check_installed_updates_async"]


def _texts(control):
    if isinstance(control, ft.Text):
        yield str(control.value)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _texts(content)
    for child in getattr(control, "controls", []):
        yield from _texts(child)


def test_manual_check_is_deferred_off_thread_and_preserves_list(installed_page, monkeypatch):
    page, pending = installed_page
    items = page.installed_items["mods"]
    controls = list(page.installed_containers["mods"].controls)
    main_thread = threading.get_ident()
    calls = []

    def check(installed, version):
        assert threading.get_ident() != main_thread
        assert installed == items and installed is not items
        assert installed[0] is not items[0]
        assert version is page.version
        calls.append(True)
        return [dict(installed[0], update_checked=True, update_available=True, latest_version={"version_number": "2.0"})]

    monkeypatch.setattr(page.app.modrinth_mods, "check_installed_updates", check, raising=False)
    page._installed_updates_button.on_click(None)

    assert calls == []
    assert page.installed_containers["mods"].controls == controls
    assert page._installed_updates_progress.visible
    assert page._installed_updates_button.disabled
    assert "installed_updates_checking" in page._installed_updates_label.value
    assert page.is_loading is False
    assert page.request_installed_updates_check() is None
    assert len(_checks(pending)) == 1

    asyncio.run(_checks(pending)[0].run())

    assert calls == [True]
    assert "update_available" not in items[0]
    assert page.installed_mods[0]["update_available"]
    assert page._installed_updates_status == "available"
    assert page._installed_updates_count == 1
    assert not page._installed_updates_progress.visible
    assert not page._installed_updates_button.disabled
    assert page.installed_containers["mods"].controls[0].content.controls[1].controls[0].icon == ft.Icons.UPDATE


def test_success_without_updates_marks_current(installed_page, monkeypatch):
    page, pending = installed_page
    monkeypatch.setattr(
        page.app.modrinth_mods, "check_installed_updates",
        lambda items, _version: [dict(item, update_available=False, update_checked=True) for item in items], raising=False,
    )
    page.request_installed_updates_check()
    asyncio.run(_checks(pending)[0].run())

    assert page._installed_updates_status == "current"
    assert "installed_updates_current" in page._installed_updates_label.value


def test_network_error_keeps_cards_and_does_not_claim_current(installed_page, monkeypatch):
    page, pending = installed_page
    controls = list(page.installed_containers["mods"].controls)
    errors = []
    page.app.log.error = errors.append

    def fail(items, _version):
        items[0]["version"] = "worker mutation"
        raise ConnectionError("offline")

    monkeypatch.setattr(page.app.modrinth_mods, "check_installed_updates", fail, raising=False)
    page.request_installed_updates_check()
    asyncio.run(_checks(pending)[0].run())

    assert page.installed_containers["mods"].controls == controls
    assert page.installed_mods[0]["version"] == "local-version"
    assert page._installed_updates_status == "failed"
    assert "installed_updates_failed" in page._installed_updates_label.value
    assert page._installed_updates_label.color == page.app.theme.error
    assert not page._installed_updates_button.disabled
    assert len(errors) == 1 and "offline" in errors[0]


@pytest.mark.parametrize("stale", ["hidden", "content-tab", "inner-tab", "version", "rescan", "list"])
@pytest.mark.parametrize("failure", [False, True])
def test_late_check_result_or_error_is_ignored(installed_page, monkeypatch, stale, failure):
    page, pending = installed_page
    errors = []
    page.app.log.error = errors.append
    monkeypatch.setattr(page.app.modrinth_mods, "check_installed_updates", lambda *_args: [], raising=False)

    async def scenario():
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def check_in_worker(_fn, *_args):
            entered.set()
            await finish.wait()
            if failure:
                raise ConnectionError("late error")
            return [_mod(update_available=True, latest_version={"version_number": "late"})]

        monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", check_in_worker)
        page.request_installed_updates_check()
        task = asyncio.create_task(_checks(pending)[0].run())
        await entered.wait()
        if stale == "hidden":
            page.before_hide()
        elif stale == "content-tab":
            page._switch_content_tab("resourcepacks")
        elif stale == "inner-tab":
            page._switch_inner_tab("modrinth")
        elif stale == "version":
            page.version = SimpleNamespace(name="Other")
        elif stale == "rescan":
            page._rebuild_installed_mods(update=False)
        else:
            page._apply_installed_content("mods", [_mod(name="Replacement")], update=False)
        controls = list(page.installed_containers["mods"].controls)
        label = page._installed_updates_label.value
        finish.set()
        await task
        assert page.installed_containers["mods"].controls == controls
        assert page._installed_updates_label.value == label

    asyncio.run(scenario())

    assert errors == []
    assert not page.installed_mods[0].get("update_available")


def test_headless_scans_never_auto_check_even_when_coroutines_are_run(installed_page, monkeypatch):
    page, pending = installed_page
    calls = []
    monkeypatch.setattr(
        page.app.modrinth_mods, "check_installed_updates", lambda *_args: calls.append(True), raising=False,
    )
    monkeypatch.setattr(page, "_scan_installed_mods_for", lambda *_args: [_mod()])
    page._rebuild_installed_mods(update=False)
    scan = next(task for task in pending if task.handler.__name__ == "_load_installed_mods_async")
    asyncio.run(scan.run())

    assert not page._can_auto_check_installed_updates()
    assert calls == []
    assert _checks(pending) == []


def test_real_session_scan_schedules_check_after_displaying_local_items(installed_page, monkeypatch):
    page, pending = installed_page
    monkeypatch.setattr(page, "_can_auto_check_installed_updates", lambda: True)
    monkeypatch.setattr(page, "_scan_installed_mods_for", lambda *_args: [_mod(name="Scanned")])
    page._rebuild_installed_mods(update=False)
    scan = next(task for task in pending if task.handler.__name__ == "_load_installed_mods_async")
    asyncio.run(scan.run())

    assert page.installed_mods[0]["name"] == "Scanned"
    assert page._installed_updates_status == "checking"
    assert len(_checks(pending)) == 1
    assert "Scanned" in list(_texts(page.installed_containers["mods"]))


@pytest.mark.parametrize("scheduler", ["headless", "raises", "closed", "empty"])
def test_manual_check_handles_missing_task_without_network(installed_page, monkeypatch, scheduler):
    page, pending = installed_page
    calls = []
    monkeypatch.setattr(
        page.app.modrinth_mods, "check_installed_updates", lambda *_args: calls.append(True), raising=False,
    )
    if scheduler == "headless":
        page.app.page.run_task = lambda *_args, **_kwargs: None
    elif scheduler == "raises":
        def reject(*_args, **_kwargs):
            raise RuntimeError("closed loop")
        page.app.page.run_task = reject
    elif scheduler == "closed":
        page.before_hide()
    else:
        page._apply_installed_content("mods", [], update=False)

    page.request_installed_updates_check()

    assert calls == []
    assert _checks(pending) == []
    expected = "failed" if scheduler == "raises" else "empty" if scheduler == "empty" else "idle"
    assert page._installed_updates_status == expected
    assert not page._installed_updates_progress.visible


def test_enabled_update_uses_normal_dependency_confirmation_route(installed_page, monkeypatch):
    page, pending = installed_page
    mod = _mod(update_available=True, latest_version={"version_number": "2.0"})
    page._apply_installed_content("mods", [mod], update=False)
    plan = SimpleNamespace(main=object(), requires_confirmation=True)
    plans = []
    dialogs = []
    page.app.feedback.confirm = lambda _title, _message, callback: callback(True)

    def build_plan(project, version, **kwargs):
        assert threading.get_ident() != main_thread
        plans.append((project, version, kwargs))
        return plan

    main_thread = threading.get_ident()
    monkeypatch.setattr(page.app.content, "scan_installed_mods", lambda _directory: [dict(mod)])
    monkeypatch.setattr(page.app.modrinth_mods, "build_dependency_plan", build_plan)
    monkeypatch.setattr(
        "launcher.pages.mods_manager_search.invoke_on_ui", lambda _page, fn, *args: fn(*args),
    )
    page._show_modrinth_dependency_plan_dialog = lambda *args: dialogs.append(args)

    async def reject_direct_download(*_args, **_kwargs):
        pytest.fail("Installed updates must go through the dependency planner")

    page._download_modrinth_candidate = reject_direct_download
    page._update_mod(mod)
    install = next(task for task in pending if task.handler.__name__ == "_install_mod_async")
    asyncio.run(install.run())

    assert plans[0][0]["project_id"] == "project-id"
    assert plans[0][1] is page.version
    assert plans[0][2]["installed_items"] == [mod]
    assert plans[0][2]["project_type"] == "mod"
    assert dialogs[0][0] is plan
    assert mod["update_available"] is True
    assert page.content_installing is False


@pytest.mark.parametrize("changes", [{"enabled": False}, {"path": "example.jar.disabled"}])
def test_disabled_mod_cannot_be_updated_or_accidentally_enabled(installed_page, changes):
    page, pending = installed_page
    warnings = []
    page.app.feedback.warning = warnings.append
    mod = _mod(update_available=True, latest_version={"version_number": "2.0"}, **changes)
    card = page._create_installed_mod_card(mod)
    button = card.content.controls[1].controls[0]

    assert button.disabled
    assert button.on_click is None
    assert button.tooltip == "installed_mod_update_disabled"
    page._update_mod(mod)

    assert warnings == ["installed_mod_update_disabled"]
    assert not any(task.handler.__name__ == "_install_mod_async" for task in pending)
    assert mod["enabled"] == changes.get("enabled", True)


def test_card_shows_verified_current_and_new_version(installed_page):
    page, _pending = installed_page
    mod = _mod(update_available=True, latest_version={"version_number": "2.0"})
    card = page._create_installed_mod_card(mod)
    text = " ".join(_texts(card))

    tooltip = str(card.content.controls[1].controls[0].tooltip)
    assert "installed_mod_update_versions" in tooltip
    assert "1.0" in text and "2.0" in tooltip
    assert "local-version" not in text
    assert not card.content.controls[1].controls[0].disabled


@pytest.mark.parametrize("invalidation", ["hide", "tab", "rescan"])
def test_update_confirmation_rejects_obsolete_context(installed_page, invalidation):
    page, pending = installed_page
    confirmations = []
    page.app.feedback.confirm = lambda _title, _message, callback: confirmations.append(callback)
    page._update_mod(_mod(update_available=True, latest_version={"version_number": "2.0"}))
    if invalidation == "hide":
        page.before_hide()
    elif invalidation == "tab":
        page._switch_inner_tab("modrinth")
    else:
        page._rebuild_installed_mods(update=False)
    confirmations[0](True)

    assert not any(task.handler.__name__ == "_install_mod_async" for task in pending)
    assert not page.app.feedback.is_busy()


@pytest.mark.parametrize("has_update", [False, True])
def test_partial_check_reports_unchecked_enabled_mods(installed_page, monkeypatch, has_update):
    page, pending = installed_page
    result = [
        _mod(update_available=has_update, update_checked=True),
        _mod(filename="unknown.jar", modrinth_project_id=None),
        _mod(filename="disabled.jar", enabled=False),
    ]
    monkeypatch.setattr(page.app.modrinth_mods, "check_installed_updates", lambda *_args: result)
    page.request_installed_updates_check()
    asyncio.run(_checks(pending)[0].run())

    assert page._installed_updates_status == ("available" if has_update else "unchecked")
    assert page._installed_updates_unchecked_count == 1
    assert "installed_updates_current" not in page._installed_updates_label.value
    assert page.trans("installed_updates_unchecked", count=1) in page._installed_updates_label.value


def test_check_without_enabled_mods_does_not_claim_current(installed_page, monkeypatch):
    page, pending = installed_page
    monkeypatch.setattr(
        page.app.modrinth_mods, "check_installed_updates", lambda *_args: [_mod(enabled=False)],
    )
    page.request_installed_updates_check()
    asyncio.run(_checks(pending)[0].run())

    assert page._installed_updates_status == "no_enabled"


def test_installed_card_opens_modrinth_project_site(installed_page):
    page, _pending = installed_page
    opened = []
    page._open_modrinth_project_url = opened.append
    card = page._create_installed_mod_card(_mod(modrinth_project_slug="example"))
    button = next(control for control in card.content.controls[1].controls if control.icon == ft.Icons.OPEN_IN_NEW_ROUNDED)

    assert button.tooltip == "open_on_site"
    button.on_click(None)
    assert opened == ["https://modrinth.com/mod/example"]


def test_unidentified_card_does_not_link_local_mod_id_to_modrinth(installed_page):
    page, _pending = installed_page
    card = page._create_installed_mod_card(_mod(modrinth_project_id=None, id="local-id"))

    assert all(control.icon != ft.Icons.OPEN_IN_NEW_ROUNDED for control in card.content.controls[1].controls)


def test_current_check_cancellation_resets_loading(installed_page, monkeypatch):
    page, pending = installed_page

    async def cancelled(*_args):
        raise asyncio.CancelledError

    monkeypatch.setattr("launcher.pages.mods_manager_installed.run_blocking", cancelled)
    page.request_installed_updates_check()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_checks(pending)[0].run())

    assert page._installed_updates_status == "idle"
    assert not page._installed_updates_button.disabled


def test_post_install_scan_can_schedule_auto_check_before_install_flag_clears(installed_page, monkeypatch):
    page, pending = installed_page
    page.content_installing = True
    monkeypatch.setattr(page, "_can_auto_check_installed_updates", lambda: True)
    monkeypatch.setattr(page, "_scan_installed_mods_for", lambda *_args: [_mod(name="Updated")])

    assert page.request_installed_updates_check() is None
    asyncio.run(page._refresh_installed_mods_after_mutation())

    assert page.installed_mods[0]["name"] == "Updated"
    assert len(_checks(pending)) == 1


def test_update_without_confirmation_installs_whole_dependency_transaction(installed_page, monkeypatch):
    page, pending = installed_page
    mod = _mod(update_available=True, latest_version={"version_number": "2.0"})
    page._apply_installed_content("mods", [mod], update=False)
    candidates = [object(), object()]
    plan = SimpleNamespace(
        main=SimpleNamespace(title="Example", version_number="2.0"),
        requires_confirmation=False,
        can_install=True,
        install_order=candidates,
    )
    installed = []
    refreshed = []
    page.app.feedback.confirm = lambda _title, _message, callback: callback(True)
    monkeypatch.setattr(page.app.modrinth_mods, "build_dependency_plan", lambda *_args, **_kwargs: plan)

    async def install_transaction(items, context):
        installed.append((items, context))

    async def refresh():
        refreshed.append(True)

    page._install_modrinth_candidates_transaction = install_transaction
    page._refresh_installed_mods_after_mutation = refresh
    page._update_mod(mod)
    task = next(task for task in pending if task.handler.__name__ == "_install_mod_async")
    asyncio.run(task.run())

    assert installed[0][0] is candidates
    assert installed[0][1]["version"] is page.version
    assert installed[0][1]["key"] == "mods"
    assert refreshed == [True]


@pytest.mark.parametrize("navigation", ["inner", "content"])
@pytest.mark.parametrize("status,count,unchecked", [
    ("available", 2, 1), ("current", 0, 0), ("unchecked", 0, 2),
    ("no_enabled", 0, 0), ("empty", 0, 0), ("failed", 0, 0),
])
def test_completed_update_status_survives_tab_round_trip(installed_page, navigation, status, count, unchecked):
    page, pending = installed_page
    page._set_installed_updates_status(status, count=count, unchecked=unchecked, update=False)
    label = page._installed_updates_label.value
    page.search_results_container.controls = [ft.Text("cached search")]

    if navigation == "inner":
        page._switch_inner_tab("modrinth")
        page._switch_inner_tab("installed")
    else:
        page._switch_content_tab("resourcepacks")
        page._switch_content_tab("mods")

    assert page._installed_updates_status == status
    assert page._installed_updates_count == count
    assert page._installed_updates_unchecked_count == unchecked
    assert page._installed_updates_label.value == label
    assert not page._installed_updates_progress.visible
    assert not page._installed_updates_button.disabled
    assert _checks(pending) == []


@pytest.mark.parametrize("invalidation", ["rescan", "mutation"])
def test_inventory_invalidation_still_clears_completed_status(installed_page, invalidation):
    page, _pending = installed_page
    page._set_installed_updates_status("available", count=2, unchecked=1, update=False)

    if invalidation == "rescan":
        page._rebuild_installed_mods(update=False)
    else:
        page._cancel_installed_updates_check()

    assert page._installed_updates_status == "idle"
    assert page._installed_updates_count == page._installed_updates_unchecked_count == 0


def test_navigation_cancels_running_check_without_leaving_spinner(installed_page):
    page, pending = installed_page
    page.search_results_container.controls = [ft.Text("cached search")]
    page.request_installed_updates_check()

    page._switch_inner_tab("modrinth")
    page._switch_inner_tab("installed")

    assert _checks(pending)[0].cancelled
    assert page._installed_updates_status == "idle"
    assert not page._installed_updates_progress.visible
    assert not page._installed_updates_button.disabled


@pytest.mark.parametrize("has_update", [False, True])
def test_completed_check_rebuilds_cached_search_cards_from_fresh_inventory(installed_page, monkeypatch, has_update):
    page, pending = installed_page
    project = {"project_id": "project-id", "title": "Example"}
    page.search_result_items = [project]
    page.search_state.query = "example"
    page.search_state.offset = 20
    page._create_search_result_card = lambda item: ft.Text(str(page._get_modrinth_install_state(item)))
    page._apply_installed_content("mods", [_mod(update_available=not has_update)], update=False)
    page.search_results_container.controls = [page._create_search_result_card(project)]
    old_card = page.search_results_container.controls[0]
    monkeypatch.setattr(page, "_search_mods", lambda: pytest.fail("Cached results should not require another search"))
    monkeypatch.setattr(
        page.app.modrinth_mods, "check_installed_updates",
        lambda items, _version: [dict(items[0], update_checked=True, update_available=has_update)],
    )
    page.request_installed_updates_check()

    asyncio.run(_checks(pending)[0].run())
    page._switch_inner_tab("modrinth")

    assert page.search_results_container.controls[0] is not old_card
    assert page.search_results_container.controls[0].value == str({"installed": True, "update_available": has_update})
    assert len(page.search_results_container.controls) == 1
    assert page.search_state.query == "example"
    assert page.search_state.offset == 20
    assert page.search_result_items == [project]


@pytest.mark.parametrize("state", ["loading", "failed"])
def test_check_does_not_replace_inflight_or_failed_search_controls(installed_page, monkeypatch, state):
    page, pending = installed_page
    page.search_state.loading = state == "loading"
    page.search_result_items = [{"project_id": "project-id"}] if state == "loading" else []
    control = ft.Text(state)
    page.search_results_container.controls = [control]
    monkeypatch.setattr(page.app.modrinth_mods, "check_installed_updates", lambda items, _version: items)
    page.request_installed_updates_check()

    asyncio.run(_checks(pending)[0].run())

    assert page.search_results_container.controls == [control]
