#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REMOVABLE_PATHS = (
    "build",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    "minecraft",
    "config.json",
    "profiles.json",
    "versions.json",
    ".tensalauncher-paths.json",
    "app.log",
)
EXCLUDED_WALK_ROOTS = {
    ROOT / ".git",
    ROOT / ".venv",
    ROOT / ".dev",
    ROOT / ".build",
    ROOT / ".idea",
    ROOT / ".codex",
}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    print(f"[clean] removed {display_path(path)}", flush=True)


def remove_generated_build_path(path: Path) -> None:
    if path.is_dir():
        for child in list(path.iterdir()):
            remove_generated_build_path(child)
        try:
            path.rmdir()
        except OSError:
            return
    else:
        path.unlink(missing_ok=True)
    print(f"[clean] removed {display_path(path)}", flush=True)


def remove_build_root(path: Path) -> None:
    if not path.exists():
        return
    for child in list(path.iterdir()):
        remove_generated_build_path(child)
    try:
        path.rmdir()
    except OSError:
        return
    print(f"[clean] removed {display_path(path)}", flush=True)


def remove_pycache(root: Path) -> None:
    for current_root, dirnames, _ in os.walk(root):
        current_path = Path(current_root)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if current_path / dirname not in EXCLUDED_WALK_ROOTS
        ]
        if current_path.name != "__pycache__":
            continue
        shutil.rmtree(current_path, ignore_errors=True)
        print(f"[clean] removed {display_path(current_path)}", flush=True)


def remove_egg_info(root: Path) -> None:
    for current_root, dirnames, _ in os.walk(root):
        current_path = Path(current_root)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if current_path / dirname not in EXCLUDED_WALK_ROOTS
        ]
        if not current_path.name.endswith(".egg-info"):
            continue
        shutil.rmtree(current_path, ignore_errors=True)
        print(f"[clean] removed {display_path(current_path)}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove generated artifacts and legacy repo-root runtime leftovers.")
    parser.add_argument(
        "--include-dev-state",
        action="store_true",
        help="Also remove the current .dev runtime directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    remove_build_root(ROOT / ".build")

    for relative_path in REMOVABLE_PATHS:
        remove_path(ROOT / relative_path)

    if args.include_dev_state:
        remove_path(ROOT / ".dev")

    remove_pycache(ROOT)
    remove_egg_info(ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
