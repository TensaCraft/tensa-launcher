from __future__ import annotations

from typing import Any


class AppContext:
    _app: Any = None

    @classmethod
    def set(cls, app: Any) -> None:
        cls._app = app

    @classmethod
    def get(cls) -> Any:
        if cls._app is None:
            raise RuntimeError("Application context is not initialized.")
        return cls._app
