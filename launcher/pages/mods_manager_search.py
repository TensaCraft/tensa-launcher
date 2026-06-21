from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Optional

import flet as ft

from launcher import ui
from launcher.core.api import ModrinthAPI
from launcher.ui.core.page_runtime import invoke_on_ui, run_blocking, run_task, schedule_update


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
        config = context["config"]
        try:
            version_data = await run_blocking(
                self.app.modrinth_mods.find_latest_version,
                mod["project_id"],
                self.version,
                project_type=context["project_type"],
                game_version=context["game_version"],
            )

            if version_data is None:
                self.app.feedback.warning(self.trans("no_compatible_version"))
                return

            install_file = self.app.modrinth_mods.select_primary_file(version_data)
            if install_file is None:
                self.app.feedback.warning(self.trans("no_file_found"))
                return

            loader = context["loader"]
            game_version = context["game_version"]
            self.app.log.info(
                f"Installing Modrinth {context['project_type']} version: "
                f"{version_data.get('version_number')} for MC {game_version} {loader or ''}"
            )

            target_dir = context["directory"]
            if target_dir is None:
                self.app.feedback.warning(self.trans("content_directory_unavailable"))
                return

            installed_item = self.app.modrinth_mods.find_installed(
                context["installed_items"],
                mod,
            )
            destination = target_dir / install_file.filename
            if installed_item and installed_item.get("path"):
                old_path = Path(installed_item["path"])
                if old_path.exists() and old_path != destination:
                    old_path.unlink()

            await run_blocking(
                ModrinthAPI.download_mod_file,
                install_file.url,
                str(destination),
            )
            self.app.content.record_modrinth_content(
                self.version,
                context["key"],
                destination,
                mod,
                version_data,
                install_file,
            )
            self.app.feedback.info(
                self.trans(config["installed_key"], name=mod.get("title", install_file.filename))
            )
            self._rebuild_installed_content(context["key"])

        except Exception as exc:
            import traceback

            self.app.log.error(f"Failed to install mod: {exc}")
            self.app.log.error(traceback.format_exc())
            self.app.feedback.warning(f"Error: {exc}")
        finally:
            self.content_installing = False

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
