import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from launcher.pages.profiles import ProfilesPage


def walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from walk(content)
    for field in ("controls", "items"):
        for child in getattr(control, field, ()):
            yield from walk(child)


def menu(control):
    return {item.key: item for item in control.items if item.key}


def click(control):
    asyncio.run(control.on_click(None))


@pytest.fixture
def profiles_app(fake_app):
    fake_app.profiles._profiles = {
        "offline": {"id": "one", "name": "Local player", "access_token": "offline", "default": True},
        "online": {
            "id": "two", "name": "Online player", "type": "microsoft", "default": False,
            "access_token": "private-access-token", "refresh_token": "private-refresh-token",
        },
    }
    fake_app.auth.profile_requires_reauth = lambda _profile: True
    return fake_app


def test_profile_menus_expose_only_applicable_actions_and_no_secrets(profiles_app):
    page = ProfilesPage(profiles_app)
    view = page.view()

    assert isinstance(view, ft.ContextMenu)
    assert view.expand is True
    assert view.content.content is page.lv
    assert set(menu(view)) == {"offline_account", "microsoft_account"}
    offline, online = page.lv.controls
    for context in (view, offline, online):
        assert isinstance(context.content, ft.GestureDetector)
        assert context.secondary_trigger is None
        assert context.tertiary_trigger is None
        assert context.secondary_items == []
        assert context.content.on_secondary_tap_up is not None
    assert set(menu(offline)) == {"set_as_default", "delete"}
    assert set(menu(online)) == {"set_as_default", "profile_sign_in_again", "delete"}
    assert menu(offline)["set_as_default"].disabled is True
    assert menu(online)["set_as_default"].disabled is False
    for control in walk(view):
        assert getattr(control, "data", None) is None
        if isinstance(control, ft.Text):
            assert "private-" not in control.value
        if isinstance(control, ft.PopupMenuItem) and control.key:
            assert control.content.value == control.key


@pytest.mark.parametrize("via_menu", [True, False])
def test_default_action_persists_profile_and_refreshes_active_state(profiles_app, via_menu):
    selected = []
    original = profiles_app.profiles.set_default_profile

    def select(key):
        selected.append(key)
        original(key)
        return True

    profiles_app.profiles.set_default_profile = select
    page = ProfilesPage(profiles_app)
    page.view()
    online = page.lv.controls[1]
    action = menu(online)["set_as_default"] if via_menu else next(
        control for control in walk(online.content) if isinstance(control, ft.Button)
    )

    click(action)

    assert selected == ["online"]
    assert page.active_profile_key == "online"
    assert menu(page.lv.controls[1])["set_as_default"].disabled is True
    assert menu(page.lv.controls[0])["set_as_default"].disabled is False
    assert page.lv.controls[1].content.content.border.top.color == profiles_app.theme.primary
    assert page.lv.controls[0].content.content.border.top.color == profiles_app.theme.border_light


def test_failed_default_save_keeps_previous_active_account(profiles_app):
    warnings = []
    profiles_app.profiles.set_default_profile = lambda _key: False
    profiles_app.feedback.warning = warnings.append
    page = ProfilesPage(profiles_app)
    page.view()

    click(menu(page.lv.controls[1])["set_as_default"])

    assert page.active_profile_key == "offline"
    assert warnings == ["profile_save_failed"]


@pytest.mark.parametrize("via_menu", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
def test_delete_actions_share_confirmation(profiles_app, via_menu, confirmed):
    requests = []
    deleted = []
    profiles_app.feedback.confirm = lambda title, question, callback: requests.append((title, question, callback))
    profiles_app.profiles.delete_profile = lambda key: deleted.append(key) or True
    page = ProfilesPage(profiles_app)
    page.view()
    online = page.lv.controls[1]
    action = menu(online)["delete"] if via_menu else next(
        control for control in walk(online.content)
        if isinstance(control, ft.IconButton) and control.icon.icon == ft.Icons.DELETE_OUTLINE
    )

    click(action)

    assert deleted == []
    assert requests[0][:2] == ("confirmation", "are_you_sure")
    requests[0][2](confirmed)
    assert deleted == (["online"] if confirmed else [])


def test_background_actions_and_reauth_use_existing_handlers(profiles_app, monkeypatch):
    page = ProfilesPage(profiles_app)
    calls = []
    monkeypatch.setattr(page, "add_offline_modal", lambda: calls.append("offline"))
    monkeypatch.setattr(page, "add_microsoft_profile", lambda: calls.append("microsoft"))
    view = page.view()

    click(menu(view)["offline_account"])
    click(menu(view)["microsoft_account"])
    click(menu(page.lv.controls[1])["profile_sign_in_again"])

    assert calls == ["offline", "microsoft", "microsoft"]


def test_profile_cards_use_responsive_identity_and_actions(profiles_app):
    name = "Long account name " * 8
    profiles_app.profiles._profiles["online"]["name"] = name
    page = ProfilesPage(profiles_app)
    page.view()
    card = page.lv.controls[1].content.content
    identity, actions = card.content.controls

    assert card.bgcolor == profiles_app.theme.bg_list
    assert card.height is None
    assert identity.col == {"xs": 12, "md": 7, "lg": 8}
    assert actions.col == {"xs": 12, "md": 5, "lg": 4}
    assert actions.wrap is True
    assert identity.controls[0].width == identity.controls[0].height == 48
    assert identity.controls[1].expand is True
    name_text = identity.controls[1].controls[0]
    assert name_text.value == name_text.tooltip == name
    assert name_text.max_lines == 1
    assert name_text.overflow == ft.TextOverflow.ELLIPSIS
    assert "profile_reauth_required" in [c.value for c in walk(card) if isinstance(c, ft.Text)]
    assert all(c.width == c.height == profiles_app.theme.button_height for c in actions.controls if isinstance(c, ft.IconButton))


def test_empty_profile_state_has_actions_and_no_fake_account(profiles_app):
    profiles_app.profiles._profiles = {}
    page = ProfilesPage(profiles_app)
    view = page.view()

    assert page.active_profile_key is None
    assert "profiles_empty" in [c.value for c in walk(view) if isinstance(c, ft.Text)]
    assert [c.content for c in walk(page.lv) if isinstance(c, ft.Button)] == ["offline_account", "microsoft_account"]
    assert set(menu(view)) == {"offline_account", "microsoft_account"}


def test_removed_default_does_not_leave_stale_active_profile(profiles_app):
    page = ProfilesPage(profiles_app)
    page.view()
    profiles_app.profiles._profiles.pop("offline")

    page._refresh_profiles()

    assert page.active_profile_key is None
    assert menu(page.lv.controls[0])["set_as_default"].disabled is False


@pytest.mark.parametrize("closed", ["hidden", "terminating", "destroyed"])
def test_menu_actions_do_not_run_after_session_closes(profiles_app, closed):
    calls = []
    page = ProfilesPage(profiles_app)
    page.set_default_profile = calls.append
    page.view()
    action = menu(page.lv.controls[1])["set_as_default"]
    if closed == "hidden":
        page.before_hide()
    elif closed == "terminating":
        profiles_app._terminating = True
    else:
        class DestroyedPage:
            @property
            def session(self):
                raise RuntimeError("closed")

        page.page = DestroyedPage()

    click(action)

    assert calls == []


def test_auth_in_progress_disables_reauth_and_does_not_start_twice(profiles_app, monkeypatch):
    scheduled = []
    monkeypatch.setattr("launcher.pages.profiles.run_task", lambda _page, task: scheduled.append(task))
    page = ProfilesPage(profiles_app)
    page.view()

    page.add_microsoft_profile()
    page.add_microsoft_profile()

    assert scheduled == [page._add_microsoft_profile_async]
    assert menu(page.lv.controls[1])["profile_sign_in_again"].disabled is True
    button = next(c for c in walk(page.lv.controls[1].content) if isinstance(c, ft.IconButton) and c.icon.icon == ft.Icons.LOGIN)
    assert button.disabled is True


@pytest.mark.parametrize("hide", [True, False])
def test_auth_completion_restores_controls_only_on_open_page(profiles_app, monkeypatch, hide):
    profiles_app.auth = SimpleNamespace(
        authenticate=lambda: {}, authenticate_with_device_code=lambda: {},
        profile_requires_reauth=lambda _profile: False,
    )
    page = ProfilesPage(profiles_app)
    page.view()
    page.microsoft_auth_in_progress = True
    refreshed = []
    monkeypatch.setattr(page, "update_list_view", lambda: refreshed.append(True))
    if hide:
        page.before_hide()

    asyncio.run(page._add_microsoft_profile_async())

    assert page.microsoft_auth_in_progress is False
    assert bool(refreshed) is not hide
