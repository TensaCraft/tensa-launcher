from typing import Any, Optional

import minecraft_launcher_lib

from launcher.application.feedback import OperationHandle
from launcher.core.versions import Version
from launcher.models.logger import Logger
from .base import BaseLoader


class ModLoader(BaseLoader):
    def __init__(self, loader_name: str):
        super().__init__()
        self.loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_name)

    def get_id(self) -> str:
        return self.loader.get_id()

    def get_name(self) -> str:
        return self.loader.get_name()

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
            loader_name = self.get_id()
            Logger.info(f"Installing {self.get_name()} for Minecraft {version.version}")

            # Use the shared mod loader installation flow from the base class
            installed_version_name, actual_loader_version = self._install_mod_loader(
                mc_version=version.version,
                loader_name=loader_name,
                requested_loader_version=loader_version,
                operation=operation,
            )

            version.path = str(self.get_game_path(version.version_id))
            version.loader = installed_version_name
            version.loader_version = actual_loader_version
            version.client = self.get_name()
            version.options["executablePath"] = self._get_required_loader_java_path(
                version.version,
                operation=operation,
            )
            version.save()

            if callback:
                callback()
        finally:
            if owns_operation:
                self.finish_feedback_operation(operation)
            else:
                self._feedback_operation = previous_operation

    def versions(self) -> list[str]:
        return self.loader.get_minecraft_versions(True)



class FabricLoader(ModLoader):
    def __init__(self):
        super().__init__("fabric")


class ForgeLoader(ModLoader):
    def __init__(self):
        super().__init__("forge")


class NeoForgeLoader(ModLoader):
    def __init__(self):
        super().__init__("neoforge")

    def verify_and_repair_version(self, version_id: str, mc_version: Optional[str] = None) -> bool:
        return super().verify_and_repair_version(version_id, mc_version)


class QuiltLoader(ModLoader):
    def __init__(self):
        super().__init__("quilt")
