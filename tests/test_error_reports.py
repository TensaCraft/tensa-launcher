from __future__ import annotations

from pathlib import Path

from launcher.application.error_reports import LauncherReportService
from launcher.ui.feedback.alert_service import Alert


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_report_service_posts_launcher_log_and_metadata(fake_app, tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text("launcher line\n", encoding="utf-8")
    extra_log = tmp_path / "latest.log"
    extra_log.write_text("minecraft line\n", encoding="utf-8")
    captured = {}

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse({"ok": True, "report_id": "report-1", "status": "new"})

    monkeypatch.setattr("launcher.application.error_reports.Logger.log_file", log_file)

    service = LauncherReportService(fake_app, session=FakeSession())
    operation = fake_app.feedback.begin_operation("Install", kind="install", status="Downloading")
    fake_app.feedback.warning("Network is slow", allow_report=False)
    result = service.submit_report(
        report_type="crash",
        severity="error",
        title="Launch failed",
        message="Minecraft exited",
        metadata={"screen": "game", "path": Path("instance/logs/latest.log")},
        attachments=[extra_log],
    )

    payload = captured["kwargs"]["json"]
    assert captured["url"] == LauncherReportService.ENDPOINT
    assert captured["kwargs"]["timeout"] == LauncherReportService.REQUEST_TIMEOUT
    assert result["report_id"] == "report-1"
    assert payload["type"] == "crash"
    assert payload["severity"] == "error"
    assert payload["launcher_version"] == fake_app.util.launcher_version
    assert payload["title"] == "Launch failed"
    assert payload["message"] == "Minecraft exited"
    assert payload["metadata"]["screen"] == "game"
    assert payload["metadata"]["path"] == "instance/logs/latest.log"
    assert payload["metadata"]["feedback"]["busy"] is True
    assert payload["metadata"]["feedback"]["active_operations"][0]["kind"] == "install"
    assert payload["metadata"]["feedback"]["recent_activity"][-1]["message"] == "Network is slow"
    assert "launcher line" in payload["log"]
    assert "minecraft line" in payload["log"]
    operation.finish(show_success=False)


def test_report_service_includes_optional_contact(fake_app, tmp_path, monkeypatch):
    captured = {}

    class FakeSession:
        def post(self, url, **kwargs):
            captured["kwargs"] = kwargs
            return FakeResponse({"ok": True, "report_id": "report-contact"})

    monkeypatch.setattr("launcher.application.error_reports.Logger.log_file", tmp_path / "missing.log")
    fake_app.config.set("report_contact", "client@example.com")

    LauncherReportService(fake_app, session=FakeSession()).submit_report(
        title="Launch failed",
        message="Minecraft exited",
    )

    payload = captured["kwargs"]["json"]
    assert payload["contact"] == "client@example.com"
    assert payload["metadata"]["contact"] == "client@example.com"


def test_warning_alert_adds_send_report_action(fake_app):
    captured = {"dialogs": [], "reports": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)

    class FakeReporter:
        def submit_report_async(self, **kwargs):
            captured["reports"].append(kwargs)
            kwargs["on_success"]({"ok": True, "report_id": "report-2"})

    fake_app.reporter = FakeReporter()
    alert = Alert(fake_app)

    alert._show_alert_impl(
        "Minecraft exited",
        is_warning=True,
        report_title="Launch failed",
        report_metadata={"screen": "game", "action": "launch"},
    )

    warning_dialog = captured["dialogs"][0]
    report_action = next(action for action in warning_dialog.actions if action.content == "send_error_report")
    report_action.on_click(None)

    assert captured["reports"]
    report = captured["reports"][0]
    assert report["report_type"] == "error"
    assert report["severity"] == "error"
    assert report["title"] == "Launch failed"
    assert report["message"] == "Minecraft exited"
    assert report["metadata"]["screen"] == "game"
    assert report["metadata"]["action"] == "launch"


def test_warning_alert_omits_report_action_without_report_context(fake_app):
    captured = {"dialogs": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)

    class FakeReporter:
        def submit_report_async(self, **_kwargs):
            raise AssertionError("generic warnings must not submit reports")

    fake_app.reporter = FakeReporter()
    alert = Alert(fake_app)

    alert._show_alert_impl("You have not set a default profile", is_warning=True)

    warning_dialog = captured["dialogs"][0]
    action_labels = [getattr(action, "content", None) for action in warning_dialog.actions]
    assert "send_error_report" not in action_labels


def test_warning_alert_report_action_ignores_duplicate_clicks(fake_app):
    captured = {"dialogs": [], "reports": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)

    class FakeReporter:
        def submit_report_async(self, **kwargs):
            captured["reports"].append(kwargs)

    fake_app.reporter = FakeReporter()
    alert = Alert(fake_app)

    alert._show_alert_impl("Minecraft exited", is_warning=True, report_title="Launch failed")

    warning_dialog = captured["dialogs"][0]
    report_action = next(action for action in warning_dialog.actions if action.content == "send_error_report")
    report_action.on_click(None)
    report_action.on_click(None)

    assert len(captured["reports"]) == 1
    assert report_action.disabled is True
    assert report_action.content == "error_report_sending"


def test_warning_alert_report_action_shows_sent_state(fake_app):
    captured = {"dialogs": [], "reports": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)

    class FakeReporter:
        def submit_report_async(self, **kwargs):
            captured["reports"].append(kwargs)
            kwargs["on_success"]({"ok": True, "report_id": "report-3"})

    fake_app.reporter = FakeReporter()
    alert = Alert(fake_app)

    alert._show_alert_impl("Minecraft exited", is_warning=True, report_title="Launch failed")

    warning_dialog = captured["dialogs"][0]
    report_action = next(action for action in warning_dialog.actions if action.content == "send_error_report")
    report_action.on_click(None)

    assert len(captured["reports"]) == 1
    assert report_action.disabled is True
    assert report_action.content == "error_report_sent_button"


def test_warning_alert_report_action_allows_retry_after_failure(fake_app):
    captured = {"dialogs": [], "reports": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)

    class FakeReporter:
        def submit_report_async(self, **kwargs):
            captured["reports"].append(kwargs)
            kwargs["on_error"](RuntimeError("network down"))

    fake_app.reporter = FakeReporter()
    alert = Alert(fake_app)

    alert._show_alert_impl("Minecraft exited", is_warning=True, report_title="Launch failed")

    warning_dialog = captured["dialogs"][0]
    report_action = next(action for action in warning_dialog.actions if action.content == "send_error_report")
    report_action.on_click(None)
    report_action.on_click(None)

    assert len(captured["reports"]) == 2
    assert report_action.disabled is False
    assert report_action.content == "error_report_retry"
