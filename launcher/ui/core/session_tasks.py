from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any

import flet as ft

from .page_runtime import run_task


@dataclass(frozen=True, slots=True)
class SessionTaskToken:
    owner_id: object
    key: str
    generation: int


class PageSessionTasks:
    def __init__(self, page: ft.Page) -> None:
        self._page = page
        self._owner_id = object()
        self._lock = RLock()
        self._closed = False
        self._generations: dict[str, int] = {}
        self._tasks: dict[int, object] = {}
        self._keyed_tasks: dict[str, int] = {}

    def begin(self, key: str) -> SessionTaskToken | None:
        task_to_cancel: object | None = None
        with self._lock:
            if self._closed:
                return None
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
            task_id = self._keyed_tasks.pop(key, None)
            if task_id is not None:
                task_to_cancel = self._tasks.pop(task_id, None)
            token = SessionTaskToken(self._owner_id, key, generation)
        self._cancel(task_to_cancel)
        return token

    def invalidate(self, key: str) -> None:
        task_to_cancel: object | None = None
        with self._lock:
            self._generations[key] = self._generations.get(key, 0) + 1
            task_id = self._keyed_tasks.pop(key, None)
            if task_id is not None:
                task_to_cancel = self._tasks.pop(task_id, None)
        self._cancel(task_to_cancel)

    def is_current(self, token: SessionTaskToken) -> bool:
        with self._lock:
            return (
                not self._closed
                and token.owner_id is self._owner_id
                and self._generations.get(token.key) == token.generation
            )

    def run(
        self,
        task: Callable[..., Any],
        *args: Any,
        token: SessionTaskToken | None = None,
        **kwargs: Any,
    ) -> object | None:
        with self._lock:
            if self._closed or (token is not None and not self.is_current(token)):
                return None

        handle = run_task(self._page, task, *args, **kwargs)
        if handle is None:
            return None

        cancel_handle = False
        handle_id = id(handle)
        with self._lock:
            if self._closed or (token is not None and not self.is_current(token)):
                cancel_handle = True
            else:
                self._tasks[handle_id] = handle
                if token is not None:
                    self._keyed_tasks[token.key] = handle_id
        if cancel_handle:
            self._cancel(handle)
            return handle

        add_done_callback = getattr(handle, "add_done_callback", None)
        if callable(add_done_callback):
            try:
                add_done_callback(lambda _completed: self._forget(handle_id, token))
            except (RuntimeError, TypeError):
                pass
        return handle

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = list(self._tasks.values())
            self._tasks.clear()
            self._keyed_tasks.clear()
            for key in self._generations:
                self._generations[key] += 1
        for task in tasks:
            self._cancel(task)

    def _forget(self, handle_id: int, token: SessionTaskToken | None) -> None:
        with self._lock:
            self._tasks.pop(handle_id, None)
            if token is not None and self._keyed_tasks.get(token.key) == handle_id:
                self._keyed_tasks.pop(token.key, None)

    @staticmethod
    def _cancel(cancellable: object | None) -> None:
        if cancellable is None:
            return
        cancel = getattr(cancellable, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except RuntimeError:
                pass


__all__ = ["PageSessionTasks", "SessionTaskToken"]
