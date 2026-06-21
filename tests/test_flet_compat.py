from __future__ import annotations

import asyncio
import inspect

from launcher.ui.core.flet_compat import show_window_when_ready


def test_show_window_when_ready_waits_for_flet_window_readiness():
    events: list[str] = []

    class FakeWindow:
        visible = False

        async def wait_until_ready_to_show(self):
            events.append("wait")

    class FakePage:
        window = FakeWindow()

        def update(self):
            events.append("update")

        def run_task(self, handler):
            if not inspect.iscoroutinefunction(handler):
                raise TypeError("handler must be a coroutine function")
            asyncio.run(handler())

    page = FakePage()

    show_window_when_ready(page)

    assert page.window.visible is True
    assert events == ["wait", "update"]
