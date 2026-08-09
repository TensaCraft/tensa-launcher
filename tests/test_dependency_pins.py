from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _project_metadata() -> dict[str, Any]:
    with (ROOT_DIR / "pyproject.toml").open("rb") as fh:
        return cast(dict[str, Any], tomllib.load(fh)["project"])


def _parse_pinned_requirements(requirements: list[str]) -> dict[str, tuple[int, ...]]:
    dependencies: dict[str, tuple[int, ...]] = {}
    for requirement in requirements:
        name, version = requirement.split("==", 1)
        dependencies[name] = _parse_version(version.split(";", 1)[0].strip())
    return dependencies


def _project_dependencies() -> dict[str, tuple[int, ...]]:
    project = _project_metadata()

    return _parse_pinned_requirements(cast(list[str], project["dependencies"]))


def _project_optional_dependencies(group: str) -> dict[str, tuple[int, ...]]:
    project = _project_metadata()
    optional = cast(dict[str, list[str]], project["optional-dependencies"])

    return _parse_pinned_requirements(optional[group])


def test_runtime_dependencies_are_pinned_to_patched_security_releases():
    dependencies = _project_dependencies()

    assert dependencies["cryptography"] >= (48, 0, 1)
    assert dependencies["requests"] >= (2, 33, 0)


def test_flet_runtime_and_build_packages_use_the_same_version():
    runtime = _project_dependencies()
    build = _project_optional_dependencies("build")

    assert runtime["flet"] == runtime["flet-desktop"] == build["flet-cli"]


def test_build_dependencies_are_pinned_to_patched_security_releases():
    for group in ("build", "dev"):
        dependencies = _project_optional_dependencies(group)

        assert dependencies["pillow"] >= (12, 2, 0)
