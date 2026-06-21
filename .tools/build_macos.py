#!/usr/bin/env python3
from __future__ import annotations

import plistlib
import shutil
from pathlib import Path

from launcher import MACOS_BUNDLE_ID

MICROPHONE_USAGE_DESCRIPTION = (
    "TensaLauncher needs microphone access so Minecraft voice chat mods can request audio input on macOS."
)


def prepare_macos_app_bundle(ctx, app_bundle: Path) -> None:
    info_plist = app_bundle / "Contents" / "Info.plist"
    if not info_plist.is_file():
        raise ctx.error(f"macOS app bundle is missing Info.plist: {info_plist}")

    with info_plist.open("rb") as handle:
        metadata = plistlib.load(handle)

    metadata["CFBundleIdentifier"] = MACOS_BUNDLE_ID
    metadata["CFBundleName"] = ctx.app_name
    metadata["CFBundleDisplayName"] = ctx.app_name
    metadata["NSMicrophoneUsageDescription"] = MICROPHONE_USAGE_DESCRIPTION

    with info_plist.open("wb") as handle:
        plistlib.dump(metadata, handle, sort_keys=False)


def build_dmg(ctx, app_bundle: Path) -> Path:
    create_dmg = shutil.which("create-dmg")
    if not create_dmg:
        raise ctx.error("create-dmg not found. Install it (brew install create-dmg) or use --skip-dmg.")

    dmg_path = ctx.target_output_dir / f"{ctx.executable_name}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()

    command = [
        create_dmg,
        "--volname",
        ctx.app_name,
        "--window-pos",
        "200",
        "120",
        "--window-size",
        "600",
        "400",
        "--icon-size",
        "100",
        "--app-drop-link",
        "425",
        "120",
        str(dmg_path),
        str(app_bundle),
    ]
    ctx.run(command)

    if not dmg_path.exists():
        raise ctx.error(f"DMG was not created: {dmg_path}")
    return dmg_path


def build_target(ctx, args, base_artifact: Path) -> list[Path]:
    app_bundle = ctx.copy_to_target(base_artifact, name=f"{ctx.executable_name}.app")
    prepare_macos_app_bundle(ctx, app_bundle)
    if args.skip_dmg:
        return [app_bundle]

    dmg_path = build_dmg(ctx, app_bundle)
    return [dmg_path]
