from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT_DIR = Path(__file__).resolve().parents[1]


def _project_metadata() -> dict[str, Any]:
    with (ROOT_DIR / "pyproject.toml").open("rb") as fh:
        return cast(dict[str, Any], tomllib.load(fh)["project"])


def _parse_pinned_requirements(requirements: list[str]) -> dict[str, Version]:
    dependencies: dict[str, Version] = {}
    for value in requirements:
        requirement = Requirement(value)
        assert requirement.url is None, f"Use a stable index release: {value}"
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1, f"Expected one exact version pin: {value}"
        pin = specifiers[0]
        assert pin.operator == "==" and "*" not in pin.version, f"Expected an exact version pin: {value}"
        version = Version(pin.version)
        assert not version.is_prerelease and not version.is_devrelease and version.local is None, value
        name = canonicalize_name(requirement.name)
        assert name not in dependencies, f"Duplicate dependency: {name}"
        dependencies[name] = version
    return dependencies


def _project_dependencies() -> dict[str, Version]:
    project = _project_metadata()

    return _parse_pinned_requirements(cast(list[str], project["dependencies"]))


def _project_optional_dependencies(group: str) -> dict[str, Version]:
    project = _project_metadata()
    optional = cast(dict[str, list[str]], project["optional-dependencies"])

    return _parse_pinned_requirements(optional[group])


def test_all_direct_dependencies_use_exact_stable_pins():
    project = _project_metadata()
    optional = cast(dict[str, list[str]], project["optional-dependencies"])
    for requirements in [project["dependencies"], *optional.values()]:
        assert _parse_pinned_requirements(requirements)


def test_runtime_dependencies_are_pinned_to_patched_security_releases():
    dependencies = _project_dependencies()

    assert dependencies["cryptography"] >= Version("50.0.1")
    assert dependencies["requests"] >= Version("2.34.2")


def test_flet_runtime_and_build_packages_use_the_same_version():
    runtime = _project_dependencies()
    build = _project_optional_dependencies("build")

    assert runtime["flet"] == runtime["flet-desktop"] == build["flet-cli"]
    assert runtime["flet"] >= Version("1.0.1")


def test_build_dependencies_are_pinned_to_patched_security_releases():
    for group in ("build", "dev"):
        dependencies = _project_optional_dependencies(group)

        assert dependencies["pillow"] >= Version("12.3.0")


def test_shortcut_icon_dependency_is_explicit_and_matches_build_and_dev():
    runtime = _project_dependencies()
    build = _project_optional_dependencies("build")
    dev = _project_optional_dependencies("dev")

    assert "pillow" in runtime, "Shortcut icon conversion requires Pillow without optional extras"
    assert runtime["pillow"] == build["pillow"] == dev["pillow"]
    assert runtime["pillow"] >= Version("12.3.0")
    pillow = next(
        Requirement(value)
        for value in _project_metadata()["dependencies"]
        if canonicalize_name(Requirement(value).name) == "pillow"
    )
    assert pillow.marker is None, "Shortcut icons require Pillow on every supported platform"


def test_dependencies_shared_by_build_and_dev_use_the_same_version():
    build = _project_optional_dependencies("build")
    dev = _project_optional_dependencies("dev")

    for name in build.keys() & dev.keys():
        assert build[name] == dev[name], name


@pytest.mark.parametrize(
    ("group", "name", "minimum"),
    [("build", "pyinstaller", "6.22.3"), ("dev", "pyright", "1.1.414"), ("dev", "ruff", "0.16.8")],
)
def test_updated_tools_do_not_regress(group: str, name: str, minimum: str):
    assert _project_optional_dependencies(group)[name] >= Version(minimum)


@pytest.mark.parametrize(("platform_system", "expected"), [("Darwin", True), ("Windows", False), ("Linux", False)])
def test_avfoundation_remains_macos_only(platform_system: str, expected: bool):
    requirements = [Requirement(value) for value in _project_metadata()["dependencies"]]
    avfoundation = next(req for req in requirements if canonicalize_name(req.name) == "pyobjc-framework-avfoundation")
    environment = default_environment()
    environment["platform_system"] = platform_system

    assert avfoundation.marker is not None
    assert avfoundation.marker.evaluate(environment) is expected
