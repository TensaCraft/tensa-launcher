from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

WINDOWS_CREATE_NEW_CONSOLE = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
PENDING_UPDATE_MARKER = "tensalauncher_pending_update.json"
LEGACY_PENDING_UPDATE_MARKER = "tcl_pending_update.json"
WINDOWS_RESUME_UPDATE_SCRIPT = "tensalauncher_resume_update.bat"


def pending_update_marker_path(temp_dir: Path | None = None) -> Path:
    return (Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir())) / PENDING_UPDATE_MARKER


def pending_update_marker_paths(temp_dir: Path | None = None) -> list[Path]:
    root = Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir())
    return [
        root / PENDING_UPDATE_MARKER,
        root / LEGACY_PENDING_UPDATE_MARKER,
    ]


def _detect_platform() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    return "unknown"


def _normalize_platform(value: str | None) -> str:
    platform_name = str(value or "").strip().lower()
    aliases = {
        "darwin": "macos",
        "macosx": "macos",
        "osx": "macos",
    }
    return aliases.get(platform_name, platform_name)


def write_pending_update_marker(
    *,
    temp_dir: Path | None = None,
    platform_name: str,
    command: Path | str,
    updater_script: Path | str,
    source: Path | str,
    target: Path | str,
) -> Path:
    marker_path = pending_update_marker_path(temp_dir)
    marker_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": platform_name,
                "command": str(command),
                "updater_script": str(updater_script),
                "source": str(source),
                "target": str(target),
                "created_at": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return marker_path


def clear_pending_update_marker(temp_dir: Path | None = None) -> None:
    for marker_path in pending_update_marker_paths(temp_dir):
        marker_path.unlink(missing_ok=True)


def _load_pending_update_marker(marker_path: Path, logger: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Removing unreadable pending update marker: {exc}")
        marker_path.unlink(missing_ok=True)
        return None

    if not isinstance(data, dict):
        logger.warning("Removing invalid pending update marker")
        marker_path.unlink(missing_ok=True)
        return None
    return data


def _required_path(data: dict[str, Any], key: str) -> Path | None:
    value = str(data.get(key) or "").strip()
    return Path(value) if value else None


def _marker_has_required_files(
    data: dict[str, Any],
    marker_path: Path,
    logger: Any,
    *,
    current_platform: str,
) -> bool:
    required = ("source", "updater_script")
    for key in required:
        path = _required_path(data, key)
        if path is None or not path.exists():
            logger.warning(f"Removing stale pending update marker; missing {key}: {path}")
            marker_path.unlink(missing_ok=True)
            return False

    target = _required_path(data, "target")
    if target is None:
        logger.warning("Removing stale pending update marker; missing target path")
        marker_path.unlink(missing_ok=True)
        return False

    if current_platform != "windows" and not target.exists():
        logger.warning(f"Removing stale pending update marker; missing target: {target}")
        marker_path.unlink(missing_ok=True)
        return False

    if current_platform == "windows" and not target.exists():
        logger.warning(f"Pending launcher update target is missing, attempting restore: {target}")
    return True


def _write_windows_resume_script(data: dict[str, Any], marker_path: Path, temp_dir: Path) -> Path:
    updater_script = _required_path(data, "updater_script")
    source = _required_path(data, "source")
    target = _required_path(data, "target")
    if updater_script is None or source is None or target is None:
        raise ValueError("pending update marker is missing required paths")

    resume_script = temp_dir / WINDOWS_RESUME_UPDATE_SCRIPT
    resume_script.write_text(
        "@echo off\n"
        f'call "{updater_script}" "{source}" "{target}" {os.getpid()} "{marker_path}"\n',
        encoding="ascii",
    )
    return resume_script


def resume_pending_update_if_needed(
    logger: Any,
    *,
    temp_dir: Path | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
) -> bool:
    marker_path = next((path for path in pending_update_marker_paths(temp_dir) if path.exists()), None)
    if marker_path is None:
        return False

    data = _load_pending_update_marker(marker_path, logger)
    if data is None:
        return False

    current_platform = _normalize_platform(platform_name or _detect_platform())
    marker_platform = _normalize_platform(str(data.get("platform") or ""))
    if marker_platform and marker_platform != current_platform:
        logger.warning(f"Removing pending update marker for another platform: {marker_platform}")
        marker_path.unlink(missing_ok=True)
        return False

    if not _marker_has_required_files(data, marker_path, logger, current_platform=current_platform):
        return False

    temp_root = Path(temp_dir) if temp_dir is not None else marker_path.parent
    logger.info("Pending launcher update found, resuming updater and exiting")

    if current_platform == "windows":
        resume_script = _write_windows_resume_script(data, marker_path, temp_root)
        popen([str(resume_script)], shell=True, creationflags=WINDOWS_CREATE_NEW_CONSOLE)
        return True

    command = _required_path(data, "command")
    if command is None or not command.exists():
        logger.warning(f"Removing stale pending update marker; missing command: {command}")
        marker_path.unlink(missing_ok=True)
        return False

    popen(str(command), shell=True, start_new_session=True)
    return True
