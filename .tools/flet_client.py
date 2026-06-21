#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import tarfile
import urllib.request
import zipfile
from contextlib import suppress
from pathlib import Path

FLET_RELEASE_BASE_URL = "https://github.com/flet-dev/flet/releases/download"


def _ensure_member_within(destination: Path, member_name: str) -> Path:
    target = (destination / member_name).resolve()
    root = destination.resolve()
    if target != root and root not in target.parents:
        raise RuntimeError(f"Archive member escapes extraction directory: {member_name}")
    return target


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    for member in archive.infolist():
        target = _ensure_member_within(destination, member.filename)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        target = _ensure_member_within(destination, member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if member.issym() or member.islnk():
            link_target = Path(member.linkname)
            resolved_link = (target.parent / link_target).resolve() if not link_target.is_absolute() else link_target
            root = destination.resolve()
            if resolved_link != root and root not in resolved_link.parents:
                raise RuntimeError(f"Archive link escapes extraction directory: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with suppress(FileExistsError):
                target.symlink_to(member.linkname)
            continue
        if not member.isfile():
            continue
        source = archive.extractfile(member)
        if source is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        with suppress(OSError):
            target.chmod(member.mode & 0o777)


def resolve_flet_desktop_release(ctx) -> tuple[str, str]:
    completed = ctx.run(
        [
            ctx.python_bin,
            "-c",
            (
                "import flet_desktop, flet_desktop.version; "
                "print(flet_desktop.version.version); "
                "print(flet_desktop.get_artifact_filename())"
            ),
        ],
        capture_output=True,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ctx.error("Unable to resolve Flet desktop runtime artifact.")
    return lines[0], lines[1]


def resolve_flet_desktop_client_archive(ctx) -> Path:
    version, artifact = resolve_flet_desktop_release(ctx)
    archive_dir = ctx.output_root / "tools" / "flet-client" / version
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / artifact
    if archive_path.exists():
        return archive_path

    download_url = os.environ.get("FLET_CLIENT_URL", f"{FLET_RELEASE_BASE_URL}/v{version}/{artifact}")
    temp_path = archive_path.with_suffix(f"{archive_path.suffix}.tmp")
    ctx.log(f"Downloading bundled Flet desktop runtime from {download_url}")
    try:
        with urllib.request.urlopen(download_url, timeout=120) as response:
            temp_path.write_bytes(response.read())
        temp_path.replace(archive_path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise ctx.error(f"Failed to download Flet desktop runtime: {exc}") from exc

    return archive_path


def extract_flet_desktop_client_archive(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)

    temp_destination = destination.parent / f".{destination.name}.tmp"
    if temp_destination.exists():
        shutil.rmtree(temp_destination)
    temp_destination.mkdir(parents=True)

    try:
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path, "r") as archive:
                _safe_extract_zip(archive, temp_destination)
        else:
            with tarfile.open(archive_path, "r:gz") as archive:
                _safe_extract_tar(archive, temp_destination)
        temp_destination.rename(destination)
    except Exception:
        shutil.rmtree(temp_destination, ignore_errors=True)
        raise

    return destination
