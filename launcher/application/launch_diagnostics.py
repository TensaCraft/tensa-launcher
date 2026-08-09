from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import DiagnosticArtifact, DiagnosticCase, DiagnosticResult, default_engine


@dataclass(frozen=True, slots=True)
class LaunchDiagnosis:
    kind: str
    severity: str
    title_key: str
    message_key: str
    evidence: list[str]


_ENGINE = default_engine()


def analyze_launch_failure(case: DiagnosticCase) -> DiagnosticResult:
    return _ENGINE.analyze(case)


def classify_launch_failure(text: str | None) -> LaunchDiagnosis:
    result = analyze_launch_failure(
        DiagnosticCase(artifacts=(DiagnosticArtifact(text=text or ""),))
    )
    primary = result.primary
    return LaunchDiagnosis(
        kind=primary.kind,
        severity=primary.severity,
        title_key=primary.title_key,
        message_key=primary.message_key,
        evidence=list(primary.evidence),
    )


__all__ = ["LaunchDiagnosis", "analyze_launch_failure", "classify_launch_failure"]
