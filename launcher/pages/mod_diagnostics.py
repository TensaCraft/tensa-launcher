from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import flet as ft

from launcher import ui
from launcher.application.mod_compatibility import (
    ModCompatibilityIssue,
    ModCompatibilityIssueKind,
    ModCompatibilityReport,
    scan_mod_compatibility,
)
from launcher.ui.core.page_runtime import run_blocking, run_task, schedule_update

_ISSUE_KEYS = {
    ModCompatibilityIssueKind.CORRUPT_JAR: "mod_diagnostics_corrupt_jar",
    ModCompatibilityIssueKind.UNREADABLE_JAR: "mod_diagnostics_unreadable_jar",
    ModCompatibilityIssueKind.MISSING_DESCRIPTOR: "mod_diagnostics_missing_descriptor",
    ModCompatibilityIssueKind.METADATA_TOO_LARGE: "mod_diagnostics_metadata_too_large",
    ModCompatibilityIssueKind.INVALID_METADATA: "mod_diagnostics_invalid_metadata",
    ModCompatibilityIssueKind.DUPLICATE_ID: "mod_diagnostics_duplicate_id",
    ModCompatibilityIssueKind.MISSING_REQUIRED_DEPENDENCY: "mod_diagnostics_missing_dependency",
    ModCompatibilityIssueKind.INCOMPATIBLE_REQUIRED_DEPENDENCY_VERSION: (
        "mod_diagnostics_incompatible_dependency_version"
    ),
    ModCompatibilityIssueKind.DECLARED_CONFLICT: "mod_diagnostics_declared_conflict",
    ModCompatibilityIssueKind.WRONG_LOADER: "mod_diagnostics_wrong_loader",
}


class ModDiagnosticsController:
    def __init__(self, app, version, version_root: Callable[[], Path]) -> None:
        self.app = app
        self.page = app.page
        self.version = version
        self.version_root = version_root
        self.dialog: ft.AlertDialog | None = None
        self._scan_generation = 0
        self._disposed = False
        self.button = ui.Button(
            text=self.app.trans("mod_diagnostics_scan"),
            icon=ft.Icons.FACT_CHECK_OUTLINED,
            variant="outline",
            tone="neutral",
            width=None,
            on_click=lambda _event: self.scan(),
        )

    def scan(self) -> None:
        if self._disposed or self.button.disabled:
            return
        self._scan_generation += 1
        generation = self._scan_generation
        self._set_busy(True)
        try:
            run_task(self.page, self._scan_async, generation)
        except Exception as exc:
            if self._is_active(generation):
                self._warn(exc)
                self._set_busy(False)

    async def _scan_async(self, generation: int) -> None:
        try:
            report = await run_blocking(
                scan_mod_compatibility,
                self.version_root() / "mods",
                self._loader(),
            )
        except Exception as exc:
            if self._is_active(generation):
                self._warn(exc)
        else:
            if self._is_active(generation):
                self._show_report(report)
        finally:
            if self._is_active(generation):
                self._set_busy(False)

    def _is_active(self, generation: int) -> bool:
        return not self._disposed and generation == self._scan_generation

    def _warn(self, error: Exception) -> None:
        self.app.feedback.warning(
            self.app.trans("mod_diagnostics_failed", error=str(error)),
            allow_report=False,
        )

    def _show_report(self, report: ModCompatibilityReport) -> None:
        issue_rows = [self._issue_row(issue) for issue in report.issues]
        if not issue_rows:
            issue_rows = [
                ui.Text(
                    self.app.trans("mod_diagnostics_compatible"),
                    color=self.app.theme.success,
                    weight=self.app.theme.font_weight_semibold,
                )
            ]
        summary = self.app.trans(
            "mod_diagnostics_summary",
            mods=len(report.descriptors),
            issues=len(report.issues),
        )
        self.dialog = ui.AlertDialog(
            modal=True,
            title=ui.Text(
                self.app.trans("mod_diagnostics_title"),
                size=self.app.theme.text_size_xl,
                weight=self.app.theme.font_weight_semibold,
            ),
            content=ui.Container(
                width=680,
                content=ui.Column(
                    controls=[
                        ui.Text(summary, color=self.app.theme.text_secondary),
                        *issue_rows,
                    ],
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ui.Button(
                    text=self.app.trans("close"),
                    variant="ghost",
                    tone="neutral",
                    on_click=lambda _event: self.close(),
                )
            ],
        )
        ui.show_dialog(self.page, self.dialog)
        schedule_update(self.page)

    def _issue_row(self, issue: ModCompatibilityIssue) -> ft.Control:
        message = self._issue_text(issue)
        files = ", ".join(path.name for path in issue.paths)
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Icon(ft.Icons.ERROR_OUTLINE, color=self.app.theme.error, size=18),
                    ui.Column(
                        controls=[
                            ui.Text(
                                message,
                                color=self.app.theme.text_color,
                                weight=self.app.theme.font_weight_semibold,
                            ),
                            ui.Text(files, color=self.app.theme.text_secondary, size=self.app.theme.text_size_sm),
                        ],
                        spacing=4,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            border=ft.Border.only(bottom=ft.BorderSide(1, self.app.theme.border_color)),
            padding=ft.Padding.only(bottom=10),
        )

    def _issue_text(self, issue: ModCompatibilityIssue) -> str:
        key = _ISSUE_KEYS[issue.kind]
        owner = issue.mod_ids[0] if issue.mod_ids else ""
        related = issue.mod_ids[1] if len(issue.mod_ids) > 1 else ""
        return str(
            self.app.trans(
                key,
                mod=owner,
                related=related,
                expected=issue.expected_loader or ", ".join(issue.version_ranges),
                actual=issue.descriptor_loader or ", ".join(issue.installed_versions),
            )
        )

    def _loader(self) -> str | None:
        raw = f"{getattr(self.version, 'client', '')} {getattr(self.version, 'loader', '')}".lower()
        for loader in ("neoforge", "fabric", "quilt", "forge"):
            if loader in raw:
                return loader
        return None

    def _set_busy(self, busy: bool) -> None:
        self.button.disabled = busy
        self.button.content = self.app.trans("mod_diagnostics_scanning" if busy else "mod_diagnostics_scan")
        if not self._disposed:
            schedule_update(self.page)

    def close(self) -> None:
        if self.dialog is not None:
            ui.close_dialog(self.page, self.dialog)
            self.dialog = None
        if not self._disposed:
            schedule_update(self.page)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._scan_generation += 1
        self.button.disabled = False
        self.button.content = self.app.trans("mod_diagnostics_scan")
        if self.dialog is not None:
            ui.close_dialog(self.page, self.dialog)
            self.dialog = None


__all__ = ["ModDiagnosticsController"]
