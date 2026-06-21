from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ClickSoundChoice:
    key: str
    label: str
    asset_path: str | None = None


class UiSoundService:
    CONFIG_KEY = "ui_click_sound_enabled"
    SOUND_CONFIG_KEY = "ui_click_sound"
    DEFAULT_SOUND_KEY = "gate_latch_click"
    SOUND_CHOICES = (
        ClickSoundChoice("typewriter_soft_click", "click_sound_typewriter", "assets/sounds/typewriter-soft-click.wav"),
        ClickSoundChoice("gate_latch_click", "click_sound_gate_latch", "assets/sounds/gate-latch-click.wav"),
        ClickSoundChoice("plastic_bubble_click", "click_sound_plastic_bubble", "assets/sounds/plastic-bubble-click.wav"),
    )
    _MIN_INTERVAL_SEC = 0.04

    def __init__(
        self,
        config: Any,
        logger: Any,
        *,
        player: Callable[[str | None], Any] | None = None,
        use_thread: bool = False,
    ) -> None:
        self.config = config
        self.logger = logger
        self._player = player or self._play_asset_click
        self._use_thread = use_thread
        self._last_play = 0.0
        self._lock = threading.Lock()

    @classmethod
    def click_sound_choices(cls) -> list[ClickSoundChoice]:
        return list(cls.SOUND_CHOICES)

    @classmethod
    def selected_sound_key(cls, config: Any) -> str:
        value = str(config.get(cls.SOUND_CONFIG_KEY, cls.DEFAULT_SOUND_KEY) or cls.DEFAULT_SOUND_KEY)
        if cls.sound_asset_path(value) is not None:
            return value
        return cls.DEFAULT_SOUND_KEY

    @classmethod
    def sound_asset_path(cls, key: str) -> str | None:
        for choice in cls.SOUND_CHOICES:
            if choice.key == key:
                return choice.asset_path
        return None

    def is_enabled(self) -> bool:
        return self.config.get(self.CONFIG_KEY, "yes") == "yes"

    def play_click(self) -> bool:
        if not self.is_enabled():
            return False
        now = time.monotonic()
        with self._lock:
            if now - self._last_play < self._MIN_INTERVAL_SEC:
                return False
            self._last_play = now
        asset_path = self.sound_asset_path(self.selected_sound_key(self.config))
        if not asset_path:
            return False
        if self._use_thread:
            threading.Thread(target=self._safe_play, args=(asset_path,), daemon=True).start()
        else:
            self._safe_play(asset_path)
        return True

    def _safe_play(self, asset_path: str) -> None:
        try:
            self._player(asset_path)
        except Exception as exc:
            debug = getattr(self.logger, "debug", None)
            if callable(debug):
                debug(f"UI click sound failed: {exc!r}")

    @classmethod
    def _play_asset_click(cls, asset_path: str) -> None:
        if not sys.platform.startswith("win"):
            return

        resource = resources.files("launcher").joinpath(asset_path)
        with resources.as_file(resource) as sound_file:
            if not sound_file.is_file():
                return
            if sound_file.suffix.lower() == ".wav":
                cls._play_wav_file(sound_file)

    @classmethod
    def _play_wav_file(cls, sound_file: Path) -> None:
        import winsound

        winsound.PlaySound(str(sound_file), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
