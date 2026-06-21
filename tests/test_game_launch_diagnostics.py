from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

from launcher.core.game import Game
from launcher.shared.app_context import AppContext


def test_game_launch_logs_early_process_exit(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    older_hs_err = game_dir / "hs_err_pid100.log"
    latest_hs_err = game_dir / "hs_err_pid200.log"
    older_hs_err.write_text("old fatal error\n", encoding="utf-8")
    latest_hs_err.write_text("latest fatal error\n", encoding="utf-8")
    os.utime(older_hs_err, (100, 100))
    os.utime(latest_hs_err, (200, 200))
    captured = {"info": [], "error": [], "popen": None, "alerts": [], "opened": []}
    fake_app.util.open_mc_dir = lambda path: captured["opened"].append(path) or None

    fake_app.feedback.warning = lambda message, **kwargs: captured["alerts"].append((message, kwargs))

    class FakeProcess:
        pid = 1234

        def poll(self):
            return 1

    class ImmediateThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    def fake_popen(cmd, **kwargs):
        captured["popen"] = SimpleNamespace(cmd=cmd, kwargs=kwargs)
        kwargs["stdout"].write("client failed\n")
        kwargs["stdout"].flush()
        return FakeProcess()

    monkeypatch.setattr(
        "minecraft_launcher_lib.command.get_minecraft_command",
        lambda *_args, **_kwargs: ["C:/Java/bin/javaw.exe", "-jar", "minecraft.jar"],
    )
    monkeypatch.setattr("launcher.core.game.subprocess.Popen", fake_popen)
    monkeypatch.setattr("launcher.core.game.threading.Thread", ImmediateThread)
    monkeypatch.setattr("launcher.core.game.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("launcher.core.game.Logger.info", lambda message: captured["info"].append(message))
    monkeypatch.setattr("launcher.core.game.Logger.error", lambda message: captured["error"].append(message))

    launched = Game()._launch("neoforge-21.1.228", "1.21.1", {"gameDirectory": str(game_dir)})

    diagnostics_log = game_dir / "logs" / "tensalauncher-launch.log"
    assert launched is True
    assert captured["popen"].kwargs["cwd"] == str(game_dir)
    assert captured["popen"].kwargs["stderr"] == subprocess.STDOUT
    assert diagnostics_log.exists()
    assert "client failed" in diagnostics_log.read_text(encoding="utf-8")
    assert any("Minecraft process started: pid=1234" in message for message in captured["info"])
    assert any("Minecraft exited shortly after start" in message for message in captured["error"])
    assert any("client failed" in message for message in captured["error"])
    assert captured["alerts"]
    assert captured["alerts"][0][1]["actions"] is not None
    assert captured["alerts"][0][1]["report_type"] == "crash"
    assert captured["alerts"][0][1]["report_metadata"]["loader"] == "neoforge-21.1.228"
    assert captured["alerts"][0][1]["report_metadata"]["hs_err_path"] == str(latest_hs_err)
    assert captured["alerts"][0][1]["report_attachments"] == [diagnostics_log, latest_hs_err]

    captured["alerts"][0][1]["actions"].on_click(None)

    assert captured["opened"] == [str(diagnostics_log)]


def test_game_start_throttles_rapid_duplicate_launches_for_same_version(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.loader = "neoforge-21.1.228"
    version.version = "1.21.1"
    version.path = str(tmp_path / "game")
    version.force_update = False
    launches = []
    now = [100.0]
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda _self, loader, mc_ver, opts, launch_key=None: launches.append(launch_key) or True)
    monkeypatch.setattr("launcher.core.game.time.monotonic", lambda: now[0])

    try:
        first = Game().start(version)
        second = Game().start(version)
        now[0] += 3.0
        third = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert first["status"] is True
    assert second["status"] is False
    assert second["text"] == "version_launch_throttled (version=Vanilla 1.20.1, seconds=3)"
    assert third["status"] is True
    assert len(launches) == 2


def test_game_start_blocks_when_same_game_directory_is_already_running(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.loader = "neoforge-21.1.228"
    version.version = "1.21.1"
    version.path = str(tmp_path / "game")
    version.force_update = False
    launches = []
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "is_game_dir_active", classmethod(lambda cls, _path: True), raising=False)
    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda *_args, **_kwargs: launches.append(True) or True)

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is False
    assert result["text"] == "version_already_running (version=Vanilla 1.20.1)"
    assert launches == []


def test_game_start_allows_duplicate_launch_when_confirmed(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.loader = "neoforge-21.1.228"
    version.version = "1.21.1"
    version.path = str(tmp_path / "game")
    version.force_update = False
    launches = []
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "is_game_dir_active", classmethod(lambda cls, _path: True), raising=False)
    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda *_args, **_kwargs: launches.append(True) or True)

    try:
        result = Game().start(version, allow_duplicate=True)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is True
    assert launches == [True]


def test_active_game_dir_keeps_tracking_duplicate_processes_until_all_exit(tmp_path):
    game_dir = tmp_path / "game"
    key = Game._normalize_game_dir_key(game_dir)

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True

        def poll(self):
            return None if self.alive else 0

    first = FakeProcess()
    second = FakeProcess()

    try:
        Game._register_active_game_dir(key, first)
        Game._register_active_game_dir(key, second)

        second.alive = False
        Game._release_active_game_dir(key, second)

        assert Game.is_game_dir_active(game_dir) is True

        first.alive = False
        Game._release_active_game_dir(key, first)

        assert Game.is_game_dir_active(game_dir) is False
    finally:
        if hasattr(Game, "_active_game_dirs"):
            Game._active_game_dirs.clear()


def test_game_start_blocks_while_install_session_is_active(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.name = "Aeronautics"
    version.loader = "neoforge-21.1.230"
    version.version = "1.21.1"
    version.path = str(tmp_path / "game")
    version.force_update = False
    launches = []
    fake_app.feedback.is_busy = lambda: True
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda *_args, **_kwargs: launches.append(True) or True)

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is False
    assert result["text"] == "installation_already_running"
    assert launches == []


def test_game_start_syncs_tensacraft_versions_even_when_saved_flag_is_false(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    sync_calls = []
    version = SimpleNamespace(
        name="Aeronautics",
        client="TensaCraft",
        loader="neoforge-21.1.228",
        version="1.21.1",
        path=str(tmp_path / "game"),
        force_update=False,
        sync_update=lambda: sync_calls.append(True),
        is_tensacraft=lambda: True,
        is_home_pinned=lambda: True,
    )
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda _self, loader, mc_ver, opts, launch_key=None: True)

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is True
    assert sync_calls == [True]


def test_game_start_syncs_tensacraft_before_integrity_verify(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    events = []
    version = SimpleNamespace(
        name="Aeronautics",
        client="TensaCraft",
        loader="neoforge-21.1.228",
        version="1.21.1",
        path=str(tmp_path / "game"),
        force_update=False,
        sync_update=lambda: events.append("sync"),
        is_tensacraft=lambda: True,
        is_home_pinned=lambda: True,
    )
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    def verify_after_sync(_self, _version):
        events.append("verify")
        assert events == ["sync", "verify"]
        return True

    monkeypatch.setattr(Game, "_verify", verify_after_sync)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(
        Game,
        "_launch",
        lambda _self, loader, mc_ver, opts, launch_key=None: events.append("launch") or True,
    )

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is True
    assert events == ["sync", "verify", "launch"]


def test_game_start_runs_world_backups_before_launch(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    events = []
    version = fake_app.versions.all()[0]
    version.loader = "neoforge-21.1.228"
    version.version = "1.21.1"
    version.path = str(tmp_path / "game")
    version.force_update = False
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }
    fake_app.world_backups = SimpleNamespace(
        auto_backup_changed_worlds=lambda backup_version, operation=None: events.append(
            ("backup", backup_version, operation is not None)
        )
    )

    monkeypatch.setattr(Game, "_verify", lambda _self, _version: events.append(("verify", _version)) or True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(
        Game,
        "_launch",
        lambda _self, loader, mc_ver, opts, launch_key=None: events.append(("launch", loader, mc_ver)) or True,
    )

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is True
    assert [event[0] for event in events] == ["verify", "backup", "launch"]
    assert events[1][1] is version
    assert events[1][2] is True


def test_game_start_skips_world_backups_when_disabled(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(tmp_path))
    version = fake_app.versions.all()[0]
    version.path = str(tmp_path / "games" / "vanilla")
    version.loader = "1.20.1"
    version.version = "1.20.1"
    version.force_update = False
    fake_app.config.set("world_backups_enabled", "no")
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    def fail_backup(*_args, **_kwargs):
        raise AssertionError("disabled world backups must not be called")

    fake_app.world_backups = SimpleNamespace(
        enabled=lambda: False,
        auto_backup_changed_worlds=fail_backup,
    )
    monkeypatch.setattr(Game, "_verify", lambda self, verify_version, operation=None: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda *args, **kwargs: True)

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is True


def test_game_start_stops_when_tensacraft_sync_fails(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    launches = []

    def fail_sync():
        raise RuntimeError("broken.jar: Permission denied")

    version = SimpleNamespace(
        name="Aeronautics",
        client="TensaCraft",
        loader="neoforge-21.1.230",
        version="1.21.1",
        path=str(tmp_path / "game"),
        force_update=True,
        sync_update=fail_sync,
        is_tensacraft=lambda: True,
        is_home_pinned=lambda: True,
    )
    fake_app.auth.get_default_profile_data = lambda: {
        "name": "PlayerOne",
        "id": "player-1",
        "access_token": "offline",
    }

    monkeypatch.setattr(Game, "_verify", lambda _self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda _self, _version, _profile: {"gameDirectory": version.path})
    monkeypatch.setattr(Game, "_launch", lambda *_args, **_kwargs: launches.append(True) or True)

    try:
        result = Game().start(version)
    finally:
        if hasattr(Game, "_recent_launches"):
            Game._recent_launches.clear()

    assert result["status"] is False
    assert result["text"] == "version_sync_failed (version=Aeronautics, error=broken.jar: Permission denied)"
    assert launches == []


def test_game_verify_restores_missing_base_minecraft_version(fake_app, monkeypatch, tmp_path):
    AppContext.set(fake_app)
    installed_versions = {"neoforge-21.1.228"}
    install_base_calls = []
    install_loader_calls = []
    progress_events = []

    class FakeIntegrityChecker:
        def __init__(self, _minecraft_dir):
            return None

        def _is_version_installed(self, version_id):
            return version_id in installed_versions

    class FakeLoader:
        def _install_minecraft_if_needed(self, mc_version):
            progress_events.append(("install_base", mc_version))
            install_base_calls.append(mc_version)
            installed_versions.add(mc_version)

        def install(self, version, loader_version=None):
            install_loader_calls.append((version, loader_version))
            installed_versions.add(version.loader)

        def verify_and_repair_version(self, version_id, mc_version):
            raise AssertionError("launch verify must not run full component repair")

    fake_loader = FakeLoader()
    version = SimpleNamespace(
        client="NeoForge",
        loader="neoforge-21.1.228",
        loader_version="21.1.228",
        version="1.21.1",
    )

    monkeypatch.setattr("launcher.core.integrity.IntegrityChecker", FakeIntegrityChecker)
    monkeypatch.setattr("launcher.core.Launcher.get_loader", lambda _key: fake_loader)
    operation = SimpleNamespace(
        update=lambda status=None, progress=None, total=None, **_kwargs: progress_events.append(
            ("update", status, progress, total)
        )
    )

    assert Game()._verify(version, operation) is True
    assert install_base_calls == ["1.21.1"]
    assert install_loader_calls == []
    assert progress_events == [
        ("update", "installing_minecraft_version (version=1.21.1)", 0, 100),
        ("install_base", "1.21.1"),
    ]


def test_game_verify_restores_missing_loader_through_component_service(fake_app, monkeypatch):
    AppContext.set(fake_app)
    installed_versions = {"1.21.1"}
    install_calls = []
    save_calls = []

    class FakeIntegrityChecker:
        def __init__(self, _minecraft_dir):
            return None

        def _is_version_installed(self, version_id):
            return version_id in installed_versions

    class FakeLoader:
        def _install_minecraft_if_needed(self, _mc_version):
            raise AssertionError("base Minecraft install should not run when the version exists")

        def install(self, *_args, **_kwargs):
            raise AssertionError("technical loader repair must use InstalledComponentsService")

        def verify_and_repair_version(self, version_id, mc_version):
            raise AssertionError("launch verify must not run full component repair")

    class FakeComponentsService:
        def __init__(self, minecraft_dir, *, games_dir=None, versions_provider=None):
            self.minecraft_dir = minecraft_dir
            self.games_dir = games_dir
            self.versions_provider = versions_provider

        def install_component(self, loader_id, minecraft_version, *, loader_version=None, operation=None):
            install_calls.append((loader_id, minecraft_version, loader_version, operation is not None))
            installed_versions.add("neoforge-21.1.230")
            return SimpleNamespace(
                version_id="neoforge-21.1.230",
                loader_name="NeoForge",
                loader_version="21.1.230",
            )

    version = SimpleNamespace(
        name="Aeronautics",
        client="NeoForge",
        loader="neoforge-21.1.230",
        loader_version="21.1.230",
        version="1.21.1",
        save=lambda: save_calls.append(True),
    )
    operation = SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr("launcher.core.integrity.IntegrityChecker", FakeIntegrityChecker)
    monkeypatch.setattr("launcher.core.Launcher.get_loader", lambda _key: FakeLoader())
    monkeypatch.setattr("launcher.core.game.InstalledComponentsService", FakeComponentsService)

    assert Game()._verify(version, operation) is True
    assert install_calls == [("neoforge", "1.21.1", "21.1.230", True)]
    assert save_calls == [True]
    assert version.loader == "neoforge-21.1.230"
    assert version.client == "NeoForge"
    assert version.loader_version == "21.1.230"


def test_game_verify_repairs_installed_loader_with_missing_libraries(fake_app, monkeypatch):
    AppContext.set(fake_app)
    installed_versions = {"1.21.1", "neoforge-21.1.232"}
    libraries_ok = [False]
    install_calls = []

    class FakeIntegrityChecker:
        def __init__(self, _minecraft_dir):
            return None

        def _is_version_installed(self, version_id):
            return version_id in installed_versions

        def _check_version_manifest(self, version_id):
            return version_id == "neoforge-21.1.232"

        def _check_libraries(self, version_id):
            return libraries_ok[0] if version_id == "neoforge-21.1.232" else True

    class FakeLoader:
        def _install_minecraft_if_needed(self, _mc_version):
            raise AssertionError("base Minecraft install should not run when the version exists")

    class FakeComponentsService:
        def __init__(self, minecraft_dir, *, games_dir=None, versions_provider=None):
            self.minecraft_dir = minecraft_dir
            self.games_dir = games_dir
            self.versions_provider = versions_provider

        def install_component(self, loader_id, minecraft_version, *, loader_version=None, operation=None):
            install_calls.append((loader_id, minecraft_version, loader_version, operation is not None))
            libraries_ok[0] = True
            return SimpleNamespace(
                version_id="neoforge-21.1.232",
                loader_name="NeoForge",
                loader_version="21.1.232",
            )

    version = SimpleNamespace(
        name="Aeronautics",
        client="NeoForge",
        loader="neoforge-21.1.232",
        loader_version="21.1.232",
        version="1.21.1",
        save=lambda: None,
    )
    operation = SimpleNamespace(update=lambda *_args, **_kwargs: None)

    monkeypatch.setattr("launcher.core.integrity.IntegrityChecker", FakeIntegrityChecker)
    monkeypatch.setattr("launcher.core.Launcher.get_loader", lambda _key: FakeLoader())
    monkeypatch.setattr("launcher.core.game.InstalledComponentsService", FakeComponentsService)

    assert Game()._verify(version, operation) is True
    assert install_calls == [("neoforge", "1.21.1", "21.1.232", True)]


def test_game_verify_does_not_open_progress_when_base_version_exists(fake_app, monkeypatch):
    AppContext.set(fake_app)
    installed_versions = {"1.21.1", "neoforge-21.1.228"}

    class FakeIntegrityChecker:
        def __init__(self, _minecraft_dir):
            return None

        def _is_version_installed(self, version_id):
            return version_id in installed_versions

    class FakeLoader:
        def _install_minecraft_if_needed(self, _mc_version):
            raise AssertionError("base Minecraft install should not run when the version exists")

        def verify_and_repair_version(self, _version_id, _mc_version):
            return True

    version = SimpleNamespace(
        client="NeoForge",
        loader="neoforge-21.1.228",
        loader_version="21.1.228",
        version="1.21.1",
    )

    monkeypatch.setattr("launcher.core.integrity.IntegrityChecker", FakeIntegrityChecker)
    monkeypatch.setattr("launcher.core.Launcher.get_loader", lambda _key: FakeLoader())
    fake_app.feedback.update_current_operation = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("progress should not open for a no-op verify")
    )

    assert Game()._verify(version) is True
