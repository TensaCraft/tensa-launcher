from __future__ import annotations

import flet as ft

from launcher import ui
from launcher.application.modrinth_mods import (
    ModrinthDependencyIssue,
    ModrinthDependencyPlan,
    ModrinthInstallCandidate,
)
from launcher.ui.core.page_runtime import schedule_update, show_dialog


class ModsManagerDependencyDialogMixin:
    def _show_modrinth_dependency_plan_dialog(self, plan: ModrinthDependencyPlan, context: dict):
        theme = self.app.theme
        main_name = plan.main.title if plan.main is not None else self.trans("modrinth_content_tab")
        optional_options: list[tuple[ModrinthInstallCandidate, ft.Checkbox]] = []
        content_controls: list[ft.Control] = [
            ui.Text(
                self.trans("modrinth_dependencies_message", name=main_name),
                color=theme.text_secondary,
                size=theme.text_size_sm,
            )
        ]

        candidate_sections = (
            ("modrinth_dependencies_to_install", plan.dependencies_to_install, False),
            ("modrinth_dependencies_to_replace", plan.dependencies_to_replace, True),
            ("modrinth_dependencies_satisfied", plan.already_satisfied, False),
        )
        for title_key, candidates, replace in candidate_sections:
            if candidates:
                content_controls.extend(
                    self._modrinth_dependency_candidate_section(title_key, candidates, replace=replace)
                )
        if plan.optional_dependencies:
            content_controls.extend(
                self._modrinth_optional_dependency_section(
                    plan.optional_dependencies,
                    optional_options,
                )
            )
        issue_sections = (
            ("modrinth_dependencies_optional_unavailable", plan.optional_dependency_issues),
            ("modrinth_dependencies_embedded", plan.skipped_embedded),
            ("modrinth_dependencies_blocked", plan.blocking_issues),
        )
        for title_key, issues in issue_sections:
            if issues:
                content_controls.extend(self._modrinth_dependency_issue_section(title_key, issues))

        actions: list[ft.Control] = []
        if plan.can_install:
            actions.append(
                ui.Button(
                    text=self.trans("modrinth_dependencies_install"),
                    icon=ft.Icons.DOWNLOAD,
                    on_click=lambda _e: self._confirm_modrinth_dependency_plan(
                        plan,
                        context,
                        [
                            candidate
                            for candidate, checkbox in optional_options
                            if bool(getattr(checkbox, "value", False))
                        ],
                    ),
                )
            )
            close_text = self.trans("cancel")
        else:
            close_text = self.trans("close")
        actions.append(
            ui.Button(
                text=close_text,
                variant="outline",
                tone="neutral",
                on_click=lambda _e: self._close_modrinth_dependency_dialog(),
            )
        )

        self.modrinth_dependency_dialog = ui.AlertDialog(
            title=ui.Text(
                self.trans("modrinth_dependencies_title"),
                color=theme.text_color,
                weight=theme.font_weight_bold,
            ),
            modal=True,
            content=ui.Column(
                content_controls,
                width=theme.modal_width,
                height=min(theme.modal_height, 520),
                spacing=theme.spacing_md,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=actions,
        )
        show_dialog(self.page, self.modrinth_dependency_dialog)
        schedule_update(self.page)

    def _modrinth_dependency_candidate_section(
        self,
        title_key: str,
        candidates: list[ModrinthInstallCandidate],
        *,
        replace: bool,
    ) -> list[ft.Control]:
        theme = self.app.theme
        rows = [
            self._modrinth_dependency_row(
                ui.Text(
                    self._modrinth_dependency_candidate_label(candidate, replace=replace),
                    color=theme.text_secondary,
                    size=theme.text_size_sm,
                    expand=True,
                ),
                candidate.page_url,
            )
            for candidate in candidates
        ]
        return [
            self._modrinth_dependency_section_panel(title_key, rows, accent=theme.primary if replace else theme.info)
        ]

    def _modrinth_optional_dependency_section(
        self,
        candidates: list[ModrinthInstallCandidate],
        options: list[tuple[ModrinthInstallCandidate, ft.Checkbox]],
    ) -> list[ft.Control]:
        theme = self.app.theme
        rows: list[ft.Control] = []
        for candidate in candidates:
            checkbox = ui.Checkbox(
                value=False,
                label=self._modrinth_dependency_candidate_label(candidate),
                expand=True,
            )
            options.append((candidate, checkbox))
            rows.append(self._modrinth_dependency_row(checkbox, candidate.page_url))
        return [self._modrinth_dependency_section_panel("modrinth_dependencies_optional", rows, accent=theme.primary)]

    def _modrinth_dependency_candidate_label(
        self,
        candidate: ModrinthInstallCandidate,
        *,
        replace: bool | None = None,
    ) -> str:
        version = candidate.version_number or candidate.install_file.filename
        replacing = candidate.action == "replace" if replace is None else replace
        if replacing:
            installed = candidate.installed_item or {}
            current = (
                installed.get("modrinth_version_number")
                or installed.get("version")
                or installed.get("filename")
                or self.trans("unknown")
            )
            return self.trans(
                "modrinth_dependency_replace_line",
                name=candidate.title,
                current=current,
                new=version,
            )
        return self.trans(
            "modrinth_dependency_install_line",
            name=candidate.title,
            version=version,
        )

    def _modrinth_dependency_issue_section(
        self,
        title_key: str,
        issues: list[ModrinthDependencyIssue],
    ) -> list[ft.Control]:
        theme = self.app.theme
        rows: list[ft.Control] = []
        for issue in issues:
            rows.append(
                self._modrinth_dependency_row(
                    ui.Text(
                        self._format_modrinth_dependency_issue(issue),
                        color=theme.error if issue.blocking else theme.text_secondary,
                        size=theme.text_size_sm,
                        expand=True,
                    ),
                    issue.project_url,
                )
            )
        accent = theme.error if any(issue.blocking for issue in issues) else theme.text_secondary
        return [self._modrinth_dependency_section_panel(title_key, rows, accent=accent)]

    def _modrinth_dependency_row(self, descriptor: ft.Control, project_url: str | None) -> ft.Control:
        return ui.Row(
            [descriptor, self._modrinth_project_open_button(project_url)],
            spacing=self.app.theme.spacing_sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _modrinth_dependency_section_panel(
        self,
        title_key: str,
        rows: list[ft.Control],
        *,
        accent: str,
    ) -> ft.Control:
        theme = self.app.theme
        return ui.Container(
            content=ui.Column(
                [
                    ui.Text(
                        self.trans(title_key),
                        color=theme.text_color,
                        weight=theme.font_weight_semibold,
                    ),
                    *rows,
                ],
                spacing=theme.spacing_sm,
                tight=True,
            ),
            bgcolor=theme.overlay(0.08, accent),
            border=ft.Border.all(1, theme.overlay(0.28, accent)),
            border_radius=ft.BorderRadius.all(theme.radius_sm),
            padding=theme.padding_md,
        )
