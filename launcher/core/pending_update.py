from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from launcher.platform.paths import PathPolicy
from launcher.storage.atomic import atomic_write_json

WINDOWS_CREATE_NEW_CONSOLE = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
PENDING_UPDATE_MARKER = "tensalauncher_pending_update.json"
LEGACY_PENDING_UPDATE_MARKER = "tcl_pending_update.json"
PENDING_UPDATE_SCHEMA = 2
PENDING_UPDATE_STAGING_DIR = "pending-update"

_SUPPORTED_PLATFORMS = frozenset({"windows", "linux", "macos"})
_UPDATER_SCRIPT_NAMES = {
    "windows": "tensalauncher_update.bat",
    "linux": "tensalauncher_update.sh",
    "macos": "tensalauncher_update.sh",
}
_UPDATER_ASSET_NAMES = {
    "windows": "windows_update.bat",
    "linux": "linux_update.sh",
    "macos": "macos_update.sh",
}
_UPDATE_SOURCE_NAMES = {
    "windows": "tensalauncher_update_payload.exe",
    "linux": "tensalauncher_update_payload.AppImage",
    "macos": "tensalauncher_update_payload.dmg",
}
_SCHEMA_1_KEYS = frozenset(
    {
        "schema",
        "platform",
        "command",
        "updater_script",
        "source",
        "target",
        "created_at",
    }
)
_SCHEMA_2_KEYS = frozenset(
    {
        "schema",
        "platform",
        "updater_script",
        "source",
        "target",
        "source_sha256",
        "updater_sha256",
        "created_at",
    }
)
_UPDATER_ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets" / "updater"


@dataclass(frozen=True, slots=True)
class PendingUpdate:
    platform: str
    updater_script: Path
    source: Path
    target: Path

    def argv(self, marker_path: Path) -> list[str]:
        args = [
            str(self.updater_script),
            str(self.source),
            str(self.target),
            str(os.getpid()),
            str(marker_path),
        ]
        return args


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))


def _prepare_private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"Pending update staging path is not a private directory: {path}")

    if os.name != "nt":
        owner_id = getattr(os, "geteuid", lambda: None)()
        if owner_id is not None and path.stat().st_uid != owner_id:
            raise PermissionError(f"Pending update staging directory is owned by another user: {path}")
        path.chmod(0o700)
    return path


def _pending_update_root(temp_dir: Path | None = None) -> Path:
    system_temp = Path(tempfile.gettempdir())
    if temp_dir is None or _same_path(Path(temp_dir), system_temp):
        root = PathPolicy.default_cache_dir() / PENDING_UPDATE_STAGING_DIR
    else:
        root = Path(temp_dir)
    return _prepare_private_directory(root)


def pending_update_marker_path(temp_dir: Path | None = None) -> Path:
    return _pending_update_root(temp_dir) / PENDING_UPDATE_MARKER


def pending_update_marker_paths(temp_dir: Path | None = None) -> list[Path]:
    root = _pending_update_root(temp_dir)
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


def _absolute_path(value: Path | str, *, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    try:
        return path.resolve()
    except OSError as exc:
        raise ValueError(f"{name} cannot be resolved: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_staged_file(source: Path, destination: Path, *, executable: bool = False) -> Path:
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Pending update file is missing or invalid: {source}")
    if _same_path(source, destination):
        staged = destination
    else:
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        staged = destination

    if staged.is_symlink() or not staged.is_file():
        raise ValueError(f"Pending update staging failed: {staged}")
    if executable and os.name != "nt":
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR)
    return staged


def _macos_app_for_executable(executable: Path) -> Path | None:
    for candidate in (executable, *executable.parents):
        if candidate.suffix.lower() == ".app":
            return candidate
    return None


def _expected_update_target(platform_name: str) -> Path | None:
    executable = _absolute_path(sys.executable, name="launcher executable")
    if platform_name == "windows":
        return executable
    if platform_name == "linux":
        appimage = os.environ.get("APPIMAGE")
        return _absolute_path(appimage, name="APPIMAGE") if appimage else executable
    if platform_name == "macos":
        return _macos_app_for_executable(executable)
    return None


def _validate_target(target: Path, platform_name: str) -> None:
    expected = _expected_update_target(platform_name)
    if expected is None or not _same_path(target, expected):
        raise ValueError(f"target is outside the current launcher installation: {target}")
    if platform_name != "windows" and not target.exists():
        raise ValueError(f"target does not exist: {target}")


def write_pending_update_marker(
    *,
    temp_dir: Path | None = None,
    platform_name: str,
    command: Path | str,
    updater_script: Path | str,
    source: Path | str,
    target: Path | str,
) -> Path:
    del command  # Schema 2 deliberately does not persist or execute caller-provided commands.

    normalized_platform = _normalize_platform(platform_name)
    if normalized_platform not in _SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported pending update platform: {platform_name}")

    source_path = _absolute_path(source, name="source")
    updater_path = _absolute_path(updater_script, name="updater_script")
    target_path = _absolute_path(target, name="target")
    _validate_target(target_path, normalized_platform)

    root = _pending_update_root(temp_dir)
    staged_source = _copy_staged_file(source_path, root / _UPDATE_SOURCE_NAMES[normalized_platform])
    staged_updater = _copy_staged_file(
        updater_path,
        root / _UPDATER_SCRIPT_NAMES[normalized_platform],
        executable=normalized_platform != "windows",
    )
    marker_path = root / PENDING_UPDATE_MARKER
    atomic_write_json(
        marker_path,
        {
            "schema": PENDING_UPDATE_SCHEMA,
            "platform": normalized_platform,
            "updater_script": str(staged_updater),
            "source": str(staged_source),
            "target": str(target_path),
            "source_sha256": _sha256(staged_source),
            "updater_sha256": _sha256(staged_updater),
            "created_at": time.time(),
        },
        ensure_ascii=True,
        indent=2,
    )
    with suppress(OSError):
        marker_path.chmod(0o600)
    (root / LEGACY_PENDING_UPDATE_MARKER).unlink(missing_ok=True)
    return marker_path


def clear_pending_update_marker(temp_dir: Path | None = None) -> None:
    for marker_path in pending_update_marker_paths(temp_dir):
        marker_path.unlink(missing_ok=True)


def _load_pending_update_marker(marker_path: Path, logger: Any) -> dict[str, Any] | None:
    if marker_path.is_symlink():
        logger.warning("Removing symlinked pending update marker")
        marker_path.unlink(missing_ok=True)
        return None
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


def _marker_path(data: dict[str, Any], key: str) -> Path:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return _absolute_path(value, name=key)


def _validate_digest(data: dict[str, Any], key: str, path: Path) -> None:
    expected = data.get(key)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"{key} must be a SHA-256 digest")
    try:
        int(expected, 16)
    except ValueError as exc:
        raise ValueError(f"{key} must be a SHA-256 digest") from exc
    if _sha256(path) != expected.lower():
        raise ValueError(f"{key} does not match {path.name}")


def _validate_created_at(data: dict[str, Any]) -> None:
    created_at = data.get("created_at")
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
        raise ValueError("created_at must be a timestamp")
    if not math.isfinite(float(created_at)) or float(created_at) <= 0:
        raise ValueError("created_at must be a timestamp")


def _validate_staged_file(path: Path, expected: Path, root: Path, *, key: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{key} is missing or invalid: {path}")
    if path.parent != root or path.name != expected.name:
        raise ValueError(f"{key} is outside the private update staging directory: {path}")


def _validate_trusted_updater(platform_name: str, updater_script: Path) -> None:
    trusted_asset = _UPDATER_ASSET_ROOT / _UPDATER_ASSET_NAMES[platform_name]
    if not trusted_asset.is_file() or _sha256(updater_script) != _sha256(trusted_asset):
        raise ValueError("updater_script does not match the packaged updater")


def _parse_pending_update(data: dict[str, Any], marker_path: Path, current_platform: str) -> PendingUpdate:
    schema = data.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema not in (1, PENDING_UPDATE_SCHEMA):
        raise ValueError(f"unsupported marker schema: {schema}")

    allowed_keys = _SCHEMA_1_KEYS if schema == 1 else _SCHEMA_2_KEYS
    unknown_keys = set(data) - allowed_keys
    if unknown_keys:
        raise ValueError(f"unsupported marker fields: {', '.join(sorted(unknown_keys))}")

    marker_platform_value = data.get("platform")
    if not isinstance(marker_platform_value, str):
        raise ValueError("platform must be a string")
    marker_platform = _normalize_platform(marker_platform_value)
    if marker_platform not in _SUPPORTED_PLATFORMS or marker_platform != current_platform:
        raise ValueError(f"marker platform does not match this launcher: {marker_platform}")

    root = marker_path.parent.resolve()
    source = _marker_path(data, "source")
    updater_script = _marker_path(data, "updater_script")
    target = _marker_path(data, "target")
    expected_source = root / _UPDATE_SOURCE_NAMES[marker_platform]
    expected_updater = root / _UPDATER_SCRIPT_NAMES[marker_platform]

    if schema == 1:
        if source.parent != root:
            raise ValueError(f"source is outside the private update staging directory: {source}")
        _validate_staged_file(updater_script, expected_updater, root, key="updater_script")
    else:
        _validate_created_at(data)
        _validate_staged_file(source, expected_source, root, key="source")
        _validate_staged_file(updater_script, expected_updater, root, key="updater_script")
        _validate_digest(data, "source_sha256", source)
        _validate_digest(data, "updater_sha256", updater_script)

    if source.is_symlink() or not source.is_file():
        raise ValueError(f"source is missing or invalid: {source}")
    _validate_trusted_updater(marker_platform, updater_script)
    _validate_target(target, marker_platform)
    return PendingUpdate(
        platform=marker_platform,
        updater_script=updater_script,
        source=source,
        target=target,
    )


def resume_pending_update_if_needed(
    logger: Any,
    *,
    temp_dir: Path | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
) -> bool:
    try:
        marker_path = next((path for path in pending_update_marker_paths(temp_dir) if path.exists()), None)
    except OSError as exc:
        logger.warning(f"Pending update storage is unavailable; continuing startup: {exc}")
        return False
    if marker_path is None:
        return False

    data = _load_pending_update_marker(marker_path, logger)
    if data is None:
        return False

    current_platform = _normalize_platform(platform_name or _detect_platform())
    try:
        pending_update = _parse_pending_update(data, marker_path, current_platform)
    except (OSError, ValueError) as exc:
        logger.warning(f"Removing invalid pending update marker: {exc}")
        marker_path.unlink(missing_ok=True)
        return False

    logger.info("Pending launcher update found, resuming updater and exiting")
    try:
        popen(
            pending_update.argv(marker_path),
            shell=False,
            **(
                {"creationflags": WINDOWS_CREATE_NEW_CONSOLE}
                if current_platform == "windows"
                else {"start_new_session": True}
            ),
        )
    except (OSError, ValueError) as exc:
        logger.warning(f"Unable to start pending updater; continuing startup: {exc}")
        marker_path.unlink(missing_ok=True)
        return False
    return True
