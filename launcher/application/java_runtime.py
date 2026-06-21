from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import minecraft_launcher_lib

from launcher.platform.java_process import java_subprocess_kwargs
from launcher.platform.windows_error_mode import suppress_windows_error_dialogs


class JavaRuntimeService:
    JAVA_CHECK_TIMEOUT_SECONDS = 8

    def __init__(self, minecraft_dir: str | Path, logger: Any) -> None:
        self.minecraft_dir = Path(minecraft_dir)
        self.log = logger

    def _log(self, level: str, message: str) -> None:
        logger_method = getattr(self.log, level, None)
        if callable(logger_method):
            logger_method(message)

    @staticmethod
    def extract_minecraft_version(version_id: str) -> str:
        if version_id.startswith("fabric-loader-") or version_id.startswith("quilt-loader-"):
            extracted = JavaRuntimeService._extract_mod_loader_minecraft_version(version_id)
            if extracted:
                return extracted

        if "-forge-" in version_id:
            return version_id.split("-forge-")[0]

        if version_id.startswith("neoforge-"):
            return version_id[9:]

        return version_id.split("-")[0]

    @staticmethod
    def _extract_mod_loader_minecraft_version(version_id: str) -> str | None:
        for prefix in ("fabric-loader-", "quilt-loader-"):
            if not version_id.startswith(prefix):
                continue
            value = version_id[len(prefix):]
            known_version = JavaRuntimeService._match_known_minecraft_suffix(value)
            if known_version:
                return known_version
            for pattern in (
                r"(?:(?<=-)|^)(\d+(?:\.\d+)+(?:-snapshot-\d+)?)$",
                r"(?:(?<=-)|^)(\d{2}w\d{2}[a-z])$",
            ):
                match = re.search(pattern, value)
                if match:
                    return match.group(1)
        return None

    @staticmethod
    def _match_known_minecraft_suffix(value: str) -> str | None:
        try:
            versions = [
                str(item.get("id") or "").strip()
                for item in minecraft_launcher_lib.utils.get_version_list()
                if str(item.get("id") or "").strip()
            ]
        except Exception:
            versions = []
        for minecraft_version in sorted(versions, key=len, reverse=True):
            if value == minecraft_version or value.endswith(f"-{minecraft_version}"):
                return minecraft_version
        return None

    @staticmethod
    def find_java_executable(runtime_path: str | Path) -> Path | None:
        base_path = Path(runtime_path)
        for candidate in ("java.exe", "java", "javaw.exe", "MinecraftJava.exe"):
            for path in base_path.rglob(candidate):
                if path.is_file():
                    return path
        return None

    def get_runtime_name(self, mc_version: str) -> str | None:
        try:
            runtime_info = minecraft_launcher_lib.runtime.get_version_runtime_information(
                mc_version,
                str(self.minecraft_dir),
            ) or {}
        except Exception as exc:
            self._log("warning", f"Could not get runtime info for {mc_version}: {exc}")
            return None
        return runtime_info.get("name")

    def get_executable_path(self, runtime_name: str) -> str | None:
        try:
            java_path = minecraft_launcher_lib.runtime.get_executable_path(
                runtime_name,
                str(self.minecraft_dir),
            )
        except Exception:
            java_path = None
        if java_path:
            return java_path

        runtime_path = self.minecraft_dir / "runtime" / runtime_name
        java_executable = self.find_java_executable(runtime_path)
        return str(java_executable) if java_executable else None

    def runtime_is_complete(self, runtime_name: str, java_path: str | Path | None = None) -> bool:
        """Return whether a launcher-managed Mojang runtime has all manifest files.

        `minecraft-launcher-lib` writes a `<runtime>.sha1` file after installing a
        runtime. If installation was interrupted, `java.exe` can exist while files
        such as `lib/jawt.lib` are missing; in that state the runtime must be
        repaired instead of being reused.
        """
        runtime_root = self.minecraft_dir / "runtime" / runtime_name
        if not runtime_root.exists():
            return False

        if java_path:
            manifest = self._manifest_for_executable(runtime_name, java_path)
            return bool(manifest and self._runtime_manifest_is_complete(manifest, manifest.parent / runtime_name))

        manifests = list(runtime_root.rglob(f"{runtime_name}.sha1"))
        return any(self._runtime_manifest_is_complete(manifest, manifest.parent / runtime_name) for manifest in manifests)

    def runtime_is_usable(self, runtime_name: str, java_path: str | Path | None = None) -> bool:
        if not java_path:
            return False
        if not self.runtime_is_complete(runtime_name, java_path):
            return False
        if not self._windows_runtime_loader_dlls_present(java_path):
            return False
        return self._java_executable_runs(java_path)

    def _windows_runtime_loader_dlls_present(self, java_path: str | Path) -> bool:
        if not sys.platform.startswith("win"):
            return True

        java_bin = Path(java_path).parent
        missing: list[str] = []
        if not (java_bin / "jli.dll").is_file():
            missing.append("jli.dll")
        if not any(java_bin.glob("vcruntime*.dll")):
            missing.append("vcruntime*.dll")
        if not missing:
            return True

        self._log(
            "warning",
            f"Java runtime loader files missing near {java_path}: {', '.join(missing)}",
        )
        return False

    def _java_executable_runs(self, java_path: str | Path) -> bool:
        path = Path(java_path)
        if not path.is_file():
            return False

        kwargs: dict[str, Any] = {}
        kwargs.update(java_subprocess_kwargs(path))
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            with suppress_windows_error_dialogs():
                result = subprocess.run(
                    [str(path), "-version"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.JAVA_CHECK_TIMEOUT_SECONDS,
                    check=False,
                    **kwargs,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log("warning", f"Java runtime validation failed for {path}: {exc}")
            return False

        if result.returncode == 0:
            return True

        details = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
        if len(details) > 500:
            details = "..." + details[-500:]
        self._log("warning", f"Java runtime validation failed for {path}: exit={result.returncode} {details}")
        return False

    def _manifest_for_executable(self, runtime_name: str, java_path: str | Path) -> Path | None:
        runtime_root = (self.minecraft_dir / "runtime" / runtime_name).resolve()
        try:
            path = Path(java_path).resolve()
        except OSError:
            return None

        for parent in path.parents:
            if parent.name != runtime_name:
                continue
            try:
                if not parent.is_relative_to(runtime_root):
                    continue
            except OSError:
                continue
            manifest = parent.parent / f"{runtime_name}.sha1"
            if manifest.is_file():
                return manifest
        return None

    def _runtime_manifest_is_complete(self, manifest: Path, base_path: Path) -> bool:
        if not base_path.exists():
            return False
        try:
            base_path = base_path.resolve()
            lines = manifest.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self._log("warning", f"Could not read Java runtime manifest {manifest}: {exc}")
            return False

        for line in lines:
            relative_path, expected_sha1 = self._parse_runtime_manifest_record(line)
            if not relative_path:
                continue
            file_path = (base_path / relative_path).resolve()
            try:
                if not file_path.is_relative_to(base_path):
                    self._log("warning", f"Unsafe Java runtime manifest path ignored: {relative_path}")
                    return False
            except OSError:
                return False
            if not file_path.is_file():
                self._log("warning", f"Java runtime file missing: {file_path}")
                return False
            if expected_sha1:
                actual_sha1 = self._file_sha1(file_path)
                if actual_sha1 != expected_sha1.lower():
                    self._log("warning", f"Java runtime file checksum mismatch: {file_path}")
                    return False

        return True

    @classmethod
    def _parse_runtime_manifest_record(cls, line: str) -> tuple[str | None, str | None]:
        text = line.strip()
        if not text or " /#// " not in text:
            return None, None
        relative_path, payload = text.split(" /#// ", 1)
        parts = payload.split()
        if not parts:
            return None, None
        sha1 = parts[0].lower() if re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]) else None
        return relative_path.strip(), sha1

    @staticmethod
    def _parse_runtime_manifest_line(line: str) -> str | None:
        relative_path, _sha1 = JavaRuntimeService._parse_runtime_manifest_record(line)
        return relative_path

    @staticmethod
    def _file_sha1(path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def has_runtime(self, version_id: str, mc_version: str | None = None) -> bool:
        version_key = mc_version or self.extract_minecraft_version(version_id)
        if not version_key:
            return True

        runtime_name = self.get_runtime_name(version_key)
        if not runtime_name:
            return True

        java_path = self.get_executable_path(runtime_name)
        return bool(java_path and Path(java_path).is_file() and self.runtime_is_complete(runtime_name, java_path))

    def install_runtime(
        self,
        version_id: str,
        mc_version: str | None = None,
        callback: Any = None,
    ) -> bool:
        version_key = mc_version or self.extract_minecraft_version(version_id)
        if not version_key:
            return True

        runtime_name = self.get_runtime_name(version_key)
        if not runtime_name:
            return True

        return self._install_runtime_with_repair(runtime_name, callback=callback)

    def ensure_runtime(
        self,
        version_id: str,
        mc_version: str | None = None,
        callback: Any = None,
        on_install=None,
    ) -> str | None:
        version_key = mc_version or self.extract_minecraft_version(version_id)
        if not version_key:
            return None

        runtime_name = self.get_runtime_name(version_key)
        if not runtime_name:
            return None

        java_path = self.get_executable_path(runtime_name)
        if java_path and Path(java_path).is_file():
            if self.runtime_is_complete(runtime_name, java_path):
                return java_path
            self._log("warning", f"Java runtime {runtime_name} is incomplete, reinstalling")
            self._remove_runtime(runtime_name)

        runtime_root = self.minecraft_dir / "runtime" / runtime_name
        if java_path or runtime_root.exists():
            self._log("warning", f"Java runtime {runtime_name} is incomplete, reinstalling")
            self._remove_runtime(runtime_name)

        if callable(on_install):
            on_install(runtime_name, version_key)

        if not self._install_runtime_with_repair(runtime_name, callback=callback):
            return None
        return self.get_executable_path(runtime_name)

    def repair_runtime(
        self,
        version_id: str,
        mc_version: str | None = None,
        callback: Any = None,
        on_install=None,
    ) -> str | None:
        version_key = mc_version or self.extract_minecraft_version(version_id)
        if not version_key:
            return None

        runtime_name = self.get_runtime_name(version_key)
        if not runtime_name:
            return None

        self._log("warning", f"Repairing Java runtime {runtime_name}")
        self._remove_runtime(runtime_name)

        if callable(on_install):
            on_install(runtime_name, version_key)

        if not self._install_runtime_with_repair(runtime_name, callback=callback):
            return None
        return self.get_executable_path(runtime_name)

    def _install_runtime_with_repair(self, runtime_name: str, callback: Any = None) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                minecraft_launcher_lib.runtime.install_jvm_runtime(
                    runtime_name,
                    str(self.minecraft_dir),
                    callback=callback,
                )
                java_path = self.get_executable_path(runtime_name)
                if java_path and Path(java_path).is_file() and self.runtime_is_complete(runtime_name, java_path):
                    return True
                last_error = RuntimeError(f"Java runtime {runtime_name} is incomplete after install")
            except Exception as exc:
                last_error = exc

            if attempt == 1:
                self._log("warning", f"Retrying Java runtime installation {runtime_name}: {last_error}")
                self._remove_runtime(runtime_name)

        if last_error is not None:
            self._log("error", f"Failed to install Java runtime {runtime_name}: {last_error}")
        return False

    def _remove_runtime(self, runtime_name: str) -> None:
        runtime_root = (self.minecraft_dir / "runtime" / runtime_name).resolve()
        allowed_root = (self.minecraft_dir / "runtime").resolve()
        try:
            if runtime_root == allowed_root or not runtime_root.is_relative_to(allowed_root):
                self._log("warning", f"Skipping unsafe Java runtime cleanup path: {runtime_root}")
                return
            shutil.rmtree(runtime_root, ignore_errors=True)
        except OSError as exc:
            self._log("warning", f"Could not remove Java runtime {runtime_name}: {exc}")
