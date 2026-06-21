from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, Optional

from launcher.models.logger import Logger


class Config:
    def __init__(
        self,
        app=None,
        filename: Optional[Path] = None,
        storage_dir: Optional[Path] = None,
    ) -> None:
        if filename:
            self._path = Path(filename)
        elif storage_dir:
            self._path = Path(storage_dir) / "config.json"
        elif app is not None:
            self._path = Path(app.util.app_state_dir) / "config.json"
        else:
            raise ValueError("Config requires either app, filename, or storage_dir to determine storage path.")
        self._lock = RLock()
        self._data: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._data = {}
            except OSError as exc:
                Logger.error(f"Unable to read config file '{self._path}': {exc}")
                self._data = {}
            else:
                try:
                    self._data = json.loads(raw)
                except json.JSONDecodeError:
                    Logger.warning(f"Config file '{self._path}' is not valid JSON; starting with empty config.")
                    self._data = {}
        return self._data

    def save(self) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=4), encoding="utf-8")
            except OSError as exc:
                Logger.error(f"Unable to save config file '{self._path}': {exc}")

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            self._data[key] = value
        if persist:
            self.save()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, data: Dict[str, Any], persist: bool = True) -> None:
        with self._lock:
            self._data.update(data)
        if persist:
            self.save()

    def delete(self, key: str, persist: bool = True) -> None:
        with self._lock:
            self._data.pop(key, None)
        if persist:
            self.save()

    def keys(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._data.keys())

    def items(self) -> Iterable[tuple[str, Any]]:
        with self._lock:
            return tuple(self._data.items())

    def __contains__(self, item: object) -> bool:
        with self._lock:
            return item in self._data
