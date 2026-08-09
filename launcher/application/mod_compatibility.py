from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from launcher.application.mod_identity import (
    DEFAULT_MAX_MOD_METADATA_BYTES,
    ModDescriptor,
    ModJarInspection,
    ModVersionConstraint,
    inspect_mod_jar,
    normalize_mod_loader,
)

_PLATFORM_DEPENDENCIES = frozenset(
    {
        "fabric_loader",
        "fabricloader",
        "forge",
        "java",
        "minecraft",
        "neoforge",
        "quilt_loader",
        "quiltloader",
    }
)
_COMPATIBLE_DESCRIPTORS = {
    "fabric": frozenset({"fabric"}),
    "quilt": frozenset({"fabric", "quilt"}),
    "forge": frozenset({"forge"}),
    # Older NeoForge versions also use META-INF/mods.toml.
    "neoforge": frozenset({"forge", "neoforge"}),
}


class ModCompatibilityIssueKind(StrEnum):
    CORRUPT_JAR = "corrupt_jar"
    UNREADABLE_JAR = "unreadable_jar"
    MISSING_DESCRIPTOR = "missing_descriptor"
    METADATA_TOO_LARGE = "metadata_too_large"
    INVALID_METADATA = "invalid_metadata"
    DUPLICATE_ID = "duplicate_id"
    MISSING_REQUIRED_DEPENDENCY = "missing_required_dependency"
    INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION = "incompatible_required_dependency_version"
    DECLARED_CONFLICT = "declared_conflict"
    WRONG_LOADER = "wrong_loader"


@dataclass(frozen=True, slots=True)
class ModCompatibilityIssue:
    kind: ModCompatibilityIssueKind
    paths: tuple[Path, ...]
    mod_ids: tuple[str, ...] = ()
    metadata_file: str | None = None
    expected_loader: str | None = None
    descriptor_loader: str | None = None
    version_ranges: tuple[str, ...] = ()
    installed_versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModCompatibilityReport:
    loader: str | None
    jars: tuple[Path, ...]
    descriptors: tuple[ModDescriptor, ...]
    issues: tuple[ModCompatibilityIssue, ...]

    @property
    def compatible(self) -> bool:
        return not self.issues


def scan_mod_compatibility(
    mods_directory: str | Path,
    loader: str | None,
    *,
    max_metadata_bytes: int = DEFAULT_MAX_MOD_METADATA_BYTES,
) -> ModCompatibilityReport:
    """Inspect enabled mod JAR metadata without extracting or modifying archives."""
    if max_metadata_bytes < 1:
        raise ValueError("max_metadata_bytes must be positive")

    directory = Path(mods_directory)
    target_loader = normalize_mod_loader(loader)
    jars = _enabled_jars(directory)
    descriptors: list[ModDescriptor] = []
    issues: list[ModCompatibilityIssue] = []

    for jar in jars:
        inspection = inspect_mod_jar(
            jar,
            target_loader,
            max_metadata_bytes=max_metadata_bytes,
        )
        descriptors.extend(inspection.descriptors)
        if inspection.error_kind is not None:
            issues.append(_inspection_issue(inspection))
            continue

        descriptor_loader = inspection.descriptors[0].loader
        compatible_loaders = _COMPATIBLE_DESCRIPTORS.get(target_loader or "")
        if compatible_loaders is not None and descriptor_loader not in compatible_loaders:
            issues.append(
                ModCompatibilityIssue(
                    kind=ModCompatibilityIssueKind.WRONG_LOADER,
                    paths=(jar,),
                    mod_ids=tuple(descriptor.mod_id for descriptor in inspection.descriptors),
                    metadata_file=inspection.metadata_file,
                    expected_loader=target_loader,
                    descriptor_loader=descriptor_loader,
                )
            )

    issues.extend(_analyze_descriptors(descriptors))
    return ModCompatibilityReport(
        loader=target_loader,
        jars=jars,
        descriptors=tuple(descriptors),
        issues=tuple(sorted(issues, key=_issue_sort_key)),
    )


def _enabled_jars(directory: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(directory.iterdir())
    except FileNotFoundError:
        return ()
    return tuple(
        sorted(
            (entry for entry in entries if entry.is_file() and entry.suffix.casefold() == ".jar"),
            key=lambda path: (path.name.casefold(), path.name),
        )
    )


def _inspection_issue(inspection: ModJarInspection) -> ModCompatibilityIssue:
    error_kind = inspection.error_kind
    if error_kind is None:
        raise ValueError("inspection does not contain an error")
    return ModCompatibilityIssue(
        kind=ModCompatibilityIssueKind(error_kind.value),
        paths=(inspection.path,),
        metadata_file=inspection.metadata_file,
    )


def _analyze_descriptors(descriptors: Sequence[ModDescriptor]) -> list[ModCompatibilityIssue]:
    advertised: dict[str, list[int]] = {}
    for index, descriptor in enumerate(descriptors):
        for advertised_id in {descriptor.mod_id, *descriptor.provides}:
            advertised.setdefault(advertised_id, []).append(index)

    issues: list[ModCompatibilityIssue] = []
    for advertised_id, owners in advertised.items():
        if len(owners) > 1:
            issues.append(
                ModCompatibilityIssue(
                    kind=ModCompatibilityIssueKind.DUPLICATE_ID,
                    paths=_owner_paths(descriptors, owners),
                    mod_ids=(advertised_id,),
                )
            )

    for index, descriptor in enumerate(descriptors):
        for dependency in descriptor.required_dependency_constraints:
            if dependency.mod_id in _PLATFORM_DEPENDENCIES:
                continue
            owners = advertised.get(dependency.mod_id, ())
            if not owners:
                issues.append(
                    ModCompatibilityIssue(
                        kind=ModCompatibilityIssueKind.MISSING_REQUIRED_DEPENDENCY,
                        paths=(descriptor.path,),
                        mod_ids=(descriptor.mod_id, dependency.mod_id),
                        metadata_file=descriptor.metadata_file,
                        version_ranges=dependency.ranges,
                    )
                )
                continue
            versions = _owner_versions(descriptors, owners)
            if dependency.ranges and _versions_match(versions, dependency.ranges) is False:
                issues.append(
                    ModCompatibilityIssue(
                        kind=ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION,
                        paths=_owner_paths(descriptors, (index, *owners)),
                        mod_ids=(descriptor.mod_id, dependency.mod_id),
                        metadata_file=descriptor.metadata_file,
                        version_ranges=dependency.ranges,
                        installed_versions=versions,
                    )
                )
        for conflict in descriptor.conflict_constraints:
            if conflict.mod_id in _PLATFORM_DEPENDENCIES:
                continue
            other_owners = [owner for owner in advertised.get(conflict.mod_id, ()) if owner != index]
            if conflict.ranges:
                other_owners = [
                    owner
                    for owner in other_owners
                    if _version_matches(descriptors[owner].version, conflict.ranges) is True
                ]
            if not other_owners:
                continue
            issues.append(
                ModCompatibilityIssue(
                    kind=ModCompatibilityIssueKind.DECLARED_CONFLICT,
                    paths=_owner_paths(descriptors, (index, *other_owners)),
                    mod_ids=(descriptor.mod_id, conflict.mod_id),
                    metadata_file=descriptor.metadata_file,
                    version_ranges=conflict.ranges,
                    installed_versions=_owner_versions(descriptors, other_owners),
                )
            )
    return issues


def _owner_paths(descriptors: Sequence[ModDescriptor], owners: Iterable[int]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {descriptors[index].path for index in owners},
            key=lambda path: (path.name.casefold(), path.name, str(path)),
        )
    )


def _owner_versions(descriptors: Sequence[ModDescriptor], owners: Iterable[int]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                version
                for owner in owners
                if (version := descriptors[owner].version) is not None
            },
            key=lambda value: (value.casefold(), value),
        )
    )


def _issue_sort_key(issue: ModCompatibilityIssue) -> tuple[object, ...]:
    return (
        issue.kind.value,
        tuple((path.name.casefold(), path.name, str(path)) for path in issue.paths),
        issue.mod_ids,
        issue.metadata_file or "",
        issue.version_ranges,
        issue.installed_versions,
    )


def _versions_match(versions: Sequence[str], ranges: tuple[str, ...]) -> bool | None:
    if not versions:
        return None
    results = tuple(_version_matches(version, ranges) for version in versions)
    if True in results:
        return True
    return None if None in results else False


def _version_matches(version: str | None, ranges: tuple[str, ...]) -> bool | None:
    if version is None:
        return None
    if not ranges:
        return True
    results = tuple(_range_matches(version, version_range) for version_range in ranges)
    if True in results:
        return True
    return None if None in results else False


def _range_matches(version: str, version_range: str) -> bool | None:
    expression = version_range.strip()
    if not expression or expression == "*":
        return True
    if expression[0] in "[(" and expression[-1] in "])":
        return _maven_range_matches(version, expression)

    candidate = _parsed_version(version)
    if candidate is None:
        if not any(character in expression for character in "<>~^* ") and not expression.endswith((".x", ".X")):
            return _without_build(version) == _without_build(expression.removeprefix("="))
        return None

    specifiers: list[str] = []
    for token in expression.split():
        specifier = _fabric_specifier(token)
        if specifier is None:
            return None
        specifiers.extend(specifier)
    try:
        return SpecifierSet(",".join(specifiers)).contains(candidate, prereleases=True)
    except InvalidSpecifier:
        return None


def _fabric_specifier(token: str) -> tuple[str, ...] | None:
    match = re.fullmatch(r"(>=|<=|>|<|=|~|\^)?(.+)", token)
    if match is None:
        return None
    operator, raw_version = match.groups()
    raw_version = _without_build(raw_version)

    wildcard = re.fullmatch(r"(\d+(?:\.\d+)*)\.(?:x|X|\*)", raw_version)
    if wildcard is not None:
        lower = tuple(int(part) for part in wildcard.group(1).split("."))
        upper = _next_version_prefix(lower)
        return (f">={_version_floor(lower)}", f"<{_version_floor(upper)}")

    parsed = _parsed_version(raw_version)
    if parsed is None:
        return (f"==={raw_version}",) if operator in {None, "="} else None
    normalized = str(parsed)
    if operator == "~":
        release = parsed.release
        major = next(iter(release), None)
        if major is None:
            return None
        minor = next(iter(release[1:]), None)
        upper = (major + 1,) if minor is None else (major, minor + 1)
        return (f">={normalized}", f"<{_version_floor(upper)}")
    if operator == "^":
        major = next(iter(parsed.release), None)
        if major is None:
            return None
        return (f">={normalized}", f"<{_version_floor((major + 1,))}")
    return (f"{'==' if operator in {None, '='} else operator}{normalized}",)


def _maven_range_matches(version: str, expression: str) -> bool | None:
    groups = re.findall(r"[\[(][^)\]]*[)\]]", expression)
    remainder = expression
    for group in groups:
        remainder = remainder.replace(group, "", 1)
    if not groups or remainder.strip(" ,"):
        return None

    results = tuple(_maven_group_matches(version, group) for group in groups)
    if True in results:
        return True
    return None if None in results else False


def _maven_group_matches(version: str, group: str) -> bool | None:
    candidate = _parsed_version(version)
    bounds = group[1:-1].split(",", 1)
    if len(bounds) == 1:
        expected = _parsed_version(bounds[0].strip())
        if candidate is not None and expected is not None:
            return candidate == expected
        return _without_build(version) == _without_build(bounds[0].strip())
    if candidate is None:
        return None

    lower_text, upper_text = (bound.strip() for bound in bounds)
    if lower_text:
        lower = _parsed_version(lower_text)
        if lower is None:
            return None
        if candidate < lower or (candidate == lower and group[0] == "("):
            return False
    if upper_text:
        upper = _parsed_version(upper_text)
        if upper is None:
            return None
        if candidate > upper or (candidate == upper and group[-1] == ")"):
            return False
    return True


def _parsed_version(value: str) -> Version | None:
    normalized = _without_build(value.strip())
    if normalized.endswith("-"):
        normalized = f"{normalized[:-1]}.dev0"
    try:
        return Version(normalized)
    except InvalidVersion:
        return None


def _without_build(value: str) -> str:
    return value.split("+", 1)[0]


def _next_version_prefix(release: tuple[int, ...]) -> tuple[int, ...]:
    return (*release[:-1], release[-1] + 1)


def _version_floor(release: tuple[int, ...]) -> str:
    return f"{'.'.join(str(part) for part in release)}.dev0"


__all__ = [
    "ModCompatibilityIssue",
    "ModCompatibilityIssueKind",
    "ModCompatibilityReport",
    "ModDescriptor",
    "ModVersionConstraint",
    "scan_mod_compatibility",
]
