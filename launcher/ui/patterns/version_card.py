from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from pathlib import Path

import flet as ft

from ..controls.icon import Icon
from ..controls.image import Image
from ..controls.progress_ring import ProgressRing
from ..controls.text import Text
from ..core.click_sound import _play_click_sound
from ..layout.column import Column
from ..layout.container import Container
from ..theme import current_theme


class VersionCard:
    @staticmethod
    def _preview_height(theme) -> int:
        return max(theme.version_image_size_compact + theme.padding_md * 2, 72)

    @classmethod
    def _preview_media_size(cls, theme) -> int:
        return min(theme.version_image_size_compact, cls._preview_height(theme) - theme.padding_md * 2)

    @classmethod
    def _action_button_size(cls, theme) -> int:
        target_size = max(theme.button_height + theme.padding_md, theme.icon_size_lg + theme.padding_xl)
        return min(target_size, cls._preview_height(theme) - theme.padding_md)

    @staticmethod
    def _is_url(image: str) -> bool:
        return image.startswith("http")

    @staticmethod
    def _control_page(control):
        try:
            return control.page
        except Exception:
            return None

    def _build_image_control(self, image: str | None):
        theme = current_theme()
        preview_size = self._preview_media_size(theme)
        if not image:
            return Icon(ft.Icons.IMAGE, size=preview_size, color=theme.text_tertiary)
        if self._is_url(image):
            return Image(src=image, width=preview_size, height=preview_size, fit=ft.BoxFit.CONTAIN)
        image_path = Path(image)
        if image_path.exists():
            return Image(src=str(image_path), width=preview_size, height=preview_size, fit=ft.BoxFit.CONTAIN)
        return Image(src_base64=image, width=preview_size, height=preview_size, fit=ft.BoxFit.CONTAIN)

    @classmethod
    def _build_action_surface(cls, icon=None, *, launching: bool = False):
        theme = current_theme()
        button_size = cls._action_button_size(theme)
        icon_size = min(button_size - theme.padding_md, theme.icon_size_lg + theme.padding_xs)
        content = (
            ProgressRing(
                width=icon_size,
                height=icon_size,
                stroke_width=theme.stroke_width_sm,
                color=theme.primary_light,
            )
            if launching
            else Icon(icon, color=theme.primary_light, size=icon_size)
        )
        return Container(
            content=content,
            width=button_size,
            height=button_size,
            bgcolor=theme.overlay(0.16, theme.primary),
            border=ft.Border.all(1, theme.overlay(0.9, theme.primary)),
            border_radius=theme.radius(lg=True),
            alignment=ft.Alignment.CENTER,
            animate_scale=ft.Animation(theme.animation_duration_fast, theme.animation_curve_default),
            scale=1.0,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=12,
                color=theme.overlay(0.25, theme.primary),
                offset=ft.Offset(0, 2),
            ),
        )

    @classmethod
    def _build_action_button(cls, on_click, icon, on_launch_start):
        action_button = cls._build_action_surface(icon)

        def on_button_hover(e):
            action_button.scale = 1.08 if e.data == "true" else 1.0
            with suppress(Exception):
                action_button.update()

        action_button.on_hover = on_button_hover

        def wrapped_on_click(e):
            _play_click_sound()
            on_launch_start()

            async def run_click_after_paint():
                await asyncio.sleep(0.05)
                if on_click:
                    result = on_click(e)
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(result, timeout=None)

            page = cls._control_page(action_button)
            if page and hasattr(page, "run_task"):
                page.run_task(run_click_after_paint)
            elif on_click:
                on_click(e)

        return ft.GestureDetector(content=action_button, on_tap=wrapped_on_click, mouse_cursor=ft.MouseCursor.CLICK)

    def create(
        self,
        title: str,
        subtitle: str | None = None,
        image: str | None = None,
        on_action_click=None,
        action_icon: ft.Icons = ft.Icons.PLAY_ARROW_ROUNDED,
    ):
        theme = current_theme()
        preview_height = self._preview_height(theme)
        original_content = self._build_image_control(image)
        image_container = Container(
            content=original_content,
            alignment=ft.Alignment.CENTER,
            height=preview_height,
            bgcolor=theme.overlay(0.15, theme.bg_list),
            border_radius=theme.radius(md=True),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=ft.Padding.all(theme.padding_xs),
        )
        launch_state = {"hovered": False, "launching": False}
        action_control = None
        if on_action_click:
            def update_preview(content):
                image_container.content = content
                with suppress(Exception):
                    image_container.update()

            def restore_preview_after_launch():
                launch_state["launching"] = False
                update_preview(action_control if launch_state["hovered"] else original_content)

            def schedule_restore():
                async def restore():
                    await asyncio.sleep(3.5)
                    restore_preview_after_launch()

                page = self._control_page(image_container)
                if page and hasattr(page, "run_task"):
                    page.run_task(restore)
                    return
                page = self._control_page(action_control) if action_control is not None else None
                if page and hasattr(page, "run_task"):
                    page.run_task(restore)

            def on_launch_start():
                launch_state["launching"] = True
                update_preview(self._build_action_surface(launching=True))
                schedule_restore()

            action_control = self._build_action_button(on_action_click, action_icon, on_launch_start)
        subtitle_control = None
        if subtitle:
            subtitle_control = Text(
                subtitle,
                size=theme.text_size_xs,
                color=theme.text_secondary,
                text_align=ft.TextAlign.CENTER,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        title_control = Text(
            title,
            size=theme.text_size_sm,
            weight=theme.font_weight_bold,
            text_align=ft.TextAlign.CENTER,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        title_block = Container(
            content=title_control,
            height=round(theme.text_size_sm * 2.6),
            alignment=ft.Alignment.CENTER,
        )
        body = Column(
            controls=[
                title_block,
                subtitle_control or Container(height=0),
                image_container,
                Container(height=theme.spacing_sm),
            ],
            spacing=theme.spacing_sm,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        card = Container(
            content=body,
            padding=ft.Padding.all(theme.card_padding),
            bgcolor=theme.overlay(0.82, theme.bg_card),
            border=ft.Border.all(1, theme.overlay(0.25, theme.primary)),
            border_radius=theme.radius(md=True),
            animate_scale=ft.Animation(theme.animation_duration_fast, theme.animation_curve_default),
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=10,
                color=theme.overlay(0.2, theme.primary),
                offset=ft.Offset(0, 3),
            ),
        )

        def apply_hover_state(is_hovered: bool):
            launch_state["hovered"] = is_hovered
            card.scale = 1.03 if is_hovered else 1.0
            if action_control is not None and not launch_state["launching"]:
                image_container.content = action_control if is_hovered else original_content
            with suppress(Exception):
                card.update()

        return ft.GestureDetector(
            content=card,
            on_enter=lambda _e: apply_hover_state(True),
            on_exit=lambda _e: apply_hover_state(False),
            mouse_cursor=ft.MouseCursor.CLICK if on_action_click else None,
        )


__all__ = ["VersionCard"]
