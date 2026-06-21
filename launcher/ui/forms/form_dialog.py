from __future__ import annotations

import asyncio
import inspect

import flet as ft

from ..controls.button import Button
from ..controls.text import Text
from ..core.page_runtime import close_dialog, run_task, schedule_update, show_dialog
from ..feedback.alert_dialog import AlertDialog
from ..layout.column import Column
from ..theme import current_theme
from .field_specs import FieldSpec, apply_field_width, build_field


class FormDialog:
    def __init__(self, app, title, fields, on_submit, on_close=None, **kwargs):
        self.app = app
        self.page = self.app.page
        self.on_close = on_close
        self.on_submit = on_submit
        self.fields = [field if isinstance(field, FieldSpec) else FieldSpec(**field) for field in fields]
        self.inputs = {}
        theme = current_theme()
        self.content_width = kwargs.get("modal_width", theme.modal_width)
        self.content = self.generate_inputs()
        self.modal = AlertDialog(
            modal=True,
            title=Text(title, color=theme.text_color),
            actions=[
                Button(text=self.app.trans("done"), on_click=lambda _e: self.handle_submit()),
                Button(text=self.app.trans("cancel"), variant="outline", tone="neutral", on_click=lambda _e: self.close()),
            ],
            content=Column(
                self.content,
                width=self.content_width,
                height=kwargs.get("modal_height", theme.modal_height // 2),
                spacing=theme.spacing_md,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def generate_inputs(self):
        controls = []
        for field in self.fields:
            input_control = build_field(
                self.app,
                field,
                on_change=lambda e, key=field.key: self.on_change(e, key),
            )
            apply_field_width(input_control, self.content_width)
            controls.append(input_control)
            self.inputs[field.key] = input_control
        return controls

    def on_change(self, e, key):
        self.inputs[key].value = e.control.value

    def handle_submit(self):
        data = {key: input_control.value for key, input_control in self.inputs.items()}
        self.close()
        runner = getattr(self.page, "run_task", None)
        if callable(runner):
            run_task(self.page, self._submit_after_close, data)
            return
        self.on_submit(data)

    async def _submit_after_close(self, data):
        await asyncio.sleep(0)
        result = self.on_submit(data)
        if inspect.isawaitable(result):
            return await result
        return result

    def open(self):
        show_dialog(self.page, self.modal)
        schedule_update(self.page)

    def close(self):
        close_dialog(self.page, self.modal)
        schedule_update(self.page)
        if self.on_close:
            self.on_close()


__all__ = ["FormDialog"]
