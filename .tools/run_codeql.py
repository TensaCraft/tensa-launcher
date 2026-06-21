#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from python_runtime import PROJECT_PYTHON_LABEL, reexec_if_needed, resolve_project_python

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = ROOT / ".codeql" / "db-python"
DEFAULT_OUTPUT = ROOT / ".codeql" / "results" / "python-security-and-quality.sarif"
DEFAULT_QUERIES = ["codeql/python-queries:codeql-suites/python-security-and-quality.qls"]
CODEQL_OVERRIDE_ENV = "TENSALAUNCHER_CODEQL_BIN"


def _candidate_from_override() -> str | None:
    override = os.environ.get(CODEQL_OVERRIDE_ENV)
    if not override:
        return None

    override_path = Path(override).expanduser()
    if override_path.is_file():
        return str(override_path)

    resolved = shutil.which(override)
    if resolved:
        return resolved

    return None


def _standard_windows_install() -> str | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None

    candidate = Path(local_app_data) / "Programs" / "CodeQL" / "codeql" / "codeql.exe"
    if candidate.is_file():
        return str(candidate)

    return None


def resolve_codeql() -> str:
    candidates = [
        _candidate_from_override(),
        shutil.which("codeql"),
        _standard_windows_install(),
    ]
    for candidate in candidates:
        if candidate:
            return candidate

    raise FileNotFoundError(
        "CodeQL CLI was not found. Install it or set "
        f"{CODEQL_OVERRIDE_ENV} to the full path of codeql/codeql.exe."
    )


def codeql_environment() -> dict[str, str]:
    python_bin = Path(resolve_project_python())
    env = os.environ.copy()
    env["PY_PYTHON"] = PROJECT_PYTHON_LABEL
    env["PY_PYTHON3"] = PROJECT_PYTHON_LABEL
    env["PATH"] = str(python_bin.parent) + os.pathsep + env.get("PATH", "")
    return env


def run_command(command: list[str], env: dict[str, str]) -> int:
    print(" ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env)
    return int(completed.returncode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local CodeQL checks for TensaLauncher.")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Create a Python CodeQL database and analyze it. Without this flag, only verifies the CLI.",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--queries", nargs="*", default=DEFAULT_QUERIES)
    parser.add_argument("--threads", default="0")
    return parser.parse_args()


def main() -> int:
    reexec_if_needed()
    args = parse_args()
    codeql = resolve_codeql()
    env = codeql_environment()

    if not args.analyze:
        return run_command([codeql, "version"], env)

    database = args.database if args.database.is_absolute() else ROOT / args.database
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    create_code = run_command(
        [
            codeql,
            "database",
            "create",
            str(database),
            "--language=python",
            f"--source-root={ROOT}",
            "--overwrite",
            f"--threads={args.threads}",
        ],
        env,
    )
    if create_code != 0:
        return create_code

    return run_command(
        [
            codeql,
            "database",
            "analyze",
            str(database),
            *args.queries,
            "--format=sarif-latest",
            f"--output={output}",
            f"--threads={args.threads}",
        ],
        env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
