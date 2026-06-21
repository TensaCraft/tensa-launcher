from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from launcher import APP_NAME


PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _find_project_root() -> Path | None:
    candidates = [Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / ".tools").is_dir():
            return candidate
    if (PACKAGE_ROOT / "pyproject.toml").is_file() and (PACKAGE_ROOT / ".tools").is_dir():
        return PACKAGE_ROOT
    return None


def _tools_dir() -> Path:
    root = _find_project_root()
    if root is None:
        raise RuntimeError(
            "Developer command requires the repository root. Run it from the project directory."
        )
    return root / ".tools"


def _run(cmd: list[str]) -> int:
    root = _find_project_root() or Path.cwd()
    completed = subprocess.run(cmd, cwd=root)
    return completed.returncode


def cmd_run(_args: argparse.Namespace) -> int:
    from launcher.main import launch

    launch()
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    tools_dir = _tools_dir()
    return _run([sys.executable, str(tools_dir / "run_tests.py")])


def cmd_compile(_args: argparse.Namespace) -> int:
    tools_dir = _tools_dir()
    return _run([sys.executable, str(tools_dir / "run_compile.py")])


def cmd_clean(args: argparse.Namespace) -> int:
    tools_dir = _tools_dir()
    cmd = [sys.executable, str(tools_dir / "clean.py")]
    if args.all:
        cmd.append("--include-dev-state")
    return _run(cmd)


def cmd_build(args: argparse.Namespace) -> int:
    tools_dir = _tools_dir()
    cmd = [sys.executable, str(tools_dir / "build.py")]

    if args.target:
        cmd.extend(["--target", args.target])
    if args.python_bin:
        cmd.extend(["--python-bin", args.python_bin])
    if args.output_root:
        cmd.extend(["--output-root", args.output_root])
    if args.skip_install:
        cmd.append("--skip-install")
    if args.no_cleanup:
        cmd.append("--no-cleanup")
    if args.linux_format:
        cmd.extend(["--linux-format", args.linux_format])
    if args.skip_dmg:
        cmd.append("--skip-dmg")
    if args.installer:
        cmd.append("--with-windows-installer")
    return _run(cmd)


def cmd_version(_args: argparse.Namespace) -> int:
    from launcher import __version__

    print(__version__)
    return 0


def cmd_release_tag(_args: argparse.Namespace) -> int:
    from launcher import __version__

    print(f"v{__version__}")
    return 0


def cmd_release_title(_args: argparse.Namespace) -> int:
    from launcher import __version__

    print(f"Release v{__version__}")
    return 0


def cmd_release_meta(_args: argparse.Namespace) -> int:
    from launcher import __version__

    print(json.dumps({"version": __version__, "tag": f"v{__version__}", "title": f"Release v{__version__}"}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tl", description=f"{APP_NAME} developer command line.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Launch the desktop app.")
    run_parser.set_defaults(func=cmd_run)

    test_parser = subparsers.add_parser("test", help="Run the test suite.")
    test_parser.set_defaults(func=cmd_test)

    compile_parser = subparsers.add_parser("compile", help="Run compile smoke.")
    compile_parser.set_defaults(func=cmd_compile)

    clean_parser = subparsers.add_parser("clean", help="Remove generated artifacts.")
    clean_parser.add_argument("--all", action="store_true", help="Also remove the current .dev state.")
    clean_parser.set_defaults(func=cmd_clean)

    build_parser = subparsers.add_parser("build", help="Build artifacts for a target platform.")
    build_parser.add_argument("--target", choices=["windows", "linux", "macos"])
    build_parser.add_argument("--python-bin")
    build_parser.add_argument("--output-root")
    build_parser.add_argument("--skip-install", action="store_true")
    build_parser.add_argument("--no-cleanup", action="store_true")
    build_parser.add_argument("--linux-format", choices=["appimage", "binary"])
    build_parser.add_argument("--skip-dmg", action="store_true")
    build_parser.add_argument("--installer", action="store_true", help="Build Windows installer too.")
    build_parser.set_defaults(func=cmd_build)

    version_parser = subparsers.add_parser("version", help="Print launcher version.")
    version_parser.set_defaults(func=cmd_version)

    release_tag_parser = subparsers.add_parser("release-tag", help="Print release tag.")
    release_tag_parser.set_defaults(func=cmd_release_tag)

    release_title_parser = subparsers.add_parser("release-title", help="Print release title.")
    release_title_parser.set_defaults(func=cmd_release_title)

    release_meta_parser = subparsers.add_parser("release-meta", help="Print release metadata JSON.")
    release_meta_parser.set_defaults(func=cmd_release_meta)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
