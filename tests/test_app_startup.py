from __future__ import annotations

from types import SimpleNamespace

from launcher.app import App


def test_startup_schedules_self_update_check_when_enabled(monkeypatch):
    thread_targets = []
    scheduled_tasks = []

    class Thread:
        def __init__(self, target, daemon=False):
            thread_targets.append((target, daemon))

        def start(self):
            return None

    app = SimpleNamespace(
        auth=SimpleNamespace(refresh_all_online_profiles=lambda: None, get_default_profile_data=lambda: None),
        config=SimpleNamespace(get=lambda key, default=None: "yes" if key == "check_updates" else default),
        page=SimpleNamespace(run_task=lambda task, *args, **kwargs: scheduled_tasks.append(task)),
        updater=SimpleNamespace(check_for_updates_async=lambda: None),
    )

    monkeypatch.setattr("launcher.app.threading.Thread", Thread)

    App._warm_up_background_tasks(app)

    assert len(thread_targets) == 1
    assert scheduled_tasks == [app.updater.check_for_updates_async]
