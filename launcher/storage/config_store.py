from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from launcher.models.logger import Logger
from launcher.storage.atomic import atomic_write_json, path_lock


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
        self._lock = path_lock(self._path)
        self._data: Dict[str, Any] = {}
        self._baseline: Dict[str, Any] = {}
        self._changed: set[str] = set()
        self._deleted: set[str] = set()
        self.reload()

    def _read_file(self) -> Dict[str, Any] | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            Logger.warning(f"Config file '{self._path}' is not valid UTF-8 JSON; keeping the last good config.")
            return None
        if not isinstance(data, dict):
            Logger.warning(f"Config file '{self._path}' must contain an object; keeping the last good config.")
            return None
        return data

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            try:
                data = self._read_file()
            except OSError as exc:
                Logger.error(f"Unable to read config file '{self._path}': {exc}")
            else:
                if data is not None:
                    self._data = data
                    self._baseline = deepcopy(data)
                    self._changed.clear()
                    self._deleted.clear()
            return self._data

    def save(self) -> None:
        with self._lock:
            try:
                data = self._read_file()
                if data is None:
                    data = dict(self._data)
                else:
                    # Include in-place edits to values returned by get/items/reload.
                    for key, value in self._data.items():
                        if key in self._changed or key not in self._baseline or value != self._baseline[key]:
                            data[key] = value
                    for key in self._deleted | (self._baseline.keys() - self._data.keys()):
                        data.pop(key, None)
                baseline = deepcopy(data)
                atomic_write_json(self._path, data, ensure_ascii=False, indent=4)
            except OSError as exc:
                Logger.error(f"Unable to save config file '{self._path}': {exc}")
                raise
            else:
                self._data = data
                self._baseline = baseline
                self._changed.clear()
                self._deleted.clear()

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            self._data[key] = value
            self._changed.add(key)
            self._deleted.discard(key)
            if persist:
                self.save()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, data: Dict[str, Any], persist: bool = True) -> None:
        with self._lock:
            self._data.update(data)
            self._changed.update(data)
            self._deleted.difference_update(data)
            if persist:
                self.save()

    def delete(self, key: str, persist: bool = True) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._changed.discard(key)
            self._deleted.add(key)
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
