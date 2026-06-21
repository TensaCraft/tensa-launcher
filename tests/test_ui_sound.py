from __future__ import annotations

import asyncio
import inspect
from importlib import resources
from types import SimpleNamespace

from launcher import ui
from launcher.application.ui_sound import UiSoundService
from launcher.ui.core.click_sound import wrap_click_handler
from launcher.shared import AppContext


class DummyConfig:
    def __init__(self, value: str = "yes") -> None:
        self.value = value

    def get(self, _key: str, default=None):
        return self.value if self.value is not None else default


def test_ui_sound_service_respects_click_sound_setting():
    played = []
    config = DummyConfig("yes")
    service = UiSoundService(config, SimpleNamespace(debug=lambda *_args, **_kwargs: None), player=played.append)

    assert service.play_click() is True
    config.value = "no"

    assert service.play_click() is False
    assert played == [UiSoundService.sound_asset_path("gate_latch_click")]


def test_ui_sound_service_uses_selected_click_sound_asset():
    played = []
    config = DummyConfig("yes")
    config.values = {
        UiSoundService.CONFIG_KEY: "yes",
        UiSoundService.SOUND_CONFIG_KEY: "typewriter_soft_click",
    }
    config.get = lambda key, default=None: config.values.get(key, default)
    service = UiSoundService(config, SimpleNamespace(debug=lambda *_args, **_kwargs: None), player=played.append)

    assert service.play_click() is True
    assert played == [UiSoundService.sound_asset_path("typewriter_soft_click")]


def test_ui_sound_service_falls_back_to_default_sound_for_unknown_selection():
    played = []
    config = DummyConfig("yes")
    config.values = {
        UiSoundService.CONFIG_KEY: "yes",
        UiSoundService.SOUND_CONFIG_KEY: "missing_sound",
    }
    config.get = lambda key, default=None: config.values.get(key, default)
    service = UiSoundService(config, SimpleNamespace(debug=lambda *_args, **_kwargs: None), player=played.append)

    assert service.play_click() is True
    assert played == [UiSoundService.sound_asset_path("gate_latch_click")]


def test_ui_sound_service_exposes_click_sound_choices():
    choices = UiSoundService.click_sound_choices()

    assert [choice.key for choice in choices] == [
        "typewriter_soft_click",
        "gate_latch_click",
        "plastic_bubble_click",
    ]
    assert all(choice.label for choice in choices)


def test_ui_sound_assets_exist_in_package():
    for choice in UiSoundService.click_sound_choices():
        if choice.asset_path:
            assert resources.files("launcher").joinpath(choice.asset_path).is_file()


def test_button_wrapper_plays_click_sound_before_handler(fake_app):
    clicks = []
    events = []
    fake_app.ui_sound = SimpleNamespace(play_click=lambda: clicks.append("click"))
    AppContext.set(fake_app)
    button = ui.Button(text="Save", on_click=lambda e: events.append(e))

    button.on_click("event")

    assert clicks == ["click"]
    assert events == ["event"]


def test_click_sound_wrapper_preserves_async_handlers(fake_app):
    clicks = []
    events = []
    fake_app.ui_sound = SimpleNamespace(play_click=lambda: clicks.append("click"))
    AppContext.set(fake_app)

    async def handle_click(event):
        events.append(event)

    wrapped = wrap_click_handler(handle_click)

    assert inspect.iscoroutinefunction(wrapped)
    asyncio.run(wrapped("event"))
    assert clicks == ["click"]
    assert events == ["event"]


def test_button_wrapper_preserves_async_handlers(fake_app):
    clicks = []
    events = []
    fake_app.ui_sound = SimpleNamespace(play_click=lambda: clicks.append("click"))
    AppContext.set(fake_app)

    async def handle_click(event):
        events.append(event)

    button = ui.Button(text="Save", on_click=handle_click)

    assert inspect.iscoroutinefunction(button.on_click)
    asyncio.run(button.on_click("event"))
    assert clicks == ["click"]
    assert events == ["event"]


def test_clickable_container_wrapper_plays_click_sound(fake_app):
    clicks = []
    events = []
    fake_app.ui_sound = SimpleNamespace(play_click=lambda: clicks.append("click"))
    AppContext.set(fake_app)
    container = ui.Container(on_click=lambda e: events.append(e))

    container.on_click("event")

    assert clicks == ["click"]
    assert events == ["event"]


def test_version_card_action_plays_click_sound(fake_app):
    clicks = []
    events = []
    fake_app.ui_sound = SimpleNamespace(play_click=lambda: clicks.append("click"))
    AppContext.set(fake_app)
    card = ui.VersionCard().create("Aeronautics", on_action_click=lambda e: events.append(e))
    card.on_enter(SimpleNamespace())
    action = card.content.content.controls[2].content

    action.on_tap("event")

    assert clicks == ["click"]
    assert events == ["event"]
