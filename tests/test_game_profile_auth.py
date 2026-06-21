from types import SimpleNamespace

from launcher.application.memory_preferences import MemoryLimits, MemoryPreferencesService
from launcher.core.game import Game
from launcher.shared import AppContext


def test_game_start_stops_when_default_online_profile_requires_reauth(fake_app, monkeypatch):
    Game._recent_launches.clear()
    Game._active_game_dirs.clear()

    fake_app.auth = SimpleNamespace(
        get_default_profile_data=lambda: {
            "name": "Player",
            "type": "microsoft",
            "access_token": None,
            "refresh_token": None,
            "reauth_required": True,
        },
        profile_requires_reauth=lambda _profile: True,
    )
    fake_app.trans = lambda key, **_: key
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.force_update = False

    verify_calls = []
    monkeypatch.setattr(Game, "_verify", lambda self, version: verify_calls.append(version) or True)

    result = Game().start(version)

    assert result == {"status": False, "text": "profile_reauth_required"}
    assert verify_calls == []


def test_game_build_opts_clamps_legacy_excessive_jvm_memory(fake_app, monkeypatch):
    monkeypatch.setattr(
        MemoryPreferencesService,
        "detect_limits",
        classmethod(lambda _cls: MemoryLimits(total_gb=6, available_gb=3, min_heap_gb=1, max_heap_gb=4, recommended_heap_gb=4)),
    )
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.options = {"jvmArguments": ["-Xmx5000G", "-Xms2G", "-XX:+UseG1GC"]}
    version.executable_path = lambda: ""

    opts = Game()._build_opts(version, {"name": "Player", "id": "uuid", "access_token": "token"})

    assert opts["jvmArguments"] == ["-Xmx4G", "-XX:+UseG1GC"]


def test_game_build_opts_uses_safe_default_max_memory(fake_app, monkeypatch):
    monkeypatch.setattr(
        MemoryPreferencesService,
        "detect_limits",
        classmethod(lambda _cls: MemoryLimits(total_gb=6, available_gb=3, min_heap_gb=1, max_heap_gb=4, recommended_heap_gb=4)),
    )
    fake_app.config.set("default_max_ram_gb", 5000)
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.options = {}
    version.executable_path = lambda: ""

    opts = Game()._build_opts(version, {"name": "Player", "id": "uuid", "access_token": "token"})

    assert opts["jvmArguments"] == ["-Xmx4G"]


def test_game_start_marks_missing_default_profile_reason(fake_app, monkeypatch):
    Game._recent_launches.clear()
    Game._active_game_dirs.clear()

    fake_app.auth = SimpleNamespace(
        get_default_profile_data=lambda: None,
        profile_requires_reauth=lambda _profile: False,
    )
    fake_app.trans = lambda key, **_: key
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.force_update = False

    verify_calls = []
    monkeypatch.setattr(Game, "_verify", lambda self, version: verify_calls.append(version) or True)

    result = Game().start(version)

    assert result == {
        "status": False,
        "text": "no_default_profile",
        "reason": "missing_profile",
    }
    assert verify_calls == []


def test_game_start_uses_selected_profile(fake_app, monkeypatch):
    Game._recent_launches.clear()
    Game._active_game_dirs.clear()

    calls = []
    fake_app.auth = SimpleNamespace(
        get_profile_data=lambda profile_key: calls.append(profile_key) or {
            "name": "SecondPlayer",
            "type": "offline",
            "access_token": "offline",
            "refresh_token": "offline",
        },
        get_default_profile_data=lambda: (_ for _ in ()).throw(
            AssertionError("selected launch must not use default profile")
        ),
        profile_requires_reauth=lambda _profile: False,
    )
    fake_app.trans = lambda key, **_: key
    AppContext.set(fake_app)
    version = fake_app.versions.all()[0]
    version.force_update = False

    monkeypatch.setattr(Game, "_verify", lambda self, _version: True)
    monkeypatch.setattr(Game, "_build_opts", lambda self, _version, profile: {"profile": profile["name"]})
    monkeypatch.setattr(Game, "_launch", lambda self, *_args, **_kwargs: True)

    result = Game().start(version, profile_key="second")

    assert result == {"status": True, "text": "version_starting"}
    assert calls == ["second"]
