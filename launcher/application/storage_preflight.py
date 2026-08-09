from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_STORAGE_RESERVE = 32 * 1024 * 1024


class StoragePreflightError(OSError):
    """Raised before an operation when its target storage cannot be used safely."""


@dataclass(frozen=True, slots=True)
class StorageRequest:
    target: Path
    required_bytes: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        if self.required_bytes < 0:
            raise ValueError("required_bytes cannot be negative")


def ensure_storage_available(
    requests: Iterable[StorageRequest],
    *,
    reserve_bytes: int = DEFAULT_STORAGE_RESERVE,
) -> None:
    """Verify writable targets and coalesced free-space requirements per volume."""
    if reserve_bytes < 0:
        raise ValueError("reserve_bytes cannot be negative")

    prepared = list(requests)
    if not prepared:
        return

    volume_requirements: dict[tuple[int, str], tuple[Path, int, list[str]]] = {}
    probed_directories: set[Path] = set()
    for request in prepared:
        target = Path(request.target).expanduser()
        directory = _target_directory(target)
        _ensure_directory(directory, target)

        resolved_directory = directory.resolve()
        if resolved_directory not in probed_directories:
            _probe_writable(resolved_directory)
            probed_directories.add(resolved_directory)

        volume = _volume_key(resolved_directory)
        current_path, current_bytes, current_labels = volume_requirements.get(
            volume,
            (resolved_directory, 0, []),
        )
        if request.label:
            current_labels.append(request.label)
        volume_requirements[volume] = (
            current_path,
            current_bytes + request.required_bytes,
            current_labels,
        )

    for directory, required_bytes, labels in volume_requirements.values():
        if required_bytes <= 0:
            continue
        try:
            free_bytes = shutil.disk_usage(directory).free
        except OSError as exc:
            raise StoragePreflightError(
                f"Could not determine free space for {directory}: {exc}"
            ) from exc
        total_required = required_bytes + reserve_bytes
        if free_bytes < total_required:
            operation = ", ".join(dict.fromkeys(labels)) or "operation"
            raise StoragePreflightError(
                f"Not enough free space for {operation} on {directory}: "
                f"requires {_format_bytes(total_required)}, "
                f"available {_format_bytes(free_bytes)}"
            )


def _target_directory(target: Path) -> Path:
    if target.exists():
        if target.is_dir():
            return target
        return target.parent
    return target.parent


def _ensure_directory(directory: Path, target: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoragePreflightError(
            f"Could not prepare storage directory {directory}: {exc}"
        ) from exc
    if not directory.is_dir():
        raise StoragePreflightError(f"Storage target parent is not a directory: {directory}")
    if target.exists() and target.is_dir() and target != directory:
        raise StoragePreflightError(f"Storage file target is a directory: {target}")


def _probe_writable(directory: Path) -> None:
    probe_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".tensalauncher-write-",
            suffix=".tmp",
            dir=directory,
        )
        os.close(descriptor)
        probe_path = Path(raw_path)
        with probe_path.open("ab") as handle:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StoragePreflightError(
            f"Storage directory is not writable: {directory}: {exc}"
        ) from exc
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass


def _volume_key(directory: Path) -> tuple[int, str]:
    try:
        return directory.stat().st_dev, ""
    except OSError:
        anchor = directory.anchor.casefold()
        return -1, anchor


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


__all__ = [
    "DEFAULT_STORAGE_RESERVE",
    "StoragePreflightError",
    "StorageRequest",
    "ensure_storage_available",
]
