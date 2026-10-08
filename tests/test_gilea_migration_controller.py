import asyncio
import json
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from launcher.application.gilea_migration import service, transaction
from launcher.application.gilea_migration.models import MigrationError, MigrationProgress, MigrationResult
from launcher.platform import gilea_migration as windows
from launcher.ui.gilea_migration import ERROR_CODES, GileaMigrationController


def make_app(answer=False):
    warnings, confirms, started = [], [], []
    app = SimpleNamespace(
        _terminating=False, _stay_on_tensa=False,
        config=SimpleNamespace(get=lambda key, default=None: default),
        page=SimpleNamespace(controls=[], update=lambda: None),
        feedback=SimpleNamespace(is_busy=lambda: False, warning=warnings.append),
        log=SimpleNamespace(warning=lambda message: None),
    )

    def confirm(title, question, callback):
        confirms.append(question)
        callback(answer)

    app.feedback.confirm = confirm
    controller = GileaMigrationController(app)
    controller.available = True

    async def start(**kwargs):
        started.append(kwargs)

    controller.start = start
    return app, controller, warnings, confirms, started


def test_offer_requires_separate_consent():
    _, controller, _, confirms, started = make_app(False)
    asyncio.run(controller.offer())
    assert len(confirms) == 1
    assert started == []
    _, controller, _, _, started = make_app(True)
    asyncio.run(controller.offer())
    assert started == [{"allow_external_copy": False}]


def test_recovery_mode_does_not_offer_automatic_migration():
    app, controller, _, confirms, _ = make_app()
    app._stay_on_tensa = True
    asyncio.run(controller.offer())
    assert confirms == []


def test_progress_uses_page_runtime_and_guards_shutdown(monkeypatch):
    app, controller, _, _, _ = make_app()
    observed = []
    monkeypatch.setattr(controller, "_render_progress", lambda event: observed.append(threading.get_ident()))

    async def exercise():
        controller._loop = asyncio.get_running_loop()
        current = threading.get_ident()
        await asyncio.to_thread(controller._progress, MigrationProgress("download", 1, 2))
        await asyncio.sleep(0)
        assert observed == [current]
        app._terminating = True
        await asyncio.to_thread(controller._progress, MigrationProgress("download", 2, 2))
        await asyncio.sleep(0)
        assert observed == [current]

    asyncio.run(exercise())


def test_cancel_closes_pending_confirmation_without_starting():
    app, controller, _, _, started = make_app()
    app.feedback.confirm = lambda *args: None

    async def exercise():
        task = asyncio.create_task(controller.offer())
        await asyncio.sleep(0)
        controller.cancel()
        await task

    asyncio.run(exercise())
    assert started == []


def test_settings_retry_and_localized_failures():
    base = Path(__file__).resolve().parents[1] / "launcher/assets/langs"
    for lang in ("en_US", "uk_UA"):
        translations = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        for code in ERROR_CODES:
            assert translations.get(f"gilea_error_{code}")
    _, controller, warnings, _, _ = make_app()
    controller._report_error(MigrationError("webview_missing"))
    assert warnings and "gilea_error_" not in warnings[0]


def test_declining_migration_keeps_normal_update_check():
    app, controller, _, _, _ = make_app(False)
    updates = []

    async def check():
        updates.append(True)

    app.updater = SimpleNamespace(check_for_updates_async=check)
    asyncio.run(controller.startup())
    assert updates == [True]


def test_prepare_reuses_committed_transfer(tmp_path, monkeypatch):
    app, controller, _, _, _ = make_app()
    app.paths = SimpleNamespace(app_state_dir=tmp_path, minecraft_dir=tmp_path / "mc")
    app.profiles = SimpleNamespace(_lock=nullcontext())
    result = MigrationResult(tmp_path / "GileaLauncher-tensa.exe", {"old": "new"}, ())
    monkeypatch.setattr(windows, "default_roots", lambda *args: object())
    monkeypatch.setattr(transaction, "recover_migration", lambda roots: None)
    monkeypatch.setattr(service, "load_committed_migration", lambda roots: result)
    monkeypatch.setattr(service, "prepare_plan", lambda *args, **kwargs: pytest.fail("re-imported"))
    assert controller._prepare() is result


def test_worker_cancellation_waits_for_cleanup():
    _, controller, _, _, _ = make_app()
    entered, cleaned = threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert controller._cancel.wait(5)
        cleaned.set()

    async def exercise():
        task = asyncio.create_task(controller._work(worker))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set() and controller.wait_for_worker(0)

    asyncio.run(exercise())


def test_late_confirmation_does_not_touch_closed_loop():
    app, controller, _, _, _ = make_app()
    callbacks = []
    app.feedback.confirm = lambda title, question, callback: callbacks.append(callback)

    async def exercise():
        task = asyncio.create_task(controller.offer())
        await asyncio.sleep(0)
        controller.cancel()
        await task

    asyncio.run(exercise())
    callbacks[0](True)
