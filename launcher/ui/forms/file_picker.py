from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import flet as ft

from ..core.page_runtime import register_service


def initial_directory_from_path(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    path = Path(raw).expanduser()
    if path.is_dir():
        return str(path)
    if path.is_file():
        return str(path.parent)
    return None


class FilePicker:
    def __init__(
        self,
        *,
        page: ft.Page,
        on_result: Callable[[Any], None] | None = None,
        on_upload: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self.page = page
        self.on_result = on_result
        self.service = ft.FilePicker(on_upload=on_upload, **kwargs)
        register_service(page, self.service)

    def pick_files(self, **kwargs: Any):
        return self._dispatch_result(self.service.pick_files(**kwargs), returns_files=True)

    def get_directory_path(self, **kwargs: Any):
        return self._dispatch_result(self.service.get_directory_path(**kwargs), returns_files=False)

    def save_file(self, **kwargs: Any):
        return self._dispatch_result(self.service.save_file(**kwargs), returns_files=False)

    def _dispatch_result(self, result: Any, *, returns_files: bool):
        if inspect.isawaitable(result):
            runner = getattr(self.page, "run_task", None)
            if not callable(runner):
                raise RuntimeError("Page.run_task is required for async FilePicker operations.")
            runner(self._resolve_result_async, result, returns_files)
            return None
        self._emit_value(result, returns_files=returns_files)
        return result

    async def _resolve_result_async(self, result: Any, returns_files: bool):
        resolved = await result
        self._emit_value(resolved, returns_files=returns_files)
        return resolved

    def _emit_value(self, value: Any, *, returns_files: bool) -> None:
        if returns_files:
            self._emit_result(files=value or [], path=None)
            return
        self._emit_result(files=[], path=value)

    def _emit_result(self, *, files: list[Any], path: str | None) -> None:
        if callable(self.on_result):
            self.on_result(SimpleNamespace(files=files, path=path))

    def __getattr__(self, item: str) -> Any:
        return getattr(self.service, item)


__all__ = ["FilePicker", "initial_directory_from_path"]
