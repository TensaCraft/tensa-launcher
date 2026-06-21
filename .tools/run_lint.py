#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from python_runtime import reexec_if_needed

ROOT = Path(__file__).resolve().parent.parent
LINT_TARGETS = [
    ".tools/build.py",
    ".tools/build_linux.py",
    ".tools/build_macos.py",
    ".tools/build_windows.py",
    ".tools/flet_client.py",
    ".tools/release_notes.py",
    ".tools/python_runtime.py",
    ".tools/run_tests.py",
    ".tools/run_lint.py",
    ".tools/run_typecheck.py",
    ".tools/smoke_packaged.py",
    ".tools/validate_commit_message.py",
    "launcher/application/setup_wizard.py",
    "launcher/core/async_downloader.py",
    "launcher/core/updater.py",
    "launcher/main.py",
    "launcher/platform/paths.py",
    "launcher/platform/system.py",
    "launcher/pages/mods_manager.py",
    "launcher/pages/mods_manager_installed.py",
    "launcher/pages/mods_manager_resourcepacks.py",
    "launcher/pages/mods_manager_search.py",
    "launcher/pages/setup_wizard.py",
    "launcher/ui/patterns/version_card.py",
    "tests/conftest.py",
    "tests/test_async_downloader.py",
    "tests/test_build_tool.py",
    "tests/test_commit_message.py",
    "tests/test_release_notes.py",
    "tests/test_setup_wizard.py",
    "tests/test_storage_paths.py",
    "tests/test_updater.py",
    "tests/test_version_card.py",
    "tests/tools_import.py",
]


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *LINT_TARGETS],
        cwd=ROOT,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    reexec_if_needed()
    raise SystemExit(main())
