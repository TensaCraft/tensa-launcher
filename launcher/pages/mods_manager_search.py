from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Optional

import flet as ft

from launcher import ui
from launcher.application.modrinth_mods import (
    ModrinthDependencyIssue,
    ModrinthDependencyPlan,
    ModrinthInstallCandidate,
)
from launcher.core.api import ModrinthAPI
from launcher.ui.core.page_runtime import (
    close_dialog,
    invoke_on_ui,
    run_blocking,
    run_task,
    schedule_update,
    show_dialog,
)


class ModsManagerSearchMixin:
    def _search_mods(self):
        if not self._is_active:
            return

        query = self.search_input.value.strip() if self.search_input.value else ""
        self.search_state.query = query
        self._load_search_page(0)

    def on_search_change(self, e):
        self.search_state.query = (e.control.value or "").strip()
        if not self._is_modrinth_tab_active() or not self._is_active:
            return

        if self._search_timer is not None:
            self._search_timer.cancel()
        self._search_timer = threading.Timer(0.35, self._trigger_search_from_timer)
        self._search_timer.daemon = True
        self._search_timer.start()

    def _trigger_search_from_timer(self):
        if not self._is_active or not self._is_modrinth_tab_active():
            return
        invoke_on_ui(self.page, self._search_mods)

    def _load_search_page(self, offset: int):
        if not self._is_active:
            return

        search_token = self.search_state.begin(self.search_state.query, offset)
        self.search_results_container.controls.clear()
        loading_indicator = ui.Container(
            ui.ProgressRing(),
            alignment=ft.Alignment.CENTER,
            padding=self.app.theme.padding_md,
        )
        self.search_results_container.controls.append(loading_indicator)
        self._update_search_pagination()
        schedule_update(self.page)
        context = self._content_context()
        threading.Thread(
            target=self._search_mods_worker,
            args=(
                search_token,
                self.search_state.query,
                self.search_state.offset,
                loading_indicator,
                context["project_type"],
                context["game_version"],
            ),
            daemon=True,
        ).start()

    def _search_mods_worker(
        self,
        search_token: int,
        query: str,
        offset: int,
        loading_indicator,
        project_type: str,
        game_version: str,
    ):
        try:
            result = self.app.catalog.search_mods(
                query,
                facets=self.app.modrinth_mods.build_search_facets(
                    self.version,
                    project_type=project_type,
                    game_version=game_version,
                ),
                offset=offset,
                limit=self.search_state.limit,
            )
        except Exception as exc:
            self.app.log.error(f"Failed to search mods: {exc}")
            invoke_on_ui(self.page, self._apply_search_error, search_token, loading_indicator, str(exc))
            return

        invoke_on_ui(self.page, self._apply_search_results, search_token, loading_indicator, result)

    def _apply_search_error(self, search_token: int, loading_indicator, error_text: str):
        if search_token != self.search_state.token or not self._is_active:
            return

        if loading_indicator in self.search_results_container.controls:
            self.search_results_container.controls.remove(loading_indicator)
        self.search_results_container.controls.append(
            ui.Container(
                ui.Text(f"Error: {error_text}", color=self.app.theme.error),
                alignment=ft.Alignment.CENTER,
                padding=self.app.theme.padding_md,
            )
        )
        self.search_state.fail(clear_results=True)
        self._update_search_pagination()
        schedule_update(self.page)

    def _apply_search_results(self, search_token: int, loading_indicator, result):
        if search_token != self.search_state.token or not self._is_active:
            return

        if loading_indicator in self.search_results_container.controls:
            self.search_results_container.controls.remove(loading_indicator)

        self.search_state.apply(result)
        self.search_result_items = list(result.items)
        if not result.items:
            self.search_results_container.controls.append(
                ui.Container(
                    ui.Text(self.trans("no_mods_found"), color=self.app.theme.text_secondary),
                    alignment=ft.Alignment.CENTER,
                    padding=self.app.theme.padding_md,
                )
            )
        else:
            for mod in result.items:
                self.search_results_container.controls.append(self._create_search_result_card(mod))

        self._update_search_pagination()
        schedule_update(self.page)

    def _create_search_result_card(self, mod: Dict) -> ui.Container:
        install_state = self._get_modrinth_install_state(mod)
        return self.cards.search_result_card(
            mod,
            installed=install_state["installed"],
            update_available=install_state["update_available"],
            on_install=lambda e, m=mod: self._install_mod(m),
            on_open_site=lambda e, m=mod: self._open_modrinth_project_page(m),
        )

    def _get_modrinth_install_state(self, project: Dict) -> dict[str, bool]:
        installed_items = self.installed_items.get(self.current_content_key, [])
        installed_item = self.app.modrinth_mods.find_installed(installed_items, project)
        update_available = bool(
            installed_item
            and installed_item.get("modrinth_version_id")
            and project.get("latest_version")
            and installed_item.get("modrinth_version_id") != project.get("latest_version")
        )
        return {"installed": installed_item is not None, "update_available": update_available}

    def _install_mod(self, mod: Dict):
        if self.content_installing or self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        context = self._content_context()
        self.content_installing = True
        self.app.feedback.info(self.trans(context["config"]["installing_key"], name=mod.get("title", "content")))
        try:
            run_task(self.page, self._install_mod_async, mod, context)
        except Exception:
            self.content_installing = False
            self.app.feedback.warning(self.trans("installation_failed"))
            raise

    async def _install_mod_async(self, mod: Dict, context: dict | None = None):
        context = context or self._content_context()
        try:
            target_dir = context["directory"]
            if target_dir is None:
                self.app.feedback.warning(self.trans("content_directory_unavailable"))
                return

            plan = await run_blocking(
                self.app.modrinth_mods.build_dependency_plan,
                mod,
                self.version,
                project_type=context["project_type"],
                game_version=context["game_version"],
                installed_items=context["installed_items"],
            )
            if plan.main is None:
                self._warn_modrinth_plan_failure(plan)
                return
            if plan.requires_confirmation:
                invoke_on_ui(self.page, self._show_modrinth_dependency_plan_dialog, plan, context)
                return

            await self._install_modrinth_plan_async(plan, context)

        except Exception as exc:
            import traceback

            self.app.log.error(f"Failed to install mod: {exc}")
            self.app.log.error(traceback.format_exc())
            self.app.feedback.warning(f"Error: {exc}")
        finally:
            self.content_installing = False

    async def _install_modrinth_plan_async(
        self,
        plan: ModrinthDependencyPlan,
        context: dict,
        selected_optional_dependencies: list[ModrinthInstallCandidate] | None = None,
    ):
        if not plan.can_install:
            self._warn_modrinth_plan_failure(plan)
            return

        main = plan.main
        if main is None:
            self._warn_modrinth_plan_failure(plan)
            return

        loader = context["loader"]
        game_version = context["game_version"]
        self.app.log.info(
            f"Installing Modrinth {context['project_type']} version: "
            f"{main.version_number} for MC {game_version} {loader or ''}"
        )

        for candidate in plan.install_order_with_optional(selected_optional_dependencies):
            await self._download_modrinth_candidate(candidate, context)

        self.app.feedback.info(
            self.trans(context["config"]["installed_key"], name=main.title)
        )
        self._rebuild_installed_content(context["key"])
        self._refresh_visible_modrinth_search_results()

    async def _download_modrinth_candidate(self, candidate: ModrinthInstallCandidate, context: dict) -> Path | None:
        if candidate.action == "satisfied":
            return None

        target_dir = context["directory"]
        if target_dir is None:
            self.app.feedback.warning(self.trans("content_directory_unavailable"))
            return None

        destination = target_dir / candidate.install_file.filename
        temporary_destination = destination.with_name(f".{destination.name}.download")
        if temporary_destination.exists():
            temporary_destination.unlink()
        installed_item = candidate.installed_item
        try:
            await run_blocking(
                ModrinthAPI.download_mod_file,
                candidate.install_file.url,
                str(temporary_destination),
            )
            temporary_destination.replace(destination)
            if installed_item and installed_item.get("path"):
                old_path = Path(installed_item["path"])
                if old_path.exists() and old_path != destination:
                    old_path.unlink()
        except Exception:
            if temporary_destination.exists():
                temporary_destination.unlink()
            raise
        self.app.content.record_modrinth_content(
            self.version,
            context["key"],
            destination,
            candidate.project,
            candidate.version_data,
            candidate.install_file,
        )
        return destination

    def _warn_modrinth_plan_failure(self, plan: ModrinthDependencyPlan):
        if plan.blocking_issues:
            self.app.feedback.warning(self._format_modrinth_dependency_issue(plan.blocking_issues[0]))
            return
        self.app.feedback.warning(self.trans("installation_failed"))

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

        if plan.dependencies_to_install:
            content_controls.extend(
                self._modrinth_dependency_candidate_section(
                    "modrinth_dependencies_to_install",
                    plan.dependencies_to_install,
                    replace=False,
                )
            )
        if plan.dependencies_to_replace:
            content_controls.extend(
                self._modrinth_dependency_candidate_section(
                    "modrinth_dependencies_to_replace",
                    plan.dependencies_to_replace,
                    replace=True,
                )
            )
        if plan.already_satisfied:
            content_controls.extend(
                self._modrinth_dependency_candidate_section(
                    "modrinth_dependencies_satisfied",
                    plan.already_satisfied,
                    replace=False,
                )
            )
        if plan.optional_dependencies:
            content_controls.extend(
                self._modrinth_optional_dependency_section(
                    plan.optional_dependencies,
                    optional_options,
                )
            )
        if plan.optional_dependency_issues:
            content_controls.extend(
                self._modrinth_dependency_issue_section(
                    "modrinth_dependencies_optional_unavailable",
                    plan.optional_dependency_issues,
                )
            )
        if plan.skipped_embedded:
            content_controls.extend(
                self._modrinth_dependency_issue_section(
                    "modrinth_dependencies_embedded",
                    plan.skipped_embedded,
                )
            )
        if plan.blocking_issues:
            content_controls.extend(
                self._modrinth_dependency_issue_section(
                    "modrinth_dependencies_blocked",
                    plan.blocking_issues,
                )
            )

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
        rows: list[ft.Control] = []
        for candidate in candidates:
            if replace:
                installed = candidate.installed_item or {}
                current = (
                    installed.get("modrinth_version_number")
                    or installed.get("version")
                    or installed.get("filename")
                    or self.trans("unknown")
                )
                line = self.trans(
                    "modrinth_dependency_replace_line",
                    name=candidate.title,
                    current=current,
                    new=candidate.version_number or candidate.install_file.filename,
                )
            else:
                line = self.trans(
                    "modrinth_dependency_install_line",
                    name=candidate.title,
                    version=candidate.version_number or candidate.install_file.filename,
                )
            rows.append(self._modrinth_dependency_candidate_row(candidate, line))
        return [self._modrinth_dependency_section_panel(title_key, rows, accent=theme.primary if replace else theme.info)]

    def _modrinth_optional_dependency_section(
        self,
        candidates: list[ModrinthInstallCandidate],
        options: list[tuple[ModrinthInstallCandidate, ft.Checkbox]],
    ) -> list[ft.Control]:
        theme = self.app.theme
        rows: list[ft.Control] = []
        for candidate in candidates:
            if candidate.action == "replace":
                installed = candidate.installed_item or {}
                current = (
                    installed.get("modrinth_version_number")
                    or installed.get("version")
                    or installed.get("filename")
                    or self.trans("unknown")
                )
                label = self.trans(
                    "modrinth_dependency_replace_line",
                    name=candidate.title,
                    current=current,
                    new=candidate.version_number or candidate.install_file.filename,
                )
            else:
                label = self.trans(
                    "modrinth_dependency_install_line",
                    name=candidate.title,
                    version=candidate.version_number or candidate.install_file.filename,
                )
            checkbox = ui.Checkbox(value=False, label=label, expand=True)
            options.append((candidate, checkbox))
            rows.append(
                ui.Row(
                    [
                        checkbox,
                        self._modrinth_project_open_button(candidate.page_url),
                    ],
                    spacing=theme.spacing_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        return [self._modrinth_dependency_section_panel("modrinth_dependencies_optional", rows, accent=theme.primary)]

    def _modrinth_dependency_candidate_row(self, candidate: ModrinthInstallCandidate, line: str) -> ft.Control:
        theme = self.app.theme
        return ui.Row(
            [
                ui.Text(line, color=theme.text_secondary, size=theme.text_size_sm, expand=True),
                self._modrinth_project_open_button(candidate.page_url),
            ],
            spacing=theme.spacing_sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
                ui.Row(
                    [
                        ui.Text(
                            self._format_modrinth_dependency_issue(issue),
                            color=theme.error if issue.blocking else theme.text_secondary,
                            size=theme.text_size_sm,
                            expand=True,
                        ),
                        self._modrinth_project_open_button(issue.project_url),
                    ],
                    spacing=theme.spacing_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        accent = theme.error if any(issue.blocking for issue in issues) else theme.text_secondary
        return [self._modrinth_dependency_section_panel(title_key, rows, accent=accent)]

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

    def _format_modrinth_dependency_issue(self, issue: ModrinthDependencyIssue) -> str:
        name = issue.display_name or self.trans("unknown")
        if issue.code == "required_file_only":
            return self.trans(
                "modrinth_dependency_file_only_issue",
                file=issue.file_name or self.trans("unknown"),
            )
        if issue.code == "incompatible_installed":
            return self.trans(
                "modrinth_dependency_incompatible_issue",
                name=name,
            )
        if issue.code == "optional_dependency":
            return self.trans(
                "modrinth_dependency_install_line",
                name=name,
                version=self.trans("optional"),
            )
        if issue.message and issue.message != issue.code:
            return issue.message
        if issue.code == "dependency_incompatible":
            return self.trans("modrinth_dependency_incompatible_build_issue", name=name)
        if issue.code == "dependency_no_file":
            return self.trans("modrinth_dependency_no_file_issue", name=name)
        return self.trans(
            "modrinth_dependency_resolution_failed",
            name=name,
        )

    def _modrinth_project_open_button(self, url: str | None) -> ft.Control:
        theme = self.app.theme
        button_size = theme.button_height_for_size("sm")
        if not url:
            return ui.Container(width=button_size, height=button_size)
        return ui.IconButton(
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            tooltip=self.trans("modrinth_dependency_open"),
            width=button_size,
            height=button_size,
            icon_size=theme.icon_size_sm,
            on_click=lambda _e: self._open_modrinth_project_url(url),
        )

    def _open_modrinth_project_url(self, url: str):
        opener = getattr(getattr(self.app, "auth", None), "device_ui", None)
        if opener is not None and callable(getattr(opener, "open_url", None)):
            if opener.open_url(url):
                return
        launch_url = getattr(self.page, "launch_url", None)
        if callable(launch_url):
            result = launch_url(url)
            if result is not False:
                return
        self.app.feedback.warning(self.trans("modrinth_dependency_open_failed"))

    def _open_modrinth_project_page(self, project: dict):
        url = self._modrinth_project_url(project)
        if not url:
            self.app.feedback.warning(self.trans("modrinth_dependency_open_failed"))
            return
        self._open_modrinth_project_url(url)

    def _modrinth_project_url(self, project: dict) -> str | None:
        identifier = str(project.get("slug") or project.get("project_id") or project.get("id") or "").strip()
        if not identifier:
            return None
        project_type = str(project.get("project_type") or self._current_config()["project_type"] or "mod").strip()
        return f"https://modrinth.com/{project_type}/{identifier}"

    def _refresh_visible_modrinth_search_results(self) -> None:
        if not self._is_modrinth_tab_active() or self.search_results_container is None:
            return
        items = list(getattr(self, "search_result_items", []) or [])
        if not items:
            return
        self.search_results_container.controls.clear()
        for item in items:
            self.search_results_container.controls.append(self._create_search_result_card(item))
        self._update_search_pagination()
        schedule_update(self.page)

    def _confirm_modrinth_dependency_plan(
        self,
        plan: ModrinthDependencyPlan,
        context: dict,
        selected_optional_dependencies: list[ModrinthInstallCandidate] | None = None,
    ):
        self._close_modrinth_dependency_dialog()
        if self.content_installing or self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        if not plan.can_install:
            self._warn_modrinth_plan_failure(plan)
            return

        self.content_installing = True
        main_name = plan.main.title if plan.main is not None else self.trans("modrinth_content_tab")
        self.app.feedback.info(self.trans("installing_modrinth_dependencies", name=main_name))
        try:
            run_task(
                self.page,
                self._install_confirmed_modrinth_plan_async,
                plan,
                context,
                selected_optional_dependencies or [],
            )
        except Exception:
            self.content_installing = False
            self.app.feedback.warning(self.trans("installation_failed"))
            raise

    async def _install_confirmed_modrinth_plan_async(
        self,
        plan: ModrinthDependencyPlan,
        context: dict,
        selected_optional_dependencies: list[ModrinthInstallCandidate] | None = None,
    ):
        try:
            await self._install_modrinth_plan_async(plan, context, selected_optional_dependencies)
        except Exception as exc:
            import traceback

            self.app.log.error(f"Failed to install Modrinth dependency plan: {exc}")
            self.app.log.error(traceback.format_exc())
            self.app.feedback.warning(f"Error: {exc}")
        finally:
            self.content_installing = False

    def _close_modrinth_dependency_dialog(self):
        dialog = self.modrinth_dependency_dialog
        if dialog is None:
            return
        close_dialog(self.page, dialog)
        self.modrinth_dependency_dialog = None
        schedule_update(self.page)

    def _get_loader_name(self) -> Optional[str]:
        return self.app.modrinth_mods.get_loader_name(self.version)

    def _go_previous_search_page(self):
        if self.search_state.loading or not self.search_state.has_previous:
            return
        self._load_search_page(self.search_state.previous_offset)

    def _go_next_search_page(self):
        if self.search_state.loading or not self.search_state.has_next:
            return
        self._load_search_page(self.search_state.next_offset)

    def _update_search_pagination(self):
        if (
            self.search_prev_button is None
            or self.search_next_button is None
            or self.search_page_label is None
            or self.search_pagination_container is None
        ):
            return

        self.search_prev_button.disabled = self.search_state.loading or not self.search_state.has_previous
        self.search_next_button.disabled = self.search_state.loading or not self.search_state.has_next
        self.search_page_label.value = self.trans(
            "page_indicator",
            current_page=self.search_state.current_page,
            total_pages=self.search_state.total_pages,
        )
        self.search_pagination_container.visible = self._is_modrinth_tab_active() and self.search_state.show_pagination
