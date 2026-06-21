from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict

import flet as ft

from launcher import ui
from launcher.ui.core.page_runtime import schedule_update


class ModsManagerResourcepacksMixin:
    def _rebuild_installed_resourcepacks(self):
        self.installed_resourcepacks = self._scan_installed_resourcepacks()
        self.installed_resourcepacks_container.controls.clear()

        if not self.installed_resourcepacks:
            self.installed_resourcepacks_container.controls.append(
                ui.Container(
                    ui.Column(
                        [
                            ui.Icon(ft.Icons.PALETTE, size=48, color=self.app.theme.text_tertiary),
                            ui.Text(
                                self.trans("no_resourcepacks_installed"),
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
            for resourcepack in self.installed_resourcepacks:
                self.installed_resourcepacks_container.controls.append(self._create_resourcepack_card(resourcepack))

        if self.page and self._is_active:
            schedule_update(self.page)

    def _create_resourcepack_card(self, rp: Dict) -> ui.Container:
        return self.cards.resourcepack_card(
            rp,
            on_toggle=lambda e, r=rp: self._toggle_resourcepack(r),
            on_open_folder=lambda e, r=rp: self._open_resourcepack_folder(r),
            on_delete=lambda e, r=rp: self._delete_resourcepack(r),
        )

    def _toggle_resourcepack(self, rp: Dict):
        try:
            enabled = self.app.content.toggle_resourcepack(self.resourcepacks_dir, rp)
            message_key = "resourcepack_enabled" if enabled else "resourcepack_disabled"
            self.app.feedback.info(self.trans(message_key, name=rp["filename"]))
            self._rebuild_installed_resourcepacks()
        except Exception as exc:
            self.app.log.error(f"Failed to toggle resourcepack: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _open_resourcepack_folder(self, rp: Dict):
        rp_path = Path(rp["path"])
        folder_path = rp_path if rp["type"] == "resourcepack_folder" else rp_path.parent

        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", str(folder_path)])
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder_path)])
            else:
                subprocess.run(["xdg-open", str(folder_path)])
        except Exception as exc:
            self.app.log.error(f"Failed to open folder: {exc}")
            self.app.feedback.warning(f"Error: {exc}")

    def _delete_resourcepack(self, rp: Dict):
        def handle_confirm(confirmed):
            if not confirmed:
                return

            try:
                self.app.content.delete_resourcepack(self.resourcepacks_dir, rp)
                self.app.feedback.info(self.trans("resourcepack_deleted", name=rp["filename"]))
                self._rebuild_installed_resourcepacks()
            except Exception as exc:
                self.app.log.error(f"Failed to delete resourcepack: {exc}")
                self.app.feedback.warning(f"Error: {exc}")

        self.app.feedback.confirm(
            self.trans("confirmation"),
            self.trans("confirm_delete_resourcepack", name=rp["filename"]),
            handle_confirm,
        )
