from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import webbrowser
from typing import Any

import flet as ft

from launcher import ui
from launcher.ui.core.page_runtime import close_dialog, invoke_on_ui, schedule_update, show_dialog


class DeviceCodeUI:
    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _call_page_api(page: Any, method_name: str, *args: Any) -> bool:
        method = getattr(page, method_name, None)
        if not callable(method):
            return False

        if inspect.iscoroutinefunction(method):
            runner = getattr(page, "run_task", None)
            if callable(runner):
                runner(method, *args)
                return True
            return False

        result = method(*args)
        if inspect.isawaitable(result):
            runner = getattr(page, "run_task", None)
            if callable(runner):

                async def _await_result():
                    return await result

                runner(_await_result)
                return True
            return False
        return True

    def open_url(self, url: str) -> bool:
        if self._open_browser(url):
            return True

        page = getattr(self.app, "page", None)
        if page is not None:
            try:
                if self._call_page_api(page, "launch_url", url):
                    return True
            except Exception as exc:
                self.app.log.warning(f"page.launch_url failed: {exc!r}")
        return False

    def copy_to_clipboard(self, text: str) -> bool:
        page = getattr(self.app, "page", None)
        if page is not None:
            try:
                if self._call_page_api(page, "set_clipboard", text):
                    return True
            except Exception as exc:
                self.app.log.warning(f"page.set_clipboard failed: {exc!r}")

        commands: list[list[str]] = []
        if sys.platform.startswith("win"):
            commands.append(["cmd", "/c", "clip"])
        elif sys.platform == "darwin":
            commands.append(["pbcopy"])
        else:
            commands.extend([["wl-copy"], ["xclip", "-selection", "clipboard"]])

        for cmd in commands:
            try:
                subprocess.run(cmd, input=text, text=True, check=True, capture_output=True)
                return True
            except Exception:
                continue
        return False

    def _build_dialog(
        self,
        *,
        user_code: str,
        verify_url: str,
        cancel_event: threading.Event,
    ) -> ft.AlertDialog:
        theme = self.app.theme
        page = self.app.page
        dialog: ft.AlertDialog | None = None

        def close_current() -> None:
            if dialog is None:
                return
            close_dialog(page, dialog)
            schedule_update(page)

        def on_cancel(_event=None) -> None:
            cancel_event.set()
            close_current()

        def on_copy_code(_event=None) -> None:
            if self.copy_to_clipboard(user_code):
                self.app.feedback.info(self.app.trans("microsoft_auth_code_copied"))
            else:
                self.app.feedback.warning(self.app.trans("microsoft_auth_copy_failed"))

        def on_copy_url(_event=None) -> None:
            if self.copy_to_clipboard(verify_url):
                self.app.feedback.info(self.app.trans("microsoft_auth_url_copied"))
            else:
                self.app.feedback.warning(self.app.trans("microsoft_auth_copy_failed"))

        def on_open_url(_event=None) -> None:
            if not self.open_url(verify_url):
                self.app.feedback.warning(self.app.trans("microsoft_auth_open_url_failed"))

        icon_shell = ui.Container(
            content=ui.Icon(ft.Icons.LOCK_OPEN_ROUNDED, color=theme.primary, size=30),
            bgcolor=ft.Colors.with_opacity(0.16, theme.primary),
            border_radius=ft.BorderRadius.all(theme.radius_md),
            padding=ft.Padding.all(14),
            alignment=ft.Alignment.CENTER,
            width=76,
            height=76,
        )

        code_shell = ui.Container(
            content=ui.Text(
                user_code,
                selectable=True,
                color=theme.text_color,
                size=theme.text_size_xl,
                weight=theme.font_weight_bold,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=ft.Colors.with_opacity(theme.alpha_input, theme.bg_list),
            border=ft.Border.all(1, theme.border_light_color),
            border_radius=ft.BorderRadius.all(theme.radius_sm),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            alignment=ft.Alignment.CENTER,
        )

        url_shell = ui.Container(
            content=ui.Text(
                verify_url,
                selectable=True,
                color=theme.text_secondary,
                size=theme.text_size_sm,
            ),
            bgcolor=ft.Colors.with_opacity(theme.alpha_input, theme.bg_list),
            border=ft.Border.all(1, theme.border_color),
            border_radius=ft.BorderRadius.all(theme.radius_sm),
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        )

        dialog = ui.AlertDialog(
            modal=True,
            bgcolor=ft.Colors.with_opacity(theme.alpha_modal_bg, theme.bg_card),
            shape=ft.RoundedRectangleBorder(radius=theme.radius_md),
            title=ui.Text(
                self.app.trans("microsoft_auth_device_code_title"),
                color=theme.text_color,
                size=theme.text_size_xl,
                weight=theme.font_weight_bold,
            ),
            content=ui.Container(
                width=theme.modal_width_md,
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[icon_shell],
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        ui.Text(
                            self.app.trans("microsoft_auth_device_code_waiting"),
                            color=theme.text_secondary,
                            size=theme.text_size_medium,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ui.Text(
                            self.app.trans("microsoft_auth_device_code_browser_hint"),
                            color=theme.text_tertiary,
                            size=theme.text_size_sm,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ui.Text(
                            self.app.trans("microsoft_auth_device_code_code_label"),
                            color=theme.text_tertiary,
                            size=theme.text_size_xs,
                            weight=theme.font_weight_medium,
                        ),
                        code_shell,
                        ui.Row(
                            controls=[
                                ui.Button(
                                    text=self.app.trans("microsoft_auth_device_code_copy_code"),
                                    icon=ft.Icons.CONTENT_COPY_ROUNDED,
                                    on_click=on_copy_code,
                                    expand=True,
                                    height=theme.button_height,
                                ),
                                ui.Button(
                                    text=self.app.trans("microsoft_auth_device_code_open_url"),
                                    icon=ft.Icons.OPEN_IN_BROWSER_ROUNDED,
                                    on_click=on_open_url,
                                    expand=True,
                                    height=theme.button_height,
                                ),
                            ],
                            spacing=theme.spacing_sm,
                        ),
                        ui.Text(
                            self.app.trans("microsoft_auth_device_code_url_label"),
                            color=theme.text_tertiary,
                            size=theme.text_size_xs,
                            weight=theme.font_weight_medium,
                        ),
                        url_shell,
                    ],
                    tight=True,
                    spacing=theme.spacing_md,
                ),
            ),
            actions=[
                ui.Button(
                    text=self.app.trans("microsoft_auth_device_code_copy_url"),
                    icon=ft.Icons.LINK_ROUNDED,
                    on_click=on_copy_url,
                    height=theme.button_height,
                    variant="outline",
                    tone="neutral",
                ),
                ui.Button(
                    text=self.app.trans("cancel"),
                    on_click=on_cancel,
                    height=theme.button_height,
                    variant="outline",
                    tone="neutral",
                ),
            ],
            on_dismiss=lambda _event: cancel_event.set(),
        )
        return dialog

    def open_dialog(
        self,
        *,
        user_code: str,
        verify_url: str,
        cancel_event: threading.Event,
    ) -> Any | None:
        page = getattr(self.app, "page", None)
        if page is None:
            return None

        dialog = self._build_dialog(
            user_code=user_code,
            verify_url=verify_url,
            cancel_event=cancel_event,
        )

        def _open() -> Any:
            try:
                show_dialog(page, dialog)
                schedule_update(page)
                return dialog
            except Exception as exc:
                self.app.log.error(f"Failed to open device code dialog: {exc!r}")
                return None

        return invoke_on_ui(page, _open)

    def close_dialog(self, dialog: Any | None) -> None:
        if dialog is None:
            return

        page = getattr(self.app, "page", None)
        if page is None:
            return

        def _close() -> None:
            try:
                close_dialog(page, dialog)
                schedule_update(page)
            except Exception as exc:
                self.app.log.debug(f"Failed to close device code dialog: {exc!r}")

        invoke_on_ui(page, _close)

    def _open_browser(self, url: str) -> bool:
        if not url:
            return False
        try:
            if sys.platform.startswith("win") and hasattr(os, "startfile"):
                os.startfile(url)
                return True
            if sys.platform == "darwin":
                subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            return webbrowser.open(url, new=2, autoraise=True)
        except Exception as exc:
            self.app.log.warning(f"Unable to open browser automatically: {exc!r}")
            return False
