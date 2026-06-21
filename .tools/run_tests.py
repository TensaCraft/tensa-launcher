#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from python_runtime import reexec_if_needed

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    import pytest

    return int(pytest.main(["-q"]))


if __name__ == "__main__":
    reexec_if_needed()
    raise SystemExit(main())
