from __future__ import annotations

import flet as ft

from ..controls.icon_button import IconButton
from ..controls.image import Image
from ..controls.text import Text
from ..core.page_runtime import invoke_on_ui
from ..layout.container import Container
from ..layout.row import Row
from ..theme import current_theme


class Header:
    def __init__(self, app) -> None:
        self.app = app
        self.reset_params()

    def reset_params(self):
        self.title = ""
        self.subtitle = ""
        self.show_back_btn = False
        self.back_action = None
        self.show_profile = False
        self.actions = None

    def set_params(self, title=None, subtitle=None, show_profile=False, show_back_btn=False, actions=None, back_action=None):
        self.title = title or ""
        self.subtitle = subtitle or ""
        self.show_back_btn = show_back_btn
        self.back_action = back_action
        self.show_profile = show_profile
        self.actions = actions

    def _profile_section(self):
        theme = current_theme()
        profile = self.app.profiles.get_default_profile()
        if profile is not None:
            avatar_id = profile.get("id") or profile.get("name")
            avatar_src = self.app.util.get_cached_skin_url(avatar_id)
            self.app.util.prefetch_skin(
                avatar_id,
                on_ready=lambda _src: invoke_on_ui(self.app.page, self.app.refresh_shell),
            )
            return Row(
                controls=[
                    Image(
                        src=avatar_src,
                        width=theme.shell_avatar_size,
                        height=theme.shell_avatar_size,
                        fit=ft.BoxFit.COVER,
                        border_radius=6,
                        error_content=IconButton(icon=ft.Icons.ACCOUNT_CIRCLE, on_click=lambda _e: self.app.show_profiles_page()),
                    ),
                    Text(profile.get("name"), size=theme.text_size_sm, weight=theme.font_weight_semibold),
                ],
                spacing=theme.spacing_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        return Row(
            controls=[
                IconButton(icon=ft.Icons.SUPERVISED_USER_CIRCLE, on_click=lambda _e: self.app.show_profiles_page()),
                Text(self.app.trans("profile_empty"), size=theme.text_size_sm, weight=theme.font_weight_semibold),
            ],
            spacing=theme.spacing_sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def view(self):
        theme = current_theme()
        leading_widgets: list[ft.Control] = []
        if self.show_back_btn:
            leading_widgets.append(
                IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    width=theme.shell_action_height,
                    height=theme.shell_action_height,
                    on_click=lambda _e: (self.back_action or self.app.show_home_page)(),
                )
            )
        if self.show_profile:
            leading_widgets.append(self._profile_section())
        if self.title:
            title_lines: list[ft.Control] = [
                Text(self.title, size=theme.text_size_lg, weight=theme.font_weight_bold, color=theme.text_color)
            ]
            if self.subtitle:
                title_lines.append(
                    Text(
                        self.subtitle,
                        size=theme.text_size_xs,
                        weight=theme.font_weight_medium,
                        color=theme.text_secondary,
                    )
                )
            leading_widgets.append(
                Container(
                    content=ft.Column(
                        controls=title_lines,
                        spacing=0,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                    padding=ft.Padding.only(left=theme.padding_sm if leading_widgets else 0),
                )
            )

        actions = list(self.actions or [])
        if getattr(self.app.progressbar, "open_button", None) is not None and self.app.progressbar.open_button not in actions:
            actions.append(self.app.progressbar.open_button)

        return Container(
            content=Row(
                controls=[
                    Container(
                        content=Row(leading_widgets, alignment=ft.MainAxisAlignment.START, spacing=theme.spacing_sm),
                        padding=ft.Padding.only(left=theme.padding_md),
                    ),
                    Container(expand=True),
                    Container(
                        content=Row(
                            actions,
                            alignment=ft.MainAxisAlignment.END,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=theme.spacing_sm,
                        ),
                        padding=ft.Padding.only(right=theme.padding_md),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=theme.header_height,
            bgcolor=theme.overlay(theme.alpha_header_footer, theme.bg_header_footer),
        )


__all__ = ["Header"]
