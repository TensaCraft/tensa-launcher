from __future__ import annotations

import asyncio

import flet as ft

from launcher.pages.minecraft_components import MinecraftComponentsPage


def test_components_page_defers_filesystem_scan_when_runtime_accepts_task(fake_app, monkeypatch) -> None:
    scans: list[bool] = []
    scheduled = []
    monkeypatch.setattr(
        "launcher.pages.minecraft_components.InstalledComponentsService.list_installed",
        lambda _self: scans.append(True) or [],
    )
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs)) or object()

    page = MinecraftComponentsPage(fake_app)

    assert scans == []
    assert scheduled[0][0] == page._refresh_installed_async


def test_components_page_replaces_loading_state_when_background_scan_fails(fake_app, monkeypatch) -> None:
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs)) or object()

    async def fail_scan(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr("launcher.pages.minecraft_components.run_blocking", fail_scan)
    page = MinecraftComponentsPage(fake_app)
    task, args, kwargs = scheduled[0]

    asyncio.run(task(*args, **kwargs))

    state = page.content_list.controls[0]
    assert isinstance(state, ft.Container)
    assert isinstance(state.content, ft.Row)
    state_text = state.content.controls[1]
    assert isinstance(state_text, ft.Text)
    assert state_text.value == "unknown_error"
    page._rebuild_content()
    assert page.content_list.controls[0] is not None
    rebuilt_state = page.content_list.controls[0]
    assert isinstance(rebuilt_state, ft.Container)
    assert isinstance(rebuilt_state.content, ft.Row)
    rebuilt_text = rebuilt_state.content.controls[1]
    assert isinstance(rebuilt_text, ft.Text)
    assert rebuilt_text.value == "unknown_error"


def test_components_page_ignores_background_result_after_hide(fake_app, monkeypatch) -> None:
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs)) or object()

    async def complete_scan(*_args, **_kwargs):
        return []

    monkeypatch.setattr("launcher.pages.minecraft_components.run_blocking", complete_scan)
    page = MinecraftComponentsPage(fake_app)
    loading_controls = page.content_list.controls
    task, args, kwargs = scheduled[0]

    page.before_hide()
    asyncio.run(task(*args, **kwargs))

    assert page.content_list.controls is loading_controls
