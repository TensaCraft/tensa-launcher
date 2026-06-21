from __future__ import annotations

from pathlib import Path
from typing import Dict

import flet as ft

from launcher import ui
from launcher.core.api import ModrinthAPI
from launcher.ui.core.page_runtime import run_blocking, run_task, schedule_update


class ModsManagerInstalledMixin:
    def _rebuild_installed_mods(self):
        self.installed_mods = self._scan_installed_mods()
        self.installed_mods_container.controls.clear()

        if not self.installed_mods:
            self.installed_mods_container.controls.append(
                ui.Container(
                    ui.Column(
                        [
                            ui.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=48, color=self.app.theme.text_tertiary),
                            ui.Text(
                                self.trans("no_mods_installed"),
                                size=self.app.theme.text_size_medium,
                                color=self.app.theme.text_secondary,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.Alignment.CENTER,
                    expand=True,
                )
            )
        else:
            for mod in self.installed_mods:
                self.installed_mods_container.controls.append(self._create_installed_mod_card(mod))

            self.is_loading = False

        if self.page and self._is_active:
            schedule_update(self.page)

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
        try:
            enabled = self.app.content.toggle_mod(mod)
            message_key = "mod_enabled" if enabled else "mod_disabled"
            self.app.feedback.info(self.trans(message_key, name=mod.get("name", mod["filename"])))
            self._rebuild_installed_mods()
        except Exception as exc:
            self.app.log.error(f"Failed to toggle mod: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _has_backup(self, mod: Dict) -> bool:
        return self.app.content.has_backup(self.mods_dir, mod["filename"])

    def _create_backup(self, mod: Dict) -> bool:
        try:
            return self.app.content.create_backup(self.mods_dir, mod)
        except Exception as exc:
            self.app.log.error(f"Failed to create backup: {exc}")
            return False

    def _restore_mod_backup(self, mod: Dict):
        def handle_confirm(confirmed):
            if not confirmed:
                return

            try:
                if not self._has_backup(mod):
                    self.app.feedback.warning(self.trans("backup_not_found"))
                    return

                self.app.content.restore_backup(self.mods_dir, mod)
                self.app.feedback.info(self.trans("mod_restored", name=mod.get("name", mod["filename"])))
                self._rebuild_installed_mods()
            except Exception as exc:
                self.app.log.error(f"Failed to restore backup: {exc}")
                self.app.feedback.warning(f"Error: {exc}")

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_restore_backup", name=mod.get("name", mod["filename"])),
            handle_confirm,
        )

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
                run_task(self.page, self._update_mod_async, mod, operation)
            except Exception:
                operation.fail(self.trans("update_failed"), notify=False)
                raise

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

            try:
                self.app.content.delete_mod(mod)
                self.app.feedback.info(self.trans("mod_deleted", name=mod.get("name", mod["filename"])))
                self._rebuild_installed_mods()
            except Exception as exc:
                self.app.log.error(f"Failed to delete mod: {exc}")
                self.app.feedback.warning(f"Error: {exc}")

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_delete_mod", name=mod.get("name", mod["filename"])),
            handle_confirm,
        )

    async def _update_mod_async(self, mod: Dict, operation):
        final_message = self.trans("update_failed")
        finish_level = "warning"
        try:
            if not self._create_backup(mod):
                self.app.feedback.warning(self.trans("backup_failed"))
                operation.fail(final_message, notify=False)
                return

            latest_version = mod["latest_version"]
            install_file = self.app.modrinth_mods.select_primary_file(latest_version)
            if install_file is None:
                self.app.feedback.warning(self.trans("no_file_found"))
                operation.fail(final_message, notify=False)
                return

            mod_path = Path(mod["path"])
            if mod_path.exists():
                mod_path.unlink()

            await run_blocking(ModrinthAPI.download_mod_file, install_file.url, str(mod_path))
            self.app.feedback.info(self.trans("mod_updated", name=mod.get("name", mod["filename"])))
            final_message = self.trans("update_complete")
            finish_level = "success"
            mod["update_available"] = False
            self._rebuild_installed_mods()

        except Exception as exc:
            import traceback

            self.app.log.error(f"Failed to update mod: {exc}")
            self.app.log.error(traceback.format_exc())
            self.app.feedback.warning(f"Error: {exc}")
        finally:
            operation.finish(final_message, show_success=False, level=finish_level)
