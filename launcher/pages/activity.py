from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from launcher import ui
from launcher.ui.core.page_runtime import run_task, schedule_update


class ActivityPanel:
    def __init__(self, app, *, scroll: bool = False) -> None:
        self.app = app
        self.page = app.page
        self.layout = ui.FormSection(app)
        self.scroll = scroll
        self._is_active = False
        self.content = ui.Container(
            expand=True,
            content=self._build_body(),
        )

    def view(self):
        return self.content

    def after_show(self) -> None:
        if self._is_active:
            return
        self._is_active = True
        try:
            run_task(self.page, self._refresh_loop)
        except Exception as exc:
            log = getattr(self.app, "log", None)
            debug = getattr(log, "debug", None)
            if callable(debug):
                debug(f"Failed to start activity refresh loop: {exc!r}")

    def before_hide(self) -> None:
        self._is_active = False

    def refresh(self) -> None:
        self._refresh_content()

    async def _refresh_loop(self) -> None:
        while self._is_active:
            await asyncio.sleep(0.5)
            if not self._is_active:
                break
            self._refresh_content()

    def _refresh_content(self) -> None:
        self.content.content = self._build_body()
        schedule_update(self.page)

    def _build_body(self) -> ft.Control:
        snapshot = self._snapshot()
        active_operations = snapshot.get("active_operations") or []
        recent_activity = snapshot.get("recent_activity") or []

        active_section = self.layout.section(
            title=self.app.trans("activity_active_operations"),
            description=self.app.trans("activity_active_operations_desc"),
            controls=[
                self.layout.wrap_control(control, {"sm": 12})
                for control in self._active_operation_rows(active_operations)
            ],
        )
        recent_section = self.layout.section(
            title=self.app.trans("activity_recent_events"),
            description=self.app.trans("activity_recent_events_desc"),
            controls=[
                self.layout.wrap_control(control, {"sm": 12})
                for control in self._activity_rows(recent_activity)
            ],
        )

        return ui.Column(
            controls=[active_section, recent_section],
            spacing=24,
            expand=self.scroll,
            scroll=ft.ScrollMode.AUTO if self.scroll else None,
        )

    def _snapshot(self) -> dict[str, Any]:
        feedback = getattr(self.app, "feedback", None)
        snapshot = getattr(feedback, "snapshot", None)
        if not callable(snapshot):
            return {"busy": False, "active_operations": [], "recent_activity": []}
        return snapshot(activity_limit=80)

    def _active_operation_rows(self, operations: list[dict[str, Any]]) -> list[ft.Control]:
        operations = self._display_operations(operations)
        if not operations:
            return [self._empty_text("activity_no_active_operations")]
        return [self._operation_row(operation) for operation in operations]

    def _activity_rows(self, entries: list[dict[str, Any]]) -> list[ft.Control]:
        if not entries:
            return [self._empty_text("activity_empty")]
        return [self._activity_row(entry) for entry in reversed(entries)]

    def _operation_row(self, operation: dict[str, Any]) -> ft.Control:
        progress, total = self._progress_values(operation)
        title = str(operation.get("title") or operation.get("kind") or "operation")
        status = str(operation.get("status") or title)
        kind = str(operation.get("kind") or "operation")
        return ui.Container(
            content=ui.Column(
                controls=[
                    ui.Row(
                        controls=[
                            ui.Text(title, color=self.app.theme.text_color, weight=self.app.theme.font_weight_semibold),
                            ui.Text(kind, color=self.app.theme.text_secondary, size=self.app.theme.text_size_xs),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ui.Text(status, color=self.app.theme.text_secondary, size=self.app.theme.text_size_sm),
                    ft.ProgressBar(value=min(progress / total, 1.0) if total > 0 else None),
                ],
                spacing=6,
                tight=True,
            ),
            bgcolor=self.app.theme.bg_card,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=self.app.theme.radius(),
            padding=ft.Padding.all(self.app.theme.padding_sm),
        )

    def _activity_row(self, entry: dict[str, Any]) -> ft.Control:
        level = str(entry.get("level") or "info")
        message = str(entry.get("message") or "")
        meta = self._activity_meta(entry)
        return ui.Container(
            content=ui.Row(
                controls=[
                    ui.Icon(self._level_icon(level), color=self._level_color(level), size=16),
                    ui.Text(
                        message,
                        color=self.app.theme.text_secondary,
                        size=self.app.theme.text_size_sm,
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ui.Text(meta, color=self.app.theme.text_tertiary, size=self.app.theme.text_size_xs),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_card,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=self.app.theme.radius(),
            padding=ft.Padding.symmetric(horizontal=self.app.theme.padding_sm, vertical=self.app.theme.padding_xs),
        )

    def _empty_text(self, key: str) -> ft.Control:
        return ui.Text(
            self.app.trans(key),
            color=self.app.theme.text_secondary,
            size=self.app.theme.text_size_sm,
        )

    @staticmethod
    def _progress_values(operation: dict[str, Any]) -> tuple[float, float]:
        try:
            progress = float(operation.get("progress") or 0)
            total = float(operation.get("total") or 100)
        except (TypeError, ValueError):
            return 0.0, 100.0
        return max(progress, 0.0), max(total, 1.0)

    def _display_operations(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._collapse_duplicate_operations(self._leaf_operations(operations))

    @staticmethod
    def _leaf_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parent_ids = {
            operation.get("parent_id")
            for operation in operations
            if operation.get("parent_id") is not None
        }
        leaf = [operation for operation in operations if operation.get("id") not in parent_ids]
        return leaf or operations

    @staticmethod
    def _collapse_duplicate_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collapsed: list[dict[str, Any]] = []
        key_index: dict[tuple[str, str], int] = {}
        for operation in operations:
            key = (
                str(operation.get("kind") or "operation").strip().lower(),
                str(operation.get("title") or "").strip().lower(),
            )
            if key in key_index:
                collapsed.pop(key_index[key])
                key_index = {
                    existing_key: index - 1 if index > key_index[key] else index
                    for existing_key, index in key_index.items()
                    if existing_key != key
                }
            key_index[key] = len(collapsed)
            collapsed.append(operation)
        return collapsed

    @staticmethod
    def _activity_meta(entry: dict[str, Any]) -> str:
        kind = str(entry.get("kind") or "").strip()
        event = str(entry.get("event") or "event").strip()
        if kind:
            return f"{kind} / {event}"
        return event

    def _level_icon(self, level: str) -> str:
        return {
            "success": ft.Icons.CHECK_CIRCLE_OUTLINE,
            "warning": ft.Icons.WARNING_AMBER,
            "error": ft.Icons.ERROR_OUTLINE,
        }.get(level, ft.Icons.INFO_OUTLINE)

    def _level_color(self, level: str) -> str:
        return {
            "success": self.app.theme.success,
            "warning": ft.Colors.AMBER,
            "error": self.app.theme.error,
        }.get(level, self.app.theme.primary)


class ActivityPage:
    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.panel = ActivityPanel(app, scroll=True)
        self.app.header.set_params(
            title=self.app.trans("activity_center"),
            subtitle=self.app.trans("activity_center_desc"),
            actions=[
                ui.Button(
                    text=self.app.trans("activity_refresh"),
                    icon=ft.Icons.REFRESH,
                    on_click=lambda _e: self.refresh(),
                    size="sm",
                )
            ],
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)
        self.content = ui.Container(
            padding=self.app.theme.version_content_padding,
            expand=True,
            content=self.panel.view(),
        )

    def __getattr__(self, name: str):
        return getattr(self.panel, name)

    def view(self):
        return self.content

    def after_show(self) -> None:
        self.panel.after_show()

    def before_hide(self) -> None:
        self.panel.before_hide()

    def refresh(self) -> None:
        self.panel.refresh()


__all__ = ["ActivityPage", "ActivityPanel"]
