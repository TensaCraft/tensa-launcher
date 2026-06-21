#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TENSALAUNCHER_CLEAR_LOG_ON_START", "1")

from launcher.main import launch


if __name__ == "__main__":
    launch()
