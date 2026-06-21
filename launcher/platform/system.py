from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import minecraft_launcher_lib
import requests

from launcher import MACOS_BUNDLE_ID

CONNECTION_TEST_HOST = "www.google.com"
CONNECTION_TEST_PORT = 80
AVATAR_CACHE_DIR = Path("cache") / "avatars"
AVATAR_SIZE = 64
AVATAR_TIMEOUT = 4.0
AVATAR_CACHE_TTL_SEC = 24 * 60 * 60
AVATAR_URLS = (
    "https://mc-heads.net/avatar/{identifier}/{size}",
    "https://minotar.net/helm/{identifier}/{size}.png",
    "https://mineskin.eu/avatar/{identifier}/{size}",
)
AVATAR_USER_AGENT = "TensaLauncher/1.0"
JAVA_EXECUTABLES = ("java.exe", "javaw.exe", "MinecraftJava.exe", "java")
JAVA_HOME_ENV_VARS = ("JAVA_HOME", "JDK_HOME", "JRE_HOME")
MACOS_MICROPHONE_SETTINGS_URLS = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone",
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
)
MACOS_MICROPHONE_STATUS_AUTHORIZED = "authorized"
MACOS_MICROPHONE_STATUS_DENIED = "denied"
MACOS_MICROPHONE_STATUS_RESTRICTED = "restricted"
MACOS_MICROPHONE_STATUS_UNAVAILABLE = "unavailable"
MACOS_MICROPHONE_STATUS_TIMEOUT = "timeout"


class SystemService:
    def __init__(self, path_service) -> None:
        self.path_service = path_service
        self._avatar_lock = threading.Lock()
        self._avatar_inflight: set[str] = set()
        self._avatar_callbacks: dict[str, list[Callable[[str | None], None]]] = {}
        self._avatar_session = requests.Session()
        self._avatar_session.headers.update({"User-Agent": AVATAR_USER_AGENT})

    def get_skin_url(self, name: str | None) -> str | None:
        return self.ensure_skin_cached(name)

    def get_cached_skin_url(self, name: str | None, *, allow_stale: bool = True) -> str | None:
        identifier = self._avatar_identifier(name)
        cache_path = self._avatar_cache_path(identifier)
        if cache_path.is_file() and cache_path.stat().st_size > 0 and (allow_stale or self._avatar_cache_is_fresh(cache_path)):
            return str(cache_path)
        return None

    def prefetch_skin(self, name: str | None, on_ready: Callable[[str | None], None] | None = None) -> bool:
        identifier = self._avatar_identifier(name)
        cache_path = self._avatar_cache_path(identifier)
        if cache_path.is_file() and cache_path.stat().st_size > 0 and self._avatar_cache_is_fresh(cache_path):
            return False

        with self._avatar_lock:
            if on_ready is not None:
                self._avatar_callbacks.setdefault(identifier, []).append(on_ready)
            if identifier in self._avatar_inflight:
                return False
            self._avatar_inflight.add(identifier)

        threading.Thread(target=self._prefetch_skin_worker, args=(identifier,), daemon=True).start()
        return True

    def _prefetch_skin_worker(self, identifier: str) -> None:
        avatar_path: str | None = None
        try:
            avatar_path = self.ensure_skin_cached(identifier)
        finally:
            with self._avatar_lock:
                self._avatar_inflight.discard(identifier)
                callbacks = self._avatar_callbacks.pop(identifier, [])
        for callback in callbacks:
            try:
                callback(avatar_path)
            except Exception:
                continue

    def ensure_skin_cached(self, name: str | None) -> str | None:
        identifier = self._avatar_identifier(name)
        cache_path = self._avatar_cache_path(identifier)
        if cache_path.is_file() and cache_path.stat().st_size > 0 and self._avatar_cache_is_fresh(cache_path):
            return str(cache_path)

        remote_identifier = quote(identifier, safe="")
        headers = {"User-Agent": AVATAR_USER_AGENT}
        cached_fallback = str(cache_path) if cache_path.is_file() and cache_path.stat().st_size > 0 else None
        last_remote_url: str | None = None

        for avatar_url in AVATAR_URLS:
            remote_url = avatar_url.format(identifier=remote_identifier, size=AVATAR_SIZE)
            last_remote_url = remote_url
            try:
                response = self._avatar_session.get(remote_url, headers=headers, timeout=AVATAR_TIMEOUT)
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue
            if not (response.headers.get("content-type") or "").startswith("image/"):
                continue
            if not response.content:
                continue
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(response.content)
            except OSError:
                return remote_url
            return str(cache_path)

        return cached_fallback or last_remote_url

    def open_mc_dir(self, *paths: str) -> str | None:
        targets = paths or (str(self.path_service.paths.minecraft_dir),)
        for partial in targets:
            full_path = Path(partial)
            if not full_path.is_absolute():
                full_path = self.path_service.paths.minecraft_dir / partial
            if full_path.exists():
                self._open_path(full_path)
            else:
                return f"Каталог '{full_path}' не існує або версія ще не була запущена"
        return None

    @staticmethod
    def is_macos() -> bool:
        return platform.system() == "Darwin"

    def open_macos_microphone_settings(self) -> bool:
        if not self.is_macos():
            return False

        for settings_url in MACOS_MICROPHONE_SETTINGS_URLS:
            try:
                subprocess.Popen(["open", settings_url])
                return True
            except OSError:
                continue
        return False

    def reset_macos_microphone_access(self) -> bool:
        if not self.is_macos():
            return False

        bundle_id = self._macos_bundle_identifier()
        if not bundle_id:
            return False
        try:
            result = subprocess.run(
                ["tccutil", "reset", "Microphone", bundle_id],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    def request_macos_microphone_access(self, timeout: float = 30.0) -> str:
        if not self.is_macos():
            return MACOS_MICROPHONE_STATUS_UNAVAILABLE

        try:
            import AVFoundation as avfoundation_module  # type: ignore[import-not-found]
        except Exception:
            self.open_macos_microphone_settings()
            return MACOS_MICROPHONE_STATUS_UNAVAILABLE

        avfoundation = cast(Any, avfoundation_module)
        media_type = getattr(avfoundation, "AVMediaTypeAudio", None)
        capture_device = getattr(avfoundation, "AVCaptureDevice", None)
        if media_type is None or capture_device is None:
            self.open_macos_microphone_settings()
            return MACOS_MICROPHONE_STATUS_UNAVAILABLE

        status = capture_device.authorizationStatusForMediaType_(media_type)
        if status == getattr(avfoundation, "AVAuthorizationStatusAuthorized", None):
            return MACOS_MICROPHONE_STATUS_AUTHORIZED
        if status == getattr(avfoundation, "AVAuthorizationStatusDenied", None):
            self.open_macos_microphone_settings()
            return MACOS_MICROPHONE_STATUS_DENIED
        if status == getattr(avfoundation, "AVAuthorizationStatusRestricted", None):
            self.open_macos_microphone_settings()
            return MACOS_MICROPHONE_STATUS_RESTRICTED

        result = {"granted": False}
        completed = threading.Event()

        def on_complete(granted):
            result["granted"] = bool(granted)
            completed.set()

        try:
            capture_device.requestAccessForMediaType_completionHandler_(media_type, on_complete)
        except Exception:
            self.open_macos_microphone_settings()
            return MACOS_MICROPHONE_STATUS_UNAVAILABLE

        if not completed.wait(max(1.0, timeout)):
            return MACOS_MICROPHONE_STATUS_TIMEOUT
        if result["granted"]:
            return MACOS_MICROPHONE_STATUS_AUTHORIZED
        self.open_macos_microphone_settings()
        return MACOS_MICROPHONE_STATUS_DENIED

    def _macos_bundle_identifier(self) -> str:
        app_bundle = self._current_macos_app_bundle()
        if app_bundle is not None:
            info_plist = app_bundle / "Contents" / "Info.plist"
            try:
                with info_plist.open("rb") as handle:
                    metadata = plistlib.load(handle)
            except (OSError, plistlib.InvalidFileException):
                metadata = {}
            bundle_id = metadata.get("CFBundleIdentifier")
            if isinstance(bundle_id, str) and bundle_id.strip():
                return bundle_id.strip()
        return MACOS_BUNDLE_ID

    @staticmethod
    def _current_macos_app_bundle() -> Path | None:
        try:
            executable = Path(sys.executable).resolve()
        except OSError:
            executable = Path(sys.executable)
        for path in (executable, *executable.parents):
            if path.suffix.lower() == ".app":
                return path
        return None

    def check_connection(self, timeout: float = 2.0) -> bool:
        try:
            socket.setdefaulttimeout(timeout)
            host = socket.gethostbyname(CONNECTION_TEST_HOST)
            with socket.create_connection((host, CONNECTION_TEST_PORT), timeout):
                return True
        except OSError:
            return False

    def get_all_java(self) -> list[dict[str, str]]:
        found: dict[str, tuple[str, str]] = {}

        def add_java(name: str | None, executable: str | Path | None) -> None:
            if not executable:
                return
            path = Path(executable).expanduser()
            try:
                path = path.resolve()
            except OSError:
                return
            if not path.is_file() or path.name.lower() not in JAVA_EXECUTABLES:
                return
            label = self._label_for_java(path, name).strip()
            found.setdefault(str(path).casefold(), (label, str(path)))

        runtime_roots: set[Path] = {self.path_service.paths.minecraft_dir / "runtime"}
        app_runtime = self.path_service.paths.app_state_dir / "runtime"
        runtime_roots.add(app_runtime)

        try:
            installed_runtimes = minecraft_launcher_lib.runtime.get_installed_jvm_runtimes(
                str(self.path_service.paths.minecraft_dir)
            )
        except Exception:
            installed_runtimes = []

        for runtime in installed_runtimes:
            executable = None
            try:
                executable = minecraft_launcher_lib.runtime.get_executable_path(
                    runtime,
                    str(self.path_service.paths.minecraft_dir),
                )
            except Exception:
                executable = None
            if not executable:
                executable = self._locate_java(self.path_service.paths.minecraft_dir / "runtime" / runtime)
            runtime_name = runtime
            try:
                info = minecraft_launcher_lib.runtime.get_jvm_runtime_information(runtime)
                runtime_name = info.get("name", runtime)
            except Exception:
                runtime_name = runtime
            add_java(runtime_name, executable)

        for runtime_root in runtime_roots:
            if not runtime_root.exists():
                continue
            for child in runtime_root.iterdir():
                if not child.is_dir():
                    continue
                add_java(child.name, self._locate_java(child))

        for java_home in self._java_home_candidates():
            add_java(None, self._locate_java(java_home))

        for executable in self._path_java_candidates():
            add_java(None, executable)

        for root in self._common_java_roots():
            for executable in self._locate_java_executables(root):
                add_java(None, executable)

        ordered = sorted(found.values(), key=lambda item: item[0].lower())
        return [{label: path} for label, path in ordered]

    @staticmethod
    def _locate_java(base_path: Path) -> str | None:
        for root, _, files in os.walk(base_path):
            files_by_name = {filename.lower(): filename for filename in files}
            for candidate in JAVA_EXECUTABLES:
                actual_name = files_by_name.get(candidate.lower())
                if actual_name:
                    return str(Path(root) / actual_name)
        return None

    def _label_for_java(self, executable_path: Path, name: str | None = None) -> str:
        clean_name = (name or "").strip()
        runtime_id = self._launcher_runtime_id(executable_path, clean_name)
        release_metadata = self._java_release_metadata(executable_path)
        java_version = release_metadata.get("JAVA_VERSION", "").strip()

        if runtime_id:
            if java_version:
                return f"Launcher Java {java_version} ({runtime_id})"
            return f"Launcher Java ({runtime_id})"

        if clean_name:
            return clean_name

        implementor = release_metadata.get("IMPLEMENTOR", "").strip()
        if java_version and implementor:
            return f"{implementor} Java {java_version}"
        if java_version:
            return f"Java {java_version}"

        parent = executable_path.parent
        runtime_root = parent.parent if parent.name.lower() == "bin" else parent
        return runtime_root.name or executable_path.name

    def _launcher_runtime_id(self, executable_path: Path, name: str) -> str | None:
        if name.startswith("java-runtime-"):
            return name
        runtime_roots = (
            self.path_service.paths.minecraft_dir / "runtime",
            self.path_service.paths.app_state_dir / "runtime",
        )
        for runtime_root in runtime_roots:
            try:
                relative_path = executable_path.relative_to(runtime_root)
            except ValueError:
                continue
            if relative_path.parts:
                return relative_path.parts[0]
        return None

    @staticmethod
    def _java_release_metadata(executable_path: Path) -> dict[str, str]:
        parent = executable_path.parent
        java_home = parent.parent if parent.name.lower() == "bin" else parent
        release_file = java_home / "release"
        if not release_file.is_file():
            return {}

        metadata: dict[str, str] = {}
        try:
            lines = release_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return metadata

        for line in lines:
            key, separator, value = line.partition("=")
            if not separator:
                continue
            metadata[key.strip()] = value.strip().strip('"')
        return metadata

    @staticmethod
    def _java_home_candidates() -> list[Path]:
        candidates: list[Path] = []
        for env_name in JAVA_HOME_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value).expanduser())
        return candidates

    @staticmethod
    def _path_java_candidates() -> list[Path]:
        candidates: list[Path] = []
        for raw_dir in os.environ.get("PATH", "").split(os.pathsep):
            if not raw_dir:
                continue
            directory = Path(raw_dir).expanduser()
            for executable in JAVA_EXECUTABLES:
                candidates.append(directory / executable)
        return candidates

    def _common_java_roots(self) -> list[Path]:
        roots: list[Path] = []

        def add_env_child(env_name: str, *parts: str) -> None:
            value = os.environ.get(env_name)
            if value:
                roots.append(Path(value).expanduser().joinpath(*parts))

        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            for child in ("Java", "Eclipse Adoptium", "Microsoft", "BellSoft", "Azul Systems", "Zulu"):
                add_env_child(env_name, child)

        add_env_child("LOCALAPPDATA", "Programs", "Java")
        add_env_child("LOCALAPPDATA", "Programs", "Eclipse Adoptium")
        add_env_child("USERPROFILE", ".jdks")
        add_env_child("APPDATA", ".minecraft", "runtime")
        add_env_child("APPDATA", ".tlauncher", "legacy", "Minecraft", "game", "runtime")
        return roots

    @staticmethod
    def _locate_java_executables(base_path: Path, *, max_depth: int = 5, max_dirs: int = 2000) -> list[Path]:
        if not base_path.exists():
            return []

        executables: list[Path] = []
        base_depth = len(base_path.parts)
        visited_dirs = 0
        for root, dirs, files in os.walk(base_path):
            visited_dirs += 1
            if visited_dirs > max_dirs:
                dirs[:] = []
                break
            depth = len(Path(root).parts) - base_depth
            if depth >= max_depth:
                dirs[:] = []
            files_by_name = {filename.lower(): filename for filename in files}
            for candidate in JAVA_EXECUTABLES:
                actual_name = files_by_name.get(candidate.lower())
                if actual_name:
                    executables.append(Path(root) / actual_name)
        return executables

    def _avatar_cache_path(self, identifier: str) -> Path:
        digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:16]
        return self.path_service.paths.app_state_dir / AVATAR_CACHE_DIR / f"{digest}.png"

    @staticmethod
    def _avatar_identifier(name: str | None) -> str:
        return (name or "").strip() or "Steve"

    @staticmethod
    def _avatar_cache_is_fresh(cache_path: Path) -> bool:
        try:
            age = time.time() - cache_path.stat().st_mtime
        except OSError:
            return False
        return age <= AVATAR_CACHE_TTL_SEC

    @staticmethod
    def _open_path(path: Path) -> None:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(path)])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
