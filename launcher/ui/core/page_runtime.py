from __future__ import annotations

import asyncio
import inspect
import threading
from contextlib import suppress
from functools import partial
from typing import Any, Callable, Optional

import flet as ft


def _dialog_controls(page: ft.Page):
    dialogs = getattr(page, "_dialogs", None)
    controls = getattr(dialogs, "controls", None)
    if isinstance(controls, list):
        return dialogs, controls
    return dialogs, None


def _remove_dialog_from_stack(page: ft.Page, dialogs, controls: list[Any] | None, dialog: ft.DialogControl) -> bool:
    if controls is None or dialog not in controls:
        return False
    controls.remove(dialog)
    stack_update = getattr(dialogs, "update", None)
    if callable(stack_update):
        stack_update()
    else:
        schedule_update(page)
    return True


def discard_closed_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    if getattr(dialog, "open", False):
        return False

    dialogs, controls = _dialog_controls(page)
    if controls is None or dialog not in controls:
        return False

    restorer = getattr(page, "_restore_dialog_on_dismiss", None)
    if callable(restorer):
        with suppress(Exception):
            restorer(dialog)

    remover = getattr(page, "_remove_dialog", None)
    if callable(remover):
        with suppress(Exception):
            remover(dialog)
            return True

    return _remove_dialog_from_stack(page, dialogs, controls, dialog)


def _uses_flet_dialog_lifecycle(page: ft.Page) -> bool:
    return callable(getattr(page, "_remove_dialog", None)) and callable(
        getattr(page, "_wrap_dialog_on_dismiss", None)
    )


def _page_loop(page: ft.Page):
    session = getattr(page, "session", None)
    connection = getattr(session, "connection", None)
    return getattr(connection, "loop", None)


def _is_on_page_loop(page: ft.Page) -> bool:
    page_loop = _page_loop(page)
    if page_loop is None:
        return False
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return current_loop is page_loop


def register_service(page: ft.Page, service: ft.Service) -> None:
    services = getattr(page, "services", None)
    if services is not None:
        if service not in services:
            services.append(service)
        return

    overlay = getattr(page, "overlay", None)
    if overlay is not None and service not in overlay:
        overlay.append(service)


def schedule_update(page: ft.Page) -> None:
    updater = getattr(page, "update", None)
    if callable(updater) and _is_on_page_loop(page):
        try:
            updater()
            return
        except RuntimeError:
            pass

    runner = getattr(page, "run_task", None)
    if callable(runner):
        async def _update():
            update_now = getattr(page, "update", None)
            if callable(update_now):
                update_now()

        try:
            runner(_update)
            return
        except (RuntimeError, TypeError):
            pass

    if callable(updater):
        try:
            updater()
            return
        except RuntimeError:
            pass

    deferred = getattr(page, "schedule_update", None)
    if callable(deferred):
        try:
            deferred()
            return
        except RuntimeError:
            pass


def run_task(page: ft.Page, task: Callable[..., Any], *args: Any, **kwargs: Any):
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        raise RuntimeError("Page.run_task is required.")
    return runner(task, *args, **kwargs)


async def run_blocking(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    bound = partial(fn, *args, **kwargs)

    def _worker() -> None:
        try:
            result = bound()
        except Exception as exc:  # pragma: no cover - thread handoff
            loop.call_soon_threadsafe(future.set_exception, exc)
            return
        loop.call_soon_threadsafe(future.set_result, result)

    threading.Thread(target=_worker, daemon=True).start()
    return await future


def invoke_on_ui(page: ft.Page, callback: Callable[..., Any], *args: Any, **kwargs: Any):
    if _is_on_page_loop(page):
        return callback(*args, **kwargs)

    runner = getattr(page, "run_task", None)
    if callable(runner):
        async def _runner():
            result = callback(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            return runner(_runner)
        except (RuntimeError, TypeError):
            pass

    return callback(*args, **kwargs)


def show_dialog(page: ft.Page, dialog: ft.DialogControl) -> None:
    dialogs, controls = _dialog_controls(page)
    if controls is not None and dialog in controls and not getattr(dialog, "open", False):
        _remove_dialog_from_stack(page, dialogs, controls, dialog)

    shower = getattr(page, "show_dialog", None)
    if callable(shower):
        try:
            shower(dialog)
            return
        except AttributeError as exc:
            if "_prepare_dialog" not in str(exc):
                raise

    opener = getattr(page, "open", None)
    if callable(opener):
        opener(dialog)
        return

    dialog.open = True
    if controls is not None and dialog not in controls:
        controls.append(dialog)
        stack_update = getattr(dialogs, "update", None)
        if callable(stack_update):
            stack_update()
            return
    schedule_update(page)


def close_dialog(page: ft.Page, dialog: Optional[ft.DialogControl] = None) -> None:
    dialogs, controls = _dialog_controls(page)
    popper = getattr(page, "pop_dialog", None)
    managed_lifecycle = _uses_flet_dialog_lifecycle(page)

    if dialog is not None:
        if callable(popper) and controls is not None:
            with suppress(Exception):
                if controls and controls[-1] is dialog:
                    popper()
                    if managed_lifecycle:
                        schedule_update(page)
                        return
                    if not getattr(dialog, "open", False):
                        _remove_dialog_from_stack(page, dialogs, controls, dialog)
                        return

        closer = getattr(page, "close", None)
        if callable(closer):
            with suppress(Exception):
                closer(dialog)
            if managed_lifecycle:
                schedule_update(page)
                return
            if not getattr(dialog, "open", False):
                _remove_dialog_from_stack(page, dialogs, controls, dialog)
                return

        with suppress(Exception):
            if getattr(dialog, "open", False):
                dialog.open = False
                updater = getattr(dialog, "update", None)
                if callable(updater):
                    updater()

        if managed_lifecycle:
            schedule_update(page)
            return

        if not getattr(dialog, "open", False) and _remove_dialog_from_stack(page, dialogs, controls, dialog):
            return
        if not getattr(dialog, "open", False):
            schedule_update(page)
            return

    if callable(popper):
        popper()
        return

    if dialog is None:
        return

    closer = getattr(page, "close", None)
    if callable(closer):
        closer(dialog)
