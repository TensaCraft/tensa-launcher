from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from launcher.app import App


class _LifecyclePage:
    def __init__(self) -> None:
        self.window = SimpleNamespace(prevent_close=True, on_event="legacy-handler")
        self.scroll = "settings-scroll"
        self.update_calls = 0
        self.on_disconnect_assignments = 0
        self._on_disconnect = None

    @property
    def on_disconnect(self):
        return self._on_disconnect

    @on_disconnect.setter
    def on_disconnect(self, handler) -> None:
        self.on_disconnect_assignments += 1
        self._on_disconnect = handler

    def update(self) -> None:
        self.update_calls += 1


class _Controller:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def before_hide(self) -> None:
        self.dispose_calls += 1


class _Feedback:
    def __init__(self) -> None:
        self.shutdown_calls: list[bool] = []

    def shutdown(self, *, update_ui: bool = True) -> None:
        self.shutdown_calls.append(update_ui)


class _StartupTask:
    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


def test_repeated_restart_disposes_each_lifecycle_once_without_rebinding(monkeypatch) -> None:
    page = _LifecyclePage()
    controllers: list[_Controller] = []
    feedback_services: list[_Feedback] = []
    lifecycle_calls: list[str] = []
    stateful_refresh_flags: list[bool] = []
    warm_up_calls = 0
    shown_pages = []

    def bootstrap_state(app: Any) -> None:
        feedback = _Feedback()
        feedback_services.append(feedback)
        app.feedback = feedback
        app.theme = object()

    def build_shell(app: Any) -> None:
        controller = _Controller()
        controllers.append(controller)
        app.current_page = controller

    def build_stateful_models(app: Any, *, schedule_java_refresh: bool = True) -> None:
        stateful_refresh_flags.append(schedule_java_refresh)

    def warm_up_background_tasks(_app: Any) -> None:
        nonlocal warm_up_calls
        warm_up_calls += 1

    monkeypatch.setattr("launcher.app.ui.set_current_theme", lambda theme: theme)
    monkeypatch.setattr(
        "launcher.app.ui.show_window_when_ready",
        lambda ready_page: shown_pages.append(ready_page),
    )
    monkeypatch.setattr(App, "_bootstrap_state", bootstrap_state)
    monkeypatch.setattr(App, "_configure_page", lambda _app: lifecycle_calls.append("configure"))
    monkeypatch.setattr(App, "_center_window", lambda _app: lifecycle_calls.append("center"))
    monkeypatch.setattr(App, "_build_ui_services", lambda _app: lifecycle_calls.append("services"))
    monkeypatch.setattr(App, "_build_stateful_models", build_stateful_models)
    monkeypatch.setattr(App, "_build_shell", build_shell)
    monkeypatch.setattr(App, "_warm_up_background_tasks", warm_up_background_tasks)

    app = App(cast(Any, page))
    disconnect_handler = page.on_disconnect
    startup_task = _StartupTask()
    app._startup_tasks.append(startup_task)

    app.restart()
    app.restart()

    assert controllers[0].dispose_calls == 1
    assert controllers[1].dispose_calls == 1
    assert feedback_services[0].shutdown_calls == [False]
    assert feedback_services[1].shutdown_calls == [False]
    assert startup_task.cancel_calls == 1
    assert app._startup_tasks == []
    assert warm_up_calls == 1
    assert stateful_refresh_flags == [True, False, False]
    assert lifecycle_calls == [
        "configure",
        "center",
        "services",
        "configure",
        "center",
        "services",
        "configure",
        "center",
        "services",
    ]
    assert page.on_disconnect_assignments == 1
    assert page.on_disconnect is disconnect_handler
    assert page.scroll is None
    assert app.current_page is controllers[2]
    assert controllers[2].dispose_calls == 0
    assert page.update_calls == 2
    assert shown_pages == [page, page]


def test_show_page_disposes_previous_controller_once(monkeypatch) -> None:
    previous = _Controller()
    current = SimpleNamespace(
        view=lambda: "new-view",
        after_show_calls=0,
    )

    def after_show() -> None:
        current.after_show_calls += 1

    current.after_show = after_show
    app = cast(Any, App.__new__(App))
    app.current_page = previous
    app.navigation = SimpleNamespace(view=lambda: "sidebar")
    app.header = SimpleNamespace(view=lambda: "header")
    app.footer = SimpleNamespace(view=lambda: "footer")
    app._main_layout = SimpleNamespace(controls=["old-sidebar"])
    app._content_area = SimpleNamespace(controls=[])
    app.page = SimpleNamespace(update_calls=0)
    app.page.update = lambda: setattr(app.page, "update_calls", app.page.update_calls + 1)
    monkeypatch.setattr(
        "launcher.app.ui.PageContainer",
        lambda *, controls: SimpleNamespace(controls=controls),
    )

    app.show_page(current)

    assert previous.dispose_calls == 1
    assert app.current_page is current
    assert current.after_show_calls == 1
    assert app.page.update_calls == 1
