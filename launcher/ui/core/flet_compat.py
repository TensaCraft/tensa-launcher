from __future__ import annotations

import asyncio
import inspect
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=None)
def _allowed_kwargs(control_cls: type[Any]) -> frozenset[str]:
    signature_params = tuple(inspect.signature(control_cls.__init__).parameters)
    if signature_params and set(signature_params).issubset({"self", "args", "kwargs"}):
        dataclass_fields = getattr(control_cls, "__dataclass_fields__", None)
        if dataclass_fields:
            return frozenset(str(name) for name in dataclass_fields)

    return frozenset(name for name in signature_params if name != "self")


def filter_control_kwargs(control_cls: type[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_kwargs(control_cls)
    return {
        key: value
        for key, value in kwargs.items()
        if key in allowed and value is not None
    }


def show_window_when_ready(page: Any) -> None:
    window = getattr(page, "window", None)
    if window is None:
        return

    wait_until_ready = getattr(window, "wait_until_ready_to_show", None)
    if callable(wait_until_ready):
        if inspect.iscoroutinefunction(wait_until_ready):
            _run_page_task(page, lambda: _wait_and_show(page, window, wait_until_ready()))
            return

        result = wait_until_ready()
        if inspect.isawaitable(result):
            _run_page_task(page, lambda: _wait_and_show(page, window, result))
            return

    _show_window(page, window)


async def _wait_and_show(page: Any, window: Any, wait_result: Any) -> None:
    if inspect.isawaitable(wait_result):
        await asyncio.wait_for(wait_result, timeout=None)
    _show_window(page, window)


def _run_page_task(page: Any, task_factory: Any) -> None:
    runner = getattr(page, "run_task", None)
    if callable(runner):
        async def task_handler() -> None:
            return await task_factory()

        runner(task_handler)
        return

    asyncio.run(task_factory())


def _show_window(page: Any, window: Any) -> None:
    try:
        window.visible = True
    except Exception:
        return

    updater = getattr(page, "update", None)
    if callable(updater):
        updater()


__all__ = ["filter_control_kwargs", "show_window_when_ready"]
