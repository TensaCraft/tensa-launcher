from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

from .model import Confidence, DiagnosticCase, DiagnosticResult, Finding

logger = logging.getLogger(__name__)


class Detector(Protocol):
    @property
    def id(self) -> str: ...

    def detect(self, case: DiagnosticCase) -> Iterable[Finding]: ...


class DiagnosticEngine:
    def __init__(self, detectors: Iterable[Detector]) -> None:
        registered: dict[str, Detector] = {}
        for detector in detectors:
            if detector.id in registered:
                raise ValueError(f"Duplicate diagnostic detector id: {detector.id}")
            registered[detector.id] = detector
        self._detectors = tuple(registered.values())

    def analyze(self, case: DiagnosticCase) -> DiagnosticResult:
        detected: list[Finding] = []
        for detector in self._detectors:
            try:
                detected.extend(detector.detect(case))
            except Exception as error:
                logger.warning(
                    "Diagnostic detector %s failed (%s)",
                    detector.id,
                    type(error).__name__,
                )
        findings = self._deduplicate(detected)
        if not findings:
            findings = [unknown_finding()]

        suppressed_ids = {suppressed_id for finding in findings for suppressed_id in finding.suppresses}
        active = [finding for finding in findings if finding.id not in suppressed_ids]
        suppressed = [finding for finding in findings if finding.id in suppressed_ids]
        if not active:
            active = [unknown_finding()]

        active.sort(key=_rank, reverse=True)
        suppressed.sort(key=_rank, reverse=True)
        return DiagnosticResult(primary=active[0], findings=tuple(active), suppressed=tuple(suppressed))

    @staticmethod
    def _deduplicate(findings: Iterable[Finding]) -> list[Finding]:
        unique: dict[str, Finding] = {}
        for finding in findings:
            current = unique.get(finding.id)
            if current is None or _rank(finding) > _rank(current):
                unique[finding.id] = finding
        return list(unique.values())


def unknown_finding() -> Finding:
    return Finding(
        id="launch.unknown",
        kind="unknown",
        severity="error",
        confidence=Confidence.LOW,
        priority=0,
        title_key="launch_diagnostic_unknown_title",
        message_key="launch_diagnostic_unknown",
    )


def _rank(finding: Finding) -> tuple[int, int, int]:
    return int(finding.confidence), finding.priority, len(finding.evidence)


__all__ = ["Detector", "DiagnosticEngine"]
