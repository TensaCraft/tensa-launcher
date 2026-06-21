#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.util
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import flet_client  # noqa: E402
from icon_assets import resolve_pack_icon  # noqa: E402
from python_runtime import (  # noqa: E402
    PROJECT_PYTHON_LABEL,
    PROJECT_PYTHON_MAJOR,
    PROJECT_PYTHON_MINOR,
    candidate_project_python_bins,
    python_version,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
PACKAGE_DIR = ROOT_DIR / "launcher"
PACK_ENTRYPOINT = PACKAGE_DIR / "main.py"
ASSETS_DIR = PACKAGE_DIR / "assets"
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
PYPROJECT_FILE = ROOT_DIR / "pyproject.toml"
FLET_RELEASE_BASE_URL = flet_client.FLET_RELEASE_BASE_URL
DEFAULT_APP_NAME = "TensaLauncher"
DEFAULT_COMPANY_NAME = "TensaCraft"
DEFAULT_INSTALLER_NAME = "TensaLauncherInstaller"


class BuildError(RuntimeError):
    """Raised for recoverable build workflow errors."""


def _quote(part: str) -> str:
    if " " in part or "\t" in part:
        return f'"{part}"'
    return part


@dataclass
class BuildContext:
    root_dir: Path
    assets_dir: Path
    dist_dir: Path
    build_dir: Path
    pyproject_file: Path
    output_root: Path
    target_output_dir: Path
    python_bin: str
    target: str
    app_name: str
    product_name: str
    company_name: str
    executable_name: str
    installer_name: str

    def log(self, message: str) -> None:
        print(f"[build] {message}", flush=True)

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.log(" ".join(_quote(part) for part in cmd))
        return subprocess.run(
            cmd,
            cwd=cwd or self.root_dir,
            text=True,
            check=check,
            capture_output=capture_output,
            env=env,
        )

    @staticmethod
    def error(message: str) -> BuildError:
        return BuildError(message)

    @staticmethod
    def ensure_executable(path: Path) -> None:
        path.chmod(path.stat().st_mode | 0o111)

    def copy_to_target(self, source: Path, *, name: str | None = None) -> Path:
        destination = self.target_output_dir / (name or source.name)
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
            if self.target == "linux":
                try:
                    self.ensure_executable(destination)
                except OSError:
                    return destination
        return destination


def resolve_target(value: str | None) -> str:
    if value:
        return value
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def ensure_python(python_bin: str) -> str:
    if Path(python_bin).is_file():
        return python_bin

    resolved = shutil.which(python_bin)
    if not resolved:
        raise BuildError(f"Python interpreter not found: {python_bin}")
    return resolved


def resolve_build_python(python_bin: str | None) -> str:
    expected = (PROJECT_PYTHON_MAJOR, PROJECT_PYTHON_MINOR)
    if python_bin:
        resolved = ensure_python(python_bin)
        try:
            version = python_version(resolved)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            raise BuildError(f"Unable to inspect Python interpreter '{resolved}': {exc}") from exc
        if version != expected:
            raise BuildError(
                f"Builds must run on Python {PROJECT_PYTHON_LABEL}; "
                f"'{resolved}' is Python {version[0]}.{version[1]}."
            )
        return resolved

    candidates = [sys.executable, *candidate_project_python_bins()]
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = ensure_python(candidate)
        except BuildError:
            continue
        normalized = str(Path(resolved).resolve()).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            if python_version(resolved) == expected:
                return resolved
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue

    raise BuildError(
        f"Python {PROJECT_PYTHON_LABEL} was not found. "
        f"Install it or pass --python-bin pointing to a Python {PROJECT_PYTHON_LABEL} executable."
    )


def clean_workdirs(ctx: BuildContext) -> None:
    if ctx.dist_dir.exists():
        shutil.rmtree(ctx.dist_dir, ignore_errors=True)
    if ctx.build_dir.exists():
        shutil.rmtree(ctx.build_dir, ignore_errors=True)


def display_path(ctx: BuildContext, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.root_dir))
    except ValueError:
        return str(path)


def reset_target_output_dir(ctx: BuildContext) -> None:
    ctx.target_output_dir.mkdir(parents=True, exist_ok=True)
    for child in ctx.target_output_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def create_workdirs(ctx: BuildContext) -> None:
    ctx.dist_dir.mkdir(parents=True, exist_ok=True)
    ctx.build_dir.mkdir(parents=True, exist_ok=True)


def install_dependencies(ctx: BuildContext) -> None:
    # flet/pyinstaller still rely on pkg_resources in several workflows.
    ctx.run([ctx.python_bin, "-m", "pip", "install", "--upgrade", "pip", "setuptools<81", "wheel"])
    project = read_project_metadata(ctx)
    requirements = [str(req).strip() for req in project.get("dependencies", []) if str(req).strip()]
    requirements.extend(
        str(req).strip()
        for req in project.get("optional-dependencies", {}).get("build", [])
        if str(req).strip()
    )
    if requirements:
        ctx.run([ctx.python_bin, "-m", "pip", "install", *requirements], cwd=ctx.root_dir)
    ctx.run(
        [
            ctx.python_bin,
            "-c",
            "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pkg_resources') else 1)",
        ]
    )


def read_project_metadata(ctx: BuildContext) -> dict:
    with ctx.pyproject_file.open("rb") as fh:
        pyproject = tomllib.load(fh)
    return pyproject.get("project", {})


def resolve_project_version(ctx: BuildContext, project: dict) -> str:
    explicit_version = str(project.get("version", "")).strip()
    if explicit_version:
        return explicit_version

    init_file = PACKAGE_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("_launcher_version", init_file)
    if spec is None or spec.loader is None:
        raise ctx.error(f"Unable to resolve launcher version from {init_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = str(getattr(module, "__version__", "")).strip()
    if not version:
        raise ctx.error("Launcher version is empty")
    return version


def read_package_meta(ctx: BuildContext) -> dict[str, str]:
    init_file = PACKAGE_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("_launcher_meta", init_file)
    if spec is None or spec.loader is None:
        raise ctx.error(f"Unable to resolve launcher metadata from {init_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app_name = str(getattr(module, "APP_NAME", DEFAULT_APP_NAME)).strip() or DEFAULT_APP_NAME
    product_name = str(getattr(module, "PRODUCT_NAME", app_name)).strip() or app_name
    executable_name = str(getattr(module, "EXECUTABLE_NAME", product_name)).strip() or product_name
    return {
        "app_name": app_name,
        "product_name": product_name,
        "company_name": str(getattr(module, "COMPANY_NAME", DEFAULT_COMPANY_NAME)).strip() or DEFAULT_COMPANY_NAME,
        "executable_name": executable_name,
        "installer_name": str(getattr(module, "INSTALLER_NAME", DEFAULT_INSTALLER_NAME)).strip()
        or DEFAULT_INSTALLER_NAME,
    }


def resolve_transliterate_lang_path(ctx: BuildContext) -> Path:
    completed = ctx.run(
        [
            ctx.python_bin,
            "-c",
            (
                "import os, transliterate; "
                "print(os.path.join(os.path.dirname(transliterate.__file__), 'contrib', 'languages'))"
            ),
        ],
        capture_output=True,
    )
    lang_path = Path(completed.stdout.strip())
    if not lang_path.is_dir():
        raise ctx.error(f"Transliterate language data not found: {lang_path}")
    return lang_path


def detect_flet_command(ctx: BuildContext) -> list[str]:
    probes = ("flet.cli", "flet")
    for module_name in probes:
        probe = ctx.run([ctx.python_bin, "-m", module_name, "--help"], check=False, capture_output=True)
        if probe.returncode == 0:
            return [ctx.python_bin, "-m", module_name]

    flet_cli = shutil.which("flet")
    if flet_cli:
        return [flet_cli]

    raise ctx.error("Flet CLI module is unavailable. Install dependencies first.")


def _shared_bundle_options(
    ctx: BuildContext,
    *,
    target: str,
    lang_path: Path,
) -> list[str]:
    data_sep = ";" if target == "windows" else ":"

    icon_path = resolve_pack_icon(ctx, target)

    options = [
        str(PACK_ENTRYPOINT),
        "--name",
        ctx.app_name,
        "--distpath",
        str(ctx.dist_dir),
        "--icon",
        str(icon_path),
        "--add-data",
        f"{ctx.assets_dir}{data_sep}launcher/assets",
        "--add-data",
        f"{lang_path}{data_sep}transliterate/contrib/languages",
    ]
    if target == "macos":
        options.extend(["--hidden-import", "AVFoundation"])
    options.extend(_flet_desktop_bundle_options(ctx, target=target))
    return options


def _resolve_flet_desktop_release(ctx: BuildContext) -> tuple[str, str]:
    return flet_client.resolve_flet_desktop_release(ctx)


def resolve_flet_desktop_client_archive(ctx: BuildContext) -> Path:
    return flet_client.resolve_flet_desktop_client_archive(ctx)


def _flet_desktop_bundle_options(ctx: BuildContext, *, target: str) -> list[str]:
    data_sep = ";" if target == "windows" else ":"
    client_archive = resolve_flet_desktop_client_archive(ctx)
    return ["--add-data", f"{client_archive}{data_sep}flet_desktop/app"]


def _flet_pack_metadata_options(ctx: BuildContext) -> list[str]:
    project = read_project_metadata(ctx)
    product_version = resolve_project_version(ctx, project)
    file_description = str(project.get("description", "TensaLauncher desktop launcher"))
    return [
        "--product-name",
        ctx.product_name,
        "--product-version",
        product_version,
        "--file-description",
        file_description,
        "--company-name",
        ctx.company_name,
        "--copyright",
        "TensaCraft",
    ]


def _resolve_built_artifact_path(ctx: BuildContext, *, target: str) -> Path:
    if target == "windows":
        return ctx.dist_dir / f"{ctx.app_name}.exe"
    if target == "linux":
        return ctx.dist_dir / ctx.app_name
    return ctx.dist_dir / f"{ctx.app_name}.app"


def build_base_artifact_with_flet(
    ctx: BuildContext,
    *,
    target: str,
    lang_path: Path,
) -> Path:
    flet_cmd = detect_flet_command(ctx)
    pack_command = flet_cmd + [
        "pack",
        *_shared_bundle_options(ctx, target=target, lang_path=lang_path),
        *_flet_pack_metadata_options(ctx),
        "-y",
    ]
    ctx.run(pack_command, cwd=ctx.build_dir)

    artifact = _resolve_built_artifact_path(ctx, target=target)
    if not artifact.exists():
        raise ctx.error(f"Built artifact not found: {artifact}")
    return artifact


def build_base_artifact(
    ctx: BuildContext,
    *,
    target: str,
    lang_path: Path,
) -> Path:
    return build_base_artifact_with_flet(ctx, target=target, lang_path=lang_path)


def load_target_builder(target: str) -> Callable[[BuildContext, argparse.Namespace, Path], list[Path]]:
    module_name = {
        "linux": "build_linux",
        "macos": "build_macos",
        "windows": "build_windows",
    }[target]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise BuildError(f"Build module not found for target '{target}': {module_name}") from exc

    builder = getattr(module, "build_target", None)
    if not callable(builder):
        raise BuildError(f"Build module '{module_name}' does not define build_target(ctx, args, base_artifact).")
    return builder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified cross-platform build runner.")
    parser.add_argument("--target", choices=["linux", "macos", "windows"], default=None)
    parser.add_argument(
        "--python-bin",
        default=None,
        help=f"Python interpreter to use. Defaults to the project Python {PROJECT_PYTHON_LABEL}.",
    )
    parser.add_argument("--output-root", default=".build", help="Directory for final artifacts.")
    parser.add_argument("--skip-install", action="store_true", help="Skip dependency installation.")
    parser.add_argument("--no-cleanup", action="store_true", help="Do not remove dist/build after success.")

    # Linux options
    parser.add_argument(
        "--linux-format",
        choices=["appimage", "binary"],
        default="appimage",
        help="Linux output format. Default: appimage.",
    )

    # macOS options
    parser.add_argument("--skip-dmg", action="store_true", help="Skip DMG creation and keep .app bundle output.")

    # Windows options
    parser.add_argument(
        "--with-windows-installer",
        action="store_true",
        help=f"Additionally build {DEFAULT_INSTALLER_NAME}.exe via Inno Setup.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = resolve_target(args.target)
    python_bin = resolve_build_python(args.python_bin)

    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = ROOT_DIR / output_root

    target_output_dir = output_root / target
    bootstrap_ctx = BuildContext(
        root_dir=ROOT_DIR,
        assets_dir=ASSETS_DIR,
        dist_dir=DIST_DIR,
        build_dir=BUILD_DIR,
        pyproject_file=PYPROJECT_FILE,
        output_root=output_root,
        target_output_dir=target_output_dir,
        python_bin=python_bin,
        target=target,
        app_name="",
        product_name="",
        company_name="",
        executable_name="",
        installer_name="",
    )
    reset_target_output_dir(bootstrap_ctx)
    package_meta = read_package_meta(bootstrap_ctx)

    ctx = BuildContext(
        root_dir=ROOT_DIR,
        assets_dir=ASSETS_DIR,
        dist_dir=DIST_DIR,
        build_dir=BUILD_DIR,
        pyproject_file=PYPROJECT_FILE,
        output_root=output_root,
        target_output_dir=target_output_dir,
        python_bin=python_bin,
        target=target,
        app_name=package_meta["app_name"],
        product_name=package_meta["product_name"],
        company_name=package_meta["company_name"],
        executable_name=package_meta["executable_name"],
        installer_name=package_meta["installer_name"],
    )

    try:
        ctx.log(f"Target: {target}")
        ctx.log(f"Python: {python_bin}")

        clean_workdirs(ctx)
        create_workdirs(ctx)

        if not args.skip_install:
            install_dependencies(ctx)

        lang_path = resolve_transliterate_lang_path(ctx)
        base_artifact = build_base_artifact(ctx, target=target, lang_path=lang_path)

        builder = load_target_builder(target)
        final_artifacts = builder(ctx, args, base_artifact)
        if not final_artifacts:
            raise ctx.error("No artifacts were produced by target builder.")

        for artifact in final_artifacts:
            ctx.log(f"Final artifact: {artifact}")

        if not args.no_cleanup:
            clean_workdirs(ctx)
            ctx.log("Cleaned temporary build directories: dist/, build/")

        return 0
    except BuildError as exc:
        ctx.log(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        ctx.log(f"ERROR: command failed with exit code {exc.returncode}")
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
