from __future__ import annotations

from pathlib import Path

from .model import DiagnosticArtifact

MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
FRESHNESS_TOLERANCE_SECONDS = 2.0
_TRUNCATION_MARKER = b"\n... diagnostic log truncated by TensaLauncher ...\n"


def read_artifact(
    path: Path,
    *,
    started_at: float | None = None,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> DiagnosticArtifact:
    try:
        stat = path.stat()
    except OSError:
        return DiagnosticArtifact(text="", fresh=False)

    fresh = started_at is None or stat.st_mtime >= started_at - FRESHNESS_TOLERANCE_SECONDS
    if not fresh or not path.is_file():
        return DiagnosticArtifact(text="", fresh=False)

    try:
        data = _read_bounded(path, stat.st_size, max_bytes)
    except OSError:
        return DiagnosticArtifact(text="", fresh=False)
    return DiagnosticArtifact(
        text=data.decode("utf-8", errors="replace"),
        fresh=True,
    )


def _read_bounded(path: Path, size: int, max_bytes: int) -> bytes:
    if max_bytes <= len(_TRUNCATION_MARKER):
        raise ValueError("max_bytes is too small for diagnostic artifact framing")
    with path.open("rb") as handle:
        if size <= max_bytes:
            return handle.read(max_bytes)
        available = max_bytes - len(_TRUNCATION_MARKER)
        head_size = available // 3
        tail_size = available - head_size
        head = handle.read(head_size)
        handle.seek(-tail_size, 2)
        return head + _TRUNCATION_MARKER + handle.read(tail_size)


__all__ = ["FRESHNESS_TOLERANCE_SECONDS", "MAX_ARTIFACT_BYTES", "read_artifact"]
