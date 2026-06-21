from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class LaunchDiagnosis:
    kind: str
    severity: str
    title_key: str
    message_key: str
    evidence: list[str]


_MISSING_MINECRAFT_PATTERNS = (
    "Mod ID: 'minecraft'",
    "Actual version: '[MISSING]'",
    "NoClassDefFoundError: net/minecraft",
    "ClassNotFoundException: net.minecraft",
)
_GRAPHICS_PATTERNS = (
    "sodium",
    "opengl",
    "buffer storage",
    "persistent mapping",
    "transparent",
    "renderer",
    "amd radeon hd",
)
_CHANNEL_PATTERNS = (
    "не вдалося з'єднатися з каналом",
    "channel",
    "absent on client",
    "missing on client",
    "server requires",
)
_LOCKED_FILE_PATTERNS = (
    "winerror 32",
    "being used by another process",
    "process cannot access the file",
)


def classify_launch_failure(text: str | None) -> LaunchDiagnosis:
    source = text or ""
    lowered = source.lower()
    if _contains_all(source, _MISSING_MINECRAFT_PATTERNS[:2]) or _contains_any(source, _MISSING_MINECRAFT_PATTERNS[2:]):
        return LaunchDiagnosis(
            kind="missing_minecraft",
            severity="error",
            title_key="launch_diagnostic_missing_minecraft_title",
            message_key="launch_diagnostic_missing_minecraft",
            evidence=_evidence(source, ("Actual version: '[MISSING]'", "NoClassDefFoundError", "ClassNotFoundException")),
        )
    if _contains_any(lowered, _LOCKED_FILE_PATTERNS):
        return LaunchDiagnosis(
            kind="locked_file",
            severity="warning",
            title_key="launch_diagnostic_locked_file_title",
            message_key="launch_diagnostic_locked_file",
            evidence=_evidence(source, ("WinError 32", "being used by another process", "process cannot access")),
        )
    if _contains_any(lowered, _CHANNEL_PATTERNS) and ("client" in lowered or "клієнт" in lowered):
        return LaunchDiagnosis(
            kind="network_channel_mismatch",
            severity="warning",
            title_key="launch_diagnostic_channel_mismatch_title",
            message_key="launch_diagnostic_channel_mismatch",
            evidence=_evidence(source, ("channel", "канал", "client", "клієнт")),
        )
    if "sodium" in lowered and _contains_any(lowered, _GRAPHICS_PATTERNS):
        return LaunchDiagnosis(
            kind="graphics_compatibility",
            severity="warning",
            title_key="launch_diagnostic_graphics_title",
            message_key="launch_diagnostic_graphics",
            evidence=_evidence(source, ("Sodium", "OpenGL", "buffer", "transparent", "AMD Radeon")),
        )
    return LaunchDiagnosis(
        kind="unknown",
        severity="error",
        title_key="launch_diagnostic_unknown_title",
        message_key="launch_diagnostic_unknown",
        evidence=[],
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _contains_all(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return all(needle.lower() in lowered for needle in needles)


def _evidence(text: str, markers: tuple[str, ...], *, limit: int = 4) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    selected: list[str] = []
    for line in lines:
        if any(re.search(re.escape(marker), line, re.IGNORECASE) for marker in markers):
            selected.append(line[:300])
            if len(selected) >= limit:
                break
    return selected


__all__ = ["LaunchDiagnosis", "classify_launch_failure"]
