from __future__ import annotations

import json
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import launcher.application.mod_compatibility as mod_compatibility
import launcher.application.mod_identity as mod_identity
from launcher.application.mod_compatibility import (
    ModCompatibilityIssueKind,
    ModVersionConstraint,
    scan_mod_compatibility,
)


def _write_jar(path: Path, members: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _write_json_jar(path: Path, metadata_file: str, payload: object) -> Path:
    return _write_jar(path, {metadata_file: json.dumps(payload)})


def test_scans_every_supported_descriptor_without_guessing_unknown_fields(tmp_path: Path) -> None:
    _write_json_jar(
        tmp_path / "a-fabric.jar",
        "fabric.mod.json",
        {
            "id": "fabric_mod",
            "name": "Fabric Mod",
            "version": "1.2.3",
            "provides": ["fabric_alias"],
            "depends": {"minecraft": "*", "java": ">=17"},
            "breaks": {"absent_mod": "*"},
            "customDependencyShape": [{"id": "not_in_depends"}],
        },
    )
    _write_json_jar(
        tmp_path / "b-quilt.jar",
        "quilt.mod.json",
        {
            "quilt_loader": {
                "id": "quilt_mod",
                "version": "2.0.0",
                "metadata": {"name": "Quilt Mod"},
                "provides": [{"id": "quilt_alias", "version": "2.0.0"}],
                "depends": [
                    {"id": "quilt_loader"},
                    {"id": "optional_mod", "optional": True},
                    {"any": [{"id": "alternative_a"}, {"id": "alternative_b"}]},
                ],
                "breaks": [{"id": "absent_quilt_conflict"}],
            }
        },
    )
    _write_jar(
        tmp_path / "c-forge.jar",
        {
            "META-INF/mods.toml": """
modLoader = "javafml"
loaderVersion = "[1,)"

[[mods]]
modId = "forge_mod"
version = "3.0.0"
displayName = "Forge Mod"
provides = ["forge_alias"]

[[mods]]
modId = "forge_companion"
version = "3.0.1"
displayName = "Forge Companion"

[[dependencies.forge_mod]]
modId = "forge"
mandatory = true

[[dependencies.forge_mod]]
modId = "optional_forge_mod"
mandatory = false
""",
        },
    )
    _write_jar(
        tmp_path / "d-neoforge.jar",
        {
            "META-INF/neoforge.mods.toml": """
modLoader = "javafml"
loaderVersion = "[1,)"

[[mods]]
modId = "neoforge_mod"
version = "4.0.0"
displayName = "NeoForge Mod"

[[dependencies.neoforge_mod]]
modId = "neoforge"
type = "required"

[[dependencies.neoforge_mod]]
modId = "absent_neoforge_conflict"
type = "incompatible"
""",
        },
    )
    _write_json_jar(
        tmp_path / "e-legacy.jar",
        "mcmod.info",
        [
            {
                "modid": "legacy_mod",
                "name": "Legacy Mod",
                "version": "5.0.0",
                "aliases": ["legacy_alias"],
                "requiredMods": ["forge@[14.0,)"],
                "dependencies": ["required-after:java@[8,)", "after:optional_legacy_mod"],
            }
        ],
    )

    report = scan_mod_compatibility(tmp_path, loader=None)

    assert report.loader is None
    assert [descriptor.mod_id for descriptor in report.descriptors] == [
        "fabric_mod",
        "quilt_mod",
        "forge_mod",
        "forge_companion",
        "neoforge_mod",
        "legacy_mod",
    ]
    by_id = {descriptor.mod_id: descriptor for descriptor in report.descriptors}
    assert by_id["fabric_mod"].name == "Fabric Mod"
    assert by_id["fabric_mod"].version == "1.2.3"
    assert by_id["fabric_mod"].provides == ("fabric_alias",)
    assert by_id["fabric_mod"].required_dependencies == ("java", "minecraft")
    assert by_id["fabric_mod"].conflicts == ("absent_mod",)
    assert by_id["fabric_mod"].required_dependency_constraints == (
        ModVersionConstraint("java", (">=17",)),
        ModVersionConstraint("minecraft"),
    )
    assert by_id["fabric_mod"].conflict_constraints == (ModVersionConstraint("absent_mod"),)
    assert by_id["quilt_mod"].loader == "quilt"
    assert by_id["quilt_mod"].provides == ("quilt_alias",)
    assert by_id["quilt_mod"].required_dependencies == ("quilt_loader",)
    assert by_id["forge_mod"].metadata_file == "META-INF/mods.toml"
    assert by_id["forge_mod"].required_dependencies == ("forge",)
    assert by_id["forge_companion"].required_dependencies == ()
    assert by_id["neoforge_mod"].loader == "neoforge"
    assert by_id["neoforge_mod"].required_dependencies == ("neoforge",)
    assert by_id["neoforge_mod"].conflicts == ("absent_neoforge_conflict",)
    assert by_id["legacy_mod"].provides == ("legacy_alias",)
    assert by_id["legacy_mod"].required_dependencies == ("forge", "java")
    assert report.issues == ()
    assert report.compatible


def test_fabric_checks_required_ranges_and_only_matching_scoped_conflicts(tmp_path: Path) -> None:
    owner = tmp_path / "owner.jar"
    dependency = tmp_path / "api.jar"
    conflict = tmp_path / "conflict.jar"
    _write_json_jar(
        owner,
        "fabric.mod.json",
        {
            "id": "owner",
            "version": "1",
            "depends": {"api": [">=2 <3", "^4"]},
            "breaks": {"bad_mod": ">=2"},
        },
    )
    _write_json_jar(dependency, "fabric.mod.json", {"id": "api", "version": "1.9"})
    _write_json_jar(conflict, "fabric.mod.json", {"id": "bad_mod", "version": "1.5"})

    incompatible = scan_mod_compatibility(tmp_path, loader="fabric")

    assert [(issue.kind, issue.mod_ids) for issue in incompatible.issues] == [
        (ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION, ("owner", "api"))
    ]
    issue = incompatible.issues[0]
    assert issue.version_ranges == (">=2 <3", "^4")
    assert issue.installed_versions == ("1.9",)
    assert issue.paths == (dependency, owner)

    dependency.unlink()
    conflict.unlink()
    _write_json_jar(dependency, "fabric.mod.json", {"id": "api", "version": "4.3+build.7"})
    _write_json_jar(conflict, "fabric.mod.json", {"id": "bad_mod", "version": "2.0"})

    compatible_dependency = scan_mod_compatibility(tmp_path, loader="fabric")

    assert [(issue.kind, issue.mod_ids) for issue in compatible_dependency.issues] == [
        (ModCompatibilityIssueKind.DECLARED_CONFLICT, ("owner", "bad_mod"))
    ]
    conflict_issue = compatible_dependency.issues[0]
    assert conflict_issue.version_ranges == (">=2",)
    assert conflict_issue.installed_versions == ("2.0",)


def test_fabric_caret_range_stays_within_declared_major(tmp_path: Path) -> None:
    _write_json_jar(
        tmp_path / "owner.jar",
        "fabric.mod.json",
        {"id": "owner", "version": "1", "depends": {"api": "^0.2.0"}},
    )
    _write_json_jar(
        tmp_path / "api.jar",
        "fabric.mod.json",
        {"id": "api", "version": "1.0.0"},
    )

    report = scan_mod_compatibility(tmp_path, loader="fabric")

    assert [(issue.kind, issue.mod_ids) for issue in report.issues] == [
        (ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION, ("owner", "api"))
    ]


def test_quilt_preserves_versions_and_validates_dependency_and_break_ranges(tmp_path: Path) -> None:
    _write_json_jar(
        tmp_path / "owner.jar",
        "quilt.mod.json",
        {
            "quilt_loader": {
                "id": "owner",
                "version": "1",
                "depends": [{"id": "api", "versions": [">=2 <3"]}],
                "breaks": [{"id": "bad_mod", "versions": "[4,5)"}],
            }
        },
    )
    api = _write_json_jar(
        tmp_path / "api.jar",
        "quilt.mod.json",
        {"quilt_loader": {"id": "api", "version": "2.5"}},
    )
    bad = _write_json_jar(
        tmp_path / "bad.jar",
        "quilt.mod.json",
        {"quilt_loader": {"id": "bad_mod", "version": "3.9"}},
    )

    report = scan_mod_compatibility(tmp_path, loader="quilt")

    owner = next(descriptor for descriptor in report.descriptors if descriptor.mod_id == "owner")
    assert owner.required_dependency_constraints == (ModVersionConstraint("api", (">=2 <3",)),)
    assert owner.conflict_constraints == (ModVersionConstraint("bad_mod", ("[4,5)",)),)
    assert report.issues == ()

    api.unlink()
    bad.unlink()
    _write_json_jar(
        api,
        "quilt.mod.json",
        {"quilt_loader": {"id": "api", "version": "3.0"}},
    )
    _write_json_jar(
        bad,
        "quilt.mod.json",
        {"quilt_loader": {"id": "bad_mod", "version": "4.2"}},
    )

    incompatible = scan_mod_compatibility(tmp_path, loader="quilt")

    assert [(issue.kind, issue.mod_ids) for issue in incompatible.issues] == [
        (ModCompatibilityIssueKind.DECLARED_CONFLICT, ("owner", "bad_mod")),
        (ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION, ("owner", "api")),
    ]


def test_forge_checks_maven_required_dependency_range(tmp_path: Path) -> None:
    _write_jar(
        tmp_path / "owner.jar",
        {
            "META-INF/mods.toml": """
[[mods]]
modId = "owner"
version = "1"

[[dependencies.owner]]
modId = "api"
mandatory = true
versionRange = "[2,3)"
""",
        },
    )
    api = _write_jar(
        tmp_path / "api.jar",
        {"META-INF/mods.toml": '[[mods]]\nmodId = "api"\nversion = "3.0"\n'},
    )

    incompatible = scan_mod_compatibility(tmp_path, loader="forge")

    assert [(issue.kind, issue.mod_ids) for issue in incompatible.issues] == [
        (ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION, ("owner", "api"))
    ]
    assert incompatible.issues[0].version_ranges == ("[2,3)",)
    assert incompatible.issues[0].installed_versions == ("3.0",)

    api.unlink()
    _write_jar(
        api,
        {"META-INF/mods.toml": '[[mods]]\nmodId = "api"\nversion = "2.4"\n'},
    )

    assert scan_mod_compatibility(tmp_path, loader="forge").issues == ()


def test_neoforge_scoped_incompatible_dependency_only_matches_declared_range(tmp_path: Path) -> None:
    _write_jar(
        tmp_path / "owner.jar",
        {
            "META-INF/neoforge.mods.toml": """
[[mods]]
modId = "owner"
version = "1"

[[dependencies.owner]]
modId = "bad_mod"
type = "incompatible"
versionRange = "[2,3)"
""",
        },
    )
    bad = _write_jar(
        tmp_path / "bad.jar",
        {"META-INF/neoforge.mods.toml": '[[mods]]\nmodId = "bad_mod"\nversion = "1.9"\n'},
    )

    assert scan_mod_compatibility(tmp_path, loader="neoforge").issues == ()

    bad.unlink()
    _write_jar(
        bad,
        {"META-INF/neoforge.mods.toml": '[[mods]]\nmodId = "bad_mod"\nversion = "2.0"\n'},
    )

    conflict = scan_mod_compatibility(tmp_path, loader="neoforge")

    assert [(issue.kind, issue.mod_ids) for issue in conflict.issues] == [
        (ModCompatibilityIssueKind.DECLARED_CONFLICT, ("owner", "bad_mod"))
    ]
    assert conflict.issues[0].version_ranges == ("[2,3)",)
    assert conflict.issues[0].installed_versions == ("2.0",)


def test_reports_deterministic_compatibility_findings(tmp_path: Path) -> None:
    main = _write_json_jar(
        tmp_path / "B-main.jar",
        "fabric.mod.json",
        {
            "id": "main_mod",
            "version": "1",
            "provides": ["shared_api"],
            "depends": {
                "missing_mod": "*",
                "minecraft": "*",
                "java": "*",
                "fabricloader": "*",
                "quilt_loader": "*",
                "forge": "*",
                "neoforge": "*",
            },
            "conflicts": {"bad_mod": "*"},
        },
    )
    duplicate = _write_json_jar(
        tmp_path / "a-duplicate.jar",
        "fabric.mod.json",
        {"id": "shared_api", "version": "1"},
    )
    bad = _write_json_jar(
        tmp_path / "c-bad.jar",
        "fabric.mod.json",
        {"id": "bad_mod", "version": "1"},
    )
    wrong = _write_jar(
        tmp_path / "d-wrong.jar",
        {
            "META-INF/neoforge.mods.toml": """
modLoader = "javafml"
loaderVersion = "[1,)"
[[mods]]
modId = "wrong_loader_mod"
version = "1"
""",
        },
    )
    corrupt = tmp_path / "e-corrupt.jar"
    corrupt.write_bytes(b"not a zip archive")
    (tmp_path / "ignored.jar.disabled").write_bytes(b"not a zip archive")

    report = scan_mod_compatibility(tmp_path, loader="Quilt Loader")

    assert report.jars == (duplicate, main, bad, wrong, corrupt)
    assert [(issue.kind, issue.mod_ids) for issue in report.issues] == [
        (ModCompatibilityIssueKind.CORRUPT_JAR, ()),
        (ModCompatibilityIssueKind.DECLARED_CONFLICT, ("main_mod", "bad_mod")),
        (ModCompatibilityIssueKind.DUPLICATE_ID, ("shared_api",)),
        (ModCompatibilityIssueKind.MISSING_REQUIRED_DEPENDENCY, ("main_mod", "missing_mod")),
        (ModCompatibilityIssueKind.WRONG_LOADER, ("wrong_loader_mod",)),
    ]

    conflict = report.issues[1]
    assert conflict.paths == (main, bad)
    duplicate_issue = report.issues[2]
    assert duplicate_issue.paths == (duplicate, main)
    wrong_loader = report.issues[-1]
    assert wrong_loader.expected_loader == "quilt"
    assert wrong_loader.descriptor_loader == "neoforge"
    assert not report.compatible


def test_quilt_all_dependencies_are_required_but_any_alternatives_are_not_guessed(tmp_path: Path) -> None:
    _write_json_jar(
        tmp_path / "quilt.jar",
        "quilt.mod.json",
        {
            "quilt_loader": {
                "id": "quilt_mod",
                "version": "1",
                "depends": [
                    {"all": [{"id": "required_a"}, {"id": "required_b"}]},
                    {"any": [{"id": "alternative_a"}, {"id": "alternative_b"}]},
                    {"id": "optional_mod", "optional": True},
                ],
            }
        },
    )
    _write_json_jar(
        tmp_path / "provider.jar",
        "fabric.mod.json",
        {"id": "provider", "version": "1", "provides": ["required_a"]},
    )

    report = scan_mod_compatibility(tmp_path, loader="quilt")

    missing = [issue for issue in report.issues if issue.kind is ModCompatibilityIssueKind.MISSING_REQUIRED_DEPENDENCY]
    assert [(issue.mod_ids, issue.paths[0].name) for issue in missing] == [(("quilt_mod", "required_b"), "quilt.jar")]
    assert all(issue.kind is not ModCompatibilityIssueKind.WRONG_LOADER for issue in report.issues)


def test_neoforge_accepts_legacy_mods_toml_but_forge_rejects_neoforge_descriptor(tmp_path: Path) -> None:
    legacy = _write_jar(
        tmp_path / "legacy-neoforge.jar",
        {
            "META-INF/mods.toml": """
modLoader = "javafml"
loaderVersion = "[1,)"
[[mods]]
modId = "legacy_neoforge"
version = "1"
""",
        },
    )

    neoforge_report = scan_mod_compatibility(tmp_path, loader="neoforge")

    assert neoforge_report.descriptors[0].path == legacy
    assert neoforge_report.descriptors[0].loader == "forge"
    assert all(issue.kind is not ModCompatibilityIssueKind.WRONG_LOADER for issue in neoforge_report.issues)

    legacy.unlink()
    modern = _write_jar(
        tmp_path / "modern-neoforge.jar",
        {
            "META-INF/neoforge.mods.toml": """
modLoader = "javafml"
loaderVersion = "[1,)"
[[mods]]
modId = "modern_neoforge"
version = "1"
""",
        },
    )

    forge_report = scan_mod_compatibility(tmp_path, loader="forge")

    assert forge_report.issues == (
        mod_compatibility.ModCompatibilityIssue(
            kind=ModCompatibilityIssueKind.WRONG_LOADER,
            paths=(modern,),
            mod_ids=("modern_neoforge",),
            metadata_file="META-INF/neoforge.mods.toml",
            expected_loader="forge",
            descriptor_loader="neoforge",
        ),
    )


def test_reports_corrupt_missing_invalid_oversized_and_unreadable_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = _write_jar(tmp_path / "a-oversized.jar", {"fabric.mod.json": b"x" * 65})
    invalid = _write_jar(tmp_path / "b-invalid.jar", {"fabric.mod.json": "{"})
    missing = _write_jar(tmp_path / "c-missing.jar", {"example.txt": "not metadata"})
    corrupt = tmp_path / "d-corrupt.jar"
    corrupt.write_bytes(b"not a zip archive")
    unreadable = _write_json_jar(
        tmp_path / "e-unreadable.jar",
        "fabric.mod.json",
        {"id": "unreadable_mod", "version": "1"},
    )
    original_zip_file = mod_identity.zipfile.ZipFile

    def open_archive(file: Any, *args: Any, **kwargs: Any) -> zipfile.ZipFile:
        if Path(str(file)) == unreadable:
            raise OSError("simulated read failure")
        return original_zip_file(file, *args, **kwargs)

    monkeypatch.setattr(mod_identity.zipfile, "ZipFile", open_archive)

    report = scan_mod_compatibility(tmp_path, loader="fabric", max_metadata_bytes=64)

    assert report.descriptors == ()
    assert [(issue.kind, issue.paths[0]) for issue in report.issues] == [
        (ModCompatibilityIssueKind.CORRUPT_JAR, corrupt),
        (ModCompatibilityIssueKind.INVALID_METADATA, invalid),
        (ModCompatibilityIssueKind.METADATA_TOO_LARGE, oversized),
        (ModCompatibilityIssueKind.MISSING_DESCRIPTOR, missing),
        (ModCompatibilityIssueKind.UNREADABLE_JAR, unreadable),
    ]


def test_scan_is_read_only_and_results_are_immutable(tmp_path: Path) -> None:
    jar = _write_json_jar(
        tmp_path / "mod.jar",
        "fabric.mod.json",
        {"id": "read_only_mod", "name": "Read Only", "version": "1"},
    )
    before = jar.read_bytes()

    report = scan_mod_compatibility(tmp_path, loader="fabric")

    assert jar.read_bytes() == before
    assert tuple(path.name for path in tmp_path.iterdir()) == ("mod.jar",)
    with pytest.raises(FrozenInstanceError):
        setattr(report, "loader", "forge")
    with pytest.raises(FrozenInstanceError):
        setattr(report.descriptors[0], "mod_id", "changed")


def test_rejects_non_positive_metadata_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        scan_mod_compatibility(tmp_path, loader="fabric", max_metadata_bytes=0)


def test_compatibility_reexports_shared_descriptor_types() -> None:
    assert mod_compatibility.ModDescriptor is mod_identity.ModDescriptor
    assert mod_compatibility.ModVersionConstraint is mod_identity.ModVersionConstraint
