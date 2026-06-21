from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from launcher import PRODUCT_NAME


def _default_log_path() -> Path:
    return _platform_fallback_log_file()


def _platform_fallback_log_file() -> Path:
    try:
        from launcher.platform.paths import PathPolicy

        return PathPolicy.default_log_dir() / "app.log"
    except Exception:
        pass

    if sys.platform.startswith("win"):
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or (Path.home() / "AppData" / "Local")
        )
        return base / PRODUCT_NAME / "app.log"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / PRODUCT_NAME / "app.log"
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return base / PRODUCT_NAME / "app.log"


class Logger:
    """Thin wrapper around ``logging`` with sane defaults for the launcher."""

    log_file = _default_log_path()
    _logger: Optional[logging.Logger] = None

    @classmethod
    def setup(cls, level: int = logging.INFO) -> logging.Logger:
        if cls._logger:
            return cls._logger

        logger = logging.getLogger("tensa.launcher")
        logger.setLevel(level)
        logger.handlers.clear()
        logger.propagate = False

        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = cls._create_file_handler(formatter)
        if file_handler is not None:
            logger.addHandler(file_handler)

        console_stream = getattr(sys, "stdout", None)
        if console_stream is not None and hasattr(console_stream, "write"):
            stream_handler = logging.StreamHandler(console_stream)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        if getattr(sys, "frozen", False):
            sys.stdout = LoggerStream(logger, level=logging.INFO)  # type: ignore[assignment]
            sys.stderr = LoggerStream(logger, level=logging.ERROR)  # type: ignore[assignment]

        cls._logger = logger
        return logger

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------
    @classmethod
    def log(cls, message: str, level: int = logging.INFO) -> None:
        if cls._logger is None:
            cls.setup()
        if cls._logger:
            cls._logger.log(level, message)

    @classmethod
    def debug(cls, message: str) -> None:
        cls.log(message, logging.DEBUG)

    @classmethod
    def info(cls, message: str) -> None:
        cls.log(message, logging.INFO)

    @classmethod
    def warning(cls, message: str) -> None:
        cls.log(message, logging.WARNING)

    @classmethod
    def error(cls, message: str) -> None:
        cls.log(message, logging.ERROR)

    @classmethod
    def clear(cls) -> None:
        for candidate in cls._candidate_log_paths():
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("", encoding="utf-8")
                cls.log_file = candidate
                return
            except OSError:
                continue

    @classmethod
    def _resolve_log_file(cls) -> Path:
        try:
            from launcher.platform.paths import LauncherPaths

            return Path(LauncherPaths.detect().app_state_dir) / "app.log"
        except Exception:
            pass
        return _default_log_path()

    @classmethod
    def _create_file_handler(cls, formatter: logging.Formatter) -> Optional[RotatingFileHandler]:
        for candidate in cls._candidate_log_paths():
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    candidate,
                    maxBytes=512 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
            except OSError:
                continue

            handler.setFormatter(formatter)
            cls.log_file = candidate
            return handler

        return None

    @classmethod
    def _candidate_log_paths(cls) -> list[Path]:
        primary = cls._resolve_log_file()
        fallback = cls._fallback_log_file()
        paths = []
        for candidate in (primary, fallback):
            if candidate not in paths:
                paths.append(candidate)
        return paths

    @staticmethod
    def _fallback_log_file() -> Path:
        return _platform_fallback_log_file()


class LoggerStream:
    def __init__(self, logger: logging.Logger, level: int = logging.INFO) -> None:
        self._logger = logger
        self._level = level
        self._is_logging = False

    def write(self, message: str) -> None:
        text = message.strip()
        if not text or self._is_logging:
            return
        try:
            self._is_logging = True
            self._logger.log(self._level, text)
        finally:
            self._is_logging = False

    def flush(self) -> None:  # pragma: no cover - required by file-like protocol
        pass


__all__ = ["Logger"]
