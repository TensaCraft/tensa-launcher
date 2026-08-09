from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from launcher.application.mod_compatibility import (
    ModCompatibilityIssue,
    ModCompatibilityIssueKind,
    ModCompatibilityReport,
)
from launcher.pages.mod_diagnostics import ModDiagnosticsController


def test_mod_diagnostics_derives_technical_loader_from_version(fake_app) -> None:
    version = type("Version", (), {"client": "TensaCraft", "loader": "neoforge-21.1.232"})()
    controller = ModDiagnosticsController(fake_app, version, lambda: Path("instance"))

    assert controller._loader() == "neoforge"


def test_mod_diagnostics_formats_dependency_with_ids(fake_app) -> None:
    version = type("Version", (), {"client": "Fabric", "loader": "fabric-loader-0.17.2"})()
    controller = ModDiagnosticsController(fake_app, version, lambda: Path("instance"))
    issue = ModCompatibilityIssue(
        kind=ModCompatibilityIssueKind.MISSING_REQUIRED_DEPENDENCY,
        paths=(Path("glowtone.jar"),),
        mod_ids=("glowtone", "fabric-api"),
    )

    assert controller._issue_text(issue) == (
        "mod_diagnostics_missing_dependency (mod=glowtone, related=fabric-api, expected=, actual=)"
    )


def test_mod_diagnostics_formats_incompatible_dependency_version(fake_app) -> None:
    controller = ModDiagnosticsController(fake_app, SimpleNamespace(loader="fabric"), lambda: Path("."))
    issue = ModCompatibilityIssue(
        kind=ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION,
        paths=(Path("api.jar"), Path("owner.jar")),
        mod_ids=("owner", "api"),
        version_ranges=(">=2", "<3"),
        installed_versions=("1.9",),
    )

    assert controller._issue_text(issue) == (
        "mod_diagnostics_incompatible_dependency_version "
        "(mod=owner, related=api, expected=>=2, <3, actual=1.9)"
    )


def test_mod_diagnostics_ignores_scan_result_after_dispose(fake_app, monkeypatch) -> None:
    scheduled = []
    shown: list[ModCompatibilityReport] = []
    report = ModCompatibilityReport(loader="fabric", jars=(), descriptors=(), issues=())
    fake_app.page.run_task = lambda task, *args: scheduled.append((task, args)) or object()

    async def return_report(*_args, **_kwargs):
        return report

    monkeypatch.setattr("launcher.pages.mod_diagnostics.run_blocking", return_report)
    version = type("Version", (), {"client": "Fabric", "loader": "fabric-loader"})()
    controller = ModDiagnosticsController(fake_app, version, lambda: Path("instance"))
    monkeypatch.setattr(controller, "_show_report", shown.append)

    controller.scan()
    controller.dispose()
    task, args = next(item for item in scheduled if item[0] == controller._scan_async)
    asyncio.run(task(*args))

    assert shown == []
    assert controller.dialog is None
    assert controller.button.disabled is False


def test_mod_diagnostics_ignores_scan_error_after_dispose(fake_app, monkeypatch) -> None:
    scheduled = []
    warnings: list[str] = []
    fake_app.page.run_task = lambda task, *args: scheduled.append((task, args)) or object()
    fake_app.feedback.warning = lambda message, **_kwargs: warnings.append(message)

    async def fail_scan(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr("launcher.pages.mod_diagnostics.run_blocking", fail_scan)
    version = type("Version", (), {"client": "Fabric", "loader": "fabric-loader"})()
    controller = ModDiagnosticsController(fake_app, version, lambda: Path("instance"))

    controller.scan()
    controller.dispose()
    task, args = next(item for item in scheduled if item[0] == controller._scan_async)
    asyncio.run(task(*args))

    assert warnings == []
