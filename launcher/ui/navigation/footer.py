from __future__ import annotations

import flet as ft

from ..controls.icon_button import IconButton
from ..controls.text import Text
from ..layout.container import Container
from ..layout.row import Row
from ..theme import current_theme


class Footer:
    def __init__(self, app) -> None:
        self.app = app
        self._root = None
        self._leading_slot = None
        self._trailing_slot = None
        self._center_slot = None
        self.reset_params()

    def reset_params(self):
        self.center_mode = None
        self.center_btn = None
        self.center_text = None
        self.center_control = None
        self.show_left_btn = False
        self.show_right_btn = False
        self.btn_right = {}
        self.btn_left = {}

    def set_params(
        self,
        center_btn: dict | None = None,
        left_btn: bool = False,
        right_btn: bool = False,
        data_left_btn: dict | None = None,
        data_right_btn: dict | None = None,
        *,
        center_text: dict | None = None,
        center_control: ft.Control | None = None,
        center_mode: str | None = None,
    ):
        self.center_btn = center_btn
        self.center_text = center_text
        self.center_control = center_control
        self.show_left_btn = bool(left_btn)
        self.show_right_btn = bool(right_btn)
        self.btn_left = data_left_btn or {}
        self.btn_right = data_right_btn or {}
        if center_mode in ("button", "text", "control"):
            self.center_mode = center_mode
        elif center_control is not None:
            self.center_mode = "control"
        elif center_text:
            self.center_mode = "text"
        elif center_btn:
            self.center_mode = "button"
        else:
            self.center_mode = None
        self._sync_view()

    def center_text_control(self):
        theme = current_theme()
        text = self.center_text.get("text", "")
        return Container(
            content=Text(
                text,
                color=self.center_text.get("color", theme.text_color),
                size=self.center_text.get("size", theme.text_size_sm),
                weight=self.center_text.get("weight"),
                no_wrap=True,
            ),
            alignment=ft.Alignment.CENTER,
        )

    def left_btn(self):
        theme = current_theme()
        return IconButton(
            icon=self.btn_left.get("icon", ft.Icons.SETTINGS),
            on_click=self.btn_left.get("on_click", lambda _e: self.app.show_settings_page()),
            tooltip=self.btn_left.get("tooltip_message", self.app.trans("settings_title")),
            width=theme.shell_action_height,
            height=theme.shell_action_height,
            variant="solid",
        )

    def right_btn(self):
        theme = current_theme()
        return IconButton(
            icon=self.btn_right.get("icon", ft.Icons.SUPERVISED_USER_CIRCLE),
            on_click=self.btn_right.get("on_click", lambda _e: self.app.show_profiles_page()),
            tooltip=self.btn_right.get("tooltip_message", self.app.trans("profile_title")),
            width=theme.shell_action_height,
            height=theme.shell_action_height,
            variant="solid",
        )

    def _center_content(self):
        if self.center_mode == "control":
            return self.center_control
        if self.center_mode == "text" and self.center_text and self.center_text.get("text"):
            return self.center_text_control()
        if self.center_mode == "button" and self.center_btn:
            theme = current_theme()
            return IconButton(
                icon=self.center_btn.get("icon"),
                on_click=self.center_btn.get("on_click"),
                width=theme.shell_action_height,
                height=theme.shell_action_height,
                variant="solid",
            )
        return None

    def _sync_view(self):
        if self._root is None:
            return
        theme = current_theme()
        self._leading_slot.content = Row(
            [self.left_btn()] if self.show_left_btn else [],
            alignment=ft.MainAxisAlignment.START,
            spacing=theme.spacing_sm,
        )
        self._trailing_slot.content = Row(
            [self.right_btn()] if self.show_right_btn else [],
            alignment=ft.MainAxisAlignment.END,
            spacing=theme.spacing_sm,
        )
        self._center_slot.content = self._center_content()

    def view(self):
        theme = current_theme()
        if self._root is None:
            self._leading_slot = Container(padding=ft.Padding.only(left=theme.padding_md))
            self._trailing_slot = Container(padding=ft.Padding.only(right=theme.padding_md))
            self._center_slot = Container(alignment=ft.Alignment.CENTER, expand=True)
            self._root = Container(
                content=ft.Stack(
                    controls=[
                        Row(
                            controls=[
                                self._leading_slot,
                                Container(expand=True),
                                self._trailing_slot,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self._center_slot,
                    ]
                ),
                height=theme.footer_height,
                bgcolor=theme.overlay(theme.alpha_header_footer, theme.bg_header_footer),
            )
        self._sync_view()
        return self._root


__all__ = ["Footer"]
