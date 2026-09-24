from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from launcher import APP_NAME
from launcher.platform.instance_shortcuts import _desktop_argument, _desktop_string
from launcher.platform.paths import is_frozen
from launcher.platform.resources import PACKAGE_ASSETS_DIR, PACKAGE_ROOT
from launcher.storage.atomic import atomic_copy_file, atomic_write_text

logger = logging.getLogger("tensa.launcher")


def _command() -> list[str]:
    executable = Path(sys.executable).absolute()
    if is_frozen():
        if sys.platform.startswith("linux") and os.environ.get("APPIMAGE"):
            executable = Path(os.environ["APPIMAGE"]).expanduser().absolute()
        return [str(executable)]
    if sys.platform == "win32" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    # Taskbar pins have no working directory; source runs must also be relocatable.
    return [str(executable), "-c", (
        f"import runpy, sys; sys.path.insert(0, {str(PACKAGE_ROOT.parent)!r}); "
        "runpy.run_module('launcher.main', run_name='__main__')"
    )]


def _register_linux_app(app_id: str, command: list[str]) -> None:
    configured = Path(os.environ.get("XDG_DATA_HOME", ""))
    data_home = configured if configured.is_absolute() else Path.home() / ".local" / "share"
    source_icon = PACKAGE_ASSETS_DIR / "logo.png"
    icon = data_home / app_id / "logo.png"
    entry = data_home / "applications" / f"{app_id}.desktop"
    for value in (*command, str(icon)):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Desktop paths cannot contain control characters")
    content = (
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        f"Name={APP_NAME}\n"
        f"Exec={' '.join(_desktop_argument(value) for value in command)}\n"
        f"Icon={_desktop_string(str(icon))}\n"
        f"StartupWMClass={app_id}\n"
        "Terminal=false\nCategories=Game;\nStartupNotify=true\n"
        "X-TensaLauncher-Managed=true\n"
    )
    if not is_frozen():
        content += "NoDisplay=true\n"
    existing = entry.read_text(encoding="utf-8") if entry.is_file() else None
    # An entry installed by a package manager or the user owns its configuration.
    if existing is not None and "X-TensaLauncher-Managed=true\n" not in existing:
        return
    if not icon.is_file() or icon.read_bytes() != source_icon.read_bytes():
        atomic_copy_file(source_icon, icon)
    if existing != content:
        atomic_write_text(entry, content)


def configure_desktop_identity() -> None:
    app_id = APP_NAME if is_frozen() else f"{APP_NAME}-dev"
    if sys.platform == "win32":
        icon = Path(sys.executable).absolute() if is_frozen() else PACKAGE_ASSETS_DIR / "logo.ico"
        # Refresh inherited values: an updater can restart us from a different EXE.
        os.environ.update({
            "FLET_APP_USER_MODEL_ID": app_id,
            "FLET_APP_RELAUNCH_COMMAND": subprocess.list2cmdline(_command()),
            "FLET_APP_RELAUNCH_DISPLAY_NAME": APP_NAME,
            "FLET_APP_RELAUNCH_ICON": f"{icon},0",
        })
    elif sys.platform.startswith("linux"):
        os.environ["FLET_APP_ID"] = app_id
        try:
            _register_linux_app(app_id, _command())
        except (OSError, ValueError):
            logger.warning("Unable to register launcher desktop identity", exc_info=True)
