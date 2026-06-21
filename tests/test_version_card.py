from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft


def _version_card_preview(card: ft.GestureDetector) -> ft.Container:
    body = card.content.content
    return body.controls[2]


def _contains_control(control: ft.Control | None, control_type: type[ft.Control]) -> bool:
    if control is None:
        return False
    if isinstance(control, control_type):
        return True
    content = getattr(control, "content", None)
    if _contains_control(content, control_type):
        return True
    for child in getattr(control, "controls", []) or []:
        if _contains_control(child, control_type):
            return True
    return False


def test_version_card_uses_replacement_action_surface_instead_of_icon_overlay(fake_app):
    card = fake_app.version_card.create(
        title="Aeronautics",
        subtitle="TensaCraft 1.21.1",
        image="bad-base64",
        on_action_click=lambda _e: None,
    )

    preview = _version_card_preview(card)

    assert not isinstance(preview.content, ft.Stack)


def test_version_card_keeps_launch_indicator_visible_after_hover_exit(fake_app):
    started = []
    card = fake_app.version_card.create(
        title="Aeronautics",
        subtitle="TensaCraft 1.21.1",
        image="bad-base64",
        on_action_click=lambda _e: started.append(True),
    )
    preview = _version_card_preview(card)

    card.on_enter(SimpleNamespace())
    action = preview.content
    action.on_tap(SimpleNamespace())
    card.on_exit(SimpleNamespace())

    assert started == [True]
    assert _contains_control(preview.content, ft.ProgressRing)


def test_version_card_defers_launch_callback_until_indicator_can_paint(fake_app, monkeypatch):
    tasks = []

    class FakePage:
        def run_task(self, task):
            tasks.append(task)

    monkeypatch.setattr(type(fake_app.version_card), "_control_page", staticmethod(lambda _control: FakePage()))
    started = []
    card = fake_app.version_card.create(
        title="Aeronautics",
        subtitle="TensaCraft 1.21.1",
        image="bad-base64",
        on_action_click=lambda _e: started.append(True),
    )
    preview = _version_card_preview(card)

    card.on_enter(SimpleNamespace())
    preview.content.on_tap(SimpleNamespace())

    assert started == []
    assert _contains_control(preview.content, ft.ProgressRing)
    asyncio.run(tasks[-1]())
    assert started == [True]
