# Launcher Diagnostics

## Purpose

The diagnostics layer converts launch artifacts into structured findings. A finding has a stable
identifier, confidence, priority, localized text keys, bounded evidence, and zero or more typed
actions. Detection is read-only. Any action that changes files must still use the existing service
responsible for that data and request confirmation when required.

## Data Flow

1. `launcher.core.game` collects only artifacts created by the current launch.
2. `launcher.application.diagnostics.artifacts` reads them with a fixed size limit.
3. `DiagnosticEngine` runs every registered detector.
4. Findings are deduplicated, generic symptoms are suppressed by exact causes, and the remaining
   findings are ranked by confidence, priority, and evidence.
5. The UI renders the primary finding, reports additional findings, and exposes only supported
   actions.

The engine must not stop after the first matching rule. Independent failures can occur in the same
launch and must remain visible.

## Adding A Detector

1. Add a small evaluator in `launcher/application/diagnostics/rules.py`, or a dedicated module when
   the rule needs its own parser.
2. Give the detector and each finding a permanent namespaced ID.
3. Require positive evidence for the exact problem. Do not classify a launch from a product or mod
   name alone.
4. Assign confidence conservatively:
   - `EXACT`: a stable exception, loader diagnostic, or validated structure proves the cause.
   - `HIGH`: multiple contextual signals identify the cause.
   - `MEDIUM`: a likely cause that still needs confirmation.
   - `LOW`: a generic fallback.
5. Add localized title, message, and action labels in both language files.
6. Add positive, negative, suppression, and multi-finding tests.
7. Register the detector in `default_engine()`. Duplicate detector IDs are rejected at startup.

Example:

```python
def _example_failure(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if "stable exception signature" not in lowered:
        return ()
    return (
        _finding(
            id="example.component.failure",
            kind="example_failure",
            severity="error",
            confidence=Confidence.EXACT,
            priority=100,
            title_key="example_failure_title",
            message_key="example_failure_message",
            evidence=_evidence(text, ("stable exception signature",)),
        ),
    )
```

## Fix Actions

Actions use stable IDs and one of three safety levels:

- `SAFE`: navigation or read-only diagnostics.
- `CONFIRM`: repair or synchronization that changes managed files.
- `MANUAL`: instructions that the launcher cannot perform safely.

Detector code describes an action but never executes it. The UI maps known action kinds to existing
application services. Unknown actions must be ignored safely.

## Mod Compatibility Scan

`launcher.application.mod_compatibility` inspects enabled JAR metadata without extracting or
modifying archives. It supports Fabric, Quilt, Forge, NeoForge, and legacy Forge descriptors. The
scan reports corrupt archives, malformed metadata, wrong loaders, duplicate mod IDs, missing
required dependencies, and declared conflicts.

The scanner and installed-mod discovery use the same bounded metadata parser. Fabric, Quilt,
Forge, and NeoForge descriptors therefore produce the same mod IDs and versions in the manager and
diagnostics.

Exact launch rules also cover loader-reported missing dependencies, VulkanMod device selection,
FTB Chunks local map save failures, and Create/Ponder block entity model failures. These rules use
exception and stack signatures from the failing launch; a mod name in a loaded-mod table is never
enough to classify a graphics or compatibility problem.

The scan is intentionally conservative. It does not guess compatibility from filenames and does
not automatically delete, replace, or download mods.

## Report Safety

- Ignore stale crash artifacts from earlier launches.
- Bound artifact reads and keep only concise evidence.
- Redact local home paths and token-like values before sending a report.
- Never include authentication headers or full command lines with access tokens.
- Keep finding and action IDs in report metadata so recurring failures can be grouped without
  parsing translated text.
