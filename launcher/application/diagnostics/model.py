from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Mapping


class Confidence(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    EXACT = 4


class ActionSafety(StrEnum):
    SAFE = "safe"
    CONFIRM = "confirm"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class DiagnosticArtifact:
    text: str
    fresh: bool = True


@dataclass(frozen=True, slots=True)
class DiagnosticCase:
    artifacts: tuple[DiagnosticArtifact, ...]
    managed_pack: bool = False


@dataclass(frozen=True, slots=True)
class FixAction:
    id: str
    kind: str
    safety: ActionSafety
    title_key: str


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    kind: str
    severity: str
    confidence: Confidence
    priority: int
    title_key: str
    message_key: str
    evidence: tuple[str, ...] = ()
    actions: tuple[FixAction, ...] = ()
    params: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    suppresses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    primary: Finding
    findings: tuple[Finding, ...]
    suppressed: tuple[Finding, ...] = ()
    engine_version: int = 1


__all__ = [
    "ActionSafety",
    "Confidence",
    "DiagnosticArtifact",
    "DiagnosticCase",
    "DiagnosticResult",
    "Finding",
    "FixAction",
]
