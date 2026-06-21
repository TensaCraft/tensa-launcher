from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import minecraft_launcher_lib

from launcher.models.logger import Logger

DEFAULT_MINECRAFT_INSTALL_ATTEMPTS = 4
DEFAULT_MINECRAFT_INSTALL_RETRY_DELAY = 0.75
JAVA_RUNTIME_FAILURE_CODES = {
    0xC000007B,  # STATUS_INVALID_IMAGE_FORMAT
    0xC0000135,  # STATUS_DLL_NOT_FOUND
    0xC0000139,  # STATUS_ENTRYPOINT_NOT_FOUND
    0xC0000142,  # STATUS_DLL_INIT_FAILED
}


def install_minecraft_version_with_retries(
    version: str,
    minecraft_directory: str | Path,
    *,
    callback: Any | None = None,
    attempts: int = DEFAULT_MINECRAFT_INSTALL_ATTEMPTS,
    retry_delay: float = DEFAULT_MINECRAFT_INSTALL_RETRY_DELAY,
    sleep: Callable[[float], None] | None = time.sleep,
    installer: Callable[..., None] | None = None,
) -> None:
    """Install a Minecraft version with retries for transient network failures."""
    install = installer or minecraft_launcher_lib.install.install_minecraft_version
    max_attempts = max(1, attempts)
    target_path = Path(minecraft_directory)
    target_path.mkdir(parents=True, exist_ok=True)
    target_dir = str(target_path)

    for attempt in range(1, max_attempts + 1):
        try:
            install(version=version, minecraft_directory=target_dir, callback=callback)
            return
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable_install_error(exc):
                raise

            Logger.warning(
                f"Minecraft {version} install attempt {attempt}/{max_attempts} failed; retrying. "
                f"{format_install_error(exc)}"
            )
            if sleep is not None and retry_delay > 0:
                sleep(retry_delay)


def is_retryable_install_error(exc: BaseException) -> bool:
    if isinstance(exc, subprocess.CalledProcessError):
        return True
    if exc.__class__.__name__ == "ExternalProgramError":
        return True

    if isinstance(exc, FileNotFoundError):
        missing_path = str(getattr(exc, "filename", "") or "").lower()
        message = repr(exc).lower()
        return (
            missing_path.endswith((".jar", ".json", ".dll", ".lib"))
            or "/libraries/" in missing_path.replace("\\", "/")
            or "/versions/" in missing_path.replace("\\", "/")
            or "no such file or directory" in message
        )

    retryable_names = {
        "ChunkedEncodingError",
        "ConnectionError",
        "ConnectionResetError",
        "HTTPError",
        "IncompleteRead",
        "ProtocolError",
        "ReadTimeout",
        "SSLError",
        "Timeout",
        "TimeoutError",
    }
    if exc.__class__.__name__ in retryable_names:
        return True

    message = repr(exc).lower()
    return any(
        marker in message
        for marker in (
            "chunkedencodingerror",
            "connection aborted",
            "connection broken",
            "connection reset",
            "eof occurred in violation of protocol",
            "incompleteread",
            "max retries exceeded",
            "read timed out",
            "remote end closed",
            "ssl",
            "temporarily unavailable",
            "timed out",
            "unexpected_eof_while_reading",
        )
    )


def is_java_runtime_process_failure(exc: BaseException) -> bool:
    if isinstance(exc, subprocess.CalledProcessError):
        return_code = _normalise_windows_status_code(exc.returncode)
        if return_code in JAVA_RUNTIME_FAILURE_CODES:
            return True

    message = format_install_error(exc).lower()
    return any(
        marker in message
        for marker in (
            "0xc000007b",
            "0xc0000135",
            "0xc0000139",
            "0xc0000142",
            "dll not found",
            "java.dll",
            "jli.dll",
            "missing dll",
            "msvcp140",
            "status_dll_not_found",
            "unable to start correctly",
            "vcruntime",
            "was not found",
        )
    )


def _normalise_windows_status_code(returncode: int | None) -> int | None:
    if returncode is None:
        return None
    return returncode + (1 << 32) if returncode < 0 else returncode


def format_install_error(exc: BaseException) -> str:
    parts = [repr(exc)]
    command = getattr(exc, "cmd", None) or getattr(exc, "command", None)
    output = decode_error_output(getattr(exc, "output", None) or getattr(exc, "stdout", None))
    stderr = decode_error_output(getattr(exc, "stderr", None))
    if command:
        parts.append(f"command={command}")
    if output:
        parts.append(f"stdout={output}")
    if stderr:
        parts.append(f"stderr={stderr}")
    return " ".join(parts)


def decode_error_output(value: object, max_chars: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    if len(text) > max_chars:
        return "..." + text[-max_chars:]
    return text
