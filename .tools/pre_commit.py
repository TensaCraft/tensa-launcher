#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from python_runtime import reexec_if_needed

ROOT = Path(__file__).resolve().parent.parent
SKIP_ENV = "TENSALAUNCHER_SKIP_PRECOMMIT"
FULL_CODEQL_ENV = "TENSALAUNCHER_PRECOMMIT_CODEQL"


def run_check(name: str, command: list[str]) -> bool:
    print(f"\n==> {name}", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode == 0:
        return True

    print(f"\n{name} failed with exit code {completed.returncode}.", file=sys.stderr)
    return False


def main() -> int:
    reexec_if_needed()

    if os.environ.get(SKIP_ENV) == "1":
        print(f"Skipping TensaLauncher pre-commit checks because {SKIP_ENV}=1.")
        return 0

    checks: list[tuple[str, list[str]]] = [
        ("CodeQL CLI", [sys.executable, ".tools/run_codeql.py"]),
        ("ruff lint", [sys.executable, ".tools/run_lint.py"]),
        ("pyright type check", [sys.executable, ".tools/run_typecheck.py"]),
        ("Python compile", [sys.executable, ".tools/run_compile.py"]),
        ("pytest", [sys.executable, ".tools/run_tests.py"]),
        ("staged whitespace", ["git", "diff", "--check", "--cached"]),
    ]

    if os.environ.get(FULL_CODEQL_ENV) == "1":
        checks.append(("CodeQL analysis", [sys.executable, ".tools/run_codeql.py", "--analyze"]))

    for name, command in checks:
        if not run_check(name, command):
            return 1

    print("\nTensaLauncher pre-commit checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
