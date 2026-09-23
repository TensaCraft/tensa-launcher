import asyncio
from unittest.mock import AsyncMock

import pytest

from launcher.pages.home import Home
from launcher.pages.versions import VersionsPage


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_launch_failure_after_navigation_still_uses_global_feedback(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    warnings = []
    updates = []
    fake_app.feedback.warning = warnings.append
    monkeypatch.setattr("launcher.pages.version_actions.schedule_update", updates.append)

    async def finish_launch(*_args, **_kwargs):
        page.before_hide()
        return {"status": False, "text": "version_sync_failed"}

    monkeypatch.setattr("launcher.pages.version_actions.run_blocking", finish_launch)

    asyncio.run(page._handle_play_async(fake_app.versions.all()[0]))

    assert warnings == ["version_sync_failed"]
    assert updates == []


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
@pytest.mark.parametrize("closed_state", ["terminating", "destroyed"])
def test_launch_completion_ignores_closed_app_session(fake_app, monkeypatch, page_type, closed_state):
    page = page_type(fake_app)
    warnings = []
    updates = []
    fake_app.feedback.warning = warnings.append
    monkeypatch.setattr("launcher.pages.version_actions.schedule_update", updates.append)

    class DestroyedPage:
        @property
        def session(self):
            raise RuntimeError("Session was destroyed")

    async def finish_launch(*_args, **_kwargs):
        if closed_state == "terminating":
            fake_app._terminating = True
        else:
            page.page = DestroyedPage()
        return {"status": False, "text": "version_sync_failed"}

    monkeypatch.setattr("launcher.pages.version_actions.run_blocking", finish_launch)

    asyncio.run(page._handle_play_async(fake_app.versions.all()[0]))

    assert warnings == []
    assert updates == []


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_queued_launch_does_not_start_after_page_hide(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    worker = AsyncMock()
    monkeypatch.setattr("launcher.pages.version_actions.run_blocking", worker)
    page.before_hide()

    asyncio.run(page._handle_play_async(fake_app.versions.all()[0]))

    worker.assert_not_awaited()


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
@pytest.mark.parametrize("success", [True, False])
def test_visible_launch_completion_preserves_feedback_and_update(fake_app, monkeypatch, page_type, success):
    page = page_type(fake_app)
    warnings = []
    infos = []
    updates = []
    fake_app.feedback.warning = warnings.append
    fake_app.feedback.info = infos.append
    monkeypatch.setattr("launcher.pages.version_actions.schedule_update", updates.append)
    monkeypatch.setattr(
        "launcher.pages.version_actions.run_blocking",
        AsyncMock(return_value={"status": success, "text": "launch_result"}),
    )

    asyncio.run(page._handle_play_async(fake_app.versions.all()[0]))

    assert warnings == ([] if success else ["launch_result"])
    assert infos == (["launch_result"] if success else [])
    assert updates == [fake_app.page]
