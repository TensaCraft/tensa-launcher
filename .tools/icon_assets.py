#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Final

BRAND_BACKGROUND: Final[tuple[int, int, int, int]] = (5, 53, 41, 255)
TRANSPARENT_BACKGROUND: Final[tuple[int, int, int, int]] = (0, 0, 0, 0)

WINDOWS_ICO_SIZES: Final[tuple[tuple[int, int], ...]] = (
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
)
MACOS_ICNS_SIZES: Final[tuple[tuple[int, int], ...]] = (
    (16, 16),
    (32, 32),
    (64, 64),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
)
LINUX_ICON_SIZE: Final[tuple[int, int]] = (512, 512)


def _load_pillow_image_module():
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to generate launcher icon assets.") from exc
    Image.init()
    return Image


def _source_logo(ctx) -> Path:
    source_logo = ctx.assets_dir / "logo.png"
    if not source_logo.is_file():
        raise ctx.error(f"Launcher logo source was not found: {source_logo}")
    return source_logo


def _icon_output_dir(ctx) -> Path:
    output_dir = ctx.build_dir / "icons"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _contained_image(
    source_logo: Path,
    size: tuple[int, int],
    *,
    background: tuple[int, int, int, int] = TRANSPARENT_BACKGROUND,
    padding_ratio: float = 0.10,
):
    Image = _load_pillow_image_module()
    width, height = size
    padding_x = min(width // 3, max(0, round(width * padding_ratio)))
    padding_y = min(height // 3, max(0, round(height * padding_ratio)))
    max_width = max(1, width - (padding_x * 2))
    max_height = max(1, height - (padding_y * 2))

    with Image.open(source_logo) as opened:
        logo = opened.convert("RGBA")
        logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, background)
        left = (width - logo.width) // 2
        top = (height - logo.height) // 2
        canvas.alpha_composite(logo, (left, top))
        return canvas


def ensure_windows_ico(ctx, source_logo: Path | None = None) -> Path:
    source_logo = source_logo or _source_logo(ctx)
    icon_path = _icon_output_dir(ctx) / f"{ctx.executable_name}.ico"
    image = _contained_image(source_logo, (256, 256), padding_ratio=0.08)
    image.save(icon_path, format="ICO", sizes=list(WINDOWS_ICO_SIZES))
    return icon_path


def ensure_macos_icns(ctx, source_logo: Path | None = None) -> Path:
    source_logo = source_logo or _source_logo(ctx)
    icon_path = _icon_output_dir(ctx) / f"{ctx.executable_name}.icns"
    image = _contained_image(source_logo, (1024, 1024), padding_ratio=0.08)
    image.save(icon_path, format="ICNS", sizes=list(MACOS_ICNS_SIZES))
    return icon_path


def ensure_linux_png(ctx, source_logo: Path | None = None) -> Path:
    source_logo = source_logo or _source_logo(ctx)
    icon_path = _icon_output_dir(ctx) / f"{ctx.executable_name}.png"
    image = _contained_image(source_logo, LINUX_ICON_SIZE, padding_ratio=0.08)
    image.save(icon_path)
    return icon_path


def resolve_pack_icon(ctx, target: str) -> Path:
    if target == "windows":
        return ensure_windows_ico(ctx)
    if target == "macos":
        return ensure_macos_icns(ctx)
    if target == "linux":
        return ensure_linux_png(ctx)
    raise ctx.error(f"Unsupported icon target: {target}")
