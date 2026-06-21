from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import launcher.core.util as util_module
import launcher.models.logger as logger_module
import launcher.platform.paths as paths_module
from launcher.domain.version import Version
from launcher.platform.resources import ResourceService
from launcher.state import StateStore
from launcher.storage.config_store import Config
from launcher.storage.profile_store import Profiles
from launcher.storage.version_store import Versions

LauncherPaths = util_module.LauncherPaths
Logger = logger_module.Logger
UtilService = util_module.UtilService


def _windows_default_app_state_dir(local_app_data: Path) -> Path:
    return local_app_data / "TensaLauncher"


def _windows_default_minecraft_dir(local_app_data: Path) -> Path:
    if local_app_data.name.casefold() == "local" and local_app_data.parent.name.casefold() == "appdata":
        return local_app_data.parent / "Roaming" / "TensaLauncher"
    return local_app_data.parent / "AppData" / "Roaming" / "TensaLauncher"


def _set_windows_app_data(monkeypatch: pytest.MonkeyPatch, local_app_data: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("APPDATA", str(_windows_default_minecraft_dir(local_app_data).parent))


def test_util_init_keeps_configured_minecraft_dir_when_unavailable(tmp_path, monkeypatch):
    app_dir = tmp_path / "launcher"
    app_dir.mkdir()
    minecraft_dir = tmp_path / "external-drive" / "minecraft"
    paths = LauncherPaths(
        app_dir=app_dir,
        app_state_dir=app_dir,
        minecraft_dir=minecraft_dir,
        games_dir=minecraft_dir / "games",
    )

    monkeypatch.setattr(LauncherPaths, "detect", classmethod(lambda cls: paths))

    service = UtilService()
    override_updates: list[object] = []

    monkeypatch.setattr(service.path_service, "migrate_legacy_app_state", lambda: None)
    monkeypatch.setattr(service, "set_minecraft_dir_override", lambda value: override_updates.append(value))

    def raise_unavailable() -> None:
        raise OSError("disk offline")

    monkeypatch.setattr(service.path_service, "init_directories", raise_unavailable)

    service.init()

    assert service.paths.minecraft_dir == minecraft_dir
    assert service.minecraft_dir_error == "disk offline"
    assert override_updates == []


def test_launcher_paths_detect_uses_dot_dev_in_development(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    dev_root = Path(paths_module.DEV_ROOT_DIRNAME)

    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr(LauncherPaths, "_resolve_dev_base", staticmethod(lambda: dev_root))
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    paths = LauncherPaths.detect()

    assert paths.app_dir.name == paths_module.DEV_ROOT_DIRNAME
    assert paths.app_state_dir == paths.app_dir
    assert paths.minecraft_dir == paths.app_dir / "minecraft"


def test_launcher_paths_detect_resolves_dev_root_from_project_root(monkeypatch, tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tensalauncher'\n", encoding="utf-8")
    (tmp_path / "launcher").mkdir()
    (tmp_path / ".tools").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    paths = LauncherPaths.detect()

    assert paths.app_dir == tmp_path / paths_module.DEV_ROOT_DIRNAME
    assert paths.app_state_dir == paths.app_dir
    assert paths.minecraft_dir == paths.app_dir / "minecraft"


def test_launcher_paths_detect_resolves_dev_root_from_module_path_when_cwd_is_wrong(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "repo"
    (project_root / "launcher" / "platform").mkdir(parents=True)
    (project_root / ".tools").mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='tensalauncher'\n", encoding="utf-8")

    foreign_cwd = tmp_path / "venv" / "Lib"
    foreign_cwd.mkdir(parents=True)

    monkeypatch.chdir(foreign_cwd)
    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)
    monkeypatch.setattr(paths_module, "__file__", str(project_root / "launcher" / "platform" / "paths.py"))

    paths = LauncherPaths.detect()

    assert paths.app_dir == project_root / paths_module.DEV_ROOT_DIRNAME
    assert paths.app_state_dir == paths.app_dir
    assert paths.minecraft_dir == paths.app_dir / "minecraft"


def test_launcher_paths_detect_uses_application_support_on_frozen_macos(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "user"
    bundle_bin = tmp_path / "Applications" / "TensaLauncher.app" / "Contents" / "MacOS" / "TensaLauncher"
    bundle_bin.parent.mkdir(parents=True)

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(bundle_bin), platform="darwin", frozen=True),
    )
    monkeypatch.setattr(paths_module, "_home_dir", lambda: home_dir)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)

    paths = LauncherPaths.detect()

    expected_root = home_dir / "Library" / "Application Support" / "TensaLauncher"
    assert paths.app_dir == expected_root
    assert paths.app_state_dir == expected_root
    assert paths.minecraft_dir == expected_root / "minecraft"


def test_launcher_paths_detect_uses_xdg_standard_dirs_on_frozen_linux(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "user"
    xdg_config_home = home_dir / ".xdg-config"
    xdg_data_home = home_dir / ".xdg-data"
    appimage = tmp_path / "Downloads" / "TensaLauncher-x86_64.AppImage"
    appimage.parent.mkdir(parents=True)

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable="/tmp/_MEI123/TensaLauncher", platform="linux", frozen=True),
    )
    monkeypatch.setattr(paths_module, "_home_dir", lambda: home_dir)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)
    monkeypatch.setenv("APPIMAGE", str(appimage))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)

    paths = LauncherPaths.detect()

    expected_root = xdg_config_home / "TensaLauncher"
    expected_minecraft = xdg_data_home / "TensaLauncher"
    assert paths.app_dir == expected_root
    assert paths.app_state_dir == expected_root
    assert paths.minecraft_dir == expected_minecraft
    assert paths.minecraft_dir != expected_root / "minecraft"
    assert appimage.parent != expected_root


def test_path_policy_exposes_platform_cache_and_log_dirs(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "user"
    xdg_cache_home = home_dir / ".xdg-cache"
    xdg_state_home = home_dir / ".xdg-state"

    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(paths_module, "_home_dir", lambda: home_dir)
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state_home))

    assert paths_module.PathPolicy.default_cache_dir() == xdg_cache_home / "TensaLauncher"
    assert paths_module.PathPolicy.default_log_dir() == xdg_state_home / "TensaLauncher"


def test_launcher_paths_detect_uses_local_app_data_default_on_windows(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "Users" / "WDAGUtilityAccount" / "AppData" / "Local"
    roaming_app_data = tmp_path / "Users" / "WDAGUtilityAccount" / "AppData" / "Roaming"
    user_profile = tmp_path / "Users" / "WDAGUtilityAccount"
    executable = tmp_path / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.setenv("APPDATA", str(roaming_app_data))
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    detected = LauncherPaths.detect()

    assert detected.app_state_dir == local_app_data / "TensaLauncher"
    assert detected.minecraft_dir == roaming_app_data / "TensaLauncher"
    assert detected.app_state_dir != user_profile / "TensaLauncher"
    assert detected.minecraft_dir != local_app_data / "TensaLauncher" / "minecraft"


def test_path_service_migrates_legacy_profile_state_to_local_app_data(monkeypatch, tmp_path: Path):
    user_profile = tmp_path / "Users" / "TensaUser"
    local_app_data = user_profile / "AppData" / "Local"
    legacy_root = user_profile / "TensaLauncher"
    target_root = local_app_data / "TensaLauncher"
    executable = tmp_path / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")
    legacy_root.mkdir(parents=True)
    (legacy_root / "config.json").write_text('{"lang":"uk_UA"}', encoding="utf-8")
    (legacy_root / "versions.json").write_text('{"vanilla": {"name": "Minecraft"}}', encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    assert json.loads((target_root / "config.json").read_text(encoding="utf-8")) == {"lang": "uk_UA"}
    assert json.loads((target_root / "versions.json").read_text(encoding="utf-8")) == {
        "vanilla": {"name": "Minecraft"}
    }


def test_path_service_migrates_legacy_linux_state_and_minecraft_data(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "home"
    xdg_config_home = home_dir / ".config"
    xdg_data_home = home_dir / ".local" / "share"
    legacy_root = xdg_data_home / "TensaLauncher"
    legacy_minecraft = legacy_root / "minecraft"
    target_root = xdg_config_home / "TensaLauncher"
    target_minecraft = xdg_data_home / "TensaLauncher"
    appimage = tmp_path / "Downloads" / "TensaLauncher-x86_64.AppImage"
    appimage.parent.mkdir(parents=True)

    legacy_minecraft.mkdir(parents=True)
    (legacy_minecraft / "games" / "aeronautics").mkdir(parents=True)
    (legacy_root / "config.json").write_text('{"minecraft_game_dir":"minecraft","lang":"uk_UA"}', encoding="utf-8")
    (legacy_root / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable="/tmp/_MEI123/TensaLauncher", platform="linux", frozen=True),
    )
    monkeypatch.setattr(paths_module, "_home_dir", lambda: home_dir)
    monkeypatch.setenv("APPIMAGE", str(appimage))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    migrated_config = json.loads((target_root / "config.json").read_text(encoding="utf-8"))
    assert migrated_config["lang"] == "uk_UA"
    assert migrated_config.get("minecraft_game_dir") is None
    assert json.loads((target_root / "versions.json").read_text(encoding="utf-8")) == {
        "aeronautics": {"name": "Aeronautics"}
    }
    assert (target_minecraft / "games" / "aeronautics").is_dir()
    assert not (target_root / "minecraft").exists()


def test_launcher_paths_detect_ignores_runtime_app_base_override(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    executable = package_dir / "TensaLauncher.exe"
    package_dir.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.setenv("TENSALAUNCHER_APP_BASE", str(package_dir))
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    detected = LauncherPaths.detect()

    expected_root = _windows_default_app_state_dir(local_app_data)
    assert detected.app_state_dir == expected_root
    assert detected.minecraft_dir == _windows_default_minecraft_dir(local_app_data)


def test_launcher_paths_detect_ignores_legacy_app_base_runtime_alias(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    legacy_override = tmp_path / "legacy-override"
    executable = tmp_path / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.setenv("TCL_APP_BASE", str(legacy_override))
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    detected = LauncherPaths.detect()

    assert detected.app_state_dir == _windows_default_app_state_dir(local_app_data)
    assert detected.app_state_dir != legacy_override


def test_launcher_paths_detect_ignores_runtime_package_state_pointer(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    executable = package_dir / "TensaLauncher.exe"
    package_dir.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    pointer_dir = local_app_data / "TensaLauncher"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / paths_module.APP_STATE_POINTER_FILENAME).write_text(
        json.dumps({paths_module.APP_STATE_POINTER_KEY: str(package_dir)}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    detected = LauncherPaths.detect()

    expected_root = _windows_default_app_state_dir(local_app_data)
    assert detected.app_state_dir == expected_root
    assert detected.minecraft_dir == _windows_default_minecraft_dir(local_app_data)


def test_path_service_repairs_runtime_package_minecraft_override(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    executable = package_dir / "TensaLauncher.exe"
    package_dir.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    accepted = service.set_minecraft_dir_override(str(package_dir / "minecraft"))

    assert accepted is False
    assert service.paths.minecraft_dir == _windows_default_minecraft_dir(local_app_data)


def test_launcher_paths_detect_uses_local_app_data_default_for_classic_windows_build(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    executable = tmp_path / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    detected = LauncherPaths.detect()

    expected_root = _windows_default_app_state_dir(local_app_data)
    assert detected.app_dir == expected_root
    assert detected.app_state_dir == expected_root
    assert detected.minecraft_dir == _windows_default_minecraft_dir(local_app_data)


def test_launcher_paths_detect_uses_local_app_data_default_even_when_sidecar_is_not_writable(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    executable = tmp_path / "Program Files" / "TensaLauncher" / "TensaLauncher.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)
    monkeypatch.setattr(paths_module, "_is_writable_directory", lambda _path: False)

    detected = LauncherPaths.detect()

    expected_root = _windows_default_app_state_dir(local_app_data)
    assert detected.app_dir == expected_root
    assert detected.app_state_dir == expected_root
    assert detected.minecraft_dir == _windows_default_minecraft_dir(local_app_data)


def test_launcher_paths_detect_uses_persisted_user_data_root(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    custom_root = tmp_path / "Games" / "TensaLauncher"
    executable = tmp_path / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.set_app_state_dir_override(str(custom_root))
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)

    detected = LauncherPaths.detect()

    assert detected.app_dir == custom_root
    assert detected.app_state_dir == custom_root
    assert detected.minecraft_dir == custom_root / "minecraft"
    assert (_windows_default_app_state_dir(local_app_data) / paths_module.APP_STATE_POINTER_FILENAME).is_file()


def test_logger_primary_path_uses_launcher_app_dir(monkeypatch, tmp_path: Path):
    app_dir = tmp_path / "TensaLauncher"
    monkeypatch.setattr(
        LauncherPaths,
        "detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=app_dir,
                app_state_dir=app_dir,
                minecraft_dir=app_dir / "minecraft",
                games_dir=app_dir / "minecraft" / "games",
            )
        ),
    )

    assert Logger._candidate_log_paths()[0] == app_dir / "app.log"


def test_logger_fallback_path_uses_product_name_on_windows(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(logger_module.sys, "platform", "win32")
    monkeypatch.setattr(paths_module, "sys", SimpleNamespace(platform="win32"))
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("APPDATA", raising=False)

    assert Logger._fallback_log_file() == local_app_data / "TensaLauncher" / "app.log"


def test_prepare_process_workdir_switches_dev_runtime_to_dot_dev(monkeypatch, tmp_path: Path):
    target = tmp_path / paths_module.DEV_ROOT_DIRNAME
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("launcher.main.is_frozen", lambda: False)
    monkeypatch.setattr(
        "launcher.main.LauncherPaths.detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=target,
                app_state_dir=target,
                minecraft_dir=target / "minecraft",
                games_dir=target / "minecraft" / "games",
            )
        ),
    )

    from launcher.main import prepare_process_workdir

    prepare_process_workdir()

    assert Path(os.getcwd()) == target
    assert target.is_dir()


def test_prepare_process_workdir_switches_classic_frozen_runtime_to_user_data(monkeypatch, tmp_path: Path):
    sidecar_dir = tmp_path / "Downloads" / "TensaLauncher"
    target = tmp_path / "LocalAppData" / "TensaLauncher"
    sidecar_dir.mkdir(parents=True)
    monkeypatch.chdir(sidecar_dir)
    monkeypatch.setattr(
        "launcher.main.LauncherPaths.detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=target,
                app_state_dir=target,
                minecraft_dir=target / "minecraft",
                games_dir=target / "minecraft" / "games",
            )
        ),
    )

    from launcher.main import prepare_process_workdir

    prepare_process_workdir()

    assert Path(os.getcwd()) == target
    assert target.is_dir()


def test_packaged_smoke_requires_bundled_flet_runtime_when_frozen(monkeypatch, tmp_path: Path):
    from launcher.main import run_packaged_smoke_test

    assets_dir = tmp_path / "assets"
    updater_dir = assets_dir / "updater"
    updater_dir.mkdir(parents=True)
    for name in ("windows_update.bat", "linux_update.sh", "macos_update.sh"):
        (updater_dir / name).write_text("echo update", encoding="utf-8")

    flet_app_dir = tmp_path / "flet_desktop" / "app"
    flet_app_dir.mkdir(parents=True)
    (flet_app_dir / "flet-windows.zip").write_bytes(b"zip")

    paths_root = tmp_path / "runtime"
    minecraft_dir = paths_root / "minecraft"
    minecraft_dir.mkdir(parents=True)

    monkeypatch.setattr("launcher.main.PACKAGE_ASSETS_DIR", assets_dir)
    monkeypatch.setattr("launcher.main.is_frozen", lambda: True)
    monkeypatch.setattr("launcher.main.flet_desktop.get_artifact_filename", lambda: "flet-windows.zip")
    monkeypatch.setattr("launcher.main.flet_desktop.get_package_bin_dir", lambda: str(flet_app_dir))
    monkeypatch.setattr(
        "launcher.main.LauncherPaths.detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=paths_root,
                app_state_dir=paths_root,
                minecraft_dir=minecraft_dir,
                games_dir=minecraft_dir / "games",
            )
        ),
    )

    assert run_packaged_smoke_test() == 0


def test_packaged_smoke_fails_without_bundled_flet_runtime_when_frozen(monkeypatch, tmp_path: Path):
    from launcher.main import run_packaged_smoke_test

    assets_dir = tmp_path / "assets"
    updater_dir = assets_dir / "updater"
    updater_dir.mkdir(parents=True)
    for name in ("windows_update.bat", "linux_update.sh", "macos_update.sh"):
        (updater_dir / name).write_text("echo update", encoding="utf-8")

    flet_app_dir = tmp_path / "flet_desktop" / "app"
    flet_app_dir.mkdir(parents=True)

    paths_root = tmp_path / "runtime"
    minecraft_dir = paths_root / "minecraft"
    minecraft_dir.mkdir(parents=True)

    monkeypatch.setattr("launcher.main.PACKAGE_ASSETS_DIR", assets_dir)
    monkeypatch.setattr("launcher.main.is_frozen", lambda: True)
    monkeypatch.setattr("launcher.main.flet_desktop.get_artifact_filename", lambda: "flet-windows.zip")
    monkeypatch.setattr("launcher.main.flet_desktop.get_package_bin_dir", lambda: str(flet_app_dir))
    monkeypatch.setattr(
        "launcher.main.LauncherPaths.detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=paths_root,
                app_state_dir=paths_root,
                minecraft_dir=minecraft_dir,
                games_dir=minecraft_dir / "games",
            )
        ),
    )

    with pytest.raises(FileNotFoundError, match="Smoke test missing bundled Flet runtime"):
        run_packaged_smoke_test()


def test_main_starts_app_when_startup_connection_check_fails(monkeypatch):
    from launcher import main as launcher_main

    started = []

    class FakeApp:
        def __init__(self, page):
            self.page = page

        def run(self):
            started.append(self.page)

    class FakePage:
        def __init__(self):
            self.added = []
            self.updated = False

        def add(self, *controls):
            self.added.extend(controls)

        def update(self):
            self.updated = True

    page = FakePage()

    monkeypatch.setattr(launcher_main.util, "check_connection", lambda: False)
    monkeypatch.setattr(launcher_main, "App", FakeApp)

    launcher_main.main(page)

    assert started == [page]
    assert page.added == []
    assert page.updated is False


def test_launch_passes_assets_dir_to_flet(monkeypatch, tmp_path: Path):
    import flet as ft

    from launcher.main import launch, main

    captured = {}
    assets_dir = tmp_path / "assets"

    monkeypatch.setattr("launcher.main.prepare_process_workdir", lambda: None)
    monkeypatch.setattr("launcher.main.setup_logging", lambda: None)
    monkeypatch.setattr("launcher.main.normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr("launcher.main.resume_pending_update_if_needed", lambda logger: False)
    monkeypatch.setattr("launcher.main.PACKAGE_ASSETS_DIR", assets_dir)
    monkeypatch.setattr("launcher.main.ft.run", lambda target, **kwargs: captured.update({"target": target, **kwargs}))
    monkeypatch.delenv("TENSALAUNCHER_CLEAR_LOG_ON_START", raising=False)
    monkeypatch.delenv("TCL_CLEAR_LOG_ON_START", raising=False)

    launch()

    assert captured["target"] is main
    assert captured["assets_dir"] == str(assets_dir)
    assert captured["view"] == ft.AppView.FLET_APP_HIDDEN


def test_launch_retries_flet_client_cache_race_once(monkeypatch, tmp_path: Path):
    from launcher.main import launch

    calls = []
    source = tmp_path / ".flet" / "client" / "flet-desktop-full-0.84.0.tmp"
    target = tmp_path / ".flet" / "client" / "flet-desktop-full-0.84.0"
    warnings = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise FileExistsError(183, "Cannot create a file when that file already exists", str(source), str(target))

    monkeypatch.setattr("launcher.main.prepare_process_workdir", lambda: None)
    monkeypatch.setattr("launcher.main.setup_logging", lambda: None)
    monkeypatch.setattr("launcher.main.normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr("launcher.main.resume_pending_update_if_needed", lambda logger: False)
    monkeypatch.setattr("launcher.main.ft.run", fake_run)
    monkeypatch.setattr("launcher.main.FLET_CLIENT_CACHE_RETRY_DELAYS", (0,))
    monkeypatch.setattr("launcher.main.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("launcher.main.Logger.warning", lambda message: warnings.append(message))
    monkeypatch.setattr(
        "launcher.main.Logger.error",
        lambda message: (_ for _ in ()).throw(AssertionError(f"Unexpected fatal log: {message}")),
    )
    monkeypatch.delenv("TENSALAUNCHER_CLEAR_LOG_ON_START", raising=False)
    monkeypatch.delenv("TCL_CLEAR_LOG_ON_START", raising=False)

    assert launch([]) == 0
    assert len(calls) == 2
    assert any("Flet desktop client cache is locked or incomplete" in message for message in warnings)


def test_launch_retries_locked_flet_client_cache_once(monkeypatch, tmp_path: Path):
    from launcher.main import launch

    calls = []
    source = tmp_path / ".flet" / "client" / "flet-desktop-full-0.85.1.tmp"
    target = tmp_path / ".flet" / "client" / "flet-desktop-full-0.85.1"
    warnings = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise PermissionError(5, "Access is denied", str(source), str(target))

    monkeypatch.setattr("launcher.main.prepare_process_workdir", lambda: None)
    monkeypatch.setattr("launcher.main.setup_logging", lambda: None)
    monkeypatch.setattr("launcher.main.normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr("launcher.main.resume_pending_update_if_needed", lambda logger: False)
    monkeypatch.setattr("launcher.main.ft.run", fake_run)
    monkeypatch.setattr("launcher.main.FLET_CLIENT_CACHE_RETRY_DELAYS", (0,))
    monkeypatch.setattr("launcher.main.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("launcher.main.Logger.warning", lambda message: warnings.append(message))
    monkeypatch.setattr(
        "launcher.main.Logger.error",
        lambda message: (_ for _ in ()).throw(AssertionError(f"Unexpected fatal log: {message}")),
    )
    monkeypatch.delenv("TENSALAUNCHER_CLEAR_LOG_ON_START", raising=False)
    monkeypatch.delenv("TCL_CLEAR_LOG_ON_START", raising=False)

    assert launch([]) == 0
    assert len(calls) == 2
    assert any("Flet desktop client cache is locked" in message for message in warnings)


def test_launch_reports_locked_flet_client_cache_when_retries_fail(monkeypatch, tmp_path: Path):
    from launcher.main import launch

    calls = []
    source = tmp_path / ".flet" / "client" / "flet-desktop-full-0.85.1.tmp"
    target = tmp_path / ".flet" / "client" / "flet-desktop-full-0.85.1"
    errors = []
    messages = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        raise PermissionError(5, "Access is denied", str(source), str(target))

    monkeypatch.setattr("launcher.main.prepare_process_workdir", lambda: None)
    monkeypatch.setattr("launcher.main.setup_logging", lambda: None)
    monkeypatch.setattr("launcher.main.normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr("launcher.main.resume_pending_update_if_needed", lambda logger: False)
    monkeypatch.setattr("launcher.main.ft.run", fake_run)
    monkeypatch.setattr("launcher.main.FLET_CLIENT_CACHE_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr("launcher.main.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("launcher.main.Logger.error", lambda message: errors.append(message))
    monkeypatch.setattr("launcher.main.show_startup_error_message", lambda title, message: messages.append((title, message)))
    monkeypatch.delenv("TENSALAUNCHER_CLEAR_LOG_ON_START", raising=False)
    monkeypatch.delenv("TCL_CLEAR_LOG_ON_START", raising=False)

    assert launch([]) == 1
    assert len(calls) == 3
    assert any("Flet desktop client cache is locked" in message for message in errors)
    assert messages
    assert ".flet" in messages[0][1]


def test_launch_resumes_pending_update_before_flet(monkeypatch):
    from launcher.main import launch

    resumed = {}

    monkeypatch.setattr("launcher.main.prepare_process_workdir", lambda: None)
    monkeypatch.setattr("launcher.main.setup_logging", lambda: None)
    monkeypatch.setattr("launcher.main.normalize_linux_frozen_runtime_env", lambda: None)
    monkeypatch.setattr(
        "launcher.main.resume_pending_update_if_needed",
        lambda logger: resumed.setdefault("called", True),
    )
    monkeypatch.setattr(
        "launcher.main.ft.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Flet should not start")),
    )
    monkeypatch.delenv("TENSALAUNCHER_CLEAR_LOG_ON_START", raising=False)
    monkeypatch.delenv("TCL_CLEAR_LOG_ON_START", raising=False)

    assert launch([]) == 0
    assert resumed["called"] is True


def test_minecraft_dir_override_does_not_create_sidecar_file(tmp_path, monkeypatch):
    dev_root = tmp_path / ".dev"
    dev_root.mkdir()

    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr(LauncherPaths, "_resolve_dev_base", staticmethod(lambda: dev_root))
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    override = tmp_path / "portable-minecraft"
    service.set_minecraft_dir_override(str(override))

    assert service.paths.minecraft_dir == override
    assert not (dev_root / ".launcher-paths.json").exists()


def test_path_service_migrates_frozen_sidecar_state_into_user_data_dir(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "user"
    legacy_base = tmp_path / "Applications" / "TCL.app" / "Contents" / "MacOS"
    legacy_base.mkdir(parents=True)
    (legacy_base / "config.json").write_text('{"lang":"uk_UA"}', encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(legacy_base / "TCL"), platform="darwin", frozen=True),
    )
    monkeypatch.setattr(paths_module, "_home_dir", lambda: home_dir)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target = home_dir / "Library" / "Application Support" / "TensaLauncher" / "config.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"lang": "uk_UA"}


def test_path_service_migrates_sidecar_minecraft_dir_to_windows_roaming_default(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    legacy_base = tmp_path / "Games" / "TensaLauncher"
    legacy_minecraft = legacy_base / "minecraft"
    (legacy_minecraft / "games" / "aeronautics").mkdir(parents=True)
    (legacy_base / "config.json").write_text('{"lang":"uk_UA"}', encoding="utf-8")
    (legacy_base / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")

    executable = legacy_base / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target_config = _windows_default_app_state_dir(local_app_data) / "config.json"
    migrated_config = json.loads(target_config.read_text(encoding="utf-8"))
    default_minecraft = _windows_default_minecraft_dir(local_app_data)
    assert migrated_config.get("minecraft_game_dir") is None
    assert (default_minecraft / "games" / "aeronautics").is_dir()


def test_path_service_migrates_legacy_relative_minecraft_dir_to_windows_roaming_default(
    monkeypatch,
    tmp_path: Path,
):
    local_app_data = tmp_path / "LocalAppData"
    legacy_base = tmp_path / "Games" / "TensaLauncher"
    legacy_minecraft = legacy_base / "minecraft"
    (legacy_minecraft / "versions" / "1.21.1").mkdir(parents=True)
    (legacy_base / "config.json").write_text('{"minecraft_game_dir":"minecraft"}', encoding="utf-8")
    (legacy_base / "versions.json").write_text('{"vanilla": {"name": "Minecraft"}}', encoding="utf-8")

    executable = legacy_base / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target_config = _windows_default_app_state_dir(local_app_data) / "config.json"
    migrated_config = json.loads(target_config.read_text(encoding="utf-8"))
    default_minecraft = _windows_default_minecraft_dir(local_app_data)
    assert migrated_config.get("minecraft_game_dir") is None
    assert (default_minecraft / "versions" / "1.21.1").is_dir()


def test_path_service_keeps_legacy_absolute_minecraft_dir_when_migrating_state(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    legacy_base = tmp_path / "Games" / "TensaLauncher"
    legacy_minecraft = legacy_base / "minecraft"
    external_minecraft = tmp_path / "ExternalDrive" / "minecraft"
    (legacy_minecraft / "games" / "aeronautics").mkdir(parents=True)
    (legacy_base / "config.json").write_text(
        json.dumps({"minecraft_game_dir": str(external_minecraft)}),
        encoding="utf-8",
    )
    (legacy_base / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")

    executable = legacy_base / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target_config = _windows_default_app_state_dir(local_app_data) / "config.json"
    assert json.loads(target_config.read_text(encoding="utf-8"))["minecraft_game_dir"] == str(external_minecraft)


def test_path_service_repairs_already_migrated_state_without_minecraft_dir(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    legacy_state_root = local_app_data / "TensaLauncher"
    target_root = _windows_default_app_state_dir(local_app_data)
    legacy_base = tmp_path / "Games" / "TensaLauncher"
    legacy_minecraft = legacy_base / "minecraft"
    (legacy_minecraft / "games" / "aeronautics").mkdir(parents=True)
    legacy_state_root.mkdir(parents=True)
    (legacy_state_root / "minecraft" / "games").mkdir(parents=True)
    (legacy_state_root / "config.json").write_text('{"lang":"uk_UA"}', encoding="utf-8")
    (legacy_state_root / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")

    executable = legacy_base / "TensaLauncher.exe"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    migrated_config = json.loads((target_root / "config.json").read_text(encoding="utf-8"))
    default_minecraft = _windows_default_minecraft_dir(local_app_data)
    assert migrated_config.get("minecraft_game_dir") is None
    assert (default_minecraft / "games" / "aeronautics").is_dir()


def test_path_service_migrates_current_relative_minecraft_dir_to_windows_roaming_default(
    monkeypatch,
    tmp_path: Path,
):
    local_app_data = tmp_path / "LocalAppData"
    legacy_state_root = local_app_data / "TensaLauncher"
    target_root = _windows_default_app_state_dir(local_app_data)
    current_minecraft = legacy_state_root / "minecraft"
    legacy_base = tmp_path / "Games" / "TensaLauncher"
    legacy_minecraft = legacy_base / "minecraft"
    (current_minecraft / "games" / "current").mkdir(parents=True)
    (legacy_minecraft / "games" / "legacy").mkdir(parents=True)
    (legacy_state_root / "config.json").write_text('{"minecraft_game_dir":"minecraft"}', encoding="utf-8")
    (legacy_state_root / "versions.json").write_text('{"current": {"name": "Current"}}', encoding="utf-8")

    executable = legacy_base / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    migrated_config = json.loads((target_root / "config.json").read_text(encoding="utf-8"))
    default_minecraft = _windows_default_minecraft_dir(local_app_data)
    assert migrated_config.get("minecraft_game_dir") is None
    assert (default_minecraft / "games" / "current").is_dir()


def test_path_service_does_not_preserve_runtime_package_minecraft_dir(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    package_minecraft = package_dir / "minecraft"
    (package_minecraft / "games" / "aeronautics").mkdir(parents=True)
    (package_dir / "config.json").write_text(
        json.dumps({"minecraft_game_dir": str(package_minecraft), "lang": "uk_UA"}),
        encoding="utf-8",
    )
    (package_dir / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")
    executable = package_dir / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target_root = _windows_default_app_state_dir(local_app_data)
    config = json.loads((target_root / "config.json").read_text(encoding="utf-8"))
    assert config["lang"] == "uk_UA"
    assert config.get("minecraft_game_dir") is None


def test_path_service_reopens_wizard_after_repairing_runtime_package_minecraft_dir(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    package_minecraft = package_dir / "minecraft"
    (package_minecraft / "games" / "aeronautics").mkdir(parents=True)
    (package_dir / "config.json").write_text(
        json.dumps(
            {
                "minecraft_game_dir": str(package_minecraft),
                "setup_wizard_completed": "yes",
                "setup_wizard_version": 1,
                "lang": "uk_UA",
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "versions.json").write_text('{"aeronautics": {"name": "Aeronautics"}}', encoding="utf-8")
    executable = package_dir / "TensaLauncher.exe"
    executable.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    service = paths_module.PathService()
    service.migrate_legacy_app_state()

    target_root = _windows_default_app_state_dir(local_app_data)
    config = json.loads((target_root / "config.json").read_text(encoding="utf-8"))
    assert config["lang"] == "uk_UA"
    assert config.get("minecraft_game_dir") is None
    assert config.get("setup_wizard_completed") is None
    assert config.get("setup_wizard_version") is None


def test_util_minecraft_dir_override_syncs_public_paths(monkeypatch, tmp_path: Path):
    dev_root = tmp_path / ".dev"
    dev_root.mkdir()
    override = tmp_path / "portable-minecraft"

    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr(LauncherPaths, "_resolve_dev_base", staticmethod(lambda: dev_root))
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    util_module.init()
    util_module.set_minecraft_dir_override(str(override))

    assert Path(util_module.minecraft_dir) == override.resolve()
    assert Path(util_module.games_path) == override.resolve() / "games"


def test_state_store_restores_saved_minecraft_dir_from_config(monkeypatch, tmp_path: Path):
    dev_root = tmp_path / ".dev"
    dev_root.mkdir()
    saved_dir = tmp_path / "portable-minecraft"

    monkeypatch.setattr("launcher.platform.paths.is_frozen", lambda: False)
    monkeypatch.setattr(LauncherPaths, "_resolve_dev_base", staticmethod(lambda: dev_root))
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    (dev_root / "config.json").write_text('{"minecraft_game_dir": "../portable-minecraft"}', encoding="utf-8")

    fake_app = SimpleNamespace(log=object())

    class FakeLauncher:
        _INSTANCE_CACHE = {}

    monkeypatch.setattr("launcher.state.UiTheme", SimpleNamespace(build=lambda: "theme"))
    monkeypatch.setattr("launcher.state.set_current_theme", lambda theme: theme)
    monkeypatch.setattr("launcher.state.FeedbackService", lambda _app: "feedback")
    monkeypatch.setattr("launcher.state.ModrinthCatalogService", lambda: "catalog")
    monkeypatch.setattr("launcher.state.ModrinthModsService", lambda: "mods")
    monkeypatch.setattr("launcher.state.VersionOptionsService", lambda: "options")
    monkeypatch.setattr("launcher.state.VersionContentService", lambda *_args: "content")
    monkeypatch.setattr("launcher.state.Auth", lambda _app: "auth")
    monkeypatch.setattr("launcher.state.Profiles", lambda _app, **_kwargs: "profiles")
    monkeypatch.setattr("launcher.state.AutoUpdater", lambda _app: "updater")
    monkeypatch.setattr("launcher.state.Versions", SimpleNamespace(_instance=None, instance=lambda: "versions"))
    monkeypatch.setattr("launcher.core.Launcher", FakeLauncher)

    state = StateStore.build(fake_app)

    assert Path(state.util.minecraft_dir) == saved_dir.resolve()
    assert not (dev_root / "minecraft" / "games").exists()


def test_state_store_repairs_saved_runtime_package_minecraft_dir(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "LocalAppData"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    package_minecraft = package_dir / "minecraft"
    executable = package_dir / "TensaLauncher.exe"
    package_dir.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    state_root = local_app_data / "TensaLauncher"
    state_root.mkdir(parents=True)
    (state_root / "config.json").write_text(
        json.dumps({"minecraft_game_dir": str(package_minecraft)}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(executable), platform="win32", frozen=True),
    )
    _set_windows_app_data(monkeypatch, local_app_data)
    monkeypatch.delenv("TENSALAUNCHER_APP_BASE", raising=False)
    monkeypatch.delenv("TCL_APP_BASE", raising=False)
    monkeypatch.setattr("launcher.platform.paths._runtime_app_state_dir_override", None)
    monkeypatch.setattr("launcher.platform.paths._runtime_minecraft_dir_override", None)

    fake_app = SimpleNamespace(log=object())

    class FakeLauncher:
        _INSTANCE_CACHE = {}

    monkeypatch.setattr("launcher.state.UiTheme", SimpleNamespace(build=lambda: "theme"))
    monkeypatch.setattr("launcher.state.set_current_theme", lambda theme: theme)
    monkeypatch.setattr("launcher.state.FeedbackService", lambda _app: "feedback")
    monkeypatch.setattr("launcher.state.ModrinthCatalogService", lambda: "catalog")
    monkeypatch.setattr("launcher.state.ModrinthModsService", lambda: "mods")
    monkeypatch.setattr("launcher.state.VersionOptionsService", lambda: "options")
    monkeypatch.setattr("launcher.state.VersionContentService", lambda *_args: "content")
    monkeypatch.setattr("launcher.state.WorldBackupService", lambda *_args, **_kwargs: "backups")
    monkeypatch.setattr("launcher.state.UiSoundService", lambda *_args, **_kwargs: "sound")
    monkeypatch.setattr("launcher.state.Auth", lambda _app: "auth")
    monkeypatch.setattr("launcher.state.Profiles", lambda _app, **_kwargs: "profiles")
    monkeypatch.setattr("launcher.state.AutoUpdater", lambda _app: "updater")
    monkeypatch.setattr("launcher.state.Versions", SimpleNamespace(_instance=None, instance=lambda: "versions"))
    monkeypatch.setattr("launcher.core.Launcher", FakeLauncher)

    state = StateStore.build(fake_app)

    assert Path(state.util.minecraft_dir) == _windows_default_minecraft_dir(local_app_data)
    assert Config(storage_dir=_windows_default_app_state_dir(local_app_data)).get("minecraft_game_dir") is None


def test_versions_store_uses_configured_state_and_minecraft_dirs(tmp_path: Path):
    state_root = tmp_path / "state"
    minecraft_dir = tmp_path / "minecraft"
    version_dir = minecraft_dir / "games" / "demo"
    version_dir.mkdir(parents=True)

    Versions._instance = None
    Versions.configure(storage_dir=state_root, minecraft_dir=minecraft_dir)

    store = Versions.instance()
    store.add(Version("demo", {"name": "Demo", "path": "games/demo"}))

    assert (state_root / "versions.json").is_file()
    assert not (minecraft_dir / "versions.json").exists()

    store.remove("demo", delete_files=True)

    assert not version_dir.exists()


def test_state_files_use_app_state_dir_not_package_dir(monkeypatch, tmp_path: Path):
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    state_root = tmp_path / "LocalAppData" / "TensaLauncher"
    minecraft_dir = state_root / "minecraft"
    package_dir.mkdir(parents=True)

    Config(storage_dir=state_root).set("lang", "uk_UA")

    profile_app = SimpleNamespace(
        util=SimpleNamespace(
            app_state_dir=state_root,
            get_user_secret=lambda: Fernet.generate_key().decode("ascii"),
            get_legacy_user_secret=lambda: None,
        ),
        log=SimpleNamespace(error=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
        trans=lambda key, **_kwargs: key,
    )
    Profiles(profile_app).create_profile(
        "Player",
        {
            "name": "Player",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    )

    Versions._instance = None
    Versions.configure(storage_dir=state_root, minecraft_dir=minecraft_dir)
    Versions.instance().add(Version("demo", {"name": "Demo", "path": "games/demo"}))

    monkeypatch.setattr(
        LauncherPaths,
        "detect",
        classmethod(
            lambda cls: LauncherPaths(
                app_dir=state_root,
                app_state_dir=state_root,
                minecraft_dir=minecraft_dir,
                games_dir=minecraft_dir / "games",
            )
        ),
    )
    logger_module.Logger._logger = None
    logger = Logger.setup()
    Logger.info("storage-path-regression")
    for handler in logger.handlers:
        handler.flush()
        handler.close()
    logger.handlers.clear()
    logger_module.Logger._logger = None

    for filename in ("config.json", "profiles.json", "versions.json", "app.log"):
        assert (state_root / filename).is_file()
        assert not (package_dir / filename).exists()


def test_resource_service_resolves_packaged_assets_without_root_assets():
    service = ResourceService(type("Stubpaths_module.PathService", (), {"paths": None})())

    new_style = service.get_resource_path("langs", "uk_UA.json")
    legacy_style = service.get_resource_path("assets", "langs", "uk_UA.json")

    assert new_style is not None and new_style.exists()
    assert legacy_style == new_style


def test_translation_files_are_valid_json():
    for file_name in ("uk_UA.json", "en_US.json"):
        path = Path("launcher/assets/langs") / file_name
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        assert isinstance(data, dict)
        assert "autoupdate" in data
        assert "include_beta_updates" in data
        assert "{status}" not in data["launcher_version_header_with_update"]
        assert "Channel" not in data["launcher_update_channel_stable"]
        assert "Канал" not in data["launcher_update_channel_stable"]
