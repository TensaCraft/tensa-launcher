from __future__ import annotations

import hashlib
import hmac
import json
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

DEFAULT_MAX_MOD_METADATA_BYTES = 1024 * 1024
DEFAULT_FILE_HASH_CHUNK_SIZE = 1024 * 1024
_SUPPORTED_FILE_HASH_LENGTHS = {
    "sha512": 128,
    "sha1": 40,
}
_FORMATS = (
    ("fabric.mod.json", "fabric"),
    ("quilt.mod.json", "quilt"),
    ("META-INF/neoforge.mods.toml", "neoforge"),
    ("META-INF/mods.toml", "forge"),
    ("mcmod.info", "forge"),
)
_FORMAT_PREFERENCE = {
    "fabric": ("fabric.mod.json", "quilt.mod.json"),
    "quilt": ("quilt.mod.json", "fabric.mod.json"),
    "forge": ("META-INF/mods.toml", "mcmod.info"),
    "neoforge": ("META-INF/neoforge.mods.toml", "META-INF/mods.toml", "mcmod.info"),
}


class ModMatchKind(StrEnum):
    OWNED = "owned"
    HINT = "hint"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ModMatch:
    item: Mapping[str, Any] | None
    kind: ModMatchKind
    reason: str

    @property
    def owned(self) -> bool:
        return self.kind is ModMatchKind.OWNED

    @property
    def can_replace(self) -> bool:
        return self.owned


@dataclass(frozen=True, slots=True)
class ModVersionConstraint:
    mod_id: str
    ranges: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModDescriptor:
    path: Path
    metadata_file: str
    loader: str
    mod_id: str
    name: str | None
    version: str | None
    description: str | None = None
    provides: tuple[str, ...] = ()
    required_dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    required_dependency_constraints: tuple[ModVersionConstraint, ...] = ()
    conflict_constraints: tuple[ModVersionConstraint, ...] = ()


class ModInspectionErrorKind(StrEnum):
    CORRUPT_JAR = "corrupt_jar"
    UNREADABLE_JAR = "unreadable_jar"
    MISSING_DESCRIPTOR = "missing_descriptor"
    METADATA_TOO_LARGE = "metadata_too_large"
    INVALID_METADATA = "invalid_metadata"


@dataclass(frozen=True, slots=True)
class ModJarInspection:
    path: Path
    descriptors: tuple[ModDescriptor, ...] = ()
    error_kind: ModInspectionErrorKind | None = None
    metadata_file: str | None = None

    @property
    def primary_descriptor(self) -> ModDescriptor | None:
        return self.descriptors[0] if self.descriptors else None


class _MetadataTooLargeError(Exception):
    pass


class _InvalidMetadataError(Exception):
    pass


class ModIdentityService:
    @classmethod
    def match_project(
        cls,
        installed_items: Sequence[Mapping[str, Any]],
        project: Mapping[str, Any],
    ) -> ModMatch:
        project_id = cls._text(project.get("project_id") or project.get("id"))
        if project_id:
            owned = [
                item
                for item in installed_items
                if cls.owns_item(item, project_id)
            ]
            if len(owned) == 1:
                return ModMatch(owned[0], ModMatchKind.OWNED, "verified_modrinth_provenance")
            if len(owned) > 1:
                return ModMatch(None, ModMatchKind.AMBIGUOUS, "duplicate_verified_modrinth_provenance")

        project_hints = {
            value
            for value in (
                cls.normalize_identifier(project.get("slug")),
                cls.normalize_identifier(project.get("title")),
            )
            if value
        }
        hints: list[Mapping[str, Any]] = [
            item
            for item in installed_items
            if project_id and cls._text(item.get("modrinth_project_id")) == project_id
        ]
        if project_hints:
            for item in installed_items:
                installed_hints = {
                    value
                    for value in (
                        cls.normalize_identifier(item.get("id")),
                        cls.normalize_identifier(item.get("name")),
                        cls.normalize_identifier(item.get("filename")),
                        cls.normalize_identifier(item.get("modrinth_project_slug")),
                    )
                    if value
                }
                if any(
                    installed == project_hint or installed.startswith(f"{project_hint}-")
                    for installed in installed_hints
                    for project_hint in project_hints
                ) and all(candidate is not item for candidate in hints):
                    hints.append(item)

        if len(hints) == 1:
            reason = (
                "unverified_modrinth_provenance"
                if project_id and cls._text(hints[0].get("modrinth_project_id")) == project_id
                else "declared_or_display_identity"
            )
            return ModMatch(hints[0], ModMatchKind.HINT, reason)
        if len(hints) > 1:
            return ModMatch(None, ModMatchKind.AMBIGUOUS, "multiple_identity_hints")
        if not project_hints:
            return ModMatch(None, ModMatchKind.NONE, "missing_project_identity")
        return ModMatch(None, ModMatchKind.NONE, "no_identity_match")

    @staticmethod
    def normalize_identifier(value: Any) -> str:
        text = str(value or "").strip().casefold()
        if text.endswith(".disabled"):
            text = text[:-9]
        for suffix in (".jar", ".zip"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return "-".join(part for part in text.replace("_", "-").split() if part)

    @staticmethod
    def owns_item(installed_item: Mapping[str, Any] | None, project_id: str) -> bool:
        if installed_item is None or not project_id:
            return False
        return bool(
            str(installed_item.get("modrinth_project_id") or "") == project_id
            and installed_item.get("modrinth_provenance_authoritative") is True
            and normalize_file_hash(
                installed_item.get("modrinth_hash_algorithm"),
                installed_item.get("modrinth_file_hash"),
            )
            is not None
        )

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()


def normalize_file_hash(algorithm: object, digest: object) -> tuple[str, str] | None:
    normalized_algorithm = str(algorithm or "").strip().casefold()
    normalized_digest = str(digest or "").strip().casefold()
    digest_length = _SUPPORTED_FILE_HASH_LENGTHS.get(normalized_algorithm)
    if digest_length is None or len(normalized_digest) != digest_length:
        return None
    if any(character not in "0123456789abcdef" for character in normalized_digest):
        return None
    return normalized_algorithm, normalized_digest


def compute_file_hash(
    path: str | Path,
    algorithm: str,
    *,
    chunk_size: int = DEFAULT_FILE_HASH_CHUNK_SIZE,
) -> str:
    normalized_algorithm = str(algorithm or "").strip().casefold()
    if normalized_algorithm not in _SUPPORTED_FILE_HASH_LENGTHS:
        raise ValueError(f"Unsupported file hash algorithm: {algorithm}")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    digest = hashlib.new(normalized_algorithm)
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_hash(
    path: str | Path,
    algorithm: object,
    digest: object,
    *,
    chunk_size: int = DEFAULT_FILE_HASH_CHUNK_SIZE,
) -> bool:
    normalized = normalize_file_hash(algorithm, digest)
    if normalized is None:
        return False
    normalized_algorithm, normalized_digest = normalized
    try:
        actual_digest = compute_file_hash(
            path,
            normalized_algorithm,
            chunk_size=chunk_size,
        )
    except OSError:
        return False
    return hmac.compare_digest(actual_digest, normalized_digest)


def inspect_mod_jar(
    path: str | Path,
    loader: str | None = None,
    *,
    max_metadata_bytes: int = DEFAULT_MAX_MOD_METADATA_BYTES,
) -> ModJarInspection:
    """Read bounded metadata from one mod JAR without extracting the archive."""
    if max_metadata_bytes < 1:
        raise ValueError("max_metadata_bytes must be positive")

    jar_path = Path(path)
    metadata_file: str | None = None
    try:
        with zipfile.ZipFile(jar_path) as archive:
            metadata_file = _select_metadata_file(archive, normalize_mod_loader(loader))
            if metadata_file is None:
                return ModJarInspection(
                    path=jar_path,
                    error_kind=ModInspectionErrorKind.MISSING_DESCRIPTOR,
                )
            payload = _read_metadata(archive, metadata_file, max_metadata_bytes)
    except _MetadataTooLargeError:
        return ModJarInspection(
            path=jar_path,
            error_kind=ModInspectionErrorKind.METADATA_TOO_LARGE,
            metadata_file=metadata_file,
        )
    except _InvalidMetadataError:
        return ModJarInspection(
            path=jar_path,
            error_kind=ModInspectionErrorKind.INVALID_METADATA,
            metadata_file=metadata_file,
        )
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError):
        return ModJarInspection(
            path=jar_path,
            error_kind=ModInspectionErrorKind.CORRUPT_JAR,
            metadata_file=metadata_file,
        )
    except (OSError, RuntimeError):
        return ModJarInspection(
            path=jar_path,
            error_kind=ModInspectionErrorKind.UNREADABLE_JAR,
            metadata_file=metadata_file,
        )

    try:
        descriptors = _parse_metadata(jar_path, metadata_file, payload)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError, _InvalidMetadataError):
        return ModJarInspection(
            path=jar_path,
            error_kind=ModInspectionErrorKind.INVALID_METADATA,
            metadata_file=metadata_file,
        )
    return ModJarInspection(
        path=jar_path,
        descriptors=descriptors,
        metadata_file=metadata_file,
    )


def _select_metadata_file(archive: zipfile.ZipFile, target_loader: str | None) -> str | None:
    available = {info.filename for info in archive.infolist() if not info.is_dir()}
    preferred = _FORMAT_PREFERENCE.get(target_loader or "", ())
    remaining = tuple(metadata_file for metadata_file, _loader in _FORMATS if metadata_file not in preferred)
    return next((metadata_file for metadata_file in (*preferred, *remaining) if metadata_file in available), None)


def _read_metadata(archive: zipfile.ZipFile, metadata_file: str, max_bytes: int) -> bytes:
    matching = [info for info in archive.infolist() if info.filename == metadata_file and not info.is_dir()]
    if len(matching) != 1:
        raise _InvalidMetadataError
    info = matching[0]
    if info.file_size > max_bytes:
        raise _MetadataTooLargeError
    with archive.open(info) as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise _MetadataTooLargeError
    return payload


def _parse_metadata(path: Path, metadata_file: str, payload: bytes) -> tuple[ModDescriptor, ...]:
    loader = dict(_FORMATS)[metadata_file]
    if metadata_file == "fabric.mod.json":
        return (_parse_fabric(path, metadata_file, loader, payload),)
    if metadata_file == "quilt.mod.json":
        return (_parse_quilt(path, metadata_file, loader, payload),)
    if metadata_file == "mcmod.info":
        return _parse_mcmod(path, metadata_file, loader, payload)
    return _parse_toml(path, metadata_file, loader, payload)


def _parse_fabric(path: Path, metadata_file: str, loader: str, payload: bytes) -> ModDescriptor:
    root = _as_mapping(json.loads(payload.decode("utf-8-sig")))
    if root is None:
        raise _InvalidMetadataError
    required = _mapping_constraints(root.get("depends"))
    conflicts = _merge_constraints(
        _mapping_constraints(root.get("conflicts")),
        _mapping_constraints(root.get("breaks")),
    )
    return _descriptor(
        path=path,
        metadata_file=metadata_file,
        loader=loader,
        mod_id=root.get("id"),
        name=root.get("name"),
        version=root.get("version"),
        description=root.get("description"),
        provides=_ids(root.get("provides")),
        required_constraints=required,
        conflict_constraints=conflicts,
    )


def _parse_quilt(path: Path, metadata_file: str, loader: str, payload: bytes) -> ModDescriptor:
    root = _as_mapping(json.loads(payload.decode("utf-8-sig")))
    quilt_loader = _as_mapping(root.get("quilt_loader")) if root is not None else None
    if quilt_loader is None:
        raise _InvalidMetadataError
    metadata = _as_mapping(quilt_loader.get("metadata"))
    required = _quilt_dependency_constraints(quilt_loader.get("depends"), alternatives_are_required=False)
    conflicts = _quilt_dependency_constraints(quilt_loader.get("breaks"), alternatives_are_required=True)
    return _descriptor(
        path=path,
        metadata_file=metadata_file,
        loader=loader,
        mod_id=quilt_loader.get("id"),
        name=metadata.get("name") if metadata is not None else None,
        version=quilt_loader.get("version"),
        description=metadata.get("description") if metadata is not None else None,
        provides=_ids(quilt_loader.get("provides")),
        required_constraints=required,
        conflict_constraints=conflicts,
    )


def _parse_toml(path: Path, metadata_file: str, loader: str, payload: bytes) -> tuple[ModDescriptor, ...]:
    root = _as_mapping(tomllib.loads(payload.decode("utf-8-sig")))
    if root is None:
        raise _InvalidMetadataError
    dependencies = _as_mapping(root.get("dependencies")) or {}
    descriptors: list[ModDescriptor] = []
    for mod in _mapping_entries(root.get("mods")):
        mod_id = _clean_id(mod.get("modId"))
        if mod_id is None:
            continue
        required, conflicts = _toml_dependency_constraints(dependencies, mod_id)
        descriptors.append(
            _descriptor(
                path=path,
                metadata_file=metadata_file,
                loader=loader,
                mod_id=mod_id,
                name=mod.get("displayName"),
                version=mod.get("version"),
                description=mod.get("description"),
                provides=_ids(mod.get("provides")),
                required_constraints=required,
                conflict_constraints=conflicts,
            )
        )
    if not descriptors:
        raise _InvalidMetadataError
    return tuple(descriptors)


def _parse_mcmod(path: Path, metadata_file: str, loader: str, payload: bytes) -> tuple[ModDescriptor, ...]:
    root = json.loads(payload.decode("utf-8-sig"))
    entries: Sequence[object]
    root_mapping = _as_mapping(root)
    if isinstance(root, list):
        entries = root
    elif root_mapping is not None and isinstance(root_mapping.get("modList"), list):
        entries = cast(list[object], root_mapping["modList"])
    elif root_mapping is not None:
        entries = (root_mapping,)
    else:
        raise _InvalidMetadataError

    descriptors: list[ModDescriptor] = []
    for entry in entries:
        mod = _as_mapping(entry)
        if mod is None:
            continue
        required = {
            constraint
            for value in _strings(mod.get("requiredMods"))
            if (constraint := _legacy_dependency_constraint(value, require_prefix=False)) is not None
        }
        required.update(
            constraint
            for value in _strings(mod.get("dependencies"))
            if (constraint := _legacy_dependency_constraint(value, require_prefix=True)) is not None
        )
        descriptors.append(
            _descriptor(
                path=path,
                metadata_file=metadata_file,
                loader=loader,
                mod_id=mod.get("modid", mod.get("modId")),
                name=mod.get("name"),
                version=mod.get("version"),
                description=mod.get("description"),
                provides=(*_ids(mod.get("provides")), *_ids(mod.get("aliases"))),
                required_constraints=required,
                conflict_constraints=(
                    *_id_constraints(mod.get("conflicts")),
                    *_id_constraints(mod.get("incompatibleMods")),
                ),
            )
        )
    if not descriptors:
        raise _InvalidMetadataError
    return tuple(descriptors)


def _descriptor(
    *,
    path: Path,
    metadata_file: str,
    loader: str,
    mod_id: object,
    name: object,
    version: object,
    description: object,
    provides: Iterable[str],
    required_constraints: Iterable[ModVersionConstraint],
    conflict_constraints: Iterable[ModVersionConstraint],
) -> ModDescriptor:
    cleaned_id = _clean_id(mod_id)
    if cleaned_id is None:
        raise _InvalidMetadataError
    required = _sorted_constraints(required_constraints)
    conflicts = _sorted_constraints(conflict_constraints)
    return ModDescriptor(
        path=path,
        metadata_file=metadata_file,
        loader=loader,
        mod_id=cleaned_id,
        name=_clean_text(name),
        version=_clean_text(version),
        description=_clean_text(description),
        provides=_sorted_ids(value for value in provides if value != cleaned_id),
        required_dependencies=tuple(constraint.mod_id for constraint in required),
        conflicts=tuple(constraint.mod_id for constraint in conflicts),
        required_dependency_constraints=required,
        conflict_constraints=conflicts,
    )


def _toml_dependency_constraints(
    dependencies: Mapping[str, object],
    owner_id: str,
) -> tuple[tuple[ModVersionConstraint, ...], tuple[ModVersionConstraint, ...]]:
    entries: object = ()
    for candidate_owner, candidate_entries in dependencies.items():
        if _clean_id(candidate_owner) == owner_id:
            entries = candidate_entries
            break

    required: list[ModVersionConstraint] = []
    conflicts: list[ModVersionConstraint] = []
    for dependency in _mapping_entries(entries):
        dependency_id = _clean_id(dependency.get("modId"))
        if dependency_id is None:
            continue
        constraint = ModVersionConstraint(dependency_id, _version_ranges(dependency.get("versionRange")))
        dependency_type = _clean_text(dependency.get("type"))
        if dependency_type is not None and dependency_type.casefold() == "incompatible":
            conflicts.append(constraint)
        elif dependency.get("mandatory") is True or (
            dependency_type is not None and dependency_type.casefold() == "required"
        ):
            required.append(constraint)
    return _sorted_constraints(required), _sorted_constraints(conflicts)


def _quilt_dependency_constraints(
    value: object,
    *,
    alternatives_are_required: bool,
) -> tuple[ModVersionConstraint, ...]:
    found: list[ModVersionConstraint] = []
    entries = value if isinstance(value, list) else (value,)
    for entry in entries:
        if isinstance(entry, str):
            dependency_id = _clean_id(entry)
            if dependency_id is not None:
                found.append(ModVersionConstraint(dependency_id))
            continue
        dependency = _as_mapping(entry)
        if dependency is None or dependency.get("optional") is True:
            continue
        dependency_id = _clean_id(dependency.get("id"))
        if dependency_id is not None:
            versions = dependency.get("versions", dependency.get("version"))
            found.append(ModVersionConstraint(dependency_id, _version_ranges(versions)))
        found.extend(
            _quilt_dependency_constraints(
                dependency.get("all"),
                alternatives_are_required=alternatives_are_required,
            )
        )
        if alternatives_are_required:
            found.extend(_quilt_dependency_constraints(dependency.get("any"), alternatives_are_required=True))
    return _sorted_constraints(found)


def _legacy_dependency_constraint(value: str, *, require_prefix: bool) -> ModVersionConstraint | None:
    dependency = value.strip()
    prefix = ""
    if ":" in dependency:
        prefix, dependency = dependency.split(":", 1)
        prefix = prefix.casefold()
    if require_prefix and prefix not in {"required-after", "required-before"}:
        return None
    dependency_id, separator, version_range = dependency.partition("@")
    cleaned_id = _clean_id(dependency_id)
    if cleaned_id is None:
        return None
    return ModVersionConstraint(cleaned_id, _version_ranges(version_range if separator else None))


def normalize_mod_loader(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "fabric_loader": "fabric",
        "fabricloader": "fabric",
        "quilt_loader": "quilt",
        "quiltloader": "quilt",
        "neo_forge": "neoforge",
    }
    return aliases.get(normalized, normalized or None)


def _mapping_constraints(value: object) -> tuple[ModVersionConstraint, ...]:
    mapping = _as_mapping(value)
    if mapping is None:
        return ()
    return _sorted_constraints(
        ModVersionConstraint(dependency_id, _version_ranges(version_range))
        for raw_id, version_range in mapping.items()
        if (dependency_id := _clean_id(raw_id)) is not None
    )


def _id_constraints(value: object) -> tuple[ModVersionConstraint, ...]:
    return tuple(ModVersionConstraint(mod_id) for mod_id in _ids(value))


def _version_ranges(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, list) else (value,)
    return tuple(
        sorted(
            {
                cleaned
                for item in values
                if isinstance(item, str) and (cleaned := item.strip()) not in {"", "*"}
            },
            key=lambda item: (item.casefold(), item),
        )
    )


def _merge_constraints(*groups: Iterable[ModVersionConstraint]) -> tuple[ModVersionConstraint, ...]:
    return _sorted_constraints(constraint for group in groups for constraint in group)


def _sorted_constraints(values: Iterable[ModVersionConstraint]) -> tuple[ModVersionConstraint, ...]:
    grouped: dict[str, set[str] | None] = {}
    for constraint in values:
        current = grouped.get(constraint.mod_id)
        if current is None and constraint.mod_id in grouped:
            continue
        if not constraint.ranges:
            grouped[constraint.mod_id] = None
            continue
        if current is None:
            current = set()
            grouped[constraint.mod_id] = current
        current.update(constraint.ranges)
    return tuple(
        ModVersionConstraint(
            mod_id,
            tuple(sorted(grouped[mod_id] or (), key=lambda item: (item.casefold(), item))),
        )
        for mod_id in sorted(grouped)
    )


def _ids(value: object) -> tuple[str, ...]:
    found: list[str] = []
    entries = value if isinstance(value, list) else (value,)
    for entry in entries:
        if isinstance(entry, str):
            candidate = _clean_id(entry)
        else:
            mapping = _as_mapping(entry)
            candidate = _clean_id(mapping.get("id")) if mapping is not None else None
        if candidate is not None:
            found.append(candidate)
    return _sorted_ids(found)


def _strings(value: object) -> tuple[str, ...]:
    entries = value if isinstance(value, list) else (value,)
    return tuple(entry for entry in entries if isinstance(entry, str))


def _mapping_entries(value: object) -> tuple[Mapping[str, object], ...]:
    entries = value if isinstance(value, list) else (value,)
    return tuple(mapping for entry in entries if (mapping := _as_mapping(entry)) is not None)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def _clean_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    if not cleaned or any(
        not (character.isascii() and (character.isalnum() or character in "_.-"))
        for character in cleaned
    ):
        return None
    return cleaned


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _sorted_ids(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(sorted({cleaned for value in values if (cleaned := _clean_id(value)) is not None}))


__all__ = [
    "DEFAULT_FILE_HASH_CHUNK_SIZE",
    "DEFAULT_MAX_MOD_METADATA_BYTES",
    "ModDescriptor",
    "ModIdentityService",
    "ModInspectionErrorKind",
    "ModJarInspection",
    "ModMatch",
    "ModMatchKind",
    "ModVersionConstraint",
    "compute_file_hash",
    "inspect_mod_jar",
    "normalize_file_hash",
    "normalize_mod_loader",
    "verify_file_hash",
]
