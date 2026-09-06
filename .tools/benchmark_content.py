#!/usr/bin/env python3
"""Measure local content scans using temporary synthetic installations."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from statistics import median
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launcher.application.installed_components import InstalledComponentsService  # noqa: E402
from launcher.application.version_content import VersionContentService  # noqa: E402


def measure(callback, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000)
    return round(median(samples), 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mods", type=int, default=200)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tensa-benchmark-") as directory:
        root = Path(directory)
        mods = root / "mods"
        mods.mkdir()
        for index in range(args.mods):
            with zipfile.ZipFile(mods / f"mod-{index}.jar", "w") as archive:
                archive.writestr("fabric.mod.json", json.dumps({
                    "id": f"mod_{index}", "name": f"Mod {index}", "version": "1.0",
                }))
                for entry in range(100):
                    archive.writestr(f"assets/{entry}.bin", b"x" * 64)
        for index in range(20):
            component = root / "versions" / f"1.21.{index}"
            component.mkdir(parents=True)
            (component / f"{component.name}.json").write_text(json.dumps({"id": component.name}))
            for entry in range(50):
                (component / f"{entry}.bin").write_bytes(b"x" * 64)
        service = VersionContentService(root, SimpleNamespace(debug=lambda *_: None))
        components = InstalledComponentsService(root)
        cold = measure(lambda: VersionContentService(root, service.log).scan_installed_mods(mods), 3)
        service.scan_installed_mods(mods)
        print(json.dumps({
            "mods": args.mods,
            "cold_mod_scan_ms": cold,
            "repeat_mod_scan_ms": measure(lambda: service.scan_installed_mods(mods)),
            "component_list_ms": measure(components.list_installed),
            "component_lookup_ms": measure(lambda: components.get_component("1.21.10")),
        }, indent=2))


if __name__ == "__main__":
    main()
