from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any


def _play_click_sound(args: tuple[Any, ...]) -> None:
    if not args:
        return
    event = args[0]
    control = getattr(event, "control", None)
    page = getattr(control, "page", None) or getattr(event, "page", None)
    app = getattr(page, "data", None)
    ui_sound = getattr(app, "ui_sound", None)
    play_click = getattr(ui_sound, "play_click", None)
    if callable(play_click):
        play_click()


def wrap_click_handler(handler: Callable[..., Any] | None) -> Callable[..., Any] | None:
    if handler is None:
        return None

    if inspect.iscoroutinefunction(handler):
        @functools.wraps(handler)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            _play_click_sound(args)
            return await handler(*args, **kwargs)

        return async_wrapped

    @functools.wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _play_click_sound(args)
        return handler(*args, **kwargs)

    return wrapped
