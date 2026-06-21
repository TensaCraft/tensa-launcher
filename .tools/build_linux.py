#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import textwrap
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from icon_assets import resolve_pack_icon  # noqa: E402

APPIMAGE_TOOL_URL = (
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
)


def resolve_appimagetool(ctx) -> Path:
    in_path = shutil.which("appimagetool")
    if in_path:
        return Path(in_path)

    tools_dir = ctx.output_root / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    local_tool = tools_dir / "appimagetool-x86_64.AppImage"
    if not local_tool.exists():
        ctx.log(f"Downloading appimagetool from {APPIMAGE_TOOL_URL}")
        try:
            with urllib.request.urlopen(APPIMAGE_TOOL_URL, timeout=60) as response:
                local_tool.write_bytes(response.read())
        except Exception as exc:
            raise ctx.error(f"Failed to download appimagetool: {exc}") from exc
    ctx.ensure_executable(local_tool)
    return local_tool


def build_appimage(ctx, binary_artifact: Path) -> Path:
    app_dir = ctx.target_output_dir / f"{ctx.app_name}.AppDir"
    if app_dir.exists():
        shutil.rmtree(app_dir)

    app_bin_dir = app_dir / "usr" / "bin"
    app_bin_dir.mkdir(parents=True, exist_ok=True)
    packaged_binary = app_bin_dir / ctx.executable_name
    shutil.copy2(binary_artifact, packaged_binary)
    ctx.ensure_executable(packaged_binary)

    icon_source = resolve_pack_icon(ctx, "linux")

    shutil.copy2(icon_source, app_dir / f"{ctx.app_name}.png")
    shutil.copy2(icon_source, app_dir / ".DirIcon")

    desktop_entry = textwrap.dedent(
        f"""\
        [Desktop Entry]
        Type=Application
        Name={ctx.product_name}
        Exec={ctx.executable_name}
        Icon={ctx.app_name}
        Categories=Game;
        Terminal=false
        StartupNotify=true
        X-AppImage-Name={ctx.product_name}
        """
    )
    (app_dir / f"{ctx.app_name}.desktop").write_text(desktop_entry, encoding="utf-8")

    app_run = app_dir / "AppRun"
    app_run.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            HERE="$(dirname "$(readlink -f "$0")")"
            exec "$HERE/usr/bin/{ctx.executable_name}" "$@"
            """
        ),
        encoding="utf-8",
    )
    ctx.ensure_executable(app_run)

    appimagetool = resolve_appimagetool(ctx)
    appimage_path = ctx.target_output_dir / f"{ctx.executable_name}-x86_64.AppImage"
    if appimage_path.exists():
        appimage_path.unlink()

    env = os.environ.copy()
    env.setdefault("ARCH", "x86_64")
    env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")
    ctx.run([str(appimagetool), str(app_dir), str(appimage_path)], env=env)

    if not appimage_path.exists():
        raise ctx.error(f"AppImage was not created: {appimage_path}")

    ctx.ensure_executable(appimage_path)
    shutil.rmtree(app_dir, ignore_errors=True)
    return appimage_path


def build_target(ctx, args, base_artifact: Path) -> list[Path]:
    binary_path = ctx.copy_to_target(base_artifact, name=ctx.executable_name)
    if args.linux_format == "binary":
        return [binary_path]

    appimage = build_appimage(ctx, base_artifact)
    return [binary_path, appimage]
