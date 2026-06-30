from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Iterable

import flet as ft

from ..controls.button import Button
from ..controls.icon import Icon
from ..controls.text import Text
from ..core.page_runtime import close_dialog as close_page_dialog
from ..core.page_runtime import invoke_on_ui, run_task, schedule_update, show_dialog
from ..layout.container import Container
from ..theme import current_theme
from .alert_dialog import AlertDialog
from .snackbar import SnackBar

DISCORD_SUPPORT_URL = "https://discord.gg/8GR7Smy9s"


class Alert:
    def __init__(self, app):
        self.app = app

    def _pop_active_dialog(self, fallback_dialog: ft.Control | None = None) -> None:
        popper = getattr(self.app.page, "pop_dialog", None)
        if callable(popper):
            try:
                popper()
                return
            except Exception as exc:
                self.app.log.debug(f"Dialog pop skipped: {exc}")
        if fallback_dialog is not None:
            try:
                close_page_dialog(self.app.page, fallback_dialog)
            except Exception as exc:
                self.app.log.debug(f"Dialog fallback close skipped: {exc}")

    def _show_alert_impl(
        self,
        message: str,
        is_warning: bool = False,
        actions=None,
        *,
        allow_report: bool | None = None,
        report_title: str | None = None,
        report_type: str = "error",
        report_severity: str = "error",
        report_metadata: dict[str, Any] | None = None,
        report_attachments: Iterable[str | Path] | None = None,
    ) -> None:
        theme = current_theme()
        if is_warning:
            def close_warning_dialog(_event=None) -> None:
                self._pop_active_dialog(warning_dialog)
                schedule_update(self.app.page)

            dialog_actions = []
            if allow_report is None:
                allow_report = self._has_report_context(report_title, report_metadata, report_attachments)
            if allow_report:
                report_action = self._build_report_action(
                    message=message,
                    title=report_title or self.app.trans("warning"),
                    report_type=report_type,
                    severity=report_severity,
                    metadata=report_metadata,
                    attachments=report_attachments,
                )
                if report_action is not None:
                    dialog_actions.append(report_action)
                dialog_actions.append(self._build_discord_support_action())
            dialog_actions.extend(self._normalize_actions(actions))
            dialog_actions = [
                *dialog_actions,
                Button(
                    text=self.app.trans("close"),
                    variant="ghost",
                    tone="neutral",
                    color=ft.Colors.AMBER_400,
                    on_click=close_warning_dialog,
                )
            ]
            warning_dialog = AlertDialog(
                modal=True,
                icon=Container(
                    content=Icon(ft.Icons.WARNING_ROUNDED, color=ft.Colors.AMBER_400, size=32),
                    bgcolor=theme.overlay(0.15, ft.Colors.AMBER_400),
                    border_radius=theme.radius(md=True),
                    padding=ft.Padding.all(12),
                ),
                title=Text(self.app.trans("warning"), size=theme.text_size_xl, weight=theme.font_weight_semibold),
                content=Container(
                    content=Text(message, size=theme.text_size_sm, color=theme.text_secondary),
                    padding=ft.Padding.only(top=8, bottom=8),
                ),
                actions=dialog_actions,
            )
            try:
                show_dialog(self.app.page, warning_dialog)
            except Exception as exc:
                self.app.log.debug(f"Warning dialog open skipped: {exc}")
        else:
            sidebar_width = getattr(self.app, "get_sidebar_width", lambda: theme.sidebar_width)()
            snackbar = SnackBar(
                content=Container(
                    content=Text(message, text_align=ft.TextAlign.CENTER, weight=theme.font_weight_medium),
                    alignment=ft.Alignment.CENTER,
                ),
                show_close_icon=False,
                close_icon_color=theme.color_white,
                elevation=theme.snackbar_elevation,
                behavior=ft.SnackBarBehavior.FLOATING,
                duration=theme.snackbar_duration,
                margin=ft.Margin.only(
                    bottom=theme.footer_height,
                    left=240 + sidebar_width,
                    right=240,
                ),
            )
            try:
                show_dialog(self.app.page, snackbar)
            except Exception as exc:
                self.app.log.debug(f"Snackbar open skipped: {exc}")
        try:
            schedule_update(self.app.page)
        except Exception as exc:
            self.app.log.debug(f"Alert update skipped: {exc}")

    @staticmethod
    def _has_report_context(
        report_title: str | None,
        report_metadata: dict[str, Any] | None,
        report_attachments: Iterable[str | Path] | None,
    ) -> bool:
        if report_title:
            return True
        if report_metadata:
            return True
        return report_attachments is not None

    def show_alert(
        self,
        message: str,
        is_warning: bool = False,
        actions=None,
        *,
        allow_report: bool | None = None,
        report_title: str | None = None,
        report_type: str = "error",
        report_severity: str = "error",
        report_metadata: dict[str, Any] | None = None,
        report_attachments: Iterable[str | Path] | None = None,
    ) -> None:
        invoke_on_ui(
            self.app.page,
            self._show_alert_impl,
            message,
            is_warning,
            actions,
            allow_report=allow_report,
            report_title=report_title,
            report_type=report_type,
            report_severity=report_severity,
            report_metadata=report_metadata,
            report_attachments=report_attachments,
        )

    @staticmethod
    def _normalize_actions(actions) -> list[ft.Control]:
        if actions is None:
            return []
        if isinstance(actions, (list, tuple)):
            return list(actions)
        return [actions]

    def _open_external_url(self, url: str) -> bool:
        auth = getattr(self.app, "auth", None)
        device_ui = getattr(auth, "device_ui", None)
        opener = getattr(device_ui, "open_url", None)
        if callable(opener):
            try:
                if opener(url):
                    return True
            except Exception as exc:
                self.app.log.warning(f"Device URL opener failed: {exc!r}")

        page = getattr(self.app, "page", None)
        launch_url = getattr(page, "launch_url", None)
        if not callable(launch_url):
            return False
        try:
            if inspect.iscoroutinefunction(launch_url):
                run_task(page, launch_url, url)
                return True
            result = launch_url(url)
            if inspect.isawaitable(result):

                async def _await_launch_url() -> Any:
                    return await result

                run_task(page, _await_launch_url)
            return True
        except Exception as exc:
            self.app.log.warning(f"Page URL opener failed: {exc!r}")
            return False

    def _build_discord_support_action(self) -> ft.Control:
        def open_discord(_event=None) -> None:
            if self._open_external_url(DISCORD_SUPPORT_URL):
                return
            self.show_alert(
                self.app.trans("discord_support_open_failed"),
                is_warning=True,
                allow_report=False,
            )

        return Button(
            text=self.app.trans("open_discord_support"),
            icon=ft.Icons.FORUM_OUTLINED,
            variant="outline",
            tone="neutral",
            on_click=open_discord,
        )

    def _build_report_action(
        self,
        *,
        message: str,
        title: str,
        report_type: str,
        severity: str,
        metadata: dict[str, Any] | None,
        attachments: Iterable[str | Path] | None,
    ) -> ft.Control | None:
        reporter = getattr(self.app, "reporter", None)
        submit = getattr(reporter, "submit_report_async", None)
        if not callable(submit):
            return None

        action: ft.Control | None = None
        status = {"value": "idle"}

        def set_action_state(text_key: str, *, disabled: bool) -> None:
            if action is None:
                return
            action.content = self.app.trans(text_key)
            if hasattr(action, "disabled"):
                action.disabled = disabled
            try:
                schedule_update(self.app.page)
            except Exception as exc:
                self.app.log.debug(f"Report action update skipped: {exc}")

        def on_success(result: dict[str, Any]) -> None:
            status["value"] = "sent"
            set_action_state("error_report_sent_button", disabled=True)
            report_id = result.get("report_id", "")
            self.show_alert(self.app.trans("error_report_sent", report_id=report_id))

        def on_error(exc: Exception) -> None:
            status["value"] = "idle"
            set_action_state("error_report_retry", disabled=False)
            self.show_alert(
                self.app.trans("error_report_failed", error=str(exc)),
                is_warning=True,
                allow_report=False,
            )

        def send_report(_event=None) -> None:
            if status["value"] != "idle":
                return
            status["value"] = "sending"
            set_action_state("error_report_sending", disabled=True)
            try:
                submit(
                    report_type=report_type,
                    severity=severity,
                    title=title,
                    message=message,
                    metadata=metadata,
                    attachments=attachments,
                    on_success=on_success,
                    on_error=on_error,
                )
            except Exception as exc:
                on_error(exc)

        action = Button(
            text=self.app.trans("send_error_report"),
            icon=ft.Icons.BUG_REPORT_OUTLINED,
            variant="outline",
            tone="neutral",
            on_click=send_report,
        )
        return action

    def _dispatch_dialog_callback(self, callback, *args) -> None:
        runner = getattr(self.app.page, "run_task", None)
        if callable(runner):
            try:
                run_task(self.app.page, self._dispatch_dialog_callback_async, callback, *args)
                return
            except Exception as exc:
                self.app.log.debug(f"Dialog callback scheduling failed: {exc}")
        callback(*args)

    async def _dispatch_dialog_callback_async(self, callback, *args):
        await asyncio.sleep(0)
        result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    def show_confirm(self, title: str, question: str, callback) -> None:
        def handle_response(response: bool) -> None:
            self._pop_active_dialog(confirm_dialog)
            schedule_update(self.app.page)
            self._dispatch_dialog_callback(callback, response)

        confirm_dialog = AlertDialog(
            modal=True,
            title=Text(title, size=current_theme().text_size_xl, weight=current_theme().font_weight_bold),
            content=Text(question, color=current_theme().text_secondary, size=current_theme().text_size_sm),
            actions=[
                Button(text=self.app.trans("yes"), on_click=lambda _e: handle_response(True)),
                Button(text=self.app.trans("no"), variant="outline", tone="neutral", on_click=lambda _e: handle_response(False)),
            ],
        )
        self.open_dialog(confirm_dialog)

    def open_dialog(self, dialog: ft.Control) -> None:
        try:
            show_dialog(self.app.page, dialog)
            schedule_update(self.app.page)
        except Exception as exc:
            self.app.log.debug(f"Dialog open skipped: {exc}")

    def close_dialog(self, dialog: ft.Control) -> None:
        try:
            self._pop_active_dialog(dialog)
            schedule_update(self.app.page)
        except Exception as exc:
            self.app.log.debug(f"Dialog close skipped: {exc}")


__all__ = ["Alert"]
