from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from launcher.state import StateStore


def test_state_store_binds_util_before_config(monkeypatch, tmp_path: Path):
    app = SimpleNamespace(log=object())
    layout = SimpleNamespace(
        app_state_dir=tmp_path,
        minecraft_dir=tmp_path / "minecraft",
        games_dir=tmp_path / "minecraft" / "games",
    )
    fake_util = SimpleNamespace(
        init=lambda **_kwargs: None,
        paths=layout,
        minecraft_dir="mc",
        app_state_dir=tmp_path,
        set_minecraft_dir_override=lambda _value: None,
    )

    class FakeLauncher:
        _INSTANCE_CACHE = {}

    config_called = {"ok": False}

    def fake_config(*args, **kwargs):
        storage_dir = kwargs.get("storage_dir")
        if storage_dir is not None:
            assert Path(storage_dir) == tmp_path
            config_called["ok"] = True
            return SimpleNamespace(get=lambda *_a, **_k: None)
        app_obj = args[0]
        assert getattr(app_obj, "util", None) is fake_util
        return "config"

    monkeypatch.setattr("launcher.state.util", fake_util)
    monkeypatch.setattr("launcher.state.Config", fake_config)
    monkeypatch.setattr("launcher.state.UiTheme", SimpleNamespace(build=lambda: "theme"))
    monkeypatch.setattr("launcher.state.set_current_theme", lambda theme: theme)
    monkeypatch.setattr("launcher.state.FeedbackService", lambda _app: "feedback")
    monkeypatch.setattr("launcher.state.ModrinthCatalogService", lambda: "catalog")
    monkeypatch.setattr("launcher.state.ModrinthModsService", lambda: "mods")
    monkeypatch.setattr("launcher.state.VersionOptionsService", lambda: "options")
    monkeypatch.setattr("launcher.state.VersionContentService", lambda *_args: "content")
    monkeypatch.setattr("launcher.state.WorldBackupService", lambda *_args, **_kwargs: "world_backups")
    monkeypatch.setattr("launcher.state.Auth", lambda _app: "auth")
    monkeypatch.setattr("launcher.state.Profiles", lambda _app, **_kwargs: "profiles")
    monkeypatch.setattr("launcher.state.AutoUpdater", lambda _app: "updater")
    monkeypatch.setattr("launcher.state.Versions", SimpleNamespace(_instance=None, instance=lambda: "versions"))
    monkeypatch.setattr("launcher.core.Launcher", FakeLauncher)

    state = StateStore.build(app)

    assert config_called["ok"] is True
    assert app.util is fake_util
    assert state.util is fake_util
    assert callable(state.config.get)
    assert state.world_backups == "world_backups"
