from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from launcher import cli
from launcher import main as launcher_main
from launcher.app import App
from launcher.pages.setup_wizard import SetupWizardPage
from launcher.pages.versions import VersionsPage


@pytest.fixture(autouse=True)
def isolated_instance_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher_main, "instance_directory", lambda: tmp_path / "ipc")


def test_shortcut_startup_reuses_play_handler_by_exact_id(fake_app, monkeypatch):
    fake_app._terminating = False
    version = fake_app.versions.all()[0]
    version.name = "Renamed after shortcut creation"
    scheduled = []

    def show_versions():
        fake_app.current_page = VersionsPage(fake_app)

    fake_app.show_versions_page = show_versions
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, path: False))
    monkeypatch.setattr("launcher.pages.version_actions.run_task", lambda _page, task, *args: scheduled.append((task, args)))

    asyncio.run(App.launch_version_by_id(fake_app, version.version_id))

    assert scheduled == [(fake_app.current_page._handle_play_async, (version,))]


@pytest.mark.parametrize("completed", ["no", "yes"])
def test_shortcut_startup_does_not_bypass_open_setup_wizard(fake_app, completed):
    fake_app._terminating = False
    fake_app.config.set("setup_wizard_completed", completed)
    wizard = SetupWizardPage(fake_app)
    fake_app.current_page = wizard
    version = fake_app.versions.all()[0]
    lookups = []
    navigation = []
    fake_app.versions.get = lambda version_id: lookups.append(version_id) or version
    fake_app.show_versions_page = lambda: navigation.append(True)

    asyncio.run(App.launch_version_by_id(fake_app, version.version_id))

    assert lookups == []
    assert navigation == []
    assert fake_app.current_page is wizard


@pytest.mark.parametrize("requested", ["deleted-id", "Vanilla 1.20.1", "versions/vanilla-1.20.1", "legacy-alias"])
def test_shortcut_missing_id_never_falls_back_to_another_build(fake_app, requested):
    fake_app._terminating = False
    warnings = []
    navigation = []
    version = fake_app.versions.all()[0]
    fake_app.versions.get = lambda _id: version if requested == "legacy-alias" else None
    fake_app.feedback.warning = warnings.append
    fake_app.show_versions_page = lambda: navigation.append(True)

    asyncio.run(App.launch_version_by_id(fake_app, requested))

    assert warnings == ["version_not_found"]
    assert navigation == []


def test_shortcut_startup_keeps_profile_selection(fake_app, monkeypatch):
    fake_app._terminating = False
    version = fake_app.versions.all()[0]
    selections = []
    tasks = []
    fake_app.show_versions_page = lambda: setattr(fake_app, "current_page", VersionsPage(fake_app))
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, path: False))
    monkeypatch.setattr(
        "launcher.pages.version_actions.show_launch_profile_selector",
        lambda app, value, callback: selections.append((value, callback)) or True,
    )
    monkeypatch.setattr("launcher.pages.version_actions.run_task", lambda _page, task, *args: tasks.append(args))

    asyncio.run(App.launch_version_by_id(fake_app, version.version_id))

    assert selections[0][0] is version
    assert tasks == []
    selections[0][1]("chosen-profile")
    assert tasks == [(version, False, "chosen-profile")]


def test_shortcut_startup_keeps_duplicate_confirmation(fake_app, monkeypatch):
    fake_app._terminating = False
    version = fake_app.versions.all()[0]
    confirmations = []
    fake_app.show_versions_page = lambda: setattr(fake_app, "current_page", VersionsPage(fake_app))
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, path: True))
    fake_app.feedback.confirm = lambda *args: confirmations.append(args)

    asyncio.run(App.launch_version_by_id(fake_app, version.version_id))

    assert len(confirmations) == 1
    assert "version_already_running_confirm_title" in confirmations[0][0]


def test_shortcut_startup_keeps_install_busy_guard(fake_app, monkeypatch):
    fake_app._terminating = False
    version = fake_app.versions.all()[0]
    messages = []
    scheduled = []
    fake_app.show_versions_page = lambda: setattr(fake_app, "current_page", VersionsPage(fake_app))
    fake_app.feedback.is_busy = lambda: True
    fake_app.feedback.info = messages.append
    monkeypatch.setattr("launcher.pages.version_actions.run_task", lambda *args: scheduled.append(args))

    asyncio.run(App.launch_version_by_id(fake_app, version.version_id))

    assert messages == ["installation_already_running"]
    assert scheduled == []


def test_shortcut_launch_uses_bound_version_runtime_and_auth_feedback(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    calls = []
    responses = []
    response = {"status": False, "reason": "missing_profile"}
    version.start = lambda **kwargs: calls.append(kwargs) or response
    monkeypatch.setattr("launcher.pages.version_actions.handle_launch_response", lambda app, value: responses.append(value))
    page = VersionsPage(fake_app)

    asyncio.run(page._handle_play_async(version, profile_key="selected-profile"))

    assert calls == [{"allow_duplicate": False, "profile_key": "selected-profile"}]
    assert responses == [response]


def test_main_schedules_shortcut_only_after_app_is_ready(monkeypatch):
    events = []

    class FakeApp:
        def __init__(self, page):
            events.append("init")

        def run(self):
            events.append("run")

        async def launch_version_by_id(self, version_id):
            events.append(version_id)

        def _track_startup_task(self, handle):
            events.append(handle)

    def run_task(task, *args):
        events.append((task.__name__, args))
        return "tracked"

    monkeypatch.setattr(launcher_main, "App", FakeApp)
    monkeypatch.setattr(launcher_main.util, "check_connection", lambda: False)
    asyncio.run(launcher_main.main(SimpleNamespace(run_task=run_task), launch_version="exact-id"))
    assert events == ["init", "run", ("launch_version_by_id", ("exact-id",)), "tracked"]


def test_launch_argument_is_passed_to_flet_target(monkeypatch):
    captured = []
    monkeypatch.setattr(launcher_main, "prepare_process_workdir", lambda: None)
    monkeypatch.setattr(launcher_main, "setup_logging", lambda: None)
    monkeypatch.setattr(launcher_main, "normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr(launcher_main, "resume_pending_update_if_needed", lambda _logger: False)
    monkeypatch.setattr(launcher_main.ft, "run", lambda target, **kwargs: captured.append(target))

    assert launcher_main.launch(["--launch-version=some stable-id"]) == 0
    assert captured[0].func is launcher_main.main
    assert captured[0].keywords["launch_version"] == "some stable-id"
    assert captured[0].keywords["instance"].closed


def test_launch_preserves_ignored_runtime_arguments(monkeypatch):
    captured = []
    monkeypatch.setattr(launcher_main, "prepare_process_workdir", lambda: None)
    monkeypatch.setattr(launcher_main, "setup_logging", lambda: None)
    monkeypatch.setattr(launcher_main, "normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr(launcher_main, "resume_pending_update_if_needed", lambda _logger: False)
    monkeypatch.setattr(launcher_main.ft, "run", lambda target, **kwargs: captured.append(target))
    monkeypatch.setattr(launcher_main.sys, "argv", ["host", "--runtime-option", "value"])
    assert launcher_main.launch() == 0
    assert captured[0].func is launcher_main.main
    assert captured[0].keywords["launch_version"] is None


def test_cli_run_forwards_id_without_forwarding_developer_command(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher_main, "launch", lambda argv: calls.append(argv) or 7)
    assert cli.main(["run", "--launch-version=stable-id"]) == 7
    assert calls == [["--launch-version=stable-id"]]


def test_cli_plain_run_does_not_reparse_pytest_or_tl_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher_main, "launch", lambda argv: calls.append(argv) or 0)
    assert cli.main(["run"]) == 0
    assert calls == [[]]


@pytest.mark.parametrize("argv", [["--launch-version"], ["--launch-version="], ["--launch-version=a\nb"]])
def test_invalid_shortcut_argument_is_rejected_before_startup(monkeypatch, argv):
    calls = []
    monkeypatch.setattr(launcher_main, "prepare_process_workdir", lambda: calls.append(True))
    with pytest.raises(SystemExit) as error:
        launcher_main.launch(argv)
    assert error.value.code == 2
    assert calls == []
