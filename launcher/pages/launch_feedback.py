from __future__ import annotations

from typing import Any

import flet as ft

from launcher import ui
from launcher.ui.core.page_runtime import close_dialog, schedule_update, show_dialog

MISSING_PROFILE_REASON = "missing_profile"


def handle_launch_response(app: Any, response: dict[str, Any] | None) -> None:
    if not response:
        return
    message = response.get("text")
    if response.get("status"):
        app.feedback.info(message)
        return
    if response.get("reason") == MISSING_PROFILE_REASON:
        show_profile_required_dialog(app)
        return
    app.feedback.warning(message)


def show_profile_required_dialog(app: Any) -> None:
    theme = app.theme
    dialog: ui.AlertDialog | None = None

    def open_profile_action(action: str) -> None:
        if dialog is not None:
            close_dialog(app.page, dialog)
        show_profiles = getattr(app, "show_profiles_page", None)
        if callable(show_profiles):
            try:
                show_profiles(initial_action=action)
            except TypeError:
                try:
                    show_profiles(action)
                except TypeError:
                    show_profiles()
        schedule_update(app.page)

    def close_profile_dialog(_event=None) -> None:
        if dialog is not None:
            close_dialog(app.page, dialog)
        schedule_update(app.page)

    dialog = ui.AlertDialog(
        title=ui.Text(
            app.trans("profile_required_title"),
            color=theme.text_color,
            weight=theme.font_weight_bold,
        ),
        modal=True,
        content=ui.Container(
            width=theme.modal_width,
            content=ui.Text(
                app.trans("profile_required_message"),
                color=theme.text_secondary,
                size=theme.text_size_sm,
            ),
        ),
        actions=[
            ui.Button(
                text=app.trans("microsoft_account"),
                icon=ft.Icons.ACCOUNT_CIRCLE,
                on_click=lambda _event: open_profile_action("microsoft"),
            ),
            ui.Button(
                text=app.trans("offline_account"),
                icon=ft.Icons.PERSON_ADD,
                variant="outline",
                tone="neutral",
                on_click=lambda _event: open_profile_action("offline"),
            ),
            ui.Button(
                text=app.trans("close"),
                variant="ghost",
                tone="neutral",
                on_click=close_profile_dialog,
            ),
        ],
    )
    show_dialog(app.page, dialog)
    schedule_update(app.page)
