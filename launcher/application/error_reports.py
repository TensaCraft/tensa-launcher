from __future__ import annotations

import platform
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import requests

from launcher.models.logger import Logger


class LauncherReportError(RuntimeError):
    """Raised when the launcher cannot submit a user-visible report."""


class LauncherReportService:
    ENDPOINT = "https://gigabait.uk/api/mods/launcher/logs"
    REQUEST_TIMEOUT = 15
    INLINE_LOG_LIMIT_BYTES = 1024 * 1024
    FILE_TAIL_LIMIT_BYTES = 256 * 1024

    def __init__(self, app, session: Optional[requests.Session] = None) -> None:
        self.app = app
        self.session = session or requests.Session()

    def submit_report(
        self,
        *,
        report_type: str = "error",
        severity: str = "error",
        title: str,
        message: str,
        log: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        attachments: Optional[Iterable[str | Path]] = None,
    ) -> dict[str, Any]:
        payload = self._build_payload(
            report_type=report_type,
            severity=severity,
            title=title,
            message=message,
            log=log,
            metadata=metadata,
            attachments=attachments,
        )
        try:
            response = self.session.post(self.ENDPOINT, json=payload, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            Logger.error(f"Failed to submit launcher report: {exc!r}")
            raise LauncherReportError(str(exc)) from exc

        if not isinstance(data, dict) or not data.get("ok"):
            raise LauncherReportError(f"Unexpected report API response: {data!r}")

        Logger.info(f"Launcher report submitted: {data.get('report_id', 'unknown')}")
        return data

    def submit_report_async(
        self,
        *,
        report_type: str = "error",
        severity: str = "error",
        title: str,
        message: str,
        log: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        attachments: Optional[Iterable[str | Path]] = None,
        on_success: Optional[Callable[[dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        def worker() -> None:
            try:
                result = self.submit_report(
                    report_type=report_type,
                    severity=severity,
                    title=title,
                    message=message,
                    log=log,
                    metadata=metadata,
                    attachments=attachments,
                )
            except Exception as exc:
                if on_error:
                    on_error(exc)
                return
            if on_success:
                on_success(result)

        threading.Thread(target=worker, daemon=True).start()

    def _build_payload(
        self,
        *,
        report_type: str,
        severity: str,
        title: str,
        message: str,
        log: Optional[str],
        metadata: Optional[dict[str, Any]],
        attachments: Optional[Iterable[str | Path]],
    ) -> dict[str, Any]:
        merged_metadata = self._default_metadata()
        if metadata:
            merged_metadata.update(self._sanitize_metadata(metadata))

        log_text = log if log is not None else self.collect_log_text(attachments=attachments)
        log_text = self._truncate_utf8(log_text or message or title, self.INLINE_LOG_LIMIT_BYTES)

        contact = self._report_contact()
        if contact:
            merged_metadata["contact"] = contact

        payload = {
            "type": str(report_type or "error"),
            "severity": str(severity or "error"),
            "platform": self._platform_name(),
            "launcher_version": str(getattr(getattr(self.app, "util", None), "launcher_version", "")),
            "os": platform.platform(),
            "title": str(title or "Launcher report"),
            "message": str(message or ""),
            "log": log_text,
            "metadata": merged_metadata,
        }
        if contact:
            payload["contact"] = contact
        return payload

    def collect_log_text(self, *, attachments: Optional[Iterable[str | Path]] = None) -> str:
        parts: list[str] = []
        launcher_log = getattr(Logger, "log_file", None)
        if launcher_log:
            self._append_file_tail(parts, "launcher app.log", Path(launcher_log))

        for attachment in attachments or ():
            self._append_file_tail(parts, f"diagnostic file: {attachment}", Path(attachment))

        return "\n\n".join(part for part in parts if part).strip()

    def _append_file_tail(self, parts: list[str], label: str, path: Path) -> None:
        text = self._read_tail(path, self.FILE_TAIL_LIMIT_BYTES)
        if not text:
            return
        parts.append(f"--- {label} ({path}) ---\n{text}")

    @staticmethod
    def _read_tail(path: Path, limit_bytes: int) -> str:
        if not path.exists() or not path.is_file():
            return ""
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > limit_bytes:
                    handle.seek(max(size - limit_bytes, 0))
                data = handle.read(limit_bytes)
            return data.decode("utf-8", errors="replace").strip()
        except OSError as exc:
            return f"Unable to read {path}: {exc!r}"

    def _default_metadata(self) -> dict[str, Any]:
        current_page = getattr(self.app, "current_page", None)
        feedback = getattr(self.app, "feedback", None)
        feedback_snapshot = None
        snapshot = getattr(feedback, "snapshot", None)
        if callable(snapshot):
            try:
                feedback_snapshot = snapshot(activity_limit=12)
            except Exception:
                feedback_snapshot = None
        return self._sanitize_metadata(
            {
                "screen": current_page.__class__.__name__ if current_page is not None else None,
                "python": sys.version.split()[0],
                "frozen": bool(getattr(sys, "frozen", False)),
                "feedback": feedback_snapshot,
            }
        )

    def _report_contact(self) -> str:
        config = getattr(self.app, "config", None)
        getter = getattr(config, "get", None)
        if not callable(getter):
            return ""
        return str(getter("report_contact", "") or "").strip()

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                sanitized[str(key)] = value
                continue
            if isinstance(value, Path):
                sanitized[str(key)] = value.as_posix()
                continue
            if isinstance(value, (list, tuple)):
                sanitized[str(key)] = [LauncherReportService._sanitize_value(item) for item in value]
                continue
            if isinstance(value, dict):
                sanitized[str(key)] = {
                    str(child_key): LauncherReportService._sanitize_value(child_value)
                    for child_key, child_value in value.items()
                    if child_value is not None
                }
                continue
            sanitized[str(key)] = str(value)
        return sanitized

    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, (list, tuple)):
            return [LauncherReportService._sanitize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): LauncherReportService._sanitize_value(child_value)
                for key, child_value in value.items()
                if child_value is not None
            }
        return str(value)

    @staticmethod
    def _truncate_utf8(text: str, limit_bytes: int) -> str:
        data = text.encode("utf-8", errors="replace")
        if len(data) <= limit_bytes:
            return text
        marker = "\n--- log truncated to last 1 MB ---\n".encode("utf-8")
        tail = data[-max(limit_bytes - len(marker), 0):]
        return (marker + tail).decode("utf-8", errors="replace")

    @staticmethod
    def _platform_name() -> str:
        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        return "linux"


__all__ = ["LauncherReportError", "LauncherReportService"]
