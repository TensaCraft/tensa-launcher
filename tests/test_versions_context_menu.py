from __future__ import annotations

import asyncio

import flet as ft
import pytest

from launcher.app import App
from launcher.pages.mods_manager import ModsManagerPage
from launcher.pages.version_settings import VersionSettingsPage
from launcher.pages.versions import VersionsPage


@pytest.mark.parametrize(
    ("client", "icon"),
    [
        ("neoforge", ft.Icons.CONSTRUCTION),
        ("NeoForge", ft.Icons.CONSTRUCTION),
        ("forge", ft.Icons.BUILD),
        ("Forge", ft.Icons.BUILD),
    ],
)
def test_version_icon_distinguishes_neoforge_from_forge(fake_app, client, icon):
    assert VersionsPage(fake_app).get_version_icon(client) == icon


def test_context_menu_covers_all_tabs_without_changing_visible_actions(fake_app):
    page = VersionsPage(fake_app)
    context = page.view().controls[0]
    assert isinstance(context, ft.ContextMenu)
    card = context.content.content
    actions = card.content.controls[1]
    assert [action.icon for action in actions.controls] == [
        ft.Icons.PLAY_ARROW, ft.Icons.COPY, ft.Icons.EXTENSION, ft.Icons.FOLDER,
    ]
    assert all(isinstance(action, ft.FloatingActionButton) for action in actions.controls)
    keys = {item.key for item in context.items}
    assert {f"tab:{key}" for key, *_rest in ModsManagerPage.CONTENT_TABS} <= keys
    assert not any(key and (key.startswith("settings:") or key.startswith("modrinth:")) for key in keys)
    assert {"play", "copy", "directory", "shortcut", "tab:delete"} <= keys
    assert context.primary_trigger is None
    assert context.secondary_trigger is None
    assert context.content.on_secondary_tap_up is not None


@pytest.mark.parametrize("tab", [key for key, *_rest in ModsManagerPage.CONTENT_TABS])
def test_menu_navigates_to_requested_content_without_deleting(fake_app, tab):
    opened = []
    fake_app.show_mods_manager_page = lambda version, **kwargs: opened.append((version, kwargs))
    version = fake_app.versions.all()[0]
    page = VersionsPage(fake_app)
    item = next(item for item in page._build_version_menu_items(version) if item.key == f"tab:{tab}")

    asyncio.run(item.on_click(None))

    assert opened == [(version, {"initial_tab": tab})]
    assert fake_app.versions.get(version.version_id) is version


def test_old_public_actions_still_route_normally(fake_app, monkeypatch):
    opened = []
    page = VersionsPage(fake_app)
    version = fake_app.versions.all()[0]
    fake_app.show_mods_manager_page = lambda value: opened.append(("workspace", value))
    fake_app.util.open_mc_dir = lambda value: opened.append(("directory", value))
    monkeypatch.setattr(page, "handle_play", lambda value: opened.append(("play", value)))
    monkeypatch.setattr(page, "copy_version", lambda value: opened.append(("copy", value)))
    monkeypatch.setattr(page, "create_shortcut", lambda value: opened.append(("shortcut", value)))
    for item in page._build_version_menu_items(version):
        if item.key in {"play", "copy", "directory", "shortcut"}:
            asyncio.run(item.on_click(None))
    page.view().controls[0].content.content.on_click(None)
    assert opened == [
        ("play", version), ("copy", version), ("directory", version.path),
        ("shortcut", version), ("workspace", version),
    ]


@pytest.mark.parametrize("tab", [key for key, *_rest in ModsManagerPage.CONTENT_TABS])
def test_app_navigation_opens_real_workspace_tab(fake_app, tab):
    opened = []
    fake_app.show_page = lambda page: opened.append(page)
    App.show_mods_manager_page(fake_app, fake_app.versions.all()[0], initial_tab=tab)
    assert opened[0].current_content_key == tab
    opened[0].before_hide()


@pytest.mark.parametrize("tab", [key for key, *_rest in VersionSettingsPage.TABS])
def test_app_navigation_opens_real_embedded_settings_tab(fake_app, tab):
    opened = []
    fake_app.show_page = lambda page: opened.append(page)
    App.show_mods_manager_page(fake_app, fake_app.versions.all()[0], initial_tab="settings", settings_tab=tab)
    assert opened[0].version_settings_page.active_tab == tab
    opened[0].before_hide()


def test_app_navigation_opens_supported_modrinth_subtab(fake_app, monkeypatch):
    opened = []
    fake_app.show_page = lambda page: opened.append(page)
    monkeypatch.setattr(ModsManagerPage, "_search_mods", lambda self: None)
    App.show_mods_manager_page(fake_app, fake_app.versions.all()[0], initial_tab="resourcepacks", inner_tab="modrinth")
    assert opened[0].current_content_key == "resourcepacks"
    assert opened[0].current_inner_tab == "modrinth"
    opened[0].before_hide()


def test_app_navigation_normalizes_unsupported_subtab(fake_app):
    opened = []
    fake_app.show_page = lambda page: opened.append(page)
    App.show_mods_manager_page(fake_app, fake_app.versions.all()[0], initial_tab="mods", inner_tab="modrinth")
    assert opened[0].current_content_key == "mods"
    assert opened[0].current_inner_tab == "installed"
    opened[0].before_hide()


def test_shortcut_worker_reports_on_ui_task_without_using_desktop(fake_app, monkeypatch):
    calls = []
    messages = []
    page = VersionsPage(fake_app)
    monkeypatch.setattr("launcher.pages.version_actions.create_desktop_shortcut", lambda *args, **kwargs: calls.append((args, kwargs)))
    fake_app.feedback.info = messages.append

    asyncio.run(page._create_shortcut_async("stable-id", "Build name"))

    assert calls[0][0] == ("stable-id", "Build name")
    assert calls[0][1]["icon_source"] is None
    assert calls[0][1]["icon_directory"].name == "shortcut-icons"
    assert messages == ["desktop_shortcut_created"]


def test_shortcut_worker_failure_uses_localized_feedback(fake_app, monkeypatch):
    messages = []
    page = VersionsPage(fake_app)

    def fail(*_args, **_kwargs):
        raise PermissionError("Desktop unavailable")

    monkeypatch.setattr("launcher.pages.version_actions.create_desktop_shortcut", fail)
    fake_app.feedback.warning = messages.append
    asyncio.run(page._create_shortcut_async("id", "Build"))
    assert messages == ["desktop_shortcut_failed"]


def test_shortcut_completion_after_navigation_does_not_update_ui(fake_app, monkeypatch):
    page = VersionsPage(fake_app)
    messages = []
    fake_app.feedback.info = messages.append
    fake_app.feedback.warning = messages.append

    async def finish_after_navigation(*_args, **_kwargs):
        page.before_hide()

    monkeypatch.setattr("launcher.pages.version_actions.run_blocking", finish_after_navigation)
    asyncio.run(page._create_shortcut_async("id", "Build"))
    assert messages == []


def test_closed_menu_does_not_launch_or_navigate(fake_app, monkeypatch):
    page = VersionsPage(fake_app)
    calls = []
    monkeypatch.setattr(page, "handle_play", lambda value: calls.append(value))
    play = page._build_version_menu_items(fake_app.versions.all()[0])[0]
    page.before_hide()
    asyncio.run(play.on_click(None))
    assert calls == []
