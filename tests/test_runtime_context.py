from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from launcher.application.version_runtime import VersionRuntime
from launcher.core.launcher import Launcher
from launcher.domain.version import Version
from launcher.platform.paths import StorageLayout


def _app(tmp_path: Path, name: str) -> SimpleNamespace:
    minecraft_dir = tmp_path / name / "minecraft"
    paths = StorageLayout(
        app_state_dir=tmp_path / name / "state",
        minecraft_dir=minecraft_dir,
        games_dir=minecraft_dir / "games",
    )
    app = SimpleNamespace(
        paths=paths,
        util=SimpleNamespace(
            minecraft_dir=paths.minecraft_dir,
            games_path=paths.games_dir,
        ),
    )
    app.launcher = Launcher(app)
    return app


def test_loader_registry_is_isolated_per_application_session(tmp_path: Path) -> None:
    first_app = _app(tmp_path, "first")
    second_app = _app(tmp_path, "second")

    first_loader = first_app.launcher.get_loader("minecraft")
    second_loader = second_app.launcher.get_loader("minecraft")

    assert first_loader is first_app.launcher.get_loader("minecraft")
    assert second_loader is second_app.launcher.get_loader("minecraft")
    assert first_loader is not second_loader
    assert first_loader.app is first_app
    assert second_loader.app is second_app
    assert first_loader.minecraft_dir == first_app.paths.minecraft_dir
    assert second_loader.minecraft_dir == second_app.paths.minecraft_dir


def test_version_runtime_routes_actions_to_its_bound_application() -> None:
    calls: list[tuple] = []

    class Loader:
        def install(self, version, *, loader_version=None):
            calls.append(("install", version.version_id, loader_version))

        def sync_update(self, version, *, force=False, lease=None):
            calls.append(("sync", version.version_id, force, lease))

    app = SimpleNamespace(
        launcher=SimpleNamespace(get_loader=lambda _name: Loader()),
        game=SimpleNamespace(
            start=lambda version, **kwargs: calls.append(
                ("start", version.version_id, kwargs)
            )
            or {"status": True}
        ),
    )
    version = Version(
        "demo",
        {
            "client": "NeoForge",
            "loader_version": "21.1.228",
        },
    )
    version.bind_runtime(VersionRuntime(app))

    version.install()
    version.sync_update(force=True)
    result = version.start(allow_duplicate=True, profile_key="second")

    assert result == {"status": True}
    assert calls == [
        ("install", "demo", "21.1.228"),
        ("sync", "demo", True, None),
        (
            "start",
            "demo",
            {"allow_duplicate": True, "profile_key": "second"},
        ),
    ]
