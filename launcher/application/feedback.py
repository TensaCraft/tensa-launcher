from __future__ import annotations

from dataclasses import dataclass
import itertools
import inspect
import threading
import time
from typing import Any, Callable, Literal


FeedbackLevel = Literal["info", "success", "warning", "error"]


@dataclass(slots=True)
class _OperationState:
    operation_id: int
    title: str
    kind: str
    status: str | None
    progress: float
    total: float
    parent_id: int | None
    auto_open: bool
    visible: bool
    finished: bool = False
    finish_message: str | None = None
    show_success: bool = True


@dataclass(frozen=True, slots=True)
class _ProgressRender:
    status: str
    progress: float
    total: float
    force_open: bool
    auto_open: bool


@dataclass(frozen=True, slots=True)
class _ActivityEntry:
    timestamp: float
    event: str
    level: FeedbackLevel
    message: str
    operation_id: int | None = None
    kind: str | None = None


class OperationHandle:
    def __init__(self, service: "FeedbackService", operation_id: int) -> None:
        self._service = service
        self.operation_id = operation_id

    def update(
        self,
        status: str | None = None,
        *,
        progress: float | None = None,
        total: float | None = None,
        reveal: bool = True,
        auto_open: bool | None = None,
    ) -> None:
        self._service.update_operation(
            self.operation_id,
            status=status,
            progress=progress,
            total=total,
            reveal=reveal,
            auto_open=auto_open,
        )

    def finish(
        self,
        message: str | None = None,
        *,
        show_success: bool = True,
        level: FeedbackLevel = "success",
    ) -> None:
        self._service.finish_operation(
            self.operation_id,
            message=message,
            show_success=show_success,
            level=level,
        )

    def fail(self, message: str, *, notify: bool = True) -> None:
        self._service.finish_operation(self.operation_id, message=message, show_success=False, level="warning")
        if notify:
            self._service.warning(message)


class FeedbackService:
    ACTIVITY_LIMIT = 100
    ACTIVITY_DEDUPE_WINDOW = 2.0

    def __init__(
        self,
        app,
        *,
        auto_close_delay: float = 0.8,
        render_interval: float = 0.1,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.app = app
        self.auto_close_delay = auto_close_delay
        self.render_interval = max(0.0, render_interval)
        self._timer_factory = timer_factory or threading.Timer
        self._clock = clock or time.monotonic
        self._progress_overlay: Any | None = None
        self._alert_renderer: Any | None = None
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._operations: dict[int, _OperationState] = {}
        self._active_stack: list[int] = []
        self._root_id: int | None = None
        self._progress_visible = False
        self._hide_token = 0
        self._render_token = 0
        self._render_pending = False
        self._pending_render: _ProgressRender | None = None
        self._last_render_at: float | None = None
        self._closing = False
        self._activity: list[_ActivityEntry] = []

    def attach(self, *, progress_overlay: Any | None = None, alert_renderer: Any | None = None) -> None:
        if progress_overlay is not None:
            self._progress_overlay = progress_overlay
        if alert_renderer is not None:
            self._alert_renderer = alert_renderer

    def begin_operation(
        self,
        title: str,
        *,
        kind: str = "operation",
        status: str | None = None,
        progress: float = 0,
        total: float = 100,
        visible: bool = True,
        auto_open: bool = True,
    ) -> OperationHandle:
        start_cycle = False
        render_state: _OperationState | None = None
        force_open = False
        with self._lock:
            if self._closing:
                return OperationHandle(self, -1)

            operation_id = next(self._ids)
            parent_id = self._root_id
            if parent_id is None:
                self._root_id = operation_id
                force_open = True

            state = _OperationState(
                operation_id=operation_id,
                title=title,
                kind=kind,
                status=status or title,
                progress=progress,
                total=total,
                parent_id=parent_id,
                auto_open=auto_open,
                visible=visible,
            )
            self._operations[operation_id] = state
            self._active_stack.append(operation_id)
            self._hide_token += 1
            self._record_activity_locked(
                "begin",
                "info",
                state.status or state.title,
                operation_id=state.operation_id,
                kind=state.kind,
            )

            if visible:
                start_cycle = self._reveal_locked()
                render_state = state

        if start_cycle:
            self._start_progress_cycle()
        if render_state is not None:
            self._show_progress(render_state, force_open=force_open, auto_open=auto_open, immediate=True)
        return OperationHandle(self, operation_id)

    def update_operation(
        self,
        operation_id: int,
        *,
        status: str | None = None,
        progress: float | None = None,
        total: float | None = None,
        reveal: bool = True,
        auto_open: bool | None = None,
    ) -> None:
        start_cycle = False
        render_state: _OperationState | None = None
        render_auto_open = False
        force_open = False
        activity_message: str | None = None
        with self._lock:
            state = self._operations.get(operation_id)
            if state is None or state.finished or self._closing:
                return
            if auto_open is not None:
                state.auto_open = auto_open
            if status is not None:
                state.status = status
                activity_message = status
            if progress is not None:
                state.progress = progress
            if total is not None:
                state.total = total
            if reveal:
                if not state.visible:
                    force_open = True
                state.visible = True
                start_cycle = self._reveal_locked()
                render_state = state
                render_auto_open = state.auto_open
            if activity_message:
                self._record_activity_locked(
                    "update",
                    "info",
                    activity_message,
                    operation_id=state.operation_id,
                    kind=state.kind,
                )

        if start_cycle:
            self._start_progress_cycle()
        if render_state is not None:
            self._show_progress(render_state, force_open=force_open, auto_open=render_auto_open, immediate=force_open)

    def finish_operation(
        self,
        operation_id: int,
        *,
        message: str | None = None,
        show_success: bool = True,
        level: FeedbackLevel = "success",
    ) -> None:
        finalize_state: _OperationState | None = None
        restore_state: _OperationState | None = None
        finish_state: _OperationState | None = None
        with self._lock:
            state = self._operations.get(operation_id)
            if state is None or state.finished:
                return

            state.finished = True
            state.finish_message = message
            state.show_success = show_success
            finish_state = state
            self._active_stack = [item for item in self._active_stack if item != operation_id]

            if state.parent_id is not None:
                self._discard_pending_render_locked()
                self._operations.pop(operation_id, None)
                parent = self._operations.get(state.parent_id)
                if parent is not None and not parent.finished and parent.visible and self._progress_visible:
                    restore_state = parent

            finalize_state = self._finalizable_root_locked()
            if finalize_state is not None:
                self._operations.pop(finalize_state.operation_id, None)
                self._root_id = None
                self._active_stack.clear()

        if finalize_state is not None:
            self._finalize_progress(finalize_state)
        elif restore_state is not None:
            self._show_progress(restore_state, force_open=False, auto_open=restore_state.auto_open, immediate=True)
        activity_message = str(message).strip() if message is not None else ""
        if finish_state is not None and activity_message:
            self._record_activity(
                "finish",
                level,
                activity_message,
                operation_id=finish_state.operation_id,
                kind=finish_state.kind,
            )

    def is_busy(self) -> bool:
        with self._lock:
            if self._root_id is not None:
                return True
            return any(not state.finished for state in self._operations.values())

    def notify(
        self,
        message: object,
        *,
        level: FeedbackLevel = "info",
        record_activity: bool = True,
        **kwargs: Any,
    ) -> None:
        message_text = str(message).strip() if message is not None else ""
        if not message_text:
            return
        renderer = self._alert_renderer
        if record_activity:
            self._record_activity("notify", level, message_text)
        if renderer is None:
            return
        try:
            renderer.show_alert(message_text, is_warning=level in {"warning", "error"}, **kwargs)
        except Exception as exc:
            self._log_debug(f"Feedback notification skipped: {exc!r}")

    def info(self, message: object, **kwargs: Any) -> None:
        self.notify(message, level="info", **kwargs)

    def success(self, message: object, **kwargs: Any) -> None:
        self.notify(message, level="success", **kwargs)

    def warning(self, message: object, **kwargs: Any) -> None:
        self.notify(message, level="warning", **kwargs)

    def error(self, message: object, **kwargs: Any) -> None:
        self.notify(message, level="error", **kwargs)

    def confirm(self, title: str, question: str, callback) -> None:
        renderer = self._alert_renderer
        if renderer is None:
            return
        try:
            renderer.show_confirm(title, question, callback)
        except Exception as exc:
            self._log_debug(f"Feedback confirmation skipped: {exc!r}")

    def shutdown(self, *, update_ui: bool = True) -> None:
        with self._lock:
            self._closing = True
            self._operations.clear()
            self._active_stack.clear()
            self._root_id = None
            self._hide_token += 1
            self._discard_pending_render_locked()
            was_visible = self._progress_visible
            self._progress_visible = False
        if update_ui and was_visible:
            self._hide_progress()

    async def wait_until_progress_hidden(self, *, timeout: float = 0.6) -> None:
        overlay = self._progress_overlay
        waiter = getattr(overlay, "wait_until_hidden", None)
        if not callable(waiter):
            return
        try:
            result = waiter(timeout=timeout)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self._log_debug(f"Progress hide wait skipped: {exc!r}")

    def snapshot(self, *, activity_limit: int = 20) -> dict[str, Any]:
        with self._lock:
            active = [
                self._operation_snapshot(state)
                for state in self._operations.values()
                if not state.finished
            ]
            activity = [self._activity_snapshot(item) for item in self._activity[-max(activity_limit, 0):]]
            return {
                "busy": bool(self._root_id is not None or active),
                "active_operations": active,
                "recent_activity": activity,
            }

    def recent_activity(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [self._activity_snapshot(item) for item in self._activity[-max(limit, 0):]]

    def _reveal_locked(self) -> bool:
        if self._progress_visible:
            return False
        self._progress_visible = True
        return True

    def _finalizable_root_locked(self) -> _OperationState | None:
        if self._root_id is None:
            return None
        root = self._operations.get(self._root_id)
        if root is None or not root.finished:
            return None
        has_active_children = any(
            state.parent_id == root.operation_id and not state.finished
            for state in self._operations.values()
        )
        return None if has_active_children else root

    def _finalize_progress(self, state: _OperationState) -> None:
        with self._lock:
            was_visible = self._progress_visible
            self._hide_token += 1
            self._discard_pending_render_locked()
            if was_visible:
                self._progress_visible = False
        if not was_visible:
            return
        self._hide_progress()
        if state.show_success and state.finish_message:
            self.success(state.finish_message, record_activity=False)

    def _start_progress_cycle(self) -> None:
        overlay = self._progress_overlay
        if overlay is None:
            return
        try:
            overlay.start_cycle()
        except Exception as exc:
            self._log_debug(f"Progress cycle start skipped: {exc!r}")

    def _show_progress(
        self,
        state: _OperationState,
        *,
        force_open: bool,
        auto_open: bool,
        immediate: bool = False,
    ) -> None:
        overlay = self._progress_overlay
        if overlay is None:
            return
        render = _ProgressRender(
            status=state.status or state.title,
            progress=state.progress,
            total=state.total,
            force_open=force_open,
            auto_open=auto_open,
        )
        immediate_render: _ProgressRender | None = None
        timer: threading.Timer | None = None
        fallback_render: _ProgressRender | None = None
        with self._lock:
            if self._closing:
                return

            # Download/install callbacks can be very hot; keep the latest state
            # but do not enqueue a Flet page update for every callback.
            now = self._clock()
            elapsed = None if self._last_render_at is None else now - self._last_render_at
            if immediate or self.render_interval <= 0 or elapsed is None or elapsed >= self.render_interval:
                self._discard_pending_render_locked()
                self._last_render_at = now
                immediate_render = render
            else:
                self._pending_render = render
                if not self._render_pending:
                    self._render_pending = True
                    render_token = self._render_token
                    delay = max(self.render_interval - elapsed, 0.0)
                    fallback_render = render
                    timer = self._timer_factory(delay, lambda: self._flush_pending_render(render_token))

        if immediate_render is not None:
            self._render_progress(immediate_render)
            return
        if timer is not None:
            try:
                timer.daemon = True
                timer.start()
            except Exception as exc:
                self._log_debug(f"Progress throttle timer skipped: {exc!r}")
                with self._lock:
                    self._render_pending = False
                    self._pending_render = None
                    self._last_render_at = self._clock()
                if fallback_render is not None:
                    self._render_progress(fallback_render)

    def _flush_pending_render(self, render_token: int) -> None:
        with self._lock:
            if self._closing or render_token != self._render_token or self._pending_render is None:
                return
            render = self._pending_render
            self._pending_render = None
            self._render_pending = False
            self._last_render_at = self._clock()
        self._render_progress(render)

    def _discard_pending_render_locked(self) -> None:
        self._render_token += 1
        self._pending_render = None
        self._render_pending = False

    def _render_progress(self, render: _ProgressRender) -> None:
        overlay = self._progress_overlay
        if overlay is None:
            return
        try:
            overlay.show(
                render.status,
                render.progress,
                render.total,
                force_open=render.force_open,
                auto_open=render.auto_open,
            )
        except Exception as exc:
            self._log_debug(f"Progress update skipped: {exc!r}")

    def _hide_progress(self) -> None:
        overlay = self._progress_overlay
        if overlay is None:
            return
        try:
            overlay.hide(manual=False)
        except Exception as exc:
            self._log_debug(f"Progress hide skipped: {exc!r}")

    def _log_debug(self, message: str) -> None:
        logger = getattr(self.app, "log", None)
        debug = getattr(logger, "debug", None)
        if callable(debug):
            debug(message)

    def _record_activity(
        self,
        event: str,
        level: FeedbackLevel,
        message: str,
        *,
        operation_id: int | None = None,
        kind: str | None = None,
    ) -> None:
        with self._lock:
            self._record_activity_locked(
                event,
                level,
                message,
                operation_id=operation_id,
                kind=kind,
            )

    def _record_activity_locked(
        self,
        event: str,
        level: FeedbackLevel,
        message: str,
        *,
        operation_id: int | None = None,
        kind: str | None = None,
    ) -> None:
        text = str(message or "").strip()
        if not text:
            return
        entry = _ActivityEntry(
            timestamp=round(self._clock(), 3),
            event=event,
            level=level,
            message=text,
            operation_id=operation_id,
            kind=kind,
        )
        duplicate_index = self._find_recent_duplicate_locked(entry)
        if duplicate_index is not None:
            if self._should_replace_duplicate(self._activity[duplicate_index], entry):
                self._activity[duplicate_index] = entry
            return
        self._activity.append(entry)
        if len(self._activity) > self.ACTIVITY_LIMIT:
            self._activity = self._activity[-self.ACTIVITY_LIMIT:]

    def _find_recent_duplicate_locked(self, entry: _ActivityEntry) -> int | None:
        for index in range(len(self._activity) - 1, -1, -1):
            candidate = self._activity[index]
            if entry.timestamp - candidate.timestamp > self.ACTIVITY_DEDUPE_WINDOW:
                break
            if candidate.level == entry.level and candidate.message == entry.message:
                return index
        return None

    @staticmethod
    def _should_replace_duplicate(existing: _ActivityEntry, incoming: _ActivityEntry) -> bool:
        if existing.kind in {None, "launch"} and incoming.kind not in {None, existing.kind}:
            return True
        if existing.event == "notify" and incoming.event != "notify":
            return True
        return False

    @staticmethod
    def _operation_snapshot(state: _OperationState) -> dict[str, Any]:
        return {
            "id": state.operation_id,
            "title": state.title,
            "kind": state.kind,
            "status": state.status,
            "progress": state.progress,
            "total": state.total,
            "visible": state.visible,
            "auto_open": state.auto_open,
            "parent_id": state.parent_id,
        }

    @staticmethod
    def _activity_snapshot(entry: _ActivityEntry) -> dict[str, Any]:
        return {
            "timestamp": entry.timestamp,
            "event": entry.event,
            "level": entry.level,
            "message": entry.message,
            "operation_id": entry.operation_id,
            "kind": entry.kind,
        }


__all__ = ["FeedbackLevel", "FeedbackService", "OperationHandle"]
