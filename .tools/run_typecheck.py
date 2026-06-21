#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from python_runtime import reexec_if_needed

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", "pyrightconfig.json"],
        cwd=ROOT,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    reexec_if_needed()
    raise SystemExit(main())
