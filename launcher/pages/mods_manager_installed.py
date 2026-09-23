from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Dict

import flet as ft

from launcher import ui
from launcher.application.instance_operations import InstanceOperationBusy
from launcher.ui.core.page_runtime import run_blocking, schedule_update
from launcher.ui.core.session_tasks import SessionTaskToken


class ModsManagerInstalledMixin:
    _INSTALLED_MODS_TASK_KEY = "installed-mods"
    _INSTALLED_UPDATES_TASK_KEY = "installed-mod-updates"

    def _ensure_installed_updates_state(self) -> None:
        if not hasattr(self, "_installed_updates_status"):
            self._installed_updates_status = "idle"
            self._installed_updates_count = 0
            self._installed_updates_unchecked_count = 0
            self._installed_updates_toolbar = None

    def build_installed_updates_toolbar(self) -> ft.Row:
        self._ensure_installed_updates_state()
        self._installed_updates_progress = ui.ProgressRing(width=16, height=16, stroke_width=2)
        self._installed_updates_label = ui.Text(
            size=self.app.theme.text_size_xs,
            color=self.app.theme.text_secondary,
            expand=True,
        )
        self._installed_updates_button = ui.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=self.trans("check_updates_now"),
            width=40,
            height=40,
            on_click=self.request_installed_updates_check,
        )
        self._installed_updates_toolbar = ui.Row(
            [self._installed_updates_progress, self._installed_updates_label, self._installed_updates_button],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._refresh_installed_updates_toolbar()
        return self._installed_updates_toolbar

    def _refresh_installed_updates_toolbar(self) -> None:
        if self._installed_updates_toolbar is None:
            return
        checking = self._installed_updates_status == "checking"
        self._installed_updates_progress.visible = checking
        self._installed_updates_label.value = self.trans(
            f"installed_updates_{self._installed_updates_status}",
            count=(
                self._installed_updates_unchecked_count if self._installed_updates_status == "unchecked"
                else self._installed_updates_count
            ),
        ) if self._installed_updates_status != "empty" else self.trans("no_mods_installed")
        if self._installed_updates_status == "available" and self._installed_updates_unchecked_count:
            self._installed_updates_label.value += ". " + self.trans(
                "installed_updates_unchecked", count=self._installed_updates_unchecked_count,
            )
        self._installed_updates_label.color = (
            self.app.theme.error if self._installed_updates_status == "failed"
            else self.app.theme.info if self._installed_updates_status == "available"
            else self.app.theme.text_secondary
        )
        self._installed_updates_button.disabled = checking or self.is_loading

    def _set_installed_updates_status(
        self, status: str, *, count: int = 0, unchecked: int = 0, update: bool = True,
    ) -> None:
        self._ensure_installed_updates_state()
        self._installed_updates_status = status
        self._installed_updates_count = count
        self._installed_updates_unchecked_count = unchecked
        self._refresh_installed_updates_toolbar()
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _can_auto_check_installed_updates(self) -> bool:
        # Page doubles may execute queued scan coroutines, but must never contact Modrinth.
        if not isinstance(self.page, ft.Page):
            return False
        try:
            loop = self.page.session.connection.loop
            return loop is not None and loop.is_running()
        except RuntimeError:
            return False

    def request_installed_updates_check(self, _event=None, *, _after_scan: bool = False) -> object | None:
        self._ensure_installed_updates_state()
        if (
            self._installed_updates_status == "checking"
            or self.is_loading
            or (self.content_installing and not _after_scan)
            or not self.mods_supported
            or (self.current_content_key, self.current_inner_tab) != ("mods", "installed")
        ):
            return None
        token = self._session_tasks.begin(self._INSTALLED_UPDATES_TASK_KEY)
        if token is None:
            return None
        items = self.installed_items["mods"]
        if not items:
            self._set_installed_updates_status("empty")
            return None
        check = (token, self.version, (self.current_content_key, self.current_inner_tab), items)
        self._set_installed_updates_status("checking")
        try:
            task = self._run_session_task(self._check_installed_updates_async, check, token=token)
        except Exception as exc:
            self._apply_installed_updates_error(exc, check)
            return None
        if task is None and self._installed_updates_check_is_current(check):
            # There is deliberately no synchronous network fallback for headless pages.
            self._set_installed_updates_status("idle")
        return task

    def _installed_updates_check_is_current(self, check) -> bool:
        token, version, tab_context, items = check
        return (
            self._installed_mods_scan_is_current(token, version, tab_context)
            and self.installed_items["mods"] is items
        )

    async def _check_installed_updates_async(self, check) -> None:
        if not self._installed_updates_check_is_current(check):
            return
        _token, version, _tab_context, items = check
        try:
            updated_items = await run_blocking(
                self.app.modrinth_mods.check_installed_updates, deepcopy(items), version,
            )
        except asyncio.CancelledError:
            if self._installed_updates_check_is_current(check):
                self._set_installed_updates_status("idle")
            raise
        except Exception as exc:
            self._apply_installed_updates_error(exc, check)
            return
        if not self._installed_updates_check_is_current(check):
            return
        count = sum(bool(item.get("update_available")) for item in updated_items)
        enabled_items = [
            item for item in updated_items
            if item.get("enabled", True) and not str(item.get("path", "")).endswith(".disabled")
        ]
        unchecked = sum(not item.get("update_checked", False) for item in enabled_items)
        if count:
            status = "available"
        elif unchecked:
            status = "unchecked"
        else:
            status = "current" if enabled_items else "no_enabled"
        self._set_installed_updates_status(status, count=count, unchecked=unchecked, update=False)
        self._apply_installed_content("mods", updated_items, update=False)
        if self.search_results_container is not None and self.search_result_items and not self.search_state.loading:
            self.search_results_container.controls = [
                self._create_search_result_card(project) for project in self.search_result_items
            ]
        if self.page and self._is_active:
            schedule_update(self.page)

    def _apply_installed_updates_error(self, error: Exception, check) -> None:
        if not self._installed_updates_check_is_current(check):
            return
        self.app.log.error(f"Failed to check installed mod updates: {error!r}")
        self._set_installed_updates_status("failed")

    def _cancel_installed_updates_check(self, *, reset_status: bool = True) -> None:
        self._session_tasks.invalidate(self._INSTALLED_UPDATES_TASK_KEY)
        self._session_tasks.invalidate("installed-mod-update-confirmation")
        self._ensure_installed_updates_state()
        if reset_status or self._installed_updates_status == "checking":
            self._set_installed_updates_status("idle", update=False)

    def _rebuild_installed_mods(self, *, update: bool = True) -> None:
        scan = self._begin_installed_mods_scan()
        if scan is None:
            return
        token, version, mods_dir, mods_supported, tab_context = scan
        self.is_loading = True
        self._refresh_installed_updates_toolbar()
        self._show_installed_mods_loading(update=update)
        try:
            task = self._run_session_task(
                self._load_installed_mods_async,
                scan,
                update,
                token=token,
            )
        except Exception as exc:
            self._apply_installed_mods_error(exc, token, version, tab_context, update=update)
            return
        if task is not None or not self._installed_mods_scan_is_current(token, version, tab_context):
            return

        # Headless page doubles do not run coroutines. Keep their established fallback
        # without blocking a real Flet page, whose run_task() always returns a Future.
        try:
            items = self._scan_installed_mods_for(version, mods_dir, mods_supported)
        except Exception as exc:
            self._apply_installed_mods_error(exc, token, version, tab_context, update=update)
        else:
            self._apply_installed_mods(items, token, version, tab_context, update=update)

    async def _refresh_installed_mods_after_mutation(self, *, update: bool = True) -> None:
        scan = self._begin_installed_mods_scan()
        if scan is not None:
            await self._load_installed_mods_async(scan, update)

    def _begin_installed_mods_scan(self):
        token = self._session_tasks.begin(self._INSTALLED_MODS_TASK_KEY)
        if token is None:
            return None
        self._cancel_installed_updates_check()
        return (
            token,
            self.version,
            self.mods_dir,
            self.mods_supported,
            (self.current_content_key, self.current_inner_tab),
        )

    async def _load_installed_mods_async(self, scan, update: bool) -> None:
        token, version, mods_dir, mods_supported, tab_context = scan
        if not self._installed_mods_scan_is_current(token, version, tab_context):
            return
        try:
            items = await run_blocking(
                self._scan_installed_mods_for,
                version,
                mods_dir,
                mods_supported,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._apply_installed_mods_error(
                exc, token, version, tab_context, update=update or self._is_active,
            )
        else:
            # The tab's initial update ran before this deferred scan completed.
            self._apply_installed_mods(
                items, token, version, tab_context, update=update or self._is_active,
            )
            if (
                self._installed_mods_scan_is_current(token, version, tab_context)
                and self._can_auto_check_installed_updates()
            ):
                self.request_installed_updates_check(_after_scan=True)

    def _scan_installed_mods_for(
        self,
        version,
        mods_dir: Path | None,
        mods_supported: bool,
    ) -> list[Dict]:
        items = self.app.content.scan_installed_mods(mods_dir) if mods_supported else []
        return self.app.content.apply_modrinth_metadata(version, items)

    def _apply_installed_mods(
        self,
        items: list[Dict],
        token: SessionTaskToken,
        version,
        tab_context: tuple[str, str],
        *,
        update: bool,
    ) -> None:
        if not self._installed_mods_scan_is_current(token, version, tab_context):
            return
        self.is_loading = False
        self._refresh_installed_updates_toolbar()
        self._apply_installed_content("mods", items, update=update)
        if self._is_modrinth_tab_active():
            self._refresh_visible_modrinth_search_results()

    def _apply_installed_mods_error(
        self,
        error: Exception,
        token: SessionTaskToken,
        version,
        tab_context: tuple[str, str],
        *,
        update: bool,
    ) -> None:
        if not self._installed_mods_scan_is_current(token, version, tab_context):
            return
        self.is_loading = False
        self._refresh_installed_updates_toolbar()
        self.app.log.error(f"Failed to scan installed mods: {error!r}")
        container = self.installed_containers["mods"]
        container.controls.clear()
        container.controls.append(
            ui.Container(
                ui.Row(
                    [
                        ui.Icon(ft.Icons.ERROR_OUTLINE, color=self.app.theme.error),
                        ui.Text(self.trans("unknown_error"), color=self.app.theme.text_secondary),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                alignment=ft.Alignment.CENTER,
                expand=True,
            )
        )
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _show_installed_mods_loading(self, *, update: bool) -> None:
        container = self.installed_containers["mods"]
        container.controls.clear()
        container.controls.append(
            ui.Container(
                ui.ProgressRing(),
                alignment=ft.Alignment.CENTER,
                padding=self.app.theme.padding_md,
                expand=True,
            )
        )
        if update and self.page and self._is_active:
            schedule_update(self.page)

    def _installed_mods_scan_is_current(
        self,
        token: SessionTaskToken,
        version,
        tab_context: tuple[str, str],
    ) -> bool:
        if isinstance(self.page, ft.Page):
            try:
                self.page.session
            except RuntimeError:
                return False
        return (
            self._session_tasks.is_current(token)
            and self.version is version
            and (self.current_content_key, self.current_inner_tab) == tab_context
        )

    def _cancel_installed_mods_scan(self) -> None:
        self._session_tasks.invalidate(self._INSTALLED_MODS_TASK_KEY)
        self.is_loading = False
        self._cancel_installed_updates_check(reset_status=False)

    def _create_installed_mod_card(self, mod: Dict) -> ui.Container:
        project = {
            "project_id": mod.get("modrinth_project_id"),
            "slug": mod.get("modrinth_project_slug"),
            "project_type": "mod",
        }
        return self.cards.installed_mod_card(
            mod,
            has_backup=self._has_backup(mod),
            on_update=lambda e, m=mod: self._update_mod(m),
            on_restore=lambda e, m=mod: self._restore_mod_backup(m),
            on_toggle=lambda e, m=mod: self._toggle_mod(m),
            on_delete=lambda e, m=mod: self._delete_mod(m),
            on_open_site=(
                lambda _e: self._open_modrinth_project_page(project)
            ) if project["project_id"] or project["slug"] else None,
        )

    def _toggle_mod(self, mod: Dict):
        self._cancel_installed_updates_check()
        self._run_session_task(self._toggle_mod_async, mod)

    async def _toggle_mod_async(self, mod: Dict):
        try:
            enabled = await run_blocking(
                self._run_mod_mutation_worker,
                "mod_toggle",
                self.app.content.toggle_mod,
                mod,
            )
            message_key = "mod_enabled" if enabled else "mod_disabled"
            self.app.feedback.info(self.trans(message_key, name=mod.get("name", mod["filename"])))
            self._rebuild_installed_mods()
        except Exception as exc:
            self.app.log.error(f"Failed to toggle mod: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _has_backup(self, mod: Dict) -> bool:
        return self.app.content.has_backup(self.mods_dir, mod["filename"])

    def _restore_mod_backup(self, mod: Dict):
        def handle_confirm(confirmed):
            if not confirmed:
                return
            self._cancel_installed_updates_check()
            self._run_session_task(self._restore_mod_backup_async, mod)

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_restore_backup", name=mod.get("name", mod["filename"])),
            handle_confirm,
        )

    async def _restore_mod_backup_async(self, mod: Dict):
        try:
            await run_blocking(
                self._run_mod_mutation_worker,
                "mod_restore",
                self._restore_mod_backup_worker,
                mod,
            )
            self.app.feedback.info(self.trans("mod_restored", name=mod.get("name", mod["filename"])))
            self._rebuild_installed_mods()
        except FileNotFoundError:
            self.app.feedback.warning(self.trans("backup_not_found"))
        except Exception as exc:
            self.app.log.error(f"Failed to restore backup: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _restore_mod_backup_worker(self, mod: Dict) -> None:
        mods_dir = Path(mod["path"]).parent
        if not self.app.content.has_backup(mods_dir, mod["filename"]):
            raise FileNotFoundError("Backup file was not found")
        self.app.content.restore_backup(mods_dir, mod)

    def _update_mod(self, mod: Dict):
        if not mod.get("update_available"):
            return
        if not mod.get("enabled", True) or str(mod.get("path", "")).endswith(".disabled"):
            self.app.feedback.warning(self.trans("installed_mod_update_disabled"))
            return
        token = self._session_tasks.begin("installed-mod-update-confirmation")
        if token is None:
            return
        version = self.version
        tab_context = (self.current_content_key, self.current_inner_tab)

        def handle_confirm(confirmed):
            if not confirmed or not self._installed_mods_scan_is_current(token, version, tab_context):
                return
            if not mod.get("enabled", True) or str(mod.get("path", "")).endswith(".disabled"):
                self.app.feedback.warning(self.trans("installed_mod_update_disabled"))
                return
            latest_version = mod.get("latest_version") or {}
            project = {
                "project_id": mod.get("modrinth_project_id") or latest_version.get("project_id"),
                "slug": mod.get("modrinth_project_slug") or mod.get("slug"),
                "project_type": "mod",
                "title": mod.get("modrinth_project_title") or mod.get("name") or mod.get("filename"),
            }
            if not self.app.modrinth_mods.match_installed([mod], project).owned:
                self.app.feedback.warning(self.trans("modrinth_project_mismatch"))
                return
            self._cancel_installed_updates_check()
            self._install_mod(project)

        latest_version_number = (mod.get("latest_version") or {}).get("version_number") or "?"
        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans(
                "confirm_update_mod",
                name=mod.get("name", mod["filename"]),
                current=mod.get("modrinth_version_number") or mod.get("version") or "?",
                new=latest_version_number,
            ),
            handle_confirm,
        )

    def _delete_mod(self, mod: Dict):
        def handle_confirm(confirmed):
            if not confirmed:
                return
            self._cancel_installed_updates_check()
            self._run_session_task(self._delete_mod_async, mod)

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_delete_mod", name=mod.get("name", mod["filename"])),
            handle_confirm,
        )

    async def _delete_mod_async(self, mod: Dict):
        try:
            await run_blocking(
                self._run_mod_mutation_worker,
                "mod_delete",
                self.app.content.delete_mod,
                mod,
            )
            self.app.feedback.info(self.trans("mod_deleted", name=mod.get("name", mod["filename"])))
            self._rebuild_installed_mods()
        except Exception as exc:
            self.app.log.error(f"Failed to delete mod: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _run_mod_mutation_worker(self, kind: str, callback, *args):
        version_root = self.app.content.get_version_directory(self.version)
        if version_root is None:
            raise FileNotFoundError(self.trans("content_directory_unavailable"))
        version_root = Path(version_root).resolve(strict=False)

        def mutate():
            from launcher.core.game import Game

            if Game.is_game_dir_active(version_root):
                raise RuntimeError(
                    self.trans("instance_game_running", version=self.version.name)
                )
            safe_args = args
            if args and isinstance(args[0], dict):
                safe_args = (self._validated_mod_for_root(version_root, args[0]), *args[1:])
            return callback(*safe_args)

        coordinator = getattr(self.app, "instance_operations", None)
        if coordinator is None:
            return mutate()
        try:
            return coordinator.execute(version_root, kind, mutate)
        except InstanceOperationBusy as exc:
            raise RuntimeError(
                self.trans("instance_operation_busy", version=self.version.name)
            ) from exc

    def _validated_mod_for_root(self, version_root: Path, mod: Dict) -> Dict:
        raw_path = Path(str(mod.get("path", "")))
        resolved_path = raw_path.resolve(strict=False)
        mods_root = (version_root / "mods").resolve(strict=False)
        expected_filename = (
            resolved_path.name[:-9]
            if resolved_path.name.endswith(".jar.disabled")
            else resolved_path.name
        )
        if (
            not raw_path.is_absolute()
            or resolved_path.parent != mods_root
            or not resolved_path.name.endswith((".jar", ".jar.disabled"))
            or str(mod.get("filename", "")) != expected_filename
        ):
            raise ValueError(self.trans("content_path_outside_instance"))

        safe_mod = dict(mod)
        safe_mod["path"] = str(resolved_path)
        safe_mod["filename"] = expected_filename
        return safe_mod
