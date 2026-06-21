from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Any, Iterator


_PATH_LOCK = threading.RLock()
_JAVA_OPTION_ENV_KEYS = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS")


def java_process_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return an isolated environment for launcher-managed Java processes."""
    env = dict(os.environ if base is None else base)
    for key in _JAVA_OPTION_ENV_KEYS:
        env.pop(key, None)
    return env


def java_subprocess_kwargs(java_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return subprocess kwargs that make a bundled Java runtime self-contained.

    Some Windows environments fail to start Mojang's bundled `java.exe` because
    loader-time DLLs such as `jli.dll` are not found. Running from the Java
    `bin` directory and prepending it to PATH keeps the runtime isolated from
    system Java while allowing Windows to resolve adjacent DLLs reliably.
    """
    java_bin = Path(java_path).parent
    env = java_process_env()
    env["PATH"] = _prepend_paths(_java_path_entries(java_bin), env.get("PATH"))
    env["JAVA_HOME"] = str(java_bin.parent)
    return {"cwd": str(java_bin), "env": env}


@contextmanager
def launcher_java_path(java_path: str | os.PathLike[str] | None) -> Iterator[None]:
    """Temporarily expose a bundled Java bin directory to child processes."""
    if not java_path:
        yield
        return

    java_bin = Path(java_path).parent
    java_home = str(java_bin.parent)
    with _PATH_LOCK:
        old_path = os.environ.get("PATH")
        old_java_home = os.environ.get("JAVA_HOME")
        old_java_options = {key: os.environ.get(key) for key in _JAVA_OPTION_ENV_KEYS}
        os.environ["PATH"] = _prepend_paths(_java_path_entries(java_bin), old_path)
        os.environ["JAVA_HOME"] = java_home
        for key in _JAVA_OPTION_ENV_KEYS:
            os.environ.pop(key, None)
        try:
            yield
        finally:
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
            if old_java_home is None:
                os.environ.pop("JAVA_HOME", None)
            else:
                os.environ["JAVA_HOME"] = old_java_home
            for key, value in old_java_options.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _java_path_entries(java_bin: Path) -> list[str]:
    return [str(java_bin), str(java_bin / "server")]


def _prepend_paths(prefixes: list[str], value: str | None) -> str:
    if not value:
        return os.pathsep.join(prefixes)
    parts = value.split(os.pathsep)
    normalized = {os.path.normcase(part) for part in parts}
    prepend = [prefix for prefix in prefixes if os.path.normcase(prefix) not in normalized]
    if not prepend:
        return value
    return os.pathsep.join([*prepend, value])
