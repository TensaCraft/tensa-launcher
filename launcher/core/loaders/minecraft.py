from typing import Any, Optional

import minecraft_launcher_lib

from launcher.application.feedback import OperationHandle
from launcher.core.versions import Version
from .base import BaseLoader


class MinecraftLoader(BaseLoader):
    def get_id(self) -> str:
        return "minecraft"

    def get_name(self) -> str:
        return "Minecraft"

    def install(
        self,
        version: Version,
        callback: Optional[Any] = None,
        java_path: Optional[str] = None,
        loader_version: Optional[str] = None,
        operation: OperationHandle | None = None,
    ) -> None:
        owns_operation = operation is None
        previous_operation = self._feedback_operation
        if operation is None:
            operation = self.begin_feedback_operation(status=self.app.trans("installation_started") if self.app else None)
        else:
            self._feedback_operation = operation
        try:
            self._install_minecraft_if_needed(version.version, operation=operation)

            version.path = str(self.get_game_path(version.version_id))
            version.loader = version.version
            version.client = self.get_name()
            version.save()

            if callback:
                callback()
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    def versions(self) -> list[str]:
        return [
            v["id"] for v in minecraft_launcher_lib.utils.get_version_list()
            if v.get("type") == "release"
        ]

    def verify_and_repair_version(self, version_id: str, mc_version: Optional[str] = None) -> bool:
        from launcher.models.logger import Logger

        minecraft_version = mc_version or version_id
        Logger.info(f"Checking vanilla Minecraft version: {minecraft_version}")

        if not self.integrity_checker._is_version_installed(version_id):
            Logger.warning(f"Vanilla Minecraft version {minecraft_version} is not installed, installing")
            try:
                self._install_minecraft_if_needed(minecraft_version, force_check=True)
            except Exception as exc:
                Logger.error(f"Failed to install vanilla Minecraft version {minecraft_version}: {exc}")
                return False

        return bool(self.integrity_checker._is_version_installed(version_id))
