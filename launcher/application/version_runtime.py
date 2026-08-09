from __future__ import annotations

import inspect
from typing import Any

from launcher.application.instance_operations import InstanceOperationLease


class VersionRuntime:
    """Bind domain version actions to one concrete application session."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def install(self, version: Any) -> None:
        loader = self._app.launcher.get_loader(version.client)
        loader.install(version, loader_version=version.loader_version)

    def sync_update(
        self,
        version: Any,
        *,
        force: bool = False,
        lease: InstanceOperationLease | None = None,
    ) -> None:
        loader = self._app.launcher.get_loader(version.client)
        try:
            parameters = inspect.signature(loader.sync_update).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs: dict[str, Any] = {}
        if "force" in parameters:
            kwargs["force"] = force
        if "lease" in parameters:
            kwargs["lease"] = lease
        loader.sync_update(version, **kwargs)

    def start(
        self,
        version: Any,
        *,
        allow_duplicate: bool = False,
        profile_key: str | None = None,
    ) -> Any:
        return self._app.game.start(
            version,
            allow_duplicate=allow_duplicate,
            profile_key=profile_key,
        )


__all__ = ["VersionRuntime"]
