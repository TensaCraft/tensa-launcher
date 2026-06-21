#!/usr/bin/env python3
from __future__ import annotations

import compileall
from pathlib import Path

from python_runtime import reexec_if_needed


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "launcher"
TESTS = ROOT / "tests"
TOOLS = ROOT / ".tools"


def main() -> int:
    ok = compileall.compile_dir(SRC, quiet=0)
    ok = compileall.compile_dir(TESTS, quiet=0) and ok
    ok = compileall.compile_dir(TOOLS, quiet=0) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    reexec_if_needed()
    raise SystemExit(main())
