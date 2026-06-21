#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_PYTHON_MAJOR = 3
PROJECT_PYTHON_MINOR = 13
PROJECT_PYTHON_LABEL = f"{PROJECT_PYTHON_MAJOR}.{PROJECT_PYTHON_MINOR}"
REEXEC_ENV = "TENSALAUNCHER_PROJECT_PYTHON_REEXEC"
LEGACY_REEXEC_ENV = "TCL_PROJECT_PYTHON_REEXEC"
OVERRIDE_ENV = "TENSALAUNCHER_PYTHON_BIN"
LEGACY_OVERRIDE_ENV = "TCL_PYTHON_BIN"


def current_python_version() -> tuple[int, int]:
    return sys.version_info.major, sys.version_info.minor


def is_current_project_python() -> bool:
    return current_python_version() == (PROJECT_PYTHON_MAJOR, PROJECT_PYTHON_MINOR)


def resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate)

    resolved = shutil.which(value)
    if resolved:
        return resolved

    raise FileNotFoundError(f"Python interpreter not found: {value}")


def python_version(python_bin: str) -> tuple[int, int]:
    completed = subprocess.run(
        [
            python_bin,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    major, minor = completed.stdout.strip().split(".", 1)
    return int(major), int(minor)


def _resolve_from_py_launcher() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    if not shutil.which("py"):
        return None

    completed = subprocess.run(
        [
            "py",
            f"-{PROJECT_PYTHON_LABEL}",
            "-c",
            "import sys; print(sys.executable)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    resolved = completed.stdout.strip()
    return resolved or None


def candidate_project_python_bins() -> list[str]:
    candidates: list[str] = []

    override = os.environ.get(OVERRIDE_ENV) or os.environ.get(LEGACY_OVERRIDE_ENV)
    if override:
        candidates.append(override)

    launcher_candidate = _resolve_from_py_launcher()
    if launcher_candidate:
        candidates.append(launcher_candidate)

    candidates.extend(
        [
            f"python{PROJECT_PYTHON_LABEL}",
            f"python{PROJECT_PYTHON_MAJOR}{PROJECT_PYTHON_MINOR}",
        ]
    )

    seen: set[str] = set()
    resolved_candidates: list[str] = []
    for candidate in candidates:
        try:
            resolved = resolve_executable(candidate)
        except FileNotFoundError:
            continue
        normalized = str(Path(resolved).resolve()).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved_candidates.append(resolved)
    return resolved_candidates


def resolve_project_python() -> str:
    current = resolve_executable(sys.executable)
    if is_current_project_python():
        return current

    for candidate in candidate_project_python_bins():
        try:
            if python_version(candidate) == (PROJECT_PYTHON_MAJOR, PROJECT_PYTHON_MINOR):
                return candidate
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue

    raise RuntimeError(
        f"TensaLauncher developer tools require Python {PROJECT_PYTHON_LABEL}. "
        f"Current interpreter is Python {current_python_version()[0]}.{current_python_version()[1]}."
    )


def reexec_if_needed() -> None:
    if is_current_project_python():
        return
    if os.environ.get(REEXEC_ENV) == "1" or os.environ.get(LEGACY_REEXEC_ENV) == "1":
        raise RuntimeError(
            f"TensaLauncher developer tools require Python {PROJECT_PYTHON_LABEL}; "
            f"re-exec ended on Python {current_python_version()[0]}.{current_python_version()[1]}."
        )

    python_bin = resolve_project_python()
    env = os.environ.copy()
    env[REEXEC_ENV] = "1"
    completed = subprocess.run([python_bin, *sys.argv], env=env)
    raise SystemExit(completed.returncode)
