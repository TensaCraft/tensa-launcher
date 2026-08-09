from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from launcher.application.launch_workflow import LaunchPreparationRequest, LaunchWorkflow
from launcher.core.game import Game
from launcher.core.versions import Version


class _Operation:
    def __init__(self) -> None:
        self.finish_calls: list[tuple[str | None, bool]] = []

    def finish(self, message: str | None = None, *, show_success: bool = True) -> None:
        self.finish_calls.append((message, show_success))


class _Feedback:
    def __init__(self, operation: _Operation) -> None:
        self.operation = operation
        self.begin_calls: list[tuple[str, str, bool, bool]] = []

    def begin_operation(
        self,
        title: str,
        *,
        kind: str,
        visible: bool,
        auto_open: bool,
    ) -> _Operation:
        self.begin_calls.append((title, kind, visible, auto_open))
        return self.operation


class _StepClock:
    def __init__(self) -> None:
        self.current = 0.0

    def __call__(self) -> float:
        value = self.current
        self.current += 0.01
        return value


@dataclass
class _Version:
    name: str = "Vanilla"
    loader: str = "1.21.1"
    version: str = "1.21.1"
    force_update: bool = False
    tensacraft: bool = False
    pinned: bool = True
    events: list[str] = field(default_factory=list)
    sync_error: Exception | None = None

    def is_tensacraft(self) -> bool:
        return self.tensacraft

    def is_home_pinned(self) -> bool:
        return self.pinned

    def mark_home_pinned(self) -> None:
        self.events.append("pin")
        self.pinned = True

    def sync_update(self, *, lease: object | None = None) -> None:
        self.events.append("sync")
        if self.sync_error is not None:
            raise self.sync_error


def _translate(key: str, **placeholders: Any) -> str:
    if not placeholders:
        return key
    rendered = ", ".join(f"{name}={value}" for name, value in placeholders.items())
    return f"{key} ({rendered})"


def _make_app(
    operation: _Operation,
    *,
    backup: Any | None = None,
) -> SimpleNamespace:
    auth = SimpleNamespace(
        get_default_profile_data=lambda: {
            "name": "PlayerOne",
            "id": "player-1",
            "access_token": "offline",
        },
        profile_requires_reauth=lambda _profile: False,
    )
    world_backups = SimpleNamespace(
        auto_backup_changed_worlds=backup or (lambda *_args, **_kwargs: None),
    )
    return SimpleNamespace(
        auth=auth,
        feedback=_Feedback(operation),
        trans=_translate,
        world_backups=world_backups,
    )


def _make_workflow(
    app: SimpleNamespace,
    version: _Version,
    *,
    verify_result: bool = True,
    launch_result: bool = True,
    releases: list[str] | None = None,
    clock: Callable[[], float] | None = None,
) -> LaunchWorkflow:
    release_calls = releases if releases is not None else []
    subject = version

    def verify(_version: Version, operation=None) -> bool:
        assert operation is not None
        subject.events.append("verify")
        return verify_result

    def build_options(_version: Version, profile: dict[str, Any]) -> dict[str, Any]:
        subject.events.append("options")
        return {"gameDirectory": "game", "username": profile["name"]}

    def launch(
        loader: str,
        minecraft_version: str,
        options: dict[str, Any],
        *,
        launch_key: str | None = None,
        version: Version | None = None,
    ) -> bool:
        assert (loader, minecraft_version) == ("1.21.1", "1.21.1")
        assert options["username"] == "PlayerOne"
        assert launch_key == "launch-key"
        assert version is cast(Version, subject)
        subject.events.append("launch")
        return launch_result

    return LaunchWorkflow(
        app,
        verify=verify,
        build_options=build_options,
        launch=launch,
        release_launch_slot=release_calls.append,
        clock=clock,
    )


def _request(version: _Version) -> LaunchPreparationRequest:
    return LaunchPreparationRequest(
        version=cast(Version, version),
        launch_key="launch-key",
        started_at=0.0,
    )


def test_launch_workflow_success_preserves_preparation_order_and_cleanup() -> None:
    operation = _Operation()
    backup_events: list[str] = []
    version = _Version()

    def backup(_version: Version, operation=None, *, lease=None) -> None:
        assert operation is not None
        assert lease is None
        backup_events.append("backup")
        version.events.append("backup")

    app = _make_app(operation, backup=backup)
    releases: list[str] = []
    request = _request(version)

    result = _make_workflow(app, version, releases=releases).run(request)

    assert result.as_response(_translate) == {
        "status": True,
        "text": "version_starting (version=Vanilla)",
    }
    assert version.events == ["verify", "backup", "options", "launch"]
    assert backup_events == ["backup"]
    assert operation.finish_calls == [("installation_complete", True)]
    assert releases == []
    with pytest.raises(FrozenInstanceError):
        setattr(request, "profile_key", "other")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "status", False)
    with pytest.raises(TypeError):
        cast(dict[str, Any], result.message_args)["version"] = "Changed"


def test_launch_workflow_logs_stage_timing(monkeypatch) -> None:
    operation = _Operation()
    version = _Version()
    logs: list[str] = []
    monkeypatch.setattr("launcher.application.launch_workflow.Logger.info", logs.append)

    result = _make_workflow(
        _make_app(operation),
        version,
        clock=_StepClock(),
    ).run(_request(version))

    assert result.status is True
    assert logs == [
        "Launch timing: ver=1.21.1 loader=1.21.1 "
        "auth=0ms verify=10ms sync=0ms opts=10ms launch=10ms total=60ms"
    ]


def test_launch_workflow_ignores_timing_log_failure(monkeypatch) -> None:
    operation = _Operation()
    version = _Version()
    monkeypatch.setattr(
        "launcher.application.launch_workflow.Logger.info",
        lambda _message: (_ for _ in ()).throw(OSError("log unavailable")),
    )

    result = _make_workflow(_make_app(operation), version).run(_request(version))

    assert result.status is True
    assert version.events == ["verify", "options", "launch"]


def test_tensacraft_launch_syncs_before_verify_and_pins_after_launch() -> None:
    operation = _Operation()
    version = _Version(name="Aeronautics", tensacraft=True, pinned=False)
    app = _make_app(operation)

    result = _make_workflow(app, version).run(_request(version))

    assert result.status is True
    assert version.events == ["sync", "verify", "options", "launch", "pin"]
    assert operation.finish_calls == [("syncing_files_complete", True)]


def test_forced_non_tensacraft_launch_verifies_before_sync() -> None:
    operation = _Operation()
    version = _Version(force_update=True)
    app = _make_app(operation)

    result = _make_workflow(app, version).run(_request(version))

    assert result.status is True
    assert version.events == ["verify", "sync", "options", "launch"]
    assert operation.finish_calls == [("syncing_files_complete", True)]


def test_launch_workflow_stops_after_sync_failure_and_releases_slot() -> None:
    operation = _Operation()
    version = _Version(
        name="Aeronautics",
        loader="neoforge-21.1.230",
        tensacraft=True,
        sync_error=RuntimeError("broken.jar: Permission denied"),
    )
    app = _make_app(operation)
    releases: list[str] = []

    result = _make_workflow(app, version, releases=releases).run(_request(version))

    assert result.as_response(_translate) == {
        "status": False,
        "text": "version_sync_failed (version=Aeronautics, error=broken.jar: Permission denied)",
    }
    assert version.events == ["sync"]
    assert operation.finish_calls == [(None, False)]
    assert releases == ["launch-key"]


def test_launch_workflow_stops_after_verify_failure() -> None:
    operation = _Operation()
    version = _Version()
    app = _make_app(operation)
    releases: list[str] = []

    result = _make_workflow(
        app,
        version,
        verify_result=False,
        releases=releases,
    ).run(_request(version))

    assert result.as_response(_translate) == {
        "status": False,
        "text": "version_integrity_check_failed (version=Vanilla)",
    }
    assert version.events == ["verify"]
    assert operation.finish_calls == [(None, False)]
    assert releases == ["launch-key"]


def test_launch_workflow_preserves_missing_profile_reason_and_cleanup() -> None:
    operation = _Operation()
    version = _Version()
    app = _make_app(operation)
    app.auth.get_default_profile_data = lambda: None
    releases: list[str] = []

    result = _make_workflow(app, version, releases=releases).run(_request(version))

    assert result.as_response(_translate) == {
        "status": False,
        "text": "no_default_profile",
        "reason": "missing_profile",
    }
    assert version.events == []
    assert operation.finish_calls == [(None, False)]
    assert releases == ["launch-key"]


def test_launch_workflow_stops_before_preparation_when_profile_requires_reauth() -> None:
    operation = _Operation()
    version = _Version()
    app = _make_app(operation)
    app.auth.profile_requires_reauth = lambda _profile: True
    releases: list[str] = []

    result = _make_workflow(app, version, releases=releases).run(_request(version))

    assert result.as_response(_translate) == {
        "status": False,
        "text": "profile_reauth_required",
    }
    assert version.events == []
    assert operation.finish_calls == [(None, False)]
    assert releases == ["launch-key"]


def test_launch_workflow_logs_backup_warning_and_still_launches(monkeypatch) -> None:
    operation = _Operation()
    version = _Version()
    warnings: list[str] = []

    def backup(*_args: Any, **_kwargs: Any) -> None:
        version.events.append("backup")
        raise RuntimeError("backup unavailable")

    app = _make_app(operation, backup=backup)
    monkeypatch.setattr("launcher.application.launch_workflow.Logger.warning", warnings.append)

    result = _make_workflow(app, version).run(_request(version))

    assert result.status is True
    assert version.events == ["verify", "backup", "options", "launch"]
    assert warnings == ["World backup step failed before launching Vanilla: RuntimeError('backup unavailable')"]
    assert operation.finish_calls == [("installation_complete", True)]


def test_launch_workflow_reports_failed_backups_and_still_launches(monkeypatch) -> None:
    operation = _Operation()
    version = _Version()
    warnings: list[str] = []
    app = _make_app(operation, backup=lambda *_args, **_kwargs: SimpleNamespace(created=0, failed=2))
    monkeypatch.setattr("launcher.application.launch_workflow.Logger.warning", warnings.append)

    result = _make_workflow(app, version).run(_request(version))

    assert result.status is True
    assert version.events == ["verify", "options", "launch"]
    assert warnings == ["Failed to create 2 world backup(s) before launching Vanilla"]
    assert operation.finish_calls == [("installation_complete", True)]


def test_launch_workflow_process_failure_releases_slot() -> None:
    operation = _Operation()
    version = _Version()
    app = _make_app(operation)
    releases: list[str] = []

    result = _make_workflow(
        app,
        version,
        launch_result=False,
        releases=releases,
    ).run(_request(version))

    assert result.as_response(_translate) == {
        "status": False,
        "text": "version_integrity_check_failed (version=Vanilla)",
    }
    assert version.events == ["verify", "options", "launch"]
    assert operation.finish_calls == [(None, False)]
    assert releases == ["launch-key"]


def test_game_start_cleans_operations_and_launch_slot_after_process_launch_failure(
    fake_app,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(Game, "_recent_launches", {})
    monkeypatch.setattr(Game, "_active_game_dirs", {})
    version = fake_app.versions.all()[0]
    version.path = str(tmp_path / "game")
    version.loader = "1.21.1"
    version.version = "1.21.1"
    version.force_update = False
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }
    fake_app.auth.profile_requires_reauth = lambda _profile: False
    fake_app.world_backups = SimpleNamespace(enabled=lambda: False)
    monkeypatch.setattr(Game, "_verify", lambda _self, _version, operation=None: True)
    monkeypatch.setattr(
        Game,
        "_build_opts",
        lambda _self, _version, _profile: {"gameDirectory": version.path},
    )
    monkeypatch.setattr(Game, "_launch", lambda _self, *_args, **_kwargs: False)

    game = Game(fake_app)
    launch_key = game._version_launch_key(version)
    game_dir = game._version_game_dir(version)

    result = game.start(version)

    assert result == {
        "status": False,
        "text": "version_integrity_check_failed (version=Vanilla 1.20.1)",
    }
    assert fake_app.feedback.is_busy() is False
    assert fake_app.instance_operations.active_kind(game_dir) is None
    assert launch_key not in Game._recent_launches
