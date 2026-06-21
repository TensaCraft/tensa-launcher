from __future__ import annotations

from abc import ABC, abstractmethod
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any, Optional, Union
import zipfile

import minecraft_launcher_lib
from minecraft_launcher_lib._helper import SUBPROCESS_STARTUP_INFO, download_file, empty
from minecraft_launcher_lib.install import install_libraries
import requests

from launcher.application.java_runtime import JavaRuntimeService
from launcher.application.feedback import FeedbackLevel, OperationHandle
from launcher.core import util
from launcher.core.integrity import IntegrityChecker
from launcher.core.minecraft_install import (
    decode_error_output,
    format_install_error,
    install_minecraft_version_with_retries,
    is_java_runtime_process_failure,
    is_retryable_install_error,
)
from launcher.models.logger import Logger
from launcher.platform.java_process import java_subprocess_kwargs, launcher_java_path
from launcher.platform.windows_error_mode import suppress_windows_error_dialogs
from launcher.shared import AppContext

if TYPE_CHECKING:
    from launcher.domain.version import Version


class BaseLoader(ABC):
    MOD_LOADER_INSTALL_ATTEMPTS = 3
    MINECRAFT_INSTALL_ATTEMPTS = 4
    INCOMPLETE_INSTALL_MARKER = ".tensalauncher-installing"
    SUCCESSFUL_INSTALL_MARKER = ".tensalauncher-installed"

    def __init__(
        self,
        *,
        minecraft_dir: str | Path | None = None,
        games_dir: str | Path | None = None,
    ) -> None:
        self.app = AppContext.get()
        self.minecraft_dir = self._resolve_minecraft_dir(minecraft_dir)
        self.install_dir = self._resolve_games_dir(games_dir)
        self.integrity_checker = IntegrityChecker(self.minecraft_dir)
        self.runtime = JavaRuntimeService(self.minecraft_dir, Logger)
        self._feedback_operation: OperationHandle | None = None

    def _resolve_minecraft_dir(self, override: str | Path | None = None) -> Path:
        if override is not None:
            return Path(override)
        paths = getattr(self.app, "paths", None)
        if paths is not None:
            minecraft_dir = getattr(paths, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        app_util = getattr(self.app, "util", None)
        if app_util is not None:
            minecraft_dir = getattr(app_util, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        return Path(util.minecraft_dir)

    def _resolve_games_dir(self, override: str | Path | None = None) -> Path:
        if override is not None:
            return Path(override)
        paths = getattr(self.app, "paths", None)
        if paths is not None:
            games_dir = getattr(paths, "games_dir", None)
            if games_dir:
                return Path(games_dir)
        app_util = getattr(self.app, "util", None)
        if app_util is not None:
            games_dir = getattr(app_util, "games_path", None)
            if games_dir:
                return Path(games_dir)
        return Path(util.games_path)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    @abstractmethod
    def get_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    @abstractmethod
    def install(
        self,
        version: Version,
        callback: Optional[Any] = None,
        java_path: Optional[str] = None,
        loader_version: Optional[str] = None,
        operation: OperationHandle | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def versions(self) -> list[str]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_string(name: str) -> str:
        return util.normalize_string(name)

    @classmethod
    def find_java_executable(cls, base_path: Union[str, Path]) -> Optional[str]:
        java_path = JavaRuntimeService.find_java_executable(base_path)
        return str(java_path) if java_path else None

    def get_version_java_path(
        self,
        minecraft_version: str,
        operation: OperationHandle | None = None,
    ) -> Optional[str]:
        """Return the Java path for a Minecraft version, installing it when needed."""
        had_runtime = bool(self.runtime.has_runtime(minecraft_version, minecraft_version))

        def on_install(runtime_name: str, version_key: str) -> None:
            Logger.info(f"Installing Java runtime {runtime_name} for Minecraft {version_key}")
            self._update_feedback_operation(f"Installing Java runtime {runtime_name}", operation=operation)

        java_path = self.runtime.ensure_runtime(
            minecraft_version,
            callback=self._install_callbacks(operation),
            on_install=on_install,
        )

        if java_path and not had_runtime and self.app and hasattr(self.app, "_refresh_java_versions"):
            try:
                self.app._refresh_java_versions()
            except Exception as exc:
                Logger.debug(f"Java versions refresh failed after runtime install: {exc!r}")

        return java_path

    def loaders_path(self, path: Optional[str]) -> Path:
        relative = Path(path or "")
        return self.minecraft_dir / "versions" / relative

    def loader_exists(self, path: Optional[str]) -> bool:
        return self.loaders_path(path).exists()

    def install_callback(self, operation: OperationHandle | None = None) -> Any:
        if self.app is None:
            Logger.warning("App instance is not initialised; install callbacks unavailable")
            return None
        return self.app.install_callback(self.app, operation or self._feedback_operation).get_install_callbacks()

    def _install_callbacks(self, operation: OperationHandle | None = None) -> Any:
        try:
            return self.install_callback(operation)
        except TypeError:
            return self.install_callback()

    def _get_version_java_path(
        self,
        minecraft_version: str,
        operation: OperationHandle | None = None,
    ) -> Optional[str]:
        try:
            parameters = inspect.signature(self.get_version_java_path).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "operation" in parameters:
            return self.get_version_java_path(minecraft_version, operation=operation)
        return self.get_version_java_path(minecraft_version)

    def _get_required_loader_java_path(
        self,
        minecraft_version: str,
        operation: OperationHandle | None = None,
    ) -> str:
        java_path = self._get_version_java_path(minecraft_version, operation=operation)
        if not java_path:
            raise RuntimeError(f"Unable to install required Java runtime for Minecraft {minecraft_version}")
        return java_path

    def sync_update(self, version: Version) -> None:
        return None

    def begin_feedback_operation(
        self,
        status: str | None = None,
        progress: float = 0,
        max_progress: float = 100,
        *,
        title: str | None = None,
        kind: str = "install",
        visible: bool = True,
        auto_open: bool = True,
    ) -> OperationHandle | None:
        if self.app:
            operation = self.app.feedback.begin_operation(
                title or status or self.app.trans("installation_started"),
                kind=kind,
                status=status,
                progress=progress,
                total=max_progress,
                visible=visible,
                auto_open=auto_open,
            )
            self._feedback_operation = operation
            return operation
        return None

    def finish_feedback_operation(
        self,
        operation: OperationHandle | None,
        message: str | None = None,
        *,
        show_success: bool = True,
        level: FeedbackLevel = "success",
    ) -> None:
        if operation is not None:
            operation.finish(message, show_success=show_success, level=level)
        if operation is not None and operation is self._feedback_operation:
            self._feedback_operation = None

    def _update_feedback_operation(
        self,
        status: str = "",
        progress: int = 0,
        max_progress: int = 100,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        """Update the active install operation."""
        handle = operation or self._feedback_operation
        updater = getattr(handle, "update", None)
        if callable(updater):
            updater(status or None, progress=progress, total=max_progress)

    def _busy_message(self) -> None:
        if self.app:
            self.app.feedback.info(self.app.trans("installation_already_running"))

    def _operation_title(self, fallback_key: str = "installation_started") -> str:
        if not self.app:
            return fallback_key
        return self.app.trans(fallback_key)

    def get_game_path(self, version_id: str) -> Path:
        game_path = self.install_dir / version_id
        game_path.mkdir(parents=True, exist_ok=True)
        return game_path

    def _install_minecraft_if_needed(
        self,
        mc_version: str,
        force_check: bool = False,
        operation: OperationHandle | None = None,
    ) -> None:
        """Ensure vanilla Minecraft files are present.

        minecraft-launcher-lib owns the exact download plan for base Minecraft
        files. Launch-time full integrity scans are intentionally avoided here:
        they have produced false negatives for platform-specific libraries in
        restricted package or sandbox environments and blocked otherwise
        repairable installs.
        """
        exists = self.loader_exists(mc_version)
        if exists and not force_check:
            Logger.info(f"Minecraft {mc_version} already installed, skipping")
            return

        if exists:
            Logger.info(f"Refreshing Minecraft {mc_version}")
            self._update_feedback_operation(
                self._minecraft_status("repairing_minecraft_version", mc_version, f"Repairing Minecraft {mc_version}"),
                operation=operation,
            )
        else:
            Logger.info(f"Installing Minecraft {mc_version}")
            self._update_feedback_operation(
                self._minecraft_status("installing_minecraft_version", mc_version, f"Installing Minecraft {mc_version}"),
                operation=operation,
            )

        self._install_minecraft_version_files(mc_version, operation=operation)

    def _check_minecraft_version_files(self, mc_version: str) -> dict[str, Any]:
        """Check base Minecraft files without requiring Java runtime."""
        return self.integrity_checker.check_version(mc_version, check_java=False)

    def _install_minecraft_version_files(
        self,
        mc_version: str,
        *,
        operation: OperationHandle | None = None,
    ) -> None:
        """Run minecraft-launcher-lib's base Minecraft installer."""
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}
        install_minecraft_version_with_retries(
            mc_version,
            self.minecraft_dir,
            callback=self._install_callbacks(operation),
            attempts=self.MINECRAFT_INSTALL_ATTEMPTS,
        )
        IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    def _minecraft_status(self, key: str, mc_version: str, fallback: str) -> str:
        trans = getattr(self.app, "trans", None)
        if not callable(trans):
            return fallback
        try:
            return trans(key, version=mc_version)
        except Exception:
            return fallback

    def _get_mod_loader_instance(self, loader_name: str):
        """Return a mod loader instance from minecraft_launcher_lib."""
        try:
            return minecraft_launcher_lib.mod_loader.get_mod_loader(loader_name)
        except (AttributeError, ValueError) as e:
            Logger.error(f"Failed to get mod_loader '{loader_name}': {e}")
            if self.app:
                self.app.feedback.warning(self.app.trans('missing_mod_loader_support'))
            raise

    def _install_mod_loader(
        self,
        mc_version: str,
        loader_name: str,
        requested_loader_version: Optional[str] = None,
        java_path: Optional[str] = None,
        force_check: bool = False,
        operation: OperationHandle | None = None,
    ) -> tuple[str, str]:
        """Install a mod loader and verify existing installs before reuse.

        Args:
            mc_version: Minecraft version.
            loader_name: Loader name (fabric, forge, neoforge, quilt).
            requested_loader_version: Exact loader version requested by metadata.
            java_path: Deprecated external Java path, ignored for installer stability.
            force_check: Check integrity even when the version already exists.

        Returns:
            tuple[str, str]: (installed_version_name, actual_loader_version).
        """
        mod_loader = self._get_mod_loader_instance(loader_name)

        # Use the metadata-pinned loader version when one is provided.
        requested_version = str(requested_loader_version).strip() if requested_loader_version else None
        actual_loader_version = requested_version or mod_loader.get_latest_loader_version(mc_version)
        if operation is not None:
            self._install_minecraft_if_needed(mc_version, force_check=force_check, operation=operation)
        else:
            self._install_minecraft_if_needed(mc_version, force_check=force_check)
        IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

        # Check whether the exact loader version is already installed
        installed_version_name = mod_loader.get_installed_version(mc_version, actual_loader_version)
        exists = self.loader_exists(installed_version_name)

        if exists and not force_check and self._existing_mod_loader_install_is_reusable(installed_version_name, loader_name):
            Logger.info(f"{loader_name} {actual_loader_version} already installed, skipping")
            return installed_version_name, actual_loader_version

        if exists:
            Logger.info(f"Refreshing {loader_name} {actual_loader_version}")
            self._update_feedback_operation(f"Refreshing {loader_name} {actual_loader_version}", operation=operation)

        if java_path:
            Logger.info(
                f"Ignoring external Java path while installing {loader_name} {actual_loader_version}; "
                "using launcher-managed Java runtime"
            )
        java_path = self._get_required_loader_java_path(mc_version, operation=operation)

        Logger.info(f"Installing {loader_name} {actual_loader_version} for Minecraft {mc_version}")
        self._update_feedback_operation(f"Installing {loader_name} {actual_loader_version}", operation=operation)

        self._run_mod_loader_install(
            mod_loader,
            mc_version=mc_version,
            loader_name=loader_name,
            loader_version=actual_loader_version,
            java_path=java_path,
            operation=operation,
            repair_managed_java=True,
        )

        return installed_version_name, actual_loader_version

    def _run_mod_loader_install(
        self,
        mod_loader: Any,
        *,
        mc_version: str,
        loader_name: str,
        loader_version: str,
        java_path: Optional[str],
        operation: OperationHandle | None = None,
        repair_managed_java: bool = False,
    ) -> None:
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.MOD_LOADER_INSTALL_ATTEMPTS + 1):
            try:
                with suppress_windows_error_dialogs(), launcher_java_path(java_path):
                    if self._uses_captured_fabric_quilt_installer(mod_loader, loader_name):
                        self._install_fabric_quilt_loader_with_capture(
                            mod_loader,
                            mc_version=mc_version,
                            loader_name=loader_name,
                            loader_version=loader_version,
                            java_path=java_path,
                            operation=operation,
                        )
                    elif self._uses_captured_neoforge_installer(mod_loader, loader_name):
                        self._install_neoforge_loader_with_capture(
                            mod_loader,
                            mc_version=mc_version,
                            loader_version=loader_version,
                            java_path=java_path,
                            operation=operation,
                        )
                    else:
                        mod_loader.install(
                            minecraft_version=mc_version,
                            minecraft_directory=str(self.minecraft_dir),
                            loader_version=loader_version,
                            callback=self._install_callbacks(operation),
                            java=java_path,
                        )
                return
            except Exception as exc:
                if attempt >= self.MOD_LOADER_INSTALL_ATTEMPTS or not self._is_retryable_mod_loader_install_error(exc):
                    raise RuntimeError(self._format_mod_loader_install_error(exc)) from exc

                if repair_managed_java and self._is_java_runtime_process_failure(exc):
                    repaired_java = self._repair_managed_java_runtime_after_loader_failure(
                        mc_version,
                        loader_name,
                        loader_version,
                        operation=operation,
                    )
                    if repaired_java:
                        java_path = repaired_java

                Logger.warning(
                    f"{loader_name} {loader_version} install attempt "
                    f"{attempt}/{self.MOD_LOADER_INSTALL_ATTEMPTS} failed; retrying. "
                    f"{self._format_mod_loader_install_error(exc)}"
                )
                IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}
                try:
                    self._install_minecraft_if_needed(mc_version, force_check=True, operation=operation)
                except Exception as repair_exc:
                    Logger.warning(
                        f"Pre-retry Minecraft {mc_version} repair failed: "
                        f"{self._format_mod_loader_install_error(repair_exc)}"
                    )

    @staticmethod
    def _uses_captured_fabric_quilt_installer(mod_loader: Any, loader_name: str) -> bool:
        return loader_name in {"fabric", "quilt"} and hasattr(getattr(mod_loader, "_base", None), "get_installer_url")

    @staticmethod
    def _uses_captured_neoforge_installer(mod_loader: Any, loader_name: str) -> bool:
        return loader_name == "neoforge" and hasattr(mod_loader, "get_installer_url")

    def _existing_mod_loader_install_is_reusable(self, version_id: str, loader_name: str) -> bool:
        if loader_name != "neoforge":
            return True
        if self._has_incomplete_install_marker(version_id):
            Logger.warning(f"{version_id} has an incomplete NeoForge install marker; refreshing")
            return False
        return self._version_manifest_and_libraries_exist(version_id)

    def _version_manifest_and_libraries_exist(self, version_id: str) -> bool:
        return bool(
            self.integrity_checker._check_version_manifest(version_id)
            and self.integrity_checker._check_libraries(version_id)
        )

    def _has_incomplete_install_marker(self, version_id: str) -> bool:
        version_dir = self.loaders_path(version_id)
        return (version_dir / self.INCOMPLETE_INSTALL_MARKER).exists() and not (
            version_dir / self.SUCCESSFUL_INSTALL_MARKER
        ).exists()

    def _mark_install_incomplete(self, version_id: str) -> None:
        version_dir = self.loaders_path(version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / self.INCOMPLETE_INSTALL_MARKER).write_text("1", encoding="utf-8")
        (version_dir / self.SUCCESSFUL_INSTALL_MARKER).unlink(missing_ok=True)

    def _mark_install_successful(self, version_id: str) -> None:
        version_dir = self.loaders_path(version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / self.SUCCESSFUL_INSTALL_MARKER).write_text("1", encoding="utf-8")
        (version_dir / self.INCOMPLETE_INSTALL_MARKER).unlink(missing_ok=True)

    def _install_neoforge_loader_with_capture(
        self,
        mod_loader: Any,
        *,
        mc_version: str,
        loader_version: str,
        java_path: str | None,
        operation: OperationHandle | None = None,
    ) -> None:
        callback = self._install_callbacks(operation) or {}
        installer_download_url = mod_loader.get_installer_url(mc_version, loader_version)
        installed_version = mod_loader.get_installed_version(mc_version, loader_version)

        with tempfile.TemporaryDirectory(prefix="minecraft-launcher-lib-") as tempdir:
            installer_path = Path(tempdir) / "neoforge-installer.jar"
            download_file(installer_download_url, str(installer_path), callback=callback, overwrite=True)

            profile_id, install_profile = self._prepare_neoforge_profile_from_installer(
                installer_path,
                fallback_version_id=installed_version,
            )
            self._mark_install_incomplete(profile_id)

            libraries = self._deduplicate_libraries(install_profile.get("libraries", []))
            if libraries:
                callback.get("setStatus", empty)("Downloading NeoForge libraries")
                install_libraries(profile_id, libraries, str(self.minecraft_dir), callback)

            install_minecraft_version_with_retries(
                profile_id,
                self.minecraft_dir,
                callback=callback,
                attempts=self.MINECRAFT_INSTALL_ATTEMPTS,
            )

            callback.get("setStatus", empty)("Running installer")
            command = [
                java_path or "java",
                "-jar",
                str(installer_path),
                "--install-client",
                str(self.minecraft_dir),
            ]
            run_kwargs: dict[str, Any] = {
                "cwd": tempdir,
                "check": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if java_path:
                java_kwargs = java_subprocess_kwargs(java_path)
                run_kwargs["env"] = java_kwargs["env"]
            if SUBPROCESS_STARTUP_INFO is not None:
                run_kwargs["startupinfo"] = SUBPROCESS_STARTUP_INFO
            subprocess.run(command, **run_kwargs)

        self._mark_install_successful(profile_id)

    def _prepare_neoforge_profile_from_installer(
        self,
        installer_path: Path,
        *,
        fallback_version_id: str,
    ) -> tuple[str, dict[str, Any]]:
        with zipfile.ZipFile(installer_path) as archive:
            install_profile = json.loads(archive.read("install_profile.json"))
            version_member = str(install_profile.get("json") or "version.json").lstrip("/")
            if not version_member:
                version_member = "version.json"
            version_profile = json.loads(archive.read(version_member))

        profile_id = str(version_profile.get("id") or install_profile.get("version") or fallback_version_id).strip()
        if not profile_id:
            profile_id = fallback_version_id
        version_profile["id"] = profile_id

        version_dir = self.minecraft_dir / "versions" / profile_id
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / f"{profile_id}.json").write_text(
            json.dumps(version_profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return profile_id, install_profile

    @staticmethod
    def _deduplicate_libraries(libraries: object) -> list[dict[str, Any]]:
        if not isinstance(libraries, list):
            return []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for library in libraries:
            if not isinstance(library, dict):
                continue
            name = str(library.get("name") or "").strip()
            key = name or repr(library)
            if key in seen:
                continue
            seen.add(key)
            result.append(library)
        return result

    def _install_fabric_quilt_loader_with_capture(
        self,
        mod_loader: Any,
        *,
        mc_version: str,
        loader_name: str,
        loader_version: str,
        java_path: str | None,
        operation: OperationHandle | None = None,
    ) -> None:
        callback = self._install_callbacks(operation) or {}
        base_loader = getattr(mod_loader, "_base")
        installer_download_url = base_loader.get_installer_url(mc_version, loader_version)
        installer_name = "quilt-installer.jar" if loader_name == "quilt" else "fabric-installer.jar"

        with tempfile.TemporaryDirectory(prefix="minecraft-launcher-lib-") as tempdir:
            installer_path = Path(tempdir) / installer_name
            download_file(installer_download_url, str(installer_path), callback=callback, overwrite=True)
            callback.get("setStatus", empty)("Running installer")
            command = self._fabric_quilt_installer_command(
                loader_name=loader_name,
                java_path=java_path or "java",
                installer_path=installer_path,
                minecraft_directory=self.minecraft_dir,
                mc_version=mc_version,
                loader_version=loader_version,
            )
            run_kwargs: dict[str, Any] = {
                "cwd": tempdir,
                "check": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if SUBPROCESS_STARTUP_INFO is not None:
                run_kwargs["startupinfo"] = SUBPROCESS_STARTUP_INFO
            try:
                subprocess.run(command, **run_kwargs)
            except subprocess.CalledProcessError as exc:
                if not self._is_java_certificate_path_error(exc):
                    raise
                Logger.warning(
                    f"{loader_name} installer could not validate HTTPS certificates; "
                    "installing loader profile through Python metadata client"
                )
                self._install_fabric_quilt_profile_with_python(
                    mod_loader,
                    mc_version=mc_version,
                    loader_name=loader_name,
                    loader_version=loader_version,
                    callback=callback,
                )
                return

        installed_version = mod_loader.get_installed_version(mc_version, loader_version)
        install_minecraft_version_with_retries(
            installed_version,
            self.minecraft_dir,
            callback=callback,
            attempts=self.MINECRAFT_INSTALL_ATTEMPTS,
        )

    @staticmethod
    def _fabric_quilt_installer_command(
        *,
        loader_name: str,
        java_path: str,
        installer_path: Path,
        minecraft_directory: Path,
        mc_version: str,
        loader_version: str,
    ) -> list[str]:
        command_prefix = [java_path, "-jar", str(installer_path)]
        if loader_name == "quilt":
            return [
                *command_prefix,
                "install",
                "client",
                mc_version,
                loader_version,
                f"--install-dir={minecraft_directory}",
                "--no-profile",
            ]
        return [
            *command_prefix,
            "client",
            "-dir",
            str(minecraft_directory),
            "-mcversion",
            mc_version,
            "-loader",
            loader_version,
            "-noprofile",
            "-snapshot",
        ]

    def _install_fabric_quilt_profile_with_python(
        self,
        mod_loader: Any,
        *,
        mc_version: str,
        loader_name: str,
        loader_version: str,
        callback: Any,
    ) -> None:
        callback.get("setStatus", empty)(f"Installing {loader_name} profile")
        profile = self._download_fabric_quilt_profile(
            loader_name=loader_name,
            mc_version=mc_version,
            loader_version=loader_version,
        )
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id:
            profile_id = mod_loader.get_installed_version(mc_version, loader_version)
            profile["id"] = profile_id

        version_dir = self.minecraft_dir / "versions" / profile_id
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / f"{profile_id}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        install_minecraft_version_with_retries(
            profile_id,
            self.minecraft_dir,
            callback=callback,
            attempts=self.MINECRAFT_INSTALL_ATTEMPTS,
        )

    @classmethod
    def _download_fabric_quilt_profile(
        cls,
        *,
        loader_name: str,
        mc_version: str,
        loader_version: str,
    ) -> dict[str, Any]:
        url = cls._fabric_quilt_profile_url(
            loader_name=loader_name,
            mc_version=mc_version,
            loader_version=loader_version,
        )
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "TensaLauncher"},
        )
        response.raise_for_status()
        profile = response.json()
        if not isinstance(profile, dict):
            raise RuntimeError(f"{loader_name} profile endpoint returned an invalid payload")
        return profile

    @staticmethod
    def _fabric_quilt_profile_url(*, loader_name: str, mc_version: str, loader_version: str) -> str:
        if loader_name == "quilt":
            return f"https://meta.quiltmc.org/v3/versions/loader/{mc_version}/{loader_version}/profile/json"
        return f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/profile/json"

    def _repair_managed_java_runtime_after_loader_failure(
        self,
        mc_version: str,
        loader_name: str,
        loader_version: str,
        operation: OperationHandle | None = None,
    ) -> str | None:
        Logger.warning(
            f"{loader_name} {loader_version} installer failed because Java could not start; "
            f"repairing Java runtime for Minecraft {mc_version}"
        )

        def on_install(runtime_name: str, version_key: str) -> None:
            Logger.info(f"Reinstalling Java runtime {runtime_name} for Minecraft {version_key}")
            self._update_feedback_operation(f"Reinstalling Java runtime {runtime_name}", operation=operation)

        return self.runtime.repair_runtime(
            mc_version,
            mc_version,
            callback=self._install_callbacks(operation),
            on_install=on_install,
        )

    @staticmethod
    def _is_retryable_mod_loader_install_error(exc: BaseException) -> bool:
        return is_retryable_install_error(exc)

    @staticmethod
    def _is_java_runtime_process_failure(exc: BaseException) -> bool:
        return is_java_runtime_process_failure(exc)

    @classmethod
    def _is_java_certificate_path_error(cls, exc: BaseException) -> bool:
        message = cls._format_mod_loader_install_error(exc).lower()
        return any(
            marker in message
            for marker in (
                "certificate_unknown",
                "pkix path building failed",
                "sun.security.provider.certpath.suncertpathbuilderexception",
                "unable to find valid certification path",
            )
        )

    @classmethod
    def _format_mod_loader_install_error(cls, exc: BaseException) -> str:
        return format_install_error(exc)

    @staticmethod
    def _decode_error_output(value: object, max_chars: int = 1200) -> str:
        return decode_error_output(value, max_chars=max_chars)

    def verify_and_repair_version(self, version_id: str, mc_version: Optional[str] = None) -> bool:
        """Return whether a version folder is installed.

        Full repair is intentionally not part of launch verification. The
        installer paths are idempotent and are responsible for restoring missing
        versions when required.
        """
        installed = self.integrity_checker._is_version_installed(version_id)
        if not installed:
            Logger.warning(f"Version {version_id} is not installed")
        return bool(installed)


__all__ = ["BaseLoader"]
