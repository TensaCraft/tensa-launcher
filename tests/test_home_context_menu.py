from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from launcher.pages.home import Home
from launcher.pages.version_actions import VersionActions
from launcher.pages.versions import VersionsPage


def remote_version(page):
    return page.catalog.build_stub(
        {"name": "aeronautics", "client": {"id": "aeronautics", "name": "Aeronautics"}},
        "aeronautics",
    )


def test_home_empty_space_menu_matches_build_navigation(fake_app, monkeypatch):
    fake_app.config.set("show_tensacraft_versions", "no")
    routes = []
    fake_app.show_version_create_page = lambda: routes.append("add_version")
    fake_app.show_minecraft_components_page = lambda: routes.append("minecraft_components_nav")
    fake_app.show_modpacks_page = lambda: routes.append("modpacks_title")
    fake_app.curseforge_import_modal = lambda _app: SimpleNamespace(show=lambda: routes.append("import_curseforge"))
    shell = []
    monkeypatch.setattr(fake_app.header, "set_params", lambda **params: shell.append(params))

    page = Home(fake_app)
    context = page.view()

    assert isinstance(context, ft.ContextMenu)
    assert context.content.content is page.grid
    assert context.expand is True
    assert [item.key for item in context.items] == [
        "add_version", "minecraft_components_nav", "modpacks_title", "import_curseforge",
    ]
    assert [item.content.value for item in context.items] == [button.content for button in page._build_header_actions()]
    assert shell == [{"title": "home_title"}]
    for item in context.items:
        asyncio.run(item.on_click(None))
    assert routes == [item.key for item in context.items]


def test_shared_actions_do_not_change_header_or_footer(fake_app, monkeypatch):
    def unexpected(**_params):
        raise AssertionError("Shared actions must not initialize page chrome")

    monkeypatch.setattr(fake_app.header, "set_params", unexpected)
    monkeypatch.setattr(fake_app.footer, "set_params", unexpected)
    actions = VersionActions(fake_app)
    assert actions._build_version_menu_items(fake_app.versions.all()[0])
    assert actions._build_navigation_menu_items()
    actions.before_hide()


def test_installed_home_card_has_exact_same_menu_as_versions_list(fake_app):
    version = fake_app.versions.all()[0]
    home = Home(fake_app)
    builds = VersionsPage(fake_app)
    home_context = home.create_card(version)
    builds_context = builds.view().controls[0]

    def menu_signature(context):
        return [(item.key, item.icon, getattr(item.content, "value", None)) for item in context.items]

    assert menu_signature(home_context) == menu_signature(builds_context)
    assert Home._build_version_menu_items is VersionsPage._build_version_menu_items
    assert Home.handle_play is VersionsPage.handle_play
    assert Home.copy_version is VersionsPage.copy_version
    assert Home.create_shortcut is VersionsPage.create_shortcut
    assert Home._context_menu is VersionsPage._context_menu


@pytest.mark.parametrize("remote", [False, True])
def test_home_card_keeps_primary_action_and_has_no_primary_menu(fake_app, monkeypatch, remote):
    page = Home(fake_app)
    version = remote_version(page) if remote else fake_app.versions.all()[0]
    calls = []
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return ft.Text("card")

    monkeypatch.setattr(fake_app.version_card, "create", create)
    monkeypatch.setattr(page, "start_version", calls.append)
    context = page.create_card(version)
    created[0]["on_action_click"](None)

    assert calls == [version]
    assert context.primary_trigger is None
    assert context.content.on_tap is None
    assert context.content.on_tap_down is None
    assert created[0]["action_icon"] == (ft.Icons.DOWNLOAD_ROUNDED if remote else ft.Icons.PLAY_ARROW_ROUNDED)


def test_remote_home_card_only_offers_install_and_retains_pending_key(fake_app):
    page = Home(fake_app)
    version = remote_version(page)
    context = page.create_card(version)
    page.grid = ft.GridView(controls=[context])

    assert context.key == "tensacraft:aeronautics"
    assert [item.key for item in context.items] == ["install"]
    assert page._build_version_menu_items(version) == []
    asyncio.run(context.items[0].on_click(None))
    assert page._tensacraft_install_dialog.open is True
    assert fake_app.versions.get_by_name("Aeronautics") is None
    page.hide_pending_tensacraft_pack("aeronautics")
    assert page.grid.controls == []
    page.before_hide()


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_context_menu_uses_exclusive_secondary_gesture_and_pointer_position(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    calls = []
    menu = page._context_menu(ft.Text("content"), page._build_navigation_menu_items())

    async def open_menu(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(menu, "open", open_menu)
    event = SimpleNamespace(global_position=ft.Offset(42, 87))
    assert menu.secondary_trigger is None
    assert menu.tertiary_trigger is None
    assert menu.content.on_secondary_tap_down is None
    asyncio.run(menu.content.on_secondary_tap_up(event))
    assert calls == [{"global_position": event.global_position}]
    page.before_hide()
    asyncio.run(menu.content.on_secondary_tap_up(event))
    assert len(calls) == 1


def test_home_closed_menus_cannot_navigate_launch_or_install(fake_app, monkeypatch):
    page = Home(fake_app)
    items = page._build_navigation_menu_items()
    items += page.create_card(fake_app.versions.all()[0]).items
    items += page.create_card(remote_version(page)).items
    calls = []
    monkeypatch.setattr(page, "handle_play", calls.append)
    monkeypatch.setattr(page, "_install_and_launch_tensacraft", calls.append)
    monkeypatch.setattr(page, "copy_version", calls.append)
    monkeypatch.setattr(page, "create_shortcut", calls.append)
    monkeypatch.setattr(page, "open_directory", calls.append)
    fake_app.show_mods_manager_page = lambda *args, **kwargs: calls.append(args)
    page.before_hide()

    for item in items:
        if item.on_click:
            asyncio.run(item.on_click(None))
    assert calls == []


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_copy_menu_opens_existing_confirmation_modal(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    version = fake_app.versions.all()[0]
    calls = []
    monkeypatch.setattr(
        "launcher.ui.VersionCopyModal",
        lambda app, source: SimpleNamespace(show=lambda: calls.append((app, source))),
    )
    copy = next(item for item in page._build_version_menu_items(version) if item.key == "copy")
    asyncio.run(copy.on_click(None))
    assert calls == [(fake_app, version)]


def test_home_catalog_completion_after_hide_does_not_add_cards(fake_app, monkeypatch):
    page = Home(fake_app)
    page.grid = ft.GridView()

    async def fetch(*_args):
        page.before_hide()
        return [{"name": "aeronautics", "client": {"id": "aeronautics", "name": "Aeronautics"}}]

    monkeypatch.setattr("launcher.pages.home.run_blocking", fetch)
    asyncio.run(page._load_tensacraft_versions())
    assert page.grid.controls == []


def test_home_install_completion_after_hide_finishes_operation_without_launch(fake_app, monkeypatch):
    page = Home(fake_app)
    calls = []
    installed = SimpleNamespace(start=lambda: calls.append("launch"))
    operation = SimpleNamespace(finish=lambda *args, **kwargs: calls.append("finish"))

    async def install(*_args):
        page.before_hide()
        return installed

    monkeypatch.setattr("launcher.pages.home.run_blocking", install)
    asyncio.run(page._install_and_launch_tensacraft_async("Aeronautics", "aeronautics", operation))
    assert calls == ["finish"]
    assert not getattr(fake_app, "pending_tensacraft_pack_ids", set())


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_delayed_duplicate_confirmation_cannot_launch_after_navigation(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    callbacks = []
    scheduled = []
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True))
    fake_app.feedback.confirm = lambda _title, _body, callback: callbacks.append(callback)
    monkeypatch.setattr("launcher.pages.version_actions.run_task", lambda *_args: scheduled.append(True))

    page.handle_play(fake_app.versions.all()[0])
    page.before_hide()
    callbacks[0](True)
    assert scheduled == []


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_delayed_profile_selection_cannot_launch_after_navigation(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    callbacks = []
    scheduled = []
    monkeypatch.setattr(
        "launcher.pages.version_actions.show_launch_profile_selector",
        lambda _app, _version, callback: callbacks.append(callback) or True,
    )
    monkeypatch.setattr("launcher.pages.version_actions.run_task", lambda *_args: scheduled.append(True))
    page.handle_play(fake_app.versions.all()[0])
    page.before_hide()
    callbacks[0]("default")
    assert scheduled == []


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_shared_play_keeps_duplicate_confirmation_and_selected_profile(fake_app, monkeypatch, page_type):
    page = page_type(fake_app)
    version = fake_app.versions.all()[0]
    callbacks = []
    scheduled = []
    started = []
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True))
    fake_app.feedback.confirm = lambda _title, _body, callback: callback(True)
    monkeypatch.setattr(
        "launcher.pages.version_actions.show_launch_profile_selector",
        lambda _app, _version, callback: callbacks.append(callback) or True,
    )
    fake_app.page.run_task = lambda task, *args: scheduled.append((task, args))
    version.start = lambda **kwargs: started.append(kwargs)

    page.handle_play(version)
    assert scheduled == []
    callbacks[0]("second")
    assert scheduled == [(page._handle_play_async, (version, True, "second"))]
    asyncio.run(page._handle_play_async(version, True, "second"))
    assert started == [{"allow_duplicate": True, "profile_key": "second"}]


@pytest.mark.parametrize("page_type", [Home, VersionsPage])
def test_busy_launcher_does_not_start_game_from_menu(fake_app, page_type):
    page = page_type(fake_app)
    scheduled = []
    messages = []
    fake_app.feedback.is_busy = lambda: True
    fake_app.feedback.info = messages.append
    fake_app.page.run_task = lambda *args: scheduled.append(args)

    asyncio.run(page._build_version_menu_items(fake_app.versions.all()[0])[0].on_click(None))
    assert scheduled == []
    assert messages == ["installation_already_running"]


def test_hidden_remote_confirmation_cannot_install(fake_app, monkeypatch):
    page = Home(fake_app)
    page.start_version(remote_version(page))
    dialog = page._tensacraft_install_dialog
    calls = []
    monkeypatch.setattr(page, "_start_tensacraft_install", lambda *args: calls.append(args))

    page.before_hide()
    assert dialog.open is False
    dialog.actions[0].on_click(None)
    assert calls == []
    assert page._tensacraft_install_target is None


@pytest.mark.parametrize("hide", [False, True])
def test_menu_open_only_ignores_runtime_errors_from_closed_page(fake_app, monkeypatch, hide):
    page = Home(fake_app)
    menu = page._context_menu(ft.Text("content"), page._build_navigation_menu_items())

    async def fail(**_kwargs):
        if hide:
            page.before_hide()
        raise RuntimeError("Control was removed")

    monkeypatch.setattr(menu, "open", fail)
    event = SimpleNamespace(global_position=ft.Offset(42, 87))
    if hide:
        asyncio.run(menu.content.on_secondary_tap_up(event))
    else:
        with pytest.raises(RuntimeError, match="Control was removed"):
            asyncio.run(menu.content.on_secondary_tap_up(event))
