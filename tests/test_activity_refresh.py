from __future__ import annotations

from launcher.pages.activity import ActivityPanel


def test_activity_panel_skips_unchanged_periodic_refresh(fake_app, monkeypatch) -> None:
    panel = ActivityPanel(fake_app)
    updates: list[object] = []
    monkeypatch.setattr("launcher.pages.activity.schedule_update", updates.append)

    panel._refresh_content()

    assert updates == []


def test_activity_panel_refreshes_after_snapshot_changes(fake_app, monkeypatch) -> None:
    panel = ActivityPanel(fake_app)
    updates: list[object] = []
    monkeypatch.setattr("launcher.pages.activity.schedule_update", updates.append)
    fake_app.feedback.warning("Changed", allow_report=False)

    panel._refresh_content()

    assert updates == [fake_app.page]
