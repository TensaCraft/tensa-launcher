from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from launcher.ui.core.page_runtime import invoke_on_ui, run_blocking, schedule_update


@pytest.mark.parametrize("action", ["callback", "update"])
def test_destroyed_session_drops_ui_work(action):
    calls = []

    class DestroyedPage:
        @property
        def session(self):
            raise RuntimeError("An attempt to fetch destroyed session.")

        def update(self):
            calls.append("update")

    page = DestroyedPage()
    if action == "callback":
        invoke_on_ui(page, lambda: calls.append("callback"))
    else:
        schedule_update(page)

    assert calls == []


@pytest.mark.parametrize("action", ["callback", "update"])
def test_rejected_page_task_never_falls_back_to_worker_ui_mutation(action):
    calls = []

    def reject(*_args, **_kwargs):
        raise RuntimeError("Event loop is closed")

    page = SimpleNamespace(run_task=reject, update=lambda: calls.append("update"))
    if action == "callback":
        invoke_on_ui(page, lambda: calls.append("callback"))
    else:
        schedule_update(page)

    assert calls == []


@pytest.mark.parametrize("action", ["callback", "update"])
def test_queued_ui_work_checks_session_again_before_execution(action):
    calls = []
    queued = []

    class DeferredPage:
        destroyed = False

        @property
        def session(self):
            if self.destroyed:
                raise RuntimeError("An attempt to fetch destroyed session.")
            return SimpleNamespace(connection=SimpleNamespace(loop=None))

        def update(self):
            calls.append("update")

        def run_task(self, handler):
            queued.append(handler)

    page = DeferredPage()
    if action == "callback":
        invoke_on_ui(page, lambda: calls.append("callback"))
    else:
        schedule_update(page)
    page.destroyed = True

    asyncio.run(queued[0]())

    assert calls == []


@pytest.mark.parametrize("worker_fails", [False, True])
def test_cancelled_blocking_worker_drains_before_delivering_cancellation(worker_fails):
    started = threading.Event()
    release = threading.Event()
    mutations = []

    def worker():
        started.set()
        assert release.wait(3)
        mutations.append("finished")
        if worker_fails:
            raise OSError("worker failed after cancellation")

    async def scenario():
        task = asyncio.create_task(run_blocking(worker))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert mutations == []
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert mutations == ["finished"]

    asyncio.run(scenario())
