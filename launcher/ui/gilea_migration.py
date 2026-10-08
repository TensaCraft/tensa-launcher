from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import flet as ft
import requests

from launcher import ui
from launcher.application.gilea_migration import release, service, transaction
from launcher.application.gilea_migration.models import (
    MigrationError,
    MigrationPlan,
    MigrationProgress,
    MigrationResult,
)
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.models.translator import Translator
from launcher.platform import gilea_migration as windows
from launcher.platform.security import SecurityService
from launcher.ui.core.page_runtime import close_dialog, run_blocking, schedule_update, show_dialog

ERROR_CODES = (
    "invalid_settings", "invalid_profiles", "invalid_builds", "invalid_json", "unsafe_path", "missing_profile",
    "ambiguous_build_path", "build_collision", "invalid_release", "download_failed", "unsafe_download", "checksum",
    "destination_conflict", "disk_space", "missing_build", "running_process", "source_changed", "unsupported_platform",
    "webview_missing", "process_check_failed", "backup_protection", "source_busy", "copy_consent", "journal_invalid",
    "repair_required", "launch_failed", "shortcut_conflict", "io", "cancelled",
)


class GileaMigrationController:
    def __init__(self, app: Any):
        self.app = app
        self.available = windows.supported_platform() and getattr(sys, "frozen", False) is True
        self.translate = Translator(app.config.get("lang", "en_US")).get
        self.busy = False
        self._cancel = threading.Event()
        self._worker_done = threading.Event()
        self._worker_done.set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._confirmation: asyncio.Future[bool] | None = None
        self._dialog: ft.AlertDialog | None = None
        self._label: ft.Text | None = None
        self._bar: ft.ProgressBar | None = None

    @property
    def closed(self) -> bool:
        return bool(getattr(self.app, "_terminating", False))

    async def _confirm(self, question: str) -> bool:
        self._loop = asyncio.get_running_loop()
        loop = self._loop
        future: asyncio.Future[bool] = loop.create_future()
        self._confirmation = future

        def answer(value: bool):
            def settle():
                if not future.done():
                    future.set_result(bool(value) and not self.closed and not self._cancel.is_set())
            try:
                loop.call_soon_threadsafe(settle)
            except RuntimeError:
                self._cancel.set()

        self.app.feedback.confirm(self.translate("gilea_title"), question, answer)
        try:
            return await future
        finally:
            self._confirmation = None

    async def offer(self) -> None:
        if (not self.available or self.closed or self.busy or self._confirmation is not None
                or getattr(self.app, "_stay_on_tensa", False)):
            return
        self._cancel.clear()
        if await self._confirm(self.translate("gilea_confirm")):
            await self.start(allow_external_copy=False)

    async def startup(self) -> None:
        await self.offer()
        if not self.closed and self.app.config.get("check_updates", self.app.config.get("auto_update", "yes")) == "yes":
            await self.app.updater.check_for_updates_async()

    def cancel(self) -> None:
        self._cancel.set()
        future = self._confirmation
        if future is not None and self._loop is not None:
            def settle():
                if not future.done():
                    future.set_result(False)
            try:
                self._loop.call_soon_threadsafe(settle)
            except RuntimeError:
                pass

    def wait_for_worker(self, timeout: float = 45) -> bool:
        return self._worker_done.wait(timeout)

    def _progress(self, event: MigrationProgress) -> None:
        loop = self._loop
        if loop is None or self.closed:
            return
        def render():
            if not self.closed:
                self._render_progress(event)
        try:
            loop.call_soon_threadsafe(render)
        except RuntimeError:
            self._cancel.set()

    def _render_progress(self, event: MigrationProgress) -> None:
        if self._label is not None and self._bar is not None:
            self._label.value = self.translate("gilea_phase_" + event.phase)
            self._bar.value = min(1, event.completed / event.total) if event.total else None
            schedule_update(self.app.page)

    def _show_progress(self) -> None:
        self._label = ui.Text(self.translate("gilea_phase_prepare"))
        self._bar = ft.ProgressBar(value=None)
        self._dialog = ft.AlertDialog(
            modal=True, title=ui.Text(self.translate("gilea_title")),
            content=ft.Column([self._label, self._bar], width=400, tight=True),
            actions=[ui.Button(text=self.translate("cancel"), icon=ft.Icons.CLOSE,
                               on_click=lambda event: self.cancel(), variant="text")],
        )
        show_dialog(self.app.page, self._dialog)

    def _hide_progress(self) -> None:
        if self._dialog is not None and not self.closed:
            close_dialog(self.app.page, self._dialog)
        self._dialog = None

    def _prepare(self) -> MigrationPlan | MigrationResult:
        auth = getattr(self.app, "_auth_refresh_thread", None)
        if auth is not None:
            auth.join(timeout=20)
            if auth.is_alive():
                raise MigrationError("source_busy")
        roots = windows.default_roots(Path(self.app.paths.app_state_dir), Path(self.app.paths.minecraft_dir))
        transaction.recover_migration(roots)
        committed = service.load_committed_migration(roots)
        if committed is not None:
            return committed
        default_ram = MemoryPreferencesService.normalize_max_ram_gb(self.app.config.get("default_max_ram_gb"))
        with getattr(self.app.profiles, "_lock", nullcontext()):
            return service.prepare_plan(
                roots, effective_defaults={"default_max_ram_gb": default_ram, "lang": self.app.config.get("lang", "en_US")},
                legacy_key=SecurityService().get_legacy_user_secret().encode(),
            )

    def _execute(self, plan: MigrationPlan, allow_external_copy: bool) -> MigrationResult:
        windows.protect_directory(plan.roots.recovery)
        with tempfile.TemporaryDirectory(prefix="download-", dir=plan.roots.recovery) as directory:
            with requests.Session() as session:
                session.trust_env = False
                asset = release.fetch_asset(session)
                binary = release.download_asset(asset, Path(directory) / release.ASSET_NAME, session=session,
                                                cancel=self._cancel, progress=self._progress)
            with self.app.profiles._lock:
                result = transaction.commit_migration(plan, binary, allow_external_copy=allow_external_copy,
                                                       cancel=self._cancel, progress=self._progress)
        return result

    async def _work(self, callback, *args, **kwargs):
        self._worker_done.clear()
        def execute():
            try:
                return callback(*args, **kwargs)
            finally:
                self._worker_done.set()
        task = asyncio.create_task(run_blocking(execute))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._cancel.set()
            try:
                await task
            except Exception:
                pass
            raise

    def _report_error(self, error: MigrationError) -> None:
        if self.closed:
            return
        code = error.code if error.code in ERROR_CODES else "io"
        self.app.feedback.warning(self.translate("gilea_error_" + code))

    async def start(self, *, allow_external_copy: bool = False) -> None:
        if self.busy or self.closed or not self.available or self.app.feedback.is_busy():
            return
        self.busy = True
        self._loop = asyncio.get_running_loop()
        disabled = [(control, control.disabled) for control in self.app.page.controls]
        try:
            for control, _ in disabled:
                control.disabled = True
            self._show_progress()
            plan = await self._work(self._prepare)
            if self.closed or self._cancel.is_set():
                return
            copies = [b for b in plan.adapted.builds if b.copy_required] if isinstance(plan, MigrationPlan) else []
            if copies and not allow_external_copy:
                self._hide_progress()
                names = "\n".join(str(b.source) for b in copies)
                if not await self._confirm(self.translate("gilea_copy_confirm", builds=names)):
                    return
                allow_external_copy = True
                self._show_progress()
            if isinstance(plan, MigrationPlan) and plan.adapted.warnings:
                self._hide_progress()
                details = "\n".join(self.translate("gilea_warning_" + w.code, item=w.item or "")
                                    for w in plan.adapted.warnings)
                if not await self._confirm(self.translate("gilea_warnings", details=details)):
                    return
                self._show_progress()
            result = plan if isinstance(plan, MigrationResult) else await self._work(self._execute, plan, allow_external_copy)
            if self.closed or self._cancel.is_set():
                return
            try:
                await self._work(windows.create_gilea_shortcuts, result,
                                 recovery_command=windows.recovery_command())
            except (MigrationError, OSError, subprocess.SubprocessError):
                self._hide_progress()
                self._report_error(MigrationError("shortcut_conflict"))
            if not self.closed and not self._cancel.is_set():
                await self._work(windows.launch_gilea, result, initial=isinstance(plan, MigrationPlan))
                if not self.closed:
                    self._hide_progress()
                    self.app.stop()
        except MigrationError as error:
            self._hide_progress()
            self._report_error(error)
        except OSError:
            self._hide_progress()
            self._report_error(MigrationError("io"))
        finally:
            self._hide_progress()
            if not self.closed:
                for control, value in disabled:
                    control.disabled = value
                schedule_update(self.app.page)
            self.busy = False
