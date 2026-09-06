from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import flet as ft

from launcher import ui
from launcher.application.instance_operations import InstanceOperationBusy
from launcher.application.modrinth_mods import ModrinthInstallCandidate
from launcher.ui.core.page_runtime import run_blocking, schedule_update
from launcher.ui.core.session_tasks import SessionTaskToken


class ModsManagerInstalledMixin:
    _INSTALLED_MODS_TASK_KEY = "installed-mods"

    def _rebuild_installed_mods(self, *, update: bool = True) -> None:
        scan = self._begin_installed_mods_scan()
        if scan is None:
            return
        token, version, mods_dir, mods_supported, tab_context = scan
        self.is_loading = True
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
        return (
            self._session_tasks.is_current(token)
            and self.version is version
            and (self.current_content_key, self.current_inner_tab) == tab_context
        )

    def _cancel_installed_mods_scan(self) -> None:
        self._session_tasks.invalidate(self._INSTALLED_MODS_TASK_KEY)
        self.is_loading = False

    def _create_installed_mod_card(self, mod: Dict) -> ui.Container:
        return self.cards.installed_mod_card(
            mod,
            has_backup=self._has_backup(mod),
            on_update=lambda e, m=mod: self._update_mod(m),
            on_restore=lambda e, m=mod: self._restore_mod_backup(m),
            on_toggle=lambda e, m=mod: self._toggle_mod(m),
            on_delete=lambda e, m=mod: self._delete_mod(m),
        )

    def _toggle_mod(self, mod: Dict):
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

        def handle_confirm(confirmed):
            if not confirmed:
                return
            if self.app.feedback.is_busy():
                self.app.feedback.info(self.trans("installation_already_running"))
                return
            operation = self.app.feedback.begin_operation(
                self.trans("updating_mod", name=mod.get("name", mod["filename"])),
                kind="install",
                status=self.trans("updating_mod", name=mod.get("name", mod["filename"])),
            )
            try:
                task = self._run_session_task(self._update_mod_async, mod, operation)
            except Exception:
                operation.fail(self.trans("update_failed"), notify=False)
                raise
            if task is None:
                operation.fail(self.trans("update_failed"), notify=False)

        latest_version_number = mod["latest_version"].get("version_number", "Unknown")
        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans(
                "confirm_update_mod",
                name=mod.get("name", mod["filename"]),
                current=mod.get("version", "Unknown"),
                new=latest_version_number,
            ),
            handle_confirm,
        )

    def _delete_mod(self, mod: Dict):
        def handle_confirm(confirmed):
            if not confirmed:
                return
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

    async def _update_mod_async(self, mod: Dict, operation):
        final_message = self.trans("update_failed")
        finish_level = "warning"
        try:
            latest_version = mod["latest_version"]
            install_file = self.app.modrinth_mods.select_primary_file(latest_version)
            if install_file is None:
                self.app.feedback.warning(self.trans("no_file_found"))
                operation.fail(final_message, notify=False)
                return

            project = {
                "project_id": mod.get("modrinth_project_id") or latest_version.get("project_id"),
                "slug": mod.get("modrinth_project_slug") or mod.get("slug"),
                "project_type": "mod",
                "title": mod.get("modrinth_project_title") or mod.get("name") or mod.get("filename"),
            }
            installed_match = self.app.modrinth_mods.match_installed([mod], project)
            if not installed_match.owned:
                self.app.feedback.warning(self.trans("modrinth_project_mismatch"))
                operation.fail(final_message, notify=False)
                return
            candidate = ModrinthInstallCandidate(
                project=project,
                version_data=latest_version,
                install_file=install_file,
                action="replace",
                dependency_type="selected",
                installed_item=mod,
                installed_match=installed_match,
            )
            await self._download_modrinth_candidate(candidate, {"directory": self.mods_dir, "key": "mods"})
            self.app.feedback.info(self.trans("mod_updated", name=mod.get("name", mod["filename"])))
            final_message = self.trans("update_complete")
            finish_level = "success"
            mod["update_available"] = False
            self._rebuild_installed_mods()
            self._refresh_visible_modrinth_search_results()

        except Exception as exc:
            import traceback

            self.app.log.error(f"Failed to update mod: {exc}")
            self.app.log.error(traceback.format_exc())
            self.app.feedback.warning(f"Error: {exc}")
        finally:
            operation.finish(final_message, show_success=False, level=finish_level)
