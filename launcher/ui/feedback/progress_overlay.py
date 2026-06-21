from __future__ import annotations

import asyncio

import flet as ft

from ..controls.progress_bar import ProgressBar
from ..controls.progress_ring import ProgressRing
from ..controls.text import Text
from ..core.page_runtime import close_dialog, discard_closed_dialog, invoke_on_ui, schedule_update, show_dialog
from ..layout.column import Column
from ..layout.container import Container
from ..theme import current_theme
from .bottom_sheet import BottomSheet


class ProgressOverlay:
    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.closed_manually = False
        self.status_label = None
        self.progress_bar = None
        self.bottom_sheet = None
        self.open_button = None
        self._sheet_open_requested = False
        self._dismiss_pending = False
        self._dismiss_manual = False
        self._dismiss_waiters: list[asyncio.Future[None]] = []
        self._cycle_id = 0
        self._build_ui()

    def _build_ui(self) -> None:
        theme = current_theme()
        self.status_label = Text("", size=theme.text_size_lg, weight=theme.font_weight_medium)
        self.progress_bar = ProgressBar(
            width=theme.progress_bar_width,
            color=theme.primary,
            bgcolor=theme.overlay(theme.alpha_progress_bg, theme.bg_list),
        )
        content = Container(
            content=Column(
                controls=[self.status_label, self.progress_bar],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=theme.spacing_lg,
            ),
            padding=theme.padding_2xl,
            bgcolor=theme.overlay(theme.alpha_modal_bg, theme.bg_card),
            border_radius=theme.radius(md=True),
            border=ft.Border.all(1, theme.border_color),
        )
        self.bottom_sheet = BottomSheet(content=content, open=False, on_dismiss=self._on_bottom_sheet_dismiss)
        self.open_button = Container(
            content=ft.GestureDetector(
                content=ProgressRing(
                    width=theme.progress_ring_size,
                    height=theme.progress_ring_size,
                    stroke_width=theme.stroke_width_sm,
                    color=theme.primary,
                ),
                on_tap=self.toggle_bottom_sheet,
            ),
            width=theme.progress_button_size,
            height=theme.progress_button_size,
            alignment=ft.Alignment.CENTER,
            bgcolor=theme.overlay(theme.alpha_progress_ring_bg, theme.bg_header_footer),
            border_radius=theme.radius(lg=True),
            visible=False,
            animate_opacity=theme.animation_duration_normal,
        )
        schedule_update(self.page)

    def _on_bottom_sheet_dismiss(self, _):
        self.closed_manually = self._dismiss_manual or not self._dismiss_pending
        self._sheet_open_requested = False
        self._dismiss_pending = False
        self._resolve_dismiss_waiters()

    def _resolve_dismiss_waiters(self) -> None:
        waiters = self._dismiss_waiters
        self._dismiss_waiters = []
        for waiter in waiters:
            if not waiter.done():
                loop = waiter.get_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(waiter.set_result, None)
                else:
                    waiter.set_result(None)

    async def wait_until_hidden(self, *, timeout: float = 0.6) -> None:
        if not self._dismiss_pending and not self.bottom_sheet.open and not self._sheet_open_requested:
            return
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._dismiss_waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            self._dismiss_pending = False
            self._resolve_dismiss_waiters()

    def _update_page(self):
        try:
            schedule_update(self.page)
        except Exception as exc:
            self.app.log.debug(f"Progress overlay update skipped: {exc}")

    def toggle_bottom_sheet(self, _=None) -> None:
        try:
            if self.bottom_sheet.open or self._sheet_open_requested:
                self._dismiss_pending = True
                self._dismiss_manual = True
                close_dialog(self.page, self.bottom_sheet)
                self.closed_manually = True
                self._sheet_open_requested = False
                self.bottom_sheet.open = False
                self._discard_closed_sheet()
            else:
                self._sheet_open_requested = True
                show_dialog(self.page, self.bottom_sheet)
                self.bottom_sheet.open = True
                self.closed_manually = False
        except Exception as exc:
            self._sheet_open_requested = bool(getattr(self.bottom_sheet, "open", False))
            self.app.log.debug(f"Toggle bottom sheet skipped: {exc}")
        self._update_page()

    def _start_cycle_impl(self, cycle_id: int | None = None) -> None:
        if cycle_id is not None and cycle_id != self._cycle_id:
            return
        self.closed_manually = False
        self._dismiss_pending = False
        self._dismiss_manual = False
        self.open_button.visible = True
        self._update_page()

    def start_cycle(self):
        self._cycle_id += 1
        return invoke_on_ui(self.page, self._start_cycle_impl, self._cycle_id)

    def _show_impl(
        self,
        status: str,
        progress: float = 0,
        max_progress: float = 100,
        force_open: bool = False,
        auto_open: bool = True,
        cycle_id: int | None = None,
    ):
        if cycle_id is not None and cycle_id != self._cycle_id:
            return
        self.status_label.value = status
        self.progress_bar.value = progress / max(max_progress, 1)
        self.open_button.visible = True
        if force_open and auto_open:
            self.closed_manually = False
        if auto_open and not self.bottom_sheet.open and not self._sheet_open_requested and not self.closed_manually:
            self._sheet_open_requested = True
            try:
                show_dialog(self.page, self.bottom_sheet)
                self.bottom_sheet.open = True
            except Exception as exc:
                self._sheet_open_requested = False
                self.bottom_sheet.open = False
                self.app.log.debug(f"Progress overlay open skipped: {exc}")
        self._update_page()

    def show(
        self,
        status: str,
        progress: float = 0,
        max_progress: float = 100,
        *,
        force_open: bool = False,
        auto_open: bool = True,
    ):
        cycle_id = self._cycle_id
        return invoke_on_ui(
            self.page,
            self._show_impl,
            status,
            progress,
            max_progress,
            force_open,
            auto_open,
            cycle_id,
        )

    def _hide_impl(self, _=None, *, manual: bool = False, cycle_id: int | None = None):
        if cycle_id is None:
            self._cycle_id += 1
        elif cycle_id != self._cycle_id:
            return
        was_open = bool(self.bottom_sheet.open or self._sheet_open_requested)
        self._dismiss_pending = was_open
        self._dismiss_manual = manual
        try:
            close_dialog(self.page, self.bottom_sheet)
        except Exception as exc:
            self.app.log.debug(f"Progress overlay hide skipped: {exc}")
        self.bottom_sheet.open = False
        self._sheet_open_requested = False
        self.closed_manually = manual
        self.open_button.visible = False
        if self._discard_closed_sheet() or not was_open:
            self._resolve_dismiss_waiters()
        self._update_page()

    def hide(self, _=None, *, manual: bool = False):
        self._cycle_id += 1
        return invoke_on_ui(self.page, self._hide_impl, _, manual=manual, cycle_id=self._cycle_id)

    def _discard_closed_sheet(self) -> bool:
        try:
            removed = discard_closed_dialog(self.page, self.bottom_sheet)
        except Exception as exc:
            self.app.log.debug(f"Progress overlay cleanup skipped: {exc}")
            return False
        if removed:
            self._on_bottom_sheet_dismiss(None)
        return removed

__all__ = ["ProgressOverlay"]
