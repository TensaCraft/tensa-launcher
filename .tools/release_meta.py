#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INIT_FILE = ROOT / "launcher" / "__init__.py"


def read_version() -> str:
    spec = importlib.util.spec_from_file_location("_launcher_release_meta", INIT_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load version from {INIT_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = str(getattr(module, "__version__", "")).strip()
    if not version:
        raise RuntimeError("Launcher version is empty")
    return version


def metadata() -> dict[str, str]:
    version = read_version()
    return {
        "version": version,
        "tag": f"v{version}",
        "title": f"Release v{version}",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve release metadata from launcher.__version__.")
    parser.add_argument("--field", choices=["version", "tag", "title", "json"], default="json")
    parser.add_argument("--github-env", action="store_true", help="Print VERSION/TAG/NAME entries for GITHUB_ENV.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = metadata()

    if args.github_env:
        print(f"VERSION={data['version']}")
        print(f"TAG={data['tag']}")
        print(f"NAME={data['title']}")
        return 0

    field = args.field
    if field == "json":
        print(json.dumps(data))
    else:
        print(data[field])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
