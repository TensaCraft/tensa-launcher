from __future__ import annotations

from typing import Any, Callable

import flet as ft

from launcher import ui
from launcher.pages.launch_feedback import show_profile_required_dialog
from launcher.ui.core.page_runtime import close_dialog, schedule_update, show_dialog

ASK_PROFILE_ON_LAUNCH_KEY = "ask_profile_on_launch"


def should_ask_profile_on_launch(app: Any) -> bool:
    return app.config.get(ASK_PROFILE_ON_LAUNCH_KEY, "no") == "yes"


def launch_task_args(version: Any, allow_duplicate: bool, profile_key: str | None) -> tuple:
    if profile_key is not None:
        return (version, allow_duplicate, profile_key)
    if allow_duplicate:
        return (version, allow_duplicate)
    return (version,)


def launch_start_kwargs(allow_duplicate: bool, profile_key: str | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"allow_duplicate": allow_duplicate}
    if profile_key is not None:
        kwargs["profile_key"] = profile_key
    return kwargs


def show_launch_profile_selector(
    app: Any,
    version: Any,
    on_selected: Callable[[str], None],
) -> bool:
    if not should_ask_profile_on_launch(app):
        return False

    try:
        profiles = app.profiles.get_all_profiles()
    except Exception as exc:
        app.log.error(f"Failed to load profiles for launch selection: {exc!r}")
        profiles = {}

    if not profiles:
        show_profile_required_dialog(app)
        return True

    theme = app.theme
    dialog: ui.AlertDialog | None = None

    def select_profile(profile_key: str) -> None:
        if dialog is not None:
            close_dialog(app.page, dialog)
        schedule_update(app.page)
        on_selected(profile_key)

    def close_selector(_event=None) -> None:
        if dialog is not None:
            close_dialog(app.page, dialog)
        schedule_update(app.page)

    rows = [
        _profile_row(app, profile_key, profile, select_profile)
        for profile_key, profile in _sorted_profiles(profiles)
    ]

    dialog = ui.AlertDialog(
        title=ui.Text(
            app.trans("launch_profile_select_title"),
            color=theme.text_color,
            weight=theme.font_weight_bold,
        ),
        modal=True,
        content=ui.Container(
            width=theme.modal_width,
            content=ui.Column(
                [
                    ui.Text(
                        app.trans(
                            "launch_profile_select_message",
                            version=getattr(version, "name", str(version)),
                        ),
                        color=theme.text_secondary,
                        size=theme.text_size_sm,
                    ),
                    ui.Column(rows, spacing=theme.spacing_sm, tight=True),
                ],
                spacing=theme.spacing_md,
                tight=True,
            ),
        ),
        actions=[
            ui.Button(
                text=app.trans("cancel"),
                variant="ghost",
                tone="neutral",
                on_click=close_selector,
            )
        ],
    )
    show_dialog(app.page, dialog)
    schedule_update(app.page)
    return True


def _sorted_profiles(profiles: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(
        profiles.items(),
        key=lambda item: (
            not bool(item[1].get("default")),
            str(item[1].get("name") or item[0]).lower(),
        ),
    )


def _profile_row(
    app: Any,
    profile_key: str,
    profile: dict[str, Any],
    on_selected: Callable[[str], None],
) -> ui.Container:
    theme = app.theme
    name = str(profile.get("name") or profile_key)
    profile_type = str(profile.get("type") or "").lower()
    is_offline = profile_type == "offline" or profile.get("access_token") == "offline"
    kind_label = app.trans("offline_account" if is_offline else "microsoft_account")
    default_suffix = f" • {app.trans('default')}" if profile.get("default") else ""

    return ui.Container(
        bgcolor=theme.bg_card,
        border=ft.Border.all(1, theme.border_color),
        border_radius=theme.radius(),
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        on_click=lambda _event: on_selected(profile_key),
        content=ui.Row(
            [
                ui.Icon(
                    ft.Icons.PERSON_OUTLINE if is_offline else ft.Icons.ACCOUNT_CIRCLE,
                    color=theme.primary,
                    size=22,
                ),
                ui.Column(
                    [
                        ui.Text(
                            name,
                            color=theme.text_color,
                            weight=theme.font_weight_semibold,
                        ),
                        ui.Text(
                            f"{kind_label}{default_suffix}",
                            color=theme.text_secondary,
                            size=theme.text_size_xs,
                        ),
                    ],
                    spacing=2,
                    tight=True,
                    expand=True,
                ),
                ui.Icon(ft.Icons.CHEVRON_RIGHT, color=theme.text_secondary, size=20),
            ],
            spacing=theme.spacing_sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
