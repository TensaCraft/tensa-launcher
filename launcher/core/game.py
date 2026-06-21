from __future__ import annotations

import inspect
import math
import os
import platform
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Dict, Optional, Tuple

import minecraft_launcher_lib

from launcher.application.installed_components import InstalledComponentsService
from launcher.application.launch_diagnostics import classify_launch_failure
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.core import util
from launcher.core.versions import Version
from launcher.models.logger import Logger
from launcher.platform.java_process import java_process_env
from launcher.shared import AppContext

WINDOWS_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
EARLY_EXIT_SECONDS = 5.0
LOG_TAIL_LINES = 40
LAUNCH_DIAGNOSTICS_LOG = "tensalauncher-launch.log"
LAUNCH_COOLDOWN_SECONDS = 3.0


def _log_safely(level: str, message: str) -> None:
    try:
        getattr(Logger, level)(message)
    except Exception:
        return


class Game:
    TECHNICAL_COMPONENT_LOADERS = {"minecraft", "fabric", "forge", "neoforge", "quilt"}
    TECHNICAL_MOD_LOADERS = {"fabric", "forge", "neoforge", "quilt"}
    INCOMPLETE_INSTALL_MARKER = ".tensalauncher-installing"
    SUCCESSFUL_INSTALL_MARKER = ".tensalauncher-installed"
    _recent_launches: Dict[str, float] = {}
    _active_game_dirs: Dict[str, list[object]] = {}
    _launch_guard_lock = threading.RLock()

    def __init__(self, minecraft_dir: str | Path | None = None) -> None:
        self.app = AppContext.get()
        self.mc_dir = self._resolve_minecraft_dir(self.app, minecraft_dir)

    @staticmethod
    def _resolve_minecraft_dir(app: object | None = None, override: str | Path | None = None) -> Path:
        if override is not None:
            return Path(override)
        paths = getattr(app, "paths", None)
        if paths is not None:
            minecraft_dir = getattr(paths, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        app_util = getattr(app, "util", None)
        if app_util is not None:
            minecraft_dir = getattr(app_util, "minecraft_dir", None)
            if minecraft_dir:
                return Path(minecraft_dir)
        return Path(util.minecraft_dir)

    @classmethod
    def _default_minecraft_dir(cls) -> Path:
        try:
            app = AppContext.get()
        except RuntimeError:
            app = None
        return cls._resolve_minecraft_dir(app)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    def start(
        self,
        v: Version,
        *,
        allow_duplicate: bool = False,
        profile_key: str | None = None,
    ) -> Dict[str, object]:
        t0 = time.perf_counter()
        game_dir = self._version_game_dir(v)
        if not allow_duplicate and self.is_game_dir_active(game_dir):
            _log_safely("warning", f"Launch blocked because {v.name} is already running: {game_dir}")
            return {
                "status": False,
                "text": self.app.trans("version_already_running", version=v.name),
            }
        if self.app.feedback.is_busy():
            return {"status": False, "text": self.app.trans("installation_already_running")}

        launch_key = self._version_launch_key(v)
        acquired, remaining_seconds = self._acquire_launch_slot(launch_key)
        if not acquired:
            _log_safely(
                "warning",
                f"Rapid duplicate launch ignored for {v.name}: "
                f"{launch_key} remaining={remaining_seconds:.1f}s",
            )
            return {
                "status": False,
                "text": self.app.trans(
                    "version_launch_throttled",
                    version=v.name,
                    seconds=max(1, math.ceil(remaining_seconds)),
                ),
            }

        launch_started = False
        prepare_success = False
        prepare_operation = self.app.feedback.begin_operation(
            self.app.trans("syncing_files_check"),
            kind="launch",
            visible=False,
            auto_open=False,
        )
        try:
            prof = self._get_launch_profile(profile_key)
            if not prof:
                return {
                    "status": False,
                    "text": self.app.trans("no_default_profile"),
                    "reason": "missing_profile",
                }
            requires_reauth = getattr(self.app.auth, "profile_requires_reauth", lambda _profile: False)
            if requires_reauth(prof):
                return {"status": False, "text": self.app.trans("profile_reauth_required")}

            t_auth = time.perf_counter()
            sync_ms = 0.0

            if v.is_tensacraft():
                sync_result, sync_ms = self._sync_for_launch(v)
                if sync_result is not None:
                    return sync_result

                t_verify_start = time.perf_counter()
                if not self._verify_for_launch(v, prepare_operation):
                    return {
                        "status": False,
                        "text": self.app.trans("version_integrity_check_failed", version=v.name),
                    }
                verify_ms = (time.perf_counter() - t_verify_start) * 1000.0
            else:
                t_verify_start = time.perf_counter()
                if not self._verify_for_launch(v, prepare_operation):
                    return {
                        "status": False,
                        "text": self.app.trans("version_integrity_check_failed", version=v.name),
                    }
                verify_ms = (time.perf_counter() - t_verify_start) * 1000.0

                if v.force_update:
                    sync_result, sync_ms = self._sync_for_launch(v)
                    if sync_result is not None:
                        return sync_result

            t_sync = time.perf_counter()
            self._backup_worlds_before_launch(v, prepare_operation)
            opts = self._build_opts(v, prof)
            t_opts = time.perf_counter()
            ok = self._launch(v.loader, v.version, opts, launch_key=launch_key)
            launch_started = bool(ok)
            t_launch = time.perf_counter()

            _log_safely(
                "info",
                "Launch timing: "
                f"ver={v.version} loader={v.loader} "
                f"auth={(t_auth - t0) * 1000.0:.0f}ms "
                f"verify={verify_ms:.0f}ms "
                f"sync={sync_ms:.0f}ms "
                f"opts={(t_opts - t_sync) * 1000.0:.0f}ms "
                f"launch={(t_launch - t_opts) * 1000.0:.0f}ms "
                f"total={(t_launch - t0) * 1000.0:.0f}ms",
            )

            if not ok:
                return {"status": False, "text": self.app.trans("version_integrity_check_failed", version=v.name)}
            if v.is_tensacraft() and not v.is_home_pinned():
                v.mark_home_pinned()
            prepare_success = True
            return {"status": True, "text": self.app.trans("version_starting", version=v.name)}
        finally:
            success_message = None
            if prepare_success:
                success_key = "syncing_files_complete" if v.force_update or v.is_tensacraft() else "installation_complete"
                success_message = self.app.trans(success_key)
            prepare_operation.finish(
                success_message,
                show_success=success_message is not None,
            )
            if not launch_started:
                self._release_launch_slot(launch_key)

    def _sync_for_launch(self, v: Version) -> Tuple[Dict[str, object] | None, float]:
        t_sync_start = time.perf_counter()
        try:
            v.sync_update()
        except Exception as exc:
            Logger.error(f"Version sync failed for {v.name}: {exc}")
            return (
                {
                    "status": False,
                    "text": self.app.trans("version_sync_failed", version=v.name, error=str(exc)),
                },
                (time.perf_counter() - t_sync_start) * 1000.0,
            )
        return None, (time.perf_counter() - t_sync_start) * 1000.0

    def _get_launch_profile(self, profile_key: str | None):
        if not profile_key:
            return self.app.auth.get_default_profile_data()
        profile_getter = getattr(self.app.auth, "get_profile_data", None)
        if callable(profile_getter):
            return profile_getter(profile_key)
        profile = self.app.profiles.get_profile(profile_key)
        return self.app.auth.ensure_profile_authorized(profile) if profile else None

    def _backup_worlds_before_launch(self, v: Version, operation) -> None:
        service = getattr(self.app, "world_backups", None)
        enabled = getattr(service, "enabled", None)
        if callable(enabled) and not enabled():
            return
        backup = getattr(service, "auto_backup_changed_worlds", None)
        if not callable(backup):
            return
        try:
            result = backup(v, operation=operation)
            created = int(getattr(result, "created", 0) or 0)
            failed = int(getattr(result, "failed", 0) or 0)
            if created:
                Logger.info(f"Created {created} world backup(s) before launching {v.name}")
            if failed:
                Logger.warning(f"Failed to create {failed} world backup(s) before launching {v.name}")
        except Exception as exc:
            Logger.warning(f"World backup step failed before launching {v.name}: {exc!r}")

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------
    def _verify_for_launch(self, v: Version, operation) -> bool:
        try:
            parameters = inspect.signature(self._verify).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "operation" in parameters:
            return self._verify(v, operation=operation)
        return self._verify(v)

    def _verify(self, v: Version, operation=None) -> bool:
        try:
            from launcher.core import Launcher
            from launcher.core.integrity import IntegrityChecker

            if not v.loader:
                return False

            loader_name = (v.client or "minecraft").lower()
            m = {
                "minecraft": "minecraft",
                "fabric": "fabric",
                "forge": "forge",
                "neoforge": "neoforge",
                "quilt": "quilt",
                "curseforge": "curseforge",
                "modrinth": "modrinth",
                "tensacraft": "tensacraft",
            }
            key = m.get(loader_name, "minecraft")
            loader = Launcher.get_loader(key)

            chk = IntegrityChecker(self.mc_dir)
            if not self._ensure_base_minecraft_version(v, loader, chk, operation):
                return False

            loader_installed = bool(chk._is_version_installed(v.loader))
            if loader_installed and key in self.TECHNICAL_MOD_LOADERS:
                loader_installed = self._technical_loader_ready_for_launch(chk, v.loader)

            if not loader_installed:
                if key in self.TECHNICAL_COMPONENT_LOADERS:
                    if not self._restore_component_for_launch(v, key, operation):
                        return False
                else:
                    loader.install(v, loader_version=v.loader_version)
                    self._invalidate_installed_versions_cache()
                self._invalidate_installed_versions_cache()
                if not chk._is_version_installed(v.loader):
                    return False
                if key in self.TECHNICAL_MOD_LOADERS and not self._technical_loader_ready_for_launch(chk, v.loader):
                    return False

            return bool(chk._is_version_installed(v.loader))
        except Exception as exc:
            _log_safely("error", f"Game verify failed: {exc!r}")
            return False

    def _restore_component_for_launch(self, v: Version, loader_id: str, operation=None) -> bool:
        versions_provider = getattr(getattr(self.app, "versions", None), "all", None)
        if not callable(versions_provider):
            versions_provider = None

        paths = getattr(self.app, "paths", None)
        games_dir = getattr(paths, "games_dir", None) if paths is not None else None

        service = InstalledComponentsService(
            self.mc_dir,
            games_dir=games_dir,
            versions_provider=versions_provider,
        )
        component = service.install_component(
            loader_id,
            str(getattr(v, "version", "") or ""),
            loader_version=getattr(v, "loader_version", None),
            operation=operation,
        )
        v.loader = component.version_id
        v.client = component.loader_name
        v.loader_version = component.loader_version
        save = getattr(v, "save", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                Logger.warning(f"Unable to persist repaired component for {getattr(v, 'name', v.loader)}: {exc}")
        return True

    def _ensure_base_minecraft_version(self, v: Version, loader: object, chk: object, operation=None) -> bool:
        mc_version = str(getattr(v, "version", "") or "").strip()
        if not mc_version:
            return True
        try:
            if chk._is_version_installed(mc_version):
                return True
        except Exception:
            return True

        installer = getattr(loader, "_install_minecraft_if_needed", None)
        if not callable(installer):
            return True

        Logger.info(f"Base Minecraft version {mc_version} is missing, installing")
        if operation is not None:
            operation.update(
                self.app.trans("installing_minecraft_version", version=mc_version),
                progress=0,
                total=100,
            )
        try:
            parameters = inspect.signature(installer).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "operation" in parameters:
            installer(mc_version, operation=operation)
        else:
            installer(mc_version)
        self._invalidate_installed_versions_cache()
        return bool(chk._is_version_installed(mc_version))

    def _technical_loader_ready_for_launch(self, chk: object, version_id: str) -> bool:
        if self._has_incomplete_install_marker(version_id):
            Logger.warning(f"{version_id} has an incomplete install marker; repairing before launch")
            return False

        check_manifest = getattr(chk, "_check_version_manifest", None)
        check_libraries = getattr(chk, "_check_libraries", None)
        if not callable(check_manifest) or not callable(check_libraries):
            return True

        try:
            manifest_ok = bool(check_manifest(version_id))
            libraries_ok = bool(check_libraries(version_id)) if manifest_ok else False
        except Exception as exc:
            Logger.warning(f"Unable to verify {version_id} loader libraries before launch: {exc!r}")
            return False

        if not manifest_ok or not libraries_ok:
            Logger.warning(f"{version_id} is incomplete; repairing before launch")
            return False
        return True

    def _has_incomplete_install_marker(self, version_id: str) -> bool:
        version_dir = self.mc_dir / "versions" / str(version_id or "")
        return (version_dir / self.INCOMPLETE_INSTALL_MARKER).exists() and not (
            version_dir / self.SUCCESSFUL_INSTALL_MARKER
        ).exists()

    @staticmethod
    def _invalidate_installed_versions_cache() -> None:
        from launcher.core.integrity import IntegrityChecker

        IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def _build_opts(self, v: Version, prof: dict) -> dict:
        game_dir = self._ensure_dir(v.path)
        o = {
            "username": prof.get("name"),
            "uuid": prof.get("id"),
            "token": prof.get("access_token"),
            "gameDirectory": str(game_dir),
            "nativesDirectory": str(self.mc_dir / "versions" / v.loader / "natives"),
            "launcherName": util.launcher_name,
            "launcherVersion": util.launcher_version,
        }
        exe = v.executable_path()
        if exe:
            o["executablePath"] = exe
        o.update(v.options or {})

        # Apply global GPU mode if version doesn't set it
        if "gpuMode" not in o:
            cfg_mode = self.app.config.get("gpu_mode_default")
            if cfg_mode:
                o["gpuMode"] = cfg_mode

        limits = MemoryPreferencesService.detect_limits()
        fallback_max_gb = None
        if not o.get("jvmArguments"):
            fallback_max_gb = self.app.config.get("default_max_ram_gb") or limits.recommended_heap_gb
        sanitized = MemoryPreferencesService.sanitize_jvm_arguments(
            o.get("jvmArguments"),
            fallback_max_gb=fallback_max_gb,
            limits=limits,
        )
        if sanitized.arguments:
            o["jvmArguments"] = sanitized.arguments
        else:
            o.pop("jvmArguments", None)
        if sanitized.changed:
            details = []
            if sanitized.original_max_gb is not None and sanitized.max_gb is not None:
                details.append(f"Xmx {sanitized.original_max_gb}G -> {sanitized.max_gb}G")
            if sanitized.removed_initial_heap:
                details.append("removed Xms")
            Logger.info("Normalized Minecraft JVM memory arguments: " + ", ".join(details or ["updated"]))

        return o

    def _ensure_dir(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.mc_dir / path
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------
    def _launch(self, loader_id: str, mc_ver: str, opts: dict, launch_key: Optional[str] = None) -> bool:
        try:
            is_new = self._mc_ge(mc_ver, (1, 20, 0))
            lib_opts, srv = self._normalize_server(opts, allow_legacy=not is_new)

            t_cmd0 = time.perf_counter()
            cmd = minecraft_launcher_lib.command.get_minecraft_command(
                loader_id, str(self.mc_dir), lib_opts
            )
            t_cmd1 = time.perf_counter()
            _log_safely(
                "info",
                "Launch build command: "
                f"loader={loader_id} mc={mc_ver} argv_len={len(cmd)} "
                f"time={(t_cmd1 - t_cmd0) * 1000.0:.0f}ms",
            )

            # Quick Play для нових версій; legacy ключі — для старих
            if srv and is_new:
                host, port = srv
                cmd += ["--quickPlayMultiplayer", f"{host}:{port}"]

            env = self._env_with_gpu(lib_opts, cmd)

            cwd = opts.get("gameDirectory") or str(self.mc_dir)
            cwd_path = Path(cwd)
            diagnostics_log = self._prepare_launch_diagnostics(cwd_path, loader_id, mc_ver)
            stdout_handle = None
            try:
                kw: Dict[str, object] = {"cwd": cwd}
                if env is not None:
                    kw["env"] = env
                if sys.platform == "win32":
                    kw["creationflags"] = WINDOWS_CREATE_NO_WINDOW
                if diagnostics_log is not None:
                    stdout_handle = diagnostics_log.open("a", encoding="utf-8", errors="replace")
                    kw["stdout"] = stdout_handle
                    kw["stderr"] = subprocess.STDOUT
                process = subprocess.Popen(cmd, **kw)  # type: ignore[arg-type]
            except Exception as exc:
                _log_safely("error", f"Failed to start Minecraft process: {exc!r}")
                return False
            finally:
                if stdout_handle is not None:
                    with suppress(Exception):
                        stdout_handle.close()

            java_path = cmd[0] if cmd else "unknown"
            _log_safely(
                "info",
                "Minecraft process started: "
                f"pid={getattr(process, 'pid', 'unknown')} loader={loader_id} mc={mc_ver} "
                f"java={java_path} cwd={cwd_path} diagnostics={diagnostics_log or 'disabled'}",
            )

            effective_launch_key = launch_key or self._normalize_game_dir_key(cwd_path)
            self._register_active_game_dir(effective_launch_key, process)
            threading.Thread(
                target=self._monitor_launch_process,
                args=(process, cwd_path, diagnostics_log, loader_id, mc_ver, effective_launch_key),
                daemon=True,
            ).start()

            if self.app.config.get("close_launcher_on_game", "no") == "yes":
                threading.Thread(target=self._close_later, args=(process,), daemon=True).start()

            return True
        except Exception as exc:
            _log_safely("error", f"Launch failed before Minecraft process start: {exc!r}")
            return False

    def _normalize_server(self, opts: dict, allow_legacy: bool) -> Tuple[dict, Optional[Tuple[str, int]]]:
        o = dict(opts)
        host: Optional[str] = None
        port: Optional[int] = None

        s = o.get("server")
        if isinstance(s, dict):
            host = s.get("host")
            p = s.get("port")
            try:
                port = int(p) if p is not None else None
            except Exception:
                port = None
        elif isinstance(s, str):
            host = s

        host = host or o.get("serverHost")
        if port is None:
            sp = o.get("serverPort")
            try:
                port = int(sp) if sp is not None else None
            except Exception:
                port = None

        if host:
            if port is None:
                port = 25565
            if allow_legacy:
                o["server"] = str(host)
                o["port"] = str(int(port))
            else:
                o.pop("server", None)
                o.pop("port", None)
        else:
            o.pop("server", None)
            o.pop("port", None)

        o.pop("serverHost", None)
        o.pop("serverPort", None)

        return o, (host, port) if host and port is not None else (host, 25565) if host else None

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------
    def _mc_ge(self, s: str, floor: Tuple[int, int, int]) -> bool:
        parts = [p for p in str(s).split(".") if p.isdigit()]
        nums = [int(p) for p in parts[:3]]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:3]) >= floor

    def _env_with_gpu(self, opts: dict, cmd: list) -> Optional[Dict[str, str]]:
        mode = str((opts.get("gpuMode") or "dgpu")).lower()
        sysname = platform.system().lower()
        if sysname != "windows" or mode != "dgpu":
            return java_process_env()

        java = opts.get("executablePath") or (cmd[0] if cmd else None)
        if java and os.path.isfile(java):
            try:
                self._win_high_perf(os.path.abspath(java))
            except Exception as exc:
                _log_safely("debug", f"Unable to set high-performance GPU mode for {java}: {exc!r}")
        return java_process_env()

    def _win_high_perf(self, exe_path: str) -> None:
        try:
            import winreg  # type: ignore
            key = r"Software\Microsoft\DirectX\UserGpuPreferences"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
                winreg.SetValueEx(k, exe_path, 0, winreg.REG_SZ, "GpuPreference=2;")
        except Exception as exc:
            _log_safely("debug", f"Unable to write Windows GPU preference for {exe_path}: {exc!r}")

    @staticmethod
    def _parse_int(val: Optional[object]) -> Optional[int]:
        try:
            if val is None:
                return None
            num = int(val)
            return num if num > 0 else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Launch Guard
    # ------------------------------------------------------------------
    @classmethod
    def version_game_dir(cls, v: Version) -> Path:
        raw_path = Path(str(getattr(v, "path", None) or getattr(v, "version_id", None) or getattr(v, "name", None) or ""))
        if not raw_path.is_absolute():
            raw_path = cls._default_minecraft_dir() / raw_path
        return raw_path

    def _version_game_dir(self, v: Version) -> Path:
        raw_path = Path(str(getattr(v, "path", None) or getattr(v, "version_id", None) or getattr(v, "name", None) or ""))
        if not raw_path.is_absolute():
            raw_path = self.mc_dir / raw_path
        return raw_path

    def _version_launch_key(self, v: Version) -> str:
        return self._normalize_game_dir_key(self._version_game_dir(v))

    @classmethod
    def _normalize_game_dir_key(cls, game_dir: str | Path) -> str:
        raw_path = Path(str(game_dir))
        if not raw_path.is_absolute():
            raw_path = cls._default_minecraft_dir() / raw_path
        try:
            key = str(raw_path.resolve(strict=False))
        except Exception:
            key = str(raw_path.absolute())
        return key.lower() if sys.platform == "win32" else key

    @classmethod
    def _acquire_launch_slot(cls, launch_key: str) -> Tuple[bool, float]:
        now = time.monotonic()
        with cls._launch_guard_lock:
            expired_before = now - LAUNCH_COOLDOWN_SECONDS
            for key, last_launch in list(cls._recent_launches.items()):
                if key != launch_key and last_launch <= expired_before:
                    cls._recent_launches.pop(key, None)
            last_launch = cls._recent_launches.get(launch_key)
            if last_launch is not None:
                remaining = LAUNCH_COOLDOWN_SECONDS - (now - last_launch)
                if remaining > 0:
                    return False, remaining
            cls._recent_launches[launch_key] = now
            return True, 0.0

    @classmethod
    def _release_launch_slot(cls, launch_key: Optional[str]) -> None:
        if not launch_key:
            return
        with cls._launch_guard_lock:
            cls._recent_launches.pop(launch_key, None)

    @classmethod
    def is_game_dir_active(cls, game_dir: str | Path) -> bool:
        return cls._is_launch_key_active(cls._normalize_game_dir_key(game_dir))

    @classmethod
    def _is_launch_key_active(cls, launch_key: str) -> bool:
        with cls._launch_guard_lock:
            for key, processes in list(cls._active_game_dirs.items()):
                alive_processes = [process for process in processes if cls._process_is_alive(process)]
                if alive_processes:
                    cls._active_game_dirs[key] = alive_processes
                    if key == launch_key:
                        return True
                    continue
                cls._active_game_dirs.pop(key, None)
            return False

    @classmethod
    def _register_active_game_dir(cls, launch_key: str, process: object) -> None:
        with cls._launch_guard_lock:
            cls._active_game_dirs.setdefault(launch_key, []).append(process)

    @classmethod
    def _release_active_game_dir(cls, launch_key: Optional[str], process: object | None = None) -> None:
        if not launch_key:
            return
        with cls._launch_guard_lock:
            active_processes = cls._active_game_dirs.get(launch_key)
            if not active_processes:
                return
            if process is None:
                cls._active_game_dirs.pop(launch_key, None)
                return
            remaining = [item for item in active_processes if item is not process]
            if remaining:
                cls._active_game_dirs[launch_key] = remaining
            else:
                cls._active_game_dirs.pop(launch_key, None)

    @staticmethod
    def _process_is_alive(process: object) -> bool:
        try:
            poll = getattr(process, "poll")
            return poll() is None
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def _prepare_launch_diagnostics(self, game_dir: Path, loader_id: str, mc_ver: str) -> Optional[Path]:
        try:
            logs_dir = game_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            diagnostics_log = logs_dir / LAUNCH_DIAGNOSTICS_LOG
            with diagnostics_log.open("w", encoding="utf-8", errors="replace") as handle:
                handle.write("TensaLauncher Minecraft process diagnostics\n")
                handle.write(f"loader={loader_id}\n")
                handle.write(f"minecraft={mc_ver}\n")
                handle.write(f"game_dir={game_dir}\n")
                handle.write(f"started_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                handle.write("\n")
            return diagnostics_log
        except Exception as exc:
            _log_safely("error", f"Unable to prepare launch diagnostics log for {game_dir}: {exc!r}")
            return None

    def _monitor_launch_process(
        self,
        process,
        game_dir: Path,
        diagnostics_log: Optional[Path],
        loader_id: str,
        mc_ver: str,
        launch_key: Optional[str] = None,
    ) -> None:
        try:
            started_at = time.monotonic()
            time.sleep(EARLY_EXIT_SECONDS)
            return_code = process.poll()
            exited_during_startup = return_code is not None
            if return_code is None:
                return_code = process.wait()
                if return_code == 0:
                    Logger.info(
                        "Minecraft process exited normally: "
                        f"pid={getattr(process, 'pid', 'unknown')} loader={loader_id} mc={mc_ver}"
                    )
                    return

            crash_report = self._newest_crash_report(game_dir)
            hs_err_log = self._newest_hs_err_log(game_dir)
            latest_log = game_dir / "logs" / "latest.log"
            best_path = crash_report or (latest_log if latest_log.exists() else None) or diagnostics_log or hs_err_log

            exit_context = "shortly after start" if exited_during_startup else "with a non-zero exit code"
            Logger.error(
                f"Minecraft exited {exit_context}: "
                f"pid={getattr(process, 'pid', 'unknown')} exit_code={return_code} "
                f"loader={loader_id} mc={mc_ver} elapsed={time.monotonic() - started_at:.1f}s"
            )
            self._log_tail("Minecraft crash report", crash_report)
            self._log_tail("Minecraft hs_err log", hs_err_log)
            self._log_tail("Minecraft latest.log", latest_log)
            self._log_tail("TensaLauncher launch diagnostics", diagnostics_log)
            self._show_launch_crash_alert(best_path, loader_id, mc_ver, hs_err_log)
        except Exception as exc:
            _log_safely("error", f"Launch diagnostics monitor failed: {exc!r}")
        finally:
            self._release_active_game_dir(launch_key, process)

    def _log_tail(self, label: str, path: Optional[Path]) -> None:
        tail = self._tail_text(path)
        if not tail:
            return
        _log_safely("error", f"{label} tail:\n{tail}")

    def _show_launch_crash_alert(
        self,
        path: Optional[Path],
        loader_id: str,
        mc_ver: str,
        hs_err_log: Optional[Path] = None,
    ) -> None:
        if path is None:
            return
        opener = getattr(getattr(self.app, "util", None), "open_mc_dir", None)
        trans = getattr(self.app, "trans", None)
        if not callable(opener) or not callable(trans):
            return

        try:
            import flet as ft

            from launcher import ui

            def open_diagnostics(_event=None) -> None:
                response = opener(str(path))
                if response:
                    with suppress(Exception):
                        self.app.feedback.warning(response)

            action = ui.Button(
                text=trans("open_crash_diagnostics"),
                icon=ft.Icons.FOLDER_OPEN,
                on_click=open_diagnostics,
                variant="outline",
                tone="neutral",
            )
            report_metadata = {
                "screen": "game",
                "action": "launch",
                "loader": loader_id,
                "minecraft": mc_ver,
                "diagnostic_path": str(path),
            }
            diagnosis = self._diagnose_launch_failure(path, hs_err_log)
            report_metadata["diagnostic_kind"] = diagnosis.kind
            report_metadata["diagnostic_severity"] = diagnosis.severity
            if diagnosis.evidence:
                report_metadata["diagnostic_evidence"] = diagnosis.evidence
            report_attachments = [path]
            if hs_err_log is not None:
                report_metadata["hs_err_path"] = str(hs_err_log)
                if hs_err_log != path:
                    report_attachments.append(hs_err_log)

            message = trans("version_crashed_open_logs", path=str(path))
            diagnostic_message = trans(diagnosis.message_key)
            if diagnostic_message and diagnostic_message != diagnosis.message_key:
                message = f"{message}\n\n{diagnostic_message}"

            self.app.feedback.warning(
                message,
                actions=action,
                report_title="Minecraft exited after launch",
                report_type="crash",
                report_severity="error",
                report_metadata=report_metadata,
                report_attachments=report_attachments,
            )
        except Exception as exc:
            _log_safely("error", f"Unable to show launch crash diagnostics dialog: {exc!r}")

    def _diagnose_launch_failure(self, path: Path, hs_err_log: Optional[Path] = None):
        parts = [self._tail_text(path, max_lines=80)]
        if hs_err_log is not None and hs_err_log != path:
            parts.append(self._tail_text(hs_err_log, max_lines=80))
        return classify_launch_failure("\n".join(part for part in parts if part))

    def _tail_text(self, path: Optional[Path], max_lines: int = LOG_TAIL_LINES) -> str:
        if path is None or not path.exists() or not path.is_file():
            return ""
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            return f"Unable to read {path}: {exc!r}"
        return "\n".join(lines[-max_lines:]).strip()

    def _newest_crash_report(self, game_dir: Path) -> Optional[Path]:
        crash_dir = game_dir / "crash-reports"
        if not crash_dir.exists() or not crash_dir.is_dir():
            return None
        try:
            files = [path for path in crash_dir.iterdir() if path.is_file()]
        except Exception:
            return None
        if not files:
            return None
        try:
            return max(files, key=lambda path: path.stat().st_mtime)
        except Exception:
            return None

    def _newest_hs_err_log(self, game_dir: Path) -> Optional[Path]:
        if not game_dir.exists() or not game_dir.is_dir():
            return None
        try:
            files = [path for path in game_dir.glob("hs_err_*.log") if path.is_file()]
        except Exception:
            return None
        if not files:
            return None
        try:
            return max(files, key=lambda path: path.stat().st_mtime)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Process
    # ------------------------------------------------------------------
    def _close_later(self, process=None) -> None:
        # `App.sleep` is a thin alias over `time.sleep`; keep Game independent from App internals.
        time.sleep(5)
        if process is not None:
            with suppress(Exception):
                if process.poll() is not None:
                    Logger.error("Launcher auto-close skipped because Minecraft exited during startup.")
                    return
        self.app.stop()


__all__ = ["Game"]
