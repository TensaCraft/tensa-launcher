from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, cast

from launcher.models.logger import Logger

if TYPE_CHECKING:
    from launcher.application.feedback import OperationHandle
    from launcher.application.instance_operations import InstanceOperationLease
    from launcher.core.versions import Version

Translator = Callable[..., str]
Verifier = Callable[..., bool]
OptionsBuilder = Callable[["Version", dict[str, Any]], dict[str, Any]]
ProcessLauncher = Callable[..., bool]
LaunchSlotReleaser = Callable[[str], None]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class LaunchPreparationRequest:
    version: Version
    launch_key: str
    started_at: float
    profile_key: str | None = None
    instance_lease: InstanceOperationLease | None = None


@dataclass(frozen=True, slots=True)
class LaunchWorkflowResult:
    status: bool
    message_key: str
    message_args: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_args", MappingProxyType(dict(self.message_args)))

    def as_response(self, translator: Translator) -> dict[str, Any]:
        response: dict[str, Any] = {
            "status": self.status,
            "text": translator(self.message_key, **dict(self.message_args)),
        }
        if self.reason is not None:
            response["reason"] = self.reason
        return response


class LaunchWorkflow:
    def __init__(
        self,
        app: Any,
        *,
        verify: Verifier,
        build_options: OptionsBuilder,
        launch: ProcessLauncher,
        release_launch_slot: LaunchSlotReleaser,
        clock: Clock | None = None,
    ) -> None:
        self._app = app
        self._verify = verify
        self._build_options = build_options
        self._launch = launch
        self._release_launch_slot = release_launch_slot
        self._clock = clock or time.perf_counter

    def run(self, request: LaunchPreparationRequest) -> LaunchWorkflowResult:
        version = request.version
        launch_started = False
        workflow_succeeded = False
        operation: OperationHandle | None = None
        timing = {"auth": 0.0, "verify": 0.0, "sync": 0.0, "opts": 0.0, "launch": 0.0}
        try:
            operation = self._app.feedback.begin_operation(
                self._app.trans("syncing_files_check"),
                kind="launch",
                visible=False,
                auto_open=False,
            )
            assert operation is not None
            profile = self._get_profile(request.profile_key)
            if not profile:
                return LaunchWorkflowResult(
                    status=False,
                    message_key="no_default_profile",
                    reason="missing_profile",
                )

            requires_reauth = getattr(self._app.auth, "profile_requires_reauth", lambda _profile: False)
            if requires_reauth(profile):
                return LaunchWorkflowResult(
                    status=False,
                    message_key="profile_reauth_required",
                )
            timing["auth"] = (self._clock() - request.started_at) * 1000.0

            if version.is_tensacraft():
                started_at = self._clock()
                sync_error = self._sync_version(request)
                timing["sync"] = (self._clock() - started_at) * 1000.0
                if sync_error is not None:
                    return self._sync_failure(version, sync_error)
                started_at = self._clock()
                verified = self._verify_version(version, operation)
                timing["verify"] = (self._clock() - started_at) * 1000.0
            else:
                started_at = self._clock()
                verified = self._verify_version(version, operation)
                timing["verify"] = (self._clock() - started_at) * 1000.0
                if not verified:
                    return self._verify_failure(version)
                if version.force_update:
                    started_at = self._clock()
                    sync_error = self._sync_version(request)
                    timing["sync"] = (self._clock() - started_at) * 1000.0
                    if sync_error is not None:
                        return self._sync_failure(version, sync_error)

            if not verified:
                return self._verify_failure(version)

            started_at = self._clock()
            self._backup_worlds(request, operation)
            options = self._build_options(version, profile)
            timing["opts"] = (self._clock() - started_at) * 1000.0

            started_at = self._clock()
            launch_started = self._launch_process(request, options)
            finished_at = self._clock()
            timing["launch"] = (finished_at - started_at) * 1000.0
            self._log_timing(version, timing, (finished_at - request.started_at) * 1000.0)

            if not launch_started:
                return LaunchWorkflowResult(
                    status=False,
                    message_key="version_integrity_check_failed",
                    message_args={"version": version.name},
                )
            if version.is_tensacraft() and not version.is_home_pinned():
                version.mark_home_pinned()

            workflow_succeeded = True
            return LaunchWorkflowResult(
                status=True,
                message_key="version_starting",
                message_args={"version": version.name},
            )
        finally:
            try:
                if operation is not None:
                    success_message = None
                    if workflow_succeeded:
                        success_key = (
                            "syncing_files_complete"
                            if version.force_update or version.is_tensacraft()
                            else "installation_complete"
                        )
                        success_message = self._app.trans(success_key)
                    operation.finish(
                        success_message,
                        show_success=success_message is not None,
                    )
            finally:
                if not launch_started:
                    self._release_launch_slot(request.launch_key)

    def _get_profile(self, profile_key: str | None) -> dict[str, Any] | None:
        if not profile_key:
            return cast(dict[str, Any] | None, self._app.auth.get_default_profile_data())
        profile_getter = getattr(self._app.auth, "get_profile_data", None)
        if callable(profile_getter):
            return cast(dict[str, Any] | None, profile_getter(profile_key))
        profile = self._app.profiles.get_profile(profile_key)
        if not profile:
            return None
        return cast(dict[str, Any] | None, self._app.auth.ensure_profile_authorized(profile))

    def _sync_version(self, request: LaunchPreparationRequest) -> Exception | None:
        try:
            sync_update = request.version.sync_update
            if "lease" in self._parameters(sync_update):
                sync_update(lease=request.instance_lease)
            else:
                sync_update()
        except Exception as exc:
            Logger.error(f"Version sync failed for {request.version.name}: {exc}")
            return exc
        return None

    def _verify_version(self, version: Version, operation: OperationHandle) -> bool:
        if "operation" in self._parameters(self._verify):
            verified = self._verify(version, operation=operation)
        else:
            verified = self._verify(version)
        return bool(verified)

    def _backup_worlds(self, request: LaunchPreparationRequest, operation: OperationHandle) -> None:
        service = getattr(self._app, "world_backups", None)
        enabled = getattr(service, "enabled", None)
        if callable(enabled) and not enabled():
            return
        backup = getattr(service, "auto_backup_changed_worlds", None)
        if not callable(backup):
            return
        try:
            kwargs: dict[str, Any] = {"operation": operation}
            if "lease" in self._parameters(backup):
                kwargs["lease"] = request.instance_lease
            result = backup(request.version, **kwargs)
            created = int(getattr(result, "created", 0) or 0)
            failed = int(getattr(result, "failed", 0) or 0)
            if created:
                Logger.info(f"Created {created} world backup(s) before launching {request.version.name}")
            if failed:
                Logger.warning(f"Failed to create {failed} world backup(s) before launching {request.version.name}")
        except Exception as exc:
            Logger.warning(f"World backup step failed before launching {request.version.name}: {exc!r}")

    def _launch_process(self, request: LaunchPreparationRequest, options: dict[str, Any]) -> bool:
        version = request.version
        if "version" in self._parameters(self._launch):
            launched = self._launch(
                str(version.loader or ""),
                str(version.version or ""),
                options,
                launch_key=request.launch_key,
                version=version,
            )
        else:
            launched = self._launch(
                str(version.loader or ""),
                str(version.version or ""),
                options,
                launch_key=request.launch_key,
            )
        return bool(launched)

    @staticmethod
    def _parameters(callback: Callable[..., Any]) -> Mapping[str, inspect.Parameter]:
        try:
            return inspect.signature(callback).parameters
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _sync_failure(
        version: Version,
        error: Exception,
    ) -> LaunchWorkflowResult:
        return LaunchWorkflowResult(
            status=False,
            message_key="version_sync_failed",
            message_args={"version": version.name, "error": str(error)},
        )

    @staticmethod
    def _verify_failure(version: Version) -> LaunchWorkflowResult:
        return LaunchWorkflowResult(
            status=False,
            message_key="version_integrity_check_failed",
            message_args={"version": version.name},
        )

    @staticmethod
    def _log_timing(version: Version, timing: Mapping[str, float], total_ms: float) -> None:
        try:
            Logger.info(
                "Launch timing: "
                f"ver={version.version} loader={version.loader} "
                f"auth={timing['auth']:.0f}ms "
                f"verify={timing['verify']:.0f}ms "
                f"sync={timing['sync']:.0f}ms "
                f"opts={timing['opts']:.0f}ms "
                f"launch={timing['launch']:.0f}ms "
                f"total={total_ms:.0f}ms"
            )
        except Exception:
            return


__all__ = [
    "LaunchPreparationRequest",
    "LaunchWorkflow",
    "LaunchWorkflowResult",
]
