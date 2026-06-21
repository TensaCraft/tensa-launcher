from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

import flet as ft
import flet_desktop

from launcher import APP_NAME, ui
from launcher.app import App
from launcher.core import util
from launcher.core.pending_update import resume_pending_update_if_needed
from launcher.models.logger import Logger
from launcher.platform.paths import LauncherPaths, is_frozen
from launcher.platform.resources import PACKAGE_ASSETS_DIR

FLET_CLIENT_CACHE_RETRY_DELAYS = (0.5, 1.0, 2.0)


def prepare_process_workdir() -> None:
    workdir = LauncherPaths.detect().app_dir
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)


def setup_logging() -> None:
    Logger.setup()
    Logger.info("Logging initialised")


def normalize_linux_frozen_runtime_env() -> None:
    # PyInstaller onefile sets LD_LIBRARY_PATH to its unpack dir.
    # External binaries (like flet desktop runtime) must use system libs.
    if not (sys.platform.startswith("linux") and getattr(sys, "frozen", False)):
        return

    original = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def _exception_path_text(exc: BaseException) -> str:
    return " ".join(
        str(value)
        for value in (
            getattr(exc, "filename", None),
            getattr(exc, "filename2", None),
            exc,
        )
        if value
    )


def is_flet_client_cache_error(exc: BaseException) -> bool:
    if not isinstance(exc, (FileExistsError, PermissionError)):
        return False

    normalized = _exception_path_text(exc).replace("\\", "/").lower()
    return ".flet/client/flet-desktop-" in normalized


def is_flet_client_cache_race(exc: BaseException) -> bool:
    return is_flet_client_cache_error(exc)


def show_startup_error_message(title: str, message: str) -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            windll = getattr(ctypes, "windll")
            windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}\n{message}", file=sys.stderr)


def _format_flet_client_cache_error(exc: BaseException) -> str:
    path_text = _exception_path_text(exc)
    return (
        "Не вдалося підготувати desktop-runtime лаунчера, бо інший процес або антивірус "
        "заблокував кеш Flet.\n\n"
        f"Помилка: {exc}\n"
        f"Шлях: {path_text}\n\n"
        "Що зробити:\n"
        "1. Дозвольте TensaLauncher.exe у вашому антивірусі.\n"
        "2. Додайте у виключення папку %USERPROFILE%\\.flet\\client.\n"
        "3. Закрийте всі процеси TensaLauncher і flet.\n"
        "4. Видаліть пошкоджену папку flet-desktop-* у %USERPROFILE%\\.flet\\client і запустіть лаунчер знову."
    )


def run_flet_with_client_cache_retries() -> bool:
    for attempt, delay in enumerate((*FLET_CLIENT_CACHE_RETRY_DELAYS, None), start=1):
        try:
            ft.run(main, assets_dir=str(PACKAGE_ASSETS_DIR), view=ft.AppView.FLET_APP_HIDDEN)
            return True
        except (FileExistsError, PermissionError) as exc:
            if not is_flet_client_cache_error(exc):
                raise
            if delay is None:
                Logger.error("Flet desktop client cache is locked after retries:\n" + traceback.format_exc())
                show_startup_error_message(APP_NAME, _format_flet_client_cache_error(exc))
                return False
            Logger.warning(
                "Flet desktop client cache is locked or incomplete; "
                f"retrying startup in {delay:g}s (attempt {attempt})"
            )
            time.sleep(delay)

    return False


def run_packaged_smoke_test() -> int:
    import cryptography
    import minecraft_launcher_lib
    import requests
    import transliterate

    _required_modules = (cryptography, minecraft_launcher_lib, requests, transliterate)
    if not all(getattr(module, "__file__", None) for module in _required_modules):
        raise RuntimeError("Smoke test failed to load packaged runtime dependencies")

    util.init()
    paths = LauncherPaths.detect()
    theme = ui.UiTheme.build()

    required_paths = [
        PACKAGE_ASSETS_DIR,
        paths.app_dir,
        paths.app_state_dir,
        paths.minecraft_dir,
    ]
    for path in required_paths:
        if not Path(path).exists():
            raise FileNotFoundError(f"Smoke test required path is missing: {path}")

    updater_assets = (
        PACKAGE_ASSETS_DIR / "updater" / "windows_update.bat",
        PACKAGE_ASSETS_DIR / "updater" / "linux_update.sh",
        PACKAGE_ASSETS_DIR / "updater" / "macos_update.sh",
    )
    for asset in updater_assets:
        if not asset.exists():
            raise FileNotFoundError(f"Smoke test missing updater asset: {asset}")

    if is_frozen():
        artifact = flet_desktop.get_artifact_filename()
        runtime_archive = Path(flet_desktop.get_package_bin_dir()) / artifact
        if not runtime_archive.is_file():
            raise FileNotFoundError(f"Smoke test missing bundled Flet runtime: {runtime_archive}")
        if runtime_archive.stat().st_size <= 0:
            raise RuntimeError(f"Smoke test found empty bundled Flet runtime: {runtime_archive}")

    if theme.flet_theme is None:
        raise RuntimeError("Smoke test failed to build UI theme")

    print("smoke-ok", flush=True)
    return 0


def main(page: "ft.Page") -> None:
    if util.check_connection():
        Logger.info("Internet connection available, starting app")
    else:
        Logger.warning("Startup internet check failed, starting app anyway")

    app = App(page)
    app.run()


def launch(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prepare_process_workdir()
    setup_logging()
    if os.environ.get("TENSALAUNCHER_CLEAR_LOG_ON_START") == "1":
        Logger.clear()
    normalize_linux_frozen_runtime_env()

    if "--smoke-test" in argv:
        return run_packaged_smoke_test()

    if resume_pending_update_if_needed(Logger):
        return 0

    try:
        if not run_flet_with_client_cache_retries():
            return 1
    except Exception:
        Logger.error("Fatal startup error:\n" + traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
