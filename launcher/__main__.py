from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    PACKAGE_DIR = Path(__file__).resolve().parent
    SRC_DIR = PACKAGE_DIR.parent

    sanitized_path: list[str] = []
    for entry in sys.path:
        try:
            if Path(entry).resolve() == PACKAGE_DIR:
                continue
        except OSError:
            sanitized_path.append(entry)
            continue
        sanitized_path.append(entry)
    sys.path[:] = sanitized_path

    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    shadowed_platform = sys.modules.get("platform")
    if shadowed_platform is not None:
        shadowed_file = getattr(shadowed_platform, "__file__", "") or ""
        if shadowed_file and Path(shadowed_file).resolve().is_relative_to(PACKAGE_DIR):
            sys.modules.pop("platform", None)

    from launcher.main import launch
else:
    from .main import launch


if __name__ == "__main__":
    launch()
