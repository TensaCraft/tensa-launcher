from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


class MigrationError(RuntimeError):
    def __init__(self, code: str, item: str | None = None):
        super().__init__(code)
        self.code = code
        self.item = item


@dataclass(frozen=True)
class MigrationIssue:
    code: str
    item: str | None = None


@dataclass(frozen=True)
class MigrationRoots:
    source_state: Path
    minecraft: Path
    target_state: Path
    install: Path
    recovery: Path


@dataclass(frozen=True)
class SourceSnapshot:
    config: JsonObject = field(repr=False)
    profiles: JsonObject = field(repr=False)
    versions: JsonObject = field(repr=False)
    key: bytes | None = field(repr=False)
    fingerprints: Mapping[Path, str | None]


@dataclass(frozen=True)
class BuildImport:
    source: Path
    target: Path
    metadata: JsonObject = field(repr=False)
    aliases: tuple[str, ...]
    copy_required: bool


@dataclass(frozen=True)
class AdaptedData:
    config: JsonObject = field(repr=False)
    profiles: JsonObject = field(repr=False)
    key: bytes | None = field(repr=False)
    builds: tuple[BuildImport, ...]
    warnings: tuple[MigrationIssue, ...]


@dataclass(frozen=True)
class MigrationPlan:
    roots: MigrationRoots
    snapshot: SourceSnapshot
    adapted: AdaptedData
    required_bytes: Mapping[Path, int]


@dataclass(frozen=True)
class MigrationAsset:
    url: str
    size: int
    sha256: str
    asset_id: int


@dataclass(frozen=True)
class MigrationProgress:
    phase: str
    completed: int
    total: int


@dataclass(frozen=True)
class MigrationResult:
    executable: Path
    build_aliases: Mapping[str, str]
    warnings: tuple[MigrationIssue, ...]
