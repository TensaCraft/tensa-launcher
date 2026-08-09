from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from .engine import Detector, DiagnosticEngine
from .model import ActionSafety, Confidence, DiagnosticCase, Finding, FixAction

_MISSING_MINECRAFT = (
    "noclassdeffounderror: net/minecraft",
    "classnotfoundexception: net.minecraft",
)
_LOCKED_FILE = (
    "winerror 32",
    "being used by another process",
    "process cannot access the file",
)
_MOD_INCOMPATIBILITY = (
    "incompatible mods found",
    "incompatible mod set",
    "mods are incompatible",
    "neg_hard_dep",
    "negative hard dependency",
    "conflicts with an installed or selected mod",
)
_GRAPHICS_SUBJECT = (
    "opengl",
    "buffer storage",
    "persistent mapping",
    "renderer",
    "graphics driver",
    "gpu driver",
    "video driver",
)
_GRAPHICS_FAILURE = (
    "error",
    "exception",
    "failed",
    "failure",
    "crash",
    "incompatible",
    "unsupported",
    "not support",
    "unavailable",
)
_MISSING_DEPENDENCY = (
    "not installed",
    "which is missing",
    "is missing",
    "missing or unsupported mandatory dependencies",
    "mandatory dependencies",
    "mod loading has failed",
)
_MOD_REQUIREMENT_RE = re.compile(
    r"""(?ix)
    \bmod\s+
    (?:
        ["'](?P<quoted_mod>[^"'\r\n]+)["']
        (?:\s+\((?P<mod_id>[a-z0-9_.:+-]+)\))?
        |
        (?P<plain_mod>[a-z0-9_.:+-]+)
    )
    (?:\s+\([^)\r\n]+\))?
    (?:\s+[0-9][^\s,;]*)?
    \s+requires\s+(?P<requirement>[^\r\n]+)
    """
)
_PARENTHESIZED_DEPENDENCY_RE = re.compile(
    r"""(?ix)
    \bof\s+(?:mod\s+)?
    ["'][^"'\r\n]+["']\s*
    \((?P<dependency>[a-z0-9_.:+-]+)\)
    """
)
_OF_DEPENDENCY_RE = re.compile(
    r"""(?ix)
    \bof\s+(?:mod\s+)?
    (?:
        ["'](?P<quoted_dependency>[^"'\r\n]+)["']
        |
        (?P<plain_dependency>[a-z0-9_.:+-]+)
    )
    """
)
_DIRECT_DEPENDENCY_RE = re.compile(r"(?ix)^\s*['\"]?(?P<dependency>[a-z0-9_.:+-]+)")
_NEOFORGE_MISSING_DEPENDENCY_RE = re.compile(
    r"(?i)mod id:\s*'(?P<dependency>[a-z0-9_.:+-]+)'\s*,\s*"
    r"requested by:\s*'(?P<mod>[a-z0-9_.:+-]+)'.*?"
    r"actual version:\s*'\[missing\]'"
)
_QUILT_MISSING_DEPENDENCY_RE = re.compile(
    r"(?im)^(?!\s*mod(?:\s|[\"']))(?P<mod>[^\r\n]+?)\s+requires\s+"
    r"(?P<requirement>[^\r\n]+which is missing!)\s*$"
)
_MODULE_EXPORT_CONFLICT_RE = re.compile(
    r"(?i)modules\s+(?P<first>[a-z0-9_.-]+)\s+and\s+(?P<second>[a-z0-9_.-]+)\s+"
    r"export package\s+(?P<package>[a-z0-9_.-]+)\s+to module"
)


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    evaluator: Callable[[DiagnosticCase], Iterable[Finding]]

    def detect(self, case: DiagnosticCase) -> Iterable[Finding]:
        return self.evaluator(case)


@dataclass(frozen=True, slots=True)
class MissingDependency:
    mod: str
    dependency: str
    evidence: str


def default_engine() -> DiagnosticEngine:
    detectors: tuple[Detector, ...] = (
        Rule("runtime.minecraft", _missing_minecraft),
        Rule("files.locked", _locked_file),
        Rule("mods.module_conflict", _module_conflict),
        Rule("mods.broken_mixin", _broken_mixin),
        Rule("mods.ftb_chunks_local_data", _ftb_chunks_local_data),
        Rule("mods.create_block_entity_rendering", _create_block_entity_rendering),
        Rule("mods.missing_dependency", _missing_dependency),
        Rule("mods.incompatible", _mod_incompatibility),
        Rule("network.channel", _channel_mismatch),
        Rule("graphics.vulkan_device", _vulkan_device_failure),
        Rule("graphics.initialization", _graphics_failure),
    )
    return DiagnosticEngine(detectors)


def _missing_minecraft(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    exact_manifest_failure = "mod id: 'minecraft'" in lowered and "actual version: '[missing]'" in lowered
    if not exact_manifest_failure and not _contains_any(lowered, _MISSING_MINECRAFT):
        return ()
    return (
        Finding(
            id="runtime.minecraft.missing",
            kind="missing_minecraft",
            severity="error",
            confidence=Confidence.EXACT,
            priority=100,
            title_key="launch_diagnostic_missing_minecraft_title",
            message_key="launch_diagnostic_missing_minecraft",
            evidence=_evidence(text, ("Actual version: '[MISSING]'", "NoClassDefFoundError", "ClassNotFoundException")),
            actions=(FixAction("repair_minecraft", "repair", ActionSafety.CONFIRM, "diagnostic_action_repair"),),
        ),
    )


def _locked_file(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not _contains_any(lowered, _LOCKED_FILE):
        return ()
    return (
        Finding(
            id="files.locked",
            kind="locked_file",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=110,
            title_key="launch_diagnostic_locked_file_title",
            message_key="launch_diagnostic_locked_file",
            evidence=_evidence(text, ("WinError 32", "being used by another process", "process cannot access")),
            actions=(
                (FixAction("retry_sync", "repair_sync", ActionSafety.CONFIRM, "diagnostic_action_retry_sync"),)
                if case.managed_pack
                else ()
            ),
        ),
    )


def _missing_dependency(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        ("requires" in lowered and _contains_any(lowered, _MISSING_DEPENDENCY))
        or ("currently," in lowered and "not installed" in lowered)
        or ("mandatory dependencies" in lowered and "actual version: '[missing]'" in lowered)
    ):
        return ()

    dependencies = _parse_missing_dependencies(text)
    if not dependencies:
        return (_missing_dependency_finding(case, MissingDependency("", "", ""), fallback_text=text),)
    return tuple(_missing_dependency_finding(case, dependency) for dependency in dependencies)


def _missing_dependency_finding(
    case: DiagnosticCase,
    missing: MissingDependency,
    *,
    fallback_text: str = "",
) -> Finding:
    dependency = missing.dependency
    is_fabric_api = dependency in {"fabric-api", "fabric_api", "fabricapi"}
    finding_id = (
        "mods.missing_dependency.fabric_api"
        if is_fabric_api
        else f"mods.missing_dependency.{_finding_id_part(dependency)}"
        if dependency
        else "mods.missing_dependency"
    )
    params = MappingProxyType(
        {key: value for key, value in {"mod": missing.mod, "dependency": dependency}.items() if value}
    )
    actions = [
        FixAction(
            "open_mod_manager",
            "open_mod_manager",
            ActionSafety.SAFE,
            "diagnostic_action_open_mod_manager",
        )
    ]
    if case.managed_pack:
        actions.append(FixAction("retry_sync", "repair_sync", ActionSafety.CONFIRM, "diagnostic_action_retry_sync"))
    evidence = (
        (missing.evidence[:300],)
        if missing.evidence
        else _evidence(fallback_text, ("requires", "not installed", "which is missing", "mandatory dependencies"))
    )
    return Finding(
        id=finding_id,
        kind="missing_mod_dependency",
        severity="warning",
        confidence=Confidence.EXACT if dependency else Confidence.HIGH,
        priority=100,
        title_key="launch_diagnostic_missing_mod_dependency_title",
        message_key="launch_diagnostic_missing_mod_dependency",
        evidence=evidence,
        params=params,
        actions=tuple(actions),
        suppresses=("mods.incompatible.generic",),
    )


def _parse_missing_dependencies(text: str) -> tuple[MissingDependency, ...]:
    parsed: dict[str, MissingDependency] = {}
    for match in _NEOFORGE_MISSING_DEPENDENCY_RE.finditer(text):
        dependency = _clean_id(match.group("dependency"))
        parsed[dependency] = MissingDependency(
            mod=_clean_id(match.group("mod")),
            dependency=dependency,
            evidence=match.group(0).strip(),
        )
    for match in _QUILT_MISSING_DEPENDENCY_RE.finditer(text):
        requirement = match.group("requirement")
        dependency = _dependency_id(requirement)
        if not dependency or dependency in parsed:
            continue
        parsed[dependency] = MissingDependency(
            mod=match.group("mod").strip(),
            dependency=dependency,
            evidence=match.group(0).strip(),
        )
    for match in _MOD_REQUIREMENT_RE.finditer(text):
        requirement = match.group("requirement")
        dependency = _dependency_id(requirement)
        if not dependency or dependency in parsed or not _requirement_is_missing(requirement, text, dependency):
            continue
        mod = (match.group("quoted_mod") or match.group("plain_mod") or "").strip()
        evidence = match.group(0).strip()
        parsed[dependency] = MissingDependency(mod=mod, dependency=dependency, evidence=evidence)
    return tuple(parsed.values())


def _module_conflict(case: DiagnosticCase) -> Iterable[Finding]:
    text, _lowered = _case_text(case)
    match = _MODULE_EXPORT_CONFLICT_RE.search(text)
    if match is None:
        return ()
    modules = (_clean_id(match.group("first")), _clean_id(match.group("second")))
    suppresses = tuple(f"mods.missing_dependency.{_finding_id_part(module)}" for module in modules)
    return (
        Finding(
            id="mods.module_conflict",
            kind="mod_incompatibility",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=120,
            title_key="launch_diagnostic_mod_incompatibility_title",
            message_key="launch_diagnostic_mod_incompatibility",
            evidence=(match.group(0).strip()[:300],),
            actions=_mod_issue_actions(case),
            suppresses=suppresses,
        ),
    )


def _broken_mixin(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        "illegalclassloaderror" in lowered
        and "illegal classload request" in lowered
        and "mixin is missing from" in lowered
    ):
        return ()
    return (
        Finding(
            id="mods.broken_mixin",
            kind="mod_incompatibility",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=115,
            title_key="launch_diagnostic_mod_incompatibility_title",
            message_key="launch_diagnostic_mod_incompatibility",
            evidence=_evidence(text, ("IllegalClassLoadError", "mixin is missing from")),
            actions=_mod_issue_actions(case),
        ),
    )


def _ftb_chunks_local_data(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        "java.util.concurrentmodificationexception" in lowered
        and "ftbchunks" in lowered
        and "mapmanager.saveallregions" in lowered
    ):
        return ()
    return (
        Finding(
            id="mods.ftb_chunks_local_data",
            kind="ftb_chunks_local_data",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=125,
            title_key="launch_diagnostic_ftb_chunks_title",
            message_key="launch_diagnostic_ftb_chunks",
            evidence=_evidence(text, ("ConcurrentModificationException", "MapManager.saveAllRegions")),
            suppresses=("graphics.initialization",),
        ),
    )


def _create_block_entity_rendering(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        "description: rendering block entity" in lowered
        and "bakedmodel.getmodeldata" in lowered
        and ("bakedmodelbuffererimpl" in lowered or "packagerrenderer" in lowered)
    ):
        return ()
    return (
        Finding(
            id="mods.create_block_entity_rendering",
            kind="mod_rendering_error",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=120,
            title_key="launch_diagnostic_create_rendering_title",
            message_key="launch_diagnostic_create_rendering",
            evidence=_evidence(text, ("Rendering Block Entity", "BakedModel.getModelData", "BakedModelBuffererImpl")),
            actions=(
                FixAction(
                    "open_mod_manager",
                    "open_mod_manager",
                    ActionSafety.SAFE,
                    "diagnostic_action_open_mod_manager",
                ),
            ),
            suppresses=("graphics.initialization",),
        ),
    )


def _mod_issue_actions(case: DiagnosticCase) -> tuple[FixAction, ...]:
    actions = [
        FixAction(
            "open_mod_manager",
            "open_mod_manager",
            ActionSafety.SAFE,
            "diagnostic_action_open_mod_manager",
        )
    ]
    if case.managed_pack:
        actions.append(
            FixAction(
                "retry_sync",
                "repair_sync",
                ActionSafety.CONFIRM,
                "diagnostic_action_retry_sync",
            )
        )
    return tuple(actions)


def _requirement_is_missing(requirement: str, text: str, dependency: str) -> bool:
    lowered = requirement.lower()
    if _contains_any(lowered, _MISSING_DEPENDENCY):
        return True
    return bool(
        re.search(
            rf"(?im)\bcurrently,\s+(?:mod\s+)?['\"]?{re.escape(dependency)}['\"]?\s+is\s+not\s+installed\b",
            text,
        )
    )


def _dependency_id(requirement: str) -> str:
    parenthesized = _PARENTHESIZED_DEPENDENCY_RE.search(requirement)
    if parenthesized:
        return _clean_id(parenthesized.group("dependency"))

    after_of = _OF_DEPENDENCY_RE.search(requirement)
    if after_of:
        value = after_of.group("quoted_dependency") or after_of.group("plain_dependency") or ""
        return _clean_id(value) if " " not in value.strip() else ""

    direct = _DIRECT_DEPENDENCY_RE.match(requirement)
    if direct and direct.group("dependency").lower() not in {"any", "version"}:
        return _clean_id(direct.group("dependency"))
    return ""


def _finding_id_part(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", value.lower()).strip("._-") or "unknown"


def _mod_incompatibility(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        _contains_any(lowered, _MOD_INCOMPATIBILITY)
        or ("replace mod" in lowered and "compatible with" in lowered)
        or ("breaks" in lowered and ("hard_dep" in lowered or "mod" in lowered))
    ):
        return ()
    return (
        Finding(
            id="mods.incompatible.generic",
            kind="mod_incompatibility",
            severity="warning",
            confidence=Confidence.HIGH,
            priority=80,
            title_key="launch_diagnostic_mod_incompatibility_title",
            message_key="launch_diagnostic_mod_incompatibility",
            evidence=_evidence(
                text,
                ("incompatible", "NEG_HARD_DEP", "breaks", "replace mod", "compatible with", "conflicts with"),
            ),
            actions=(
                FixAction(
                    "open_mod_manager",
                    "open_mod_manager",
                    ActionSafety.SAFE,
                    "diagnostic_action_open_mod_manager",
                ),
            ),
        ),
    )


def _channel_mismatch(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    exact = (
        "absent on client" in lowered
        or "missing on client" in lowered
        or "server requires" in lowered
        or "не вдалося з'єднатися з каналом" in lowered
    )
    contextual = (
        "channel" in lowered
        and ("client" in lowered or "клієнт" in lowered)
        and _contains_any(lowered, ("absent", "missing", "requires", "required", "відсут", "необхід"))
    )
    if not exact and not contextual:
        return ()
    return (
        Finding(
            id="network.channel_mismatch",
            kind="network_channel_mismatch",
            severity="warning",
            confidence=Confidence.HIGH,
            priority=70,
            title_key="launch_diagnostic_channel_mismatch_title",
            message_key="launch_diagnostic_channel_mismatch",
            evidence=_evidence(text, ("channel", "канал", "absent on client", "missing on client")),
            actions=(
                (FixAction("retry_sync", "repair_sync", ActionSafety.CONFIRM, "diagnostic_action_retry_sync"),)
                if case.managed_pack
                else ()
            ),
        ),
    )


def _vulkan_device_failure(case: DiagnosticCase) -> Iterable[Finding]:
    text, lowered = _case_text(case)
    if not (
        "failed to find a suitable gpu" in lowered
        and "vulkanmod.vulkan.device.devicemanager" in lowered
    ):
        return ()
    return (
        Finding(
            id="graphics.vulkan_device",
            kind="graphics_compatibility",
            severity="warning",
            confidence=Confidence.EXACT,
            priority=105,
            title_key="launch_diagnostic_vulkan_gpu_title",
            message_key="launch_diagnostic_vulkan_gpu",
            evidence=_evidence(text, ("Failed to find a suitable GPU", "DeviceManager")),
            actions=(
                FixAction(
                    "open_mod_manager",
                    "open_mod_manager",
                    ActionSafety.SAFE,
                    "diagnostic_action_open_mod_manager",
                ),
            ),
        ),
    )


def _graphics_failure(case: DiagnosticCase) -> Iterable[Finding]:
    text, _lowered = _case_text(case)
    matching = [
        line
        for line in text.splitlines()
        if _contains_any(line.lower(), _GRAPHICS_SUBJECT) and _contains_any(line.lower(), _GRAPHICS_FAILURE)
    ]
    if not matching:
        return ()
    return (
        Finding(
            id="graphics.initialization",
            kind="graphics_compatibility",
            severity="warning",
            confidence=Confidence.HIGH,
            priority=60,
            title_key="launch_diagnostic_graphics_title",
            message_key="launch_diagnostic_graphics",
            evidence=tuple(line.strip()[:300] for line in matching[:4]),
            actions=(
                FixAction(
                    "open_diagnostics",
                    "open_diagnostics",
                    ActionSafety.SAFE,
                    "open_crash_diagnostics",
                ),
            ),
        ),
    )


def _case_text(case: DiagnosticCase) -> tuple[str, str]:
    text = "\n".join(artifact.text for artifact in case.artifacts if artifact.fresh and artifact.text)
    return text, text.lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _evidence(text: str, markers: tuple[str, ...], *, limit: int = 4) -> tuple[str, ...]:
    selected: list[str] = []
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if any(marker.lower() in line.lower() for marker in markers):
            selected.append(line[:300])
            if len(selected) >= limit:
                break
    return tuple(selected)


def _clean_id(value: str) -> str:
    return value.strip(" '\".,;:").lower()


__all__ = ["default_engine"]
