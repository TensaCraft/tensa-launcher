from __future__ import annotations

from types import SimpleNamespace

from launcher.application.feedback import FeedbackService


class DummyLog:
    def debug(self, *_args, **_kwargs) -> None:
        return None


class ProgressRecorder:
    def __init__(self) -> None:
        self.starts: list[None] = []
        self.shows: list[tuple[str, float, float, bool, bool]] = []
        self.completions: list[str | None] = []
        self.hides: list[bool] = []

    def start_cycle(self) -> None:
        self.starts.append(None)

    def show(
        self,
        status: str,
        progress: float = 0,
        max_progress: float = 100,
        *,
        force_open: bool = False,
        auto_open: bool = True,
    ) -> None:
        self.shows.append((status, progress, max_progress, force_open, auto_open))

    def installation_complete(self, message: str | None = None) -> None:
        self.completions.append(message)

    def hide(self, _=None, *, manual: bool = False) -> None:
        self.hides.append(manual)


class AlertRecorder:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, bool]] = []
        self.confirms: list[tuple[str, str]] = []

    def show_alert(self, message: str, is_warning: bool = False, **_kwargs) -> None:
        self.alerts.append((message, is_warning))

    def show_confirm(self, title: str, question: str, callback) -> None:
        self.confirms.append((title, question))
        callback(True)


class FakeTimer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def fire(self) -> None:
        self.callback()


def build_service() -> tuple[FeedbackService, ProgressRecorder, AlertRecorder]:
    app = SimpleNamespace(log=DummyLog(), trans=lambda key, **_placeholders: key)
    progress = ProgressRecorder()
    alerts = AlertRecorder()
    service = FeedbackService(app, auto_close_delay=0)
    service.attach(progress_overlay=progress, alert_renderer=alerts)
    return service, progress, alerts


def test_progress_updates_are_coalesced_for_busy_operations() -> None:
    app = SimpleNamespace(log=DummyLog(), trans=lambda key, **_placeholders: key)
    progress = ProgressRecorder()
    timers: list[FakeTimer] = []
    now = [0.0]

    def timer_factory(delay, callback):
        timer = FakeTimer(delay, callback)
        timers.append(timer)
        return timer

    service = FeedbackService(
        app,
        auto_close_delay=0,
        render_interval=0.1,
        timer_factory=timer_factory,
        clock=lambda: now[0],
    )
    service.attach(progress_overlay=progress)

    operation = service.begin_operation("Install", status="Starting")
    operation.update("File 1", progress=1, total=100)
    operation.update("File 2", progress=2, total=100)
    operation.update("File 3", progress=3, total=100)

    assert progress.shows == [("Starting", 0, 100, True, True)]
    assert len(timers) == 1
    assert timers[0].started is True

    now[0] = 0.1
    timers[0].fire()

    assert progress.shows == [
        ("Starting", 0, 100, True, True),
        ("File 3", 3, 100, False, True),
    ]


def test_pending_progress_render_is_cancelled_when_operation_finishes() -> None:
    app = SimpleNamespace(log=DummyLog(), trans=lambda key, **_placeholders: key)
    progress = ProgressRecorder()
    timers: list[FakeTimer] = []
    now = [0.0]

    def timer_factory(delay, callback):
        timer = FakeTimer(delay, callback)
        timers.append(timer)
        return timer

    service = FeedbackService(
        app,
        auto_close_delay=0,
        render_interval=0.1,
        timer_factory=timer_factory,
        clock=lambda: now[0],
    )
    service.attach(progress_overlay=progress)

    operation = service.begin_operation("Install", status="Starting")
    operation.update("File 1", progress=1, total=100)
    operation.finish(show_success=False)

    assert progress.shows == [("Starting", 0, 100, True, True)]
    assert progress.hides == [False]

    now[0] = 0.1
    timers[0].fire()

    assert progress.shows == [("Starting", 0, 100, True, True)]
    assert progress.hides == [False]


def test_child_operation_completion_keeps_root_progress_visible() -> None:
    service, progress, _alerts = build_service()

    root = service.begin_operation("Install Aeronautics", status="Starting install")
    child = service.begin_operation("Minecraft", status="Installing Minecraft")

    child.finish("Minecraft installed")

    assert service.is_busy() is True
    assert progress.completions == []
    assert progress.hides == []
    assert progress.shows[-1] == ("Starting install", 0, 100, False, True)

    root.finish("Install complete")

    assert service.is_busy() is False
    assert progress.completions == []
    assert progress.hides == [False]
    assert _alerts.alerts == [("Install complete", False)]


def test_hidden_operation_reveals_when_real_progress_starts() -> None:
    service, progress, _alerts = build_service()

    operation = service.begin_operation("Prepare launch", visible=False, auto_open=False)

    assert service.is_busy() is True
    assert progress.starts == []
    assert progress.shows == []

    operation.update("Installing Minecraft 1.21.1", progress=0, total=100)

    assert progress.starts == [None]
    assert progress.shows == [("Installing Minecraft 1.21.1", 0, 100, True, False)]

    operation.finish("Ready")

    assert progress.completions == []
    assert progress.hides == [False]
    assert _alerts.alerts == [("Ready", False)]


def test_silent_hidden_operation_finishes_without_ui_noise() -> None:
    service, progress, _alerts = build_service()

    operation = service.begin_operation("Check sync", visible=False, auto_open=False)
    operation.finish(show_success=False)

    assert service.is_busy() is False
    assert progress.starts == []
    assert progress.shows == []
    assert progress.completions == []
    assert progress.hides == []


def test_hidden_operation_progress_preserves_silent_auto_open() -> None:
    app = SimpleNamespace(log=DummyLog(), trans=lambda key, **_placeholders: key)
    progress = ProgressRecorder()
    service = FeedbackService(app, auto_close_delay=0, render_interval=0)
    service.attach(progress_overlay=progress, alert_renderer=AlertRecorder())

    operation = service.begin_operation("Sync", visible=False, auto_open=False)
    operation.update("Downloading files", progress=1, total=10)
    operation.update("Installing loader", progress=2, total=10, auto_open=True)

    assert progress.shows == [
        ("Downloading files", 1, 10, True, False),
        ("Installing loader", 2, 10, False, True),
    ]


def test_finished_handle_cannot_close_new_operation() -> None:
    service, progress, _alerts = build_service()

    old = service.begin_operation("First", status="First")
    old.finish(show_success=False)
    new = service.begin_operation("Second", status="Second")

    old.finish(show_success=False)

    assert service.is_busy() is True
    assert progress.hides == [False]

    new.finish(show_success=False)

    assert service.is_busy() is False
    assert progress.hides == [False, False]


def test_notifications_and_confirmations_use_single_feedback_interface() -> None:
    service, _progress, alerts = build_service()
    responses: list[bool] = []

    service.info("Saved")
    service.warning("Broken")
    service.confirm("Confirm", "Proceed?", lambda response: responses.append(response))

    assert alerts.alerts == [("Saved", False), ("Broken", True)]
    assert alerts.confirms == [("Confirm", "Proceed?")]
    assert responses == [True]


def test_empty_notifications_are_ignored() -> None:
    service, _progress, alerts = build_service()

    service.info(None)  # type: ignore[arg-type]
    service.warning("")

    assert alerts.alerts == []


def test_feedback_snapshot_includes_active_operations_and_recent_activity() -> None:
    service, _progress, alerts = build_service()

    operation = service.begin_operation("Install", kind="install", status="Preparing")
    operation.update("Downloading", progress=2, total=10)
    service.warning("Network is slow")

    snapshot = service.snapshot()

    assert snapshot["busy"] is True
    assert snapshot["active_operations"] == [
        {
            "id": 1,
            "title": "Install",
            "kind": "install",
            "status": "Downloading",
            "progress": 2,
            "total": 10,
            "visible": True,
            "auto_open": True,
            "parent_id": None,
        }
    ]
    assert snapshot["recent_activity"][-1]["message"] == "Network is slow"
    assert snapshot["recent_activity"][-1]["level"] == "warning"
    assert alerts.alerts == [("Network is slow", True)]

    operation.finish("Done")

    complete_snapshot = service.snapshot()
    assert complete_snapshot["busy"] is False
    assert complete_snapshot["active_operations"] == []
    assert complete_snapshot["recent_activity"][-1]["event"] == "finish"


def test_finish_without_success_alert_records_success_activity() -> None:
    service, _progress, alerts = build_service()

    operation = service.begin_operation("Install", kind="install", status="Installing")
    operation.finish("Installation complete", show_success=False)

    activity = service.recent_activity(5)

    assert activity[-1]["event"] == "finish"
    assert activity[-1]["level"] == "success"
    assert alerts.alerts == []


def test_failed_operation_records_warning_activity() -> None:
    service, _progress, alerts = build_service()

    operation = service.begin_operation("Install", kind="install", status="Installing")
    operation.fail("Installation failed", notify=False)

    activity = service.recent_activity(5)

    assert activity[-1]["event"] == "finish"
    assert activity[-1]["level"] == "warning"
    assert alerts.alerts == []


def test_feedback_activity_coalesces_launch_sync_duplicate_messages() -> None:
    service, _progress, alerts = build_service()

    launch = service.begin_operation("Checking updates", kind="launch", status="Checking updates")
    sync = service.begin_operation("Checking updates", kind="sync", status="Checking updates")
    sync.update("Synchronizing updates")
    sync.finish("Update completed")
    launch.finish("Update completed")

    activity = service.recent_activity(20)
    messages = [entry["message"] for entry in activity]

    assert messages == ["Checking updates", "Synchronizing updates", "Update completed"]
    assert activity[0]["kind"] == "sync"
    assert activity[-1]["event"] == "finish"
    assert activity[-1]["kind"] == "sync"
    assert alerts.alerts == [("Update completed", False)]
