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
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, cast

import minecraft_launcher_lib

from launcher.application.diagnostics import ActionSafety, DiagnosticCase, DiagnosticResult, read_artifact
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.installed_components import InstalledComponentsService
from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationLease,
)
from launcher.application.launch_diagnostics import analyze_launch_failure
from launcher.application.launch_workflow import LaunchPreparationRequest, LaunchWorkflow
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.core import util
from launcher.core.versions import Version
from launcher.models.logger import Logger
from launcher.platform.java_process import java_process_env

WINDOWS_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
EARLY_EXIT_SECONDS = 5.0
LOG_TAIL_LINES = 40
LOG_TAIL_MAX_BYTES = 256 * 1024
LAUNCH_DIAGNOSTICS_LOG = "tensalauncher-launch.log"
LAUNCH_COOLDOWN_SECONDS = 3.0
REPAIR_SYNC_DIAGNOSIS_KINDS = {"missing_mod_dependency", "locked_file"}


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

    def __init__(self, app: Any, minecraft_dir: str | Path | None = None) -> None:
        self.app = app
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
        return Path(util.minecraft_dir)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    def start(
        self,
        v: Version,
        *,
        allow_duplicate: bool = False,
        profile_key: str | None = None,
    ) -> dict[str, Any]:
        game_dir = self._version_game_dir(v)
        coordinator = getattr(self.app, "instance_operations", None)
        if coordinator is None:
            return self._start_locked(
                v,
                allow_duplicate=allow_duplicate,
                profile_key=profile_key,
                instance_lease=None,
            )
        try:
            with coordinator.operation(game_dir, "launch") as lease:
                return self._start_locked(
                    v,
                    allow_duplicate=allow_duplicate,
                    profile_key=profile_key,
                    instance_lease=lease,
                )
        except InstanceOperationBusy as exc:
            _log_safely(
                "warning",
                f"Launch blocked by active {exc.active_kind} operation for {v.name}: {game_dir}",
            )
            return {
                "status": False,
                "text": self.app.trans(
                    "instance_operation_busy",
                    version=v.name,
                ),
            }

    def _start_locked(
        self,
        v: Version,
        *,
        allow_duplicate: bool,
        profile_key: str | None,
        instance_lease: InstanceOperationLease | None,
    ) -> dict[str, Any]:
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

        workflow = LaunchWorkflow(
            self.app,
            verify=self._verify,
            build_options=self._build_opts,
            launch=self._launch,
            release_launch_slot=self._release_launch_slot,
        )
        result = workflow.run(
            LaunchPreparationRequest(
                version=v,
                launch_key=launch_key,
                started_at=t0,
                profile_key=profile_key,
                instance_lease=instance_lease,
            )
        )
        return result.as_response(self.app.trans)

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------
    def _verify(self, v: Version, operation=None) -> bool:
        try:
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
            loader = self.app.launcher.get_loader(key)

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
        typed_versions_provider = cast(Callable[[], Iterable[Any]] | None, versions_provider)

        paths = getattr(self.app, "paths", None)
        games_dir = getattr(paths, "games_dir", None) if paths is not None else None

        service = InstalledComponentsService(
            self.mc_dir,
            games_dir=games_dir,
            versions_provider=typed_versions_provider,
            loader_provider=self.app.launcher.get_loader,
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

    def _ensure_base_minecraft_version(self, v: Version, loader: Any, chk: Any, operation=None) -> bool:
        mc_version = str(getattr(v, "version", "") or "").strip()
        if not mc_version:
            return True
        try:
            if chk._is_version_installed(mc_version):
                check_version = getattr(chk, "check_version", None)
                if not callable(check_version):
                    return True
                integrity = check_version(mc_version, mc_version, check_java=False)
                if isinstance(integrity, dict) and bool(integrity.get("valid")):
                    return True
                Logger.warning(
                    f"Base Minecraft version {mc_version} is incomplete; "
                    "minecraft-launcher-lib will repair it"
                )
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

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def _build_opts(self, v: Version, prof: dict) -> dict:
        game_dir = self._ensure_dir(str(v.path or v.version_id))
        loader_id = str(v.loader or v.version or "")
        o = {
            "username": prof.get("name"),
            "uuid": prof.get("id"),
            "token": prof.get("access_token"),
            "gameDirectory": str(game_dir),
            "nativesDirectory": str(self.mc_dir / "versions" / loader_id / "natives"),
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
    def _launch(
        self,
        loader_id: str,
        mc_ver: str,
        opts: dict,
        launch_key: Optional[str] = None,
        version: Optional[Version] = None,
    ) -> bool:
        try:
            is_new = self._mc_ge(mc_ver, (1, 20, 0))
            lib_opts, srv = self._normalize_server(opts, allow_legacy=not is_new)

            t_cmd0 = time.perf_counter()
            cmd = minecraft_launcher_lib.command.get_minecraft_command(
                loader_id, str(self.mc_dir), cast(Any, lib_opts)
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
            launch_started_at = time.time()
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
                args=(
                    process,
                    cwd_path,
                    diagnostics_log,
                    loader_id,
                    mc_ver,
                    effective_launch_key,
                    version,
                    launch_started_at,
                ),
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

            registry = cast(Any, winreg)
            key = r"Software\Microsoft\DirectX\UserGpuPreferences"
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, key, 0, registry.KEY_ALL_ACCESS) as k:
                registry.SetValueEx(k, exe_path, 0, registry.REG_SZ, "GpuPreference=2;")
        except Exception as exc:
            _log_safely("debug", f"Unable to write Windows GPU preference for {exe_path}: {exc!r}")

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
        version: Optional[Version] = None,
        launch_started_at: float | None = None,
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

            crash_report = self._newest_crash_report(game_dir, newer_than=launch_started_at)
            hs_err_log = self._newest_hs_err_log(game_dir, newer_than=launch_started_at)
            latest_log = self._fresh_file(game_dir / "logs" / "latest.log", newer_than=launch_started_at)
            diagnostics_log = self._fresh_file(diagnostics_log, newer_than=launch_started_at)
            diagnostic_paths = self._unique_paths((crash_report, latest_log, diagnostics_log, hs_err_log))
            best_path = diagnostic_paths[0] if diagnostic_paths else game_dir

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
            self._show_launch_crash_alert(
                best_path,
                loader_id,
                mc_ver,
                hs_err_log,
                version,
                diagnostic_paths=diagnostic_paths,
                launch_started_at=launch_started_at,
            )
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
        version: Optional[Version] = None,
        *,
        diagnostic_paths: tuple[Path, ...] = (),
        launch_started_at: float | None = None,
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

            open_diagnostics_action = ui.Button(
                text=str(trans("open_crash_diagnostics")),
                icon=ft.Icons.FOLDER_OPEN,
                on_click=open_diagnostics,
                variant="outline",
                tone="neutral",
            )
            report_metadata: dict[str, Any] = {
                "screen": "game",
                "action": "launch",
                "loader": loader_id,
                "minecraft": mc_ver,
                "diagnostic_path": str(path),
            }
            paths = diagnostic_paths or self._unique_paths((path, hs_err_log))
            result = self._diagnose_launch_failure(
                paths,
                managed_pack=self._is_tensacraft_version(version),
                launch_started_at=launch_started_at,
            )
            diagnosis = result.primary
            diagnostic_actions = tuple(
                {
                    action.id: action
                    for finding in result.findings
                    for action in finding.actions
                }.values()
            )
            self._mark_tensacraft_repair_sync_required(version, diagnosis, path)
            report_metadata.update(
                {
                    "diagnostic_engine_version": result.engine_version,
                    "diagnostic_kind": diagnosis.kind,
                    "diagnostic_severity": diagnosis.severity,
                    "diagnostic_finding_ids": [finding.id for finding in result.findings],
                    "diagnostic_confidence": diagnosis.confidence.name.lower(),
                    "diagnostic_action_ids": [action.id for action in diagnostic_actions],
                }
            )
            report_attachments = list(paths)
            if hs_err_log is not None:
                report_metadata["hs_err_path"] = str(hs_err_log)

            message = trans("version_crashed_open_logs", path=str(path))
            diagnostic_sections = []
            for finding in result.findings:
                diagnostic_title = trans(finding.title_key)
                diagnostic_message = trans(finding.message_key, **finding.params)
                if diagnostic_message and diagnostic_message != finding.message_key:
                    diagnostic_sections.append(f"{diagnostic_title}\n{diagnostic_message}")
            if len(result.findings) > 1:
                message = f"{message}\n\n{trans('launch_diagnostic_multiple', count=len(result.findings))}"
            if diagnostic_sections:
                message = f"{message}\n\n" + "\n\n".join(diagnostic_sections)

            actions = [open_diagnostics_action]
            if version is not None:
                if any(action.kind == "open_mod_manager" for action in diagnostic_actions):
                    actions.append(
                        ui.Button(
                            text=str(trans("diagnostic_action_open_mod_manager")),
                            icon=ft.Icons.EXTENSION,
                            on_click=lambda _event: self.app.show_mods_manager_page(version),
                            variant="outline",
                            tone="neutral",
                        )
                    )
                repair_action = next(
                    (
                        action
                        for action in diagnostic_actions
                        if action.kind in {"repair", "repair_sync"} and action.safety == ActionSafety.CONFIRM
                    ),
                    None,
                )
                if repair_action is not None:
                    actions.append(
                        ui.Button(
                            text=str(trans(repair_action.title_key)),
                            icon=ft.Icons.BUILD,
                            on_click=lambda _event: self._confirm_diagnostic_repair(version),
                            variant="outline",
                            tone="neutral",
                        )
                    )
            self.app.feedback.warning(
                message,
                actions=actions[0] if len(actions) == 1 else actions,
                report_title="Minecraft exited after launch",
                report_type="crash",
                report_severity="error",
                report_metadata=report_metadata,
                report_attachments=report_attachments,
            )
        except Exception as exc:
            _log_safely("error", f"Unable to show launch crash diagnostics dialog: {exc!r}")

    def _confirm_diagnostic_repair(self, version: Version) -> None:
        trans = self.app.trans

        def confirmed(accepted: bool) -> None:
            if not accepted:
                return
            version.force_update = True

            async def repair_and_launch() -> None:
                from launcher.ui.core.page_runtime import run_blocking

                response = await run_blocking(self.start, version)
                message = response.get("text") if response else None
                if response and response.get("status"):
                    self.app.feedback.info(message)
                elif message:
                    self.app.feedback.warning(message)

            from launcher.ui.core.page_runtime import run_task

            run_task(self.app.page, repair_and_launch)

        self.app.feedback.confirm(
            title=trans("diagnostic_repair_confirm_title", version=version.name),
            question=trans("diagnostic_repair_confirm_message"),
            callback=confirmed,
        )

    def _mark_tensacraft_repair_sync_required(self, version, diagnosis, diagnostic_path: Path) -> None:
        if version is None or diagnosis.kind not in REPAIR_SYNC_DIAGNOSIS_KINDS:
            return
        is_tensacraft = getattr(version, "is_tensacraft", None)
        if not callable(is_tensacraft) or not is_tensacraft():
            return

        try:
            game_dir = self._version_game_dir(version)

            def mark_repair_required() -> None:
                FileSyncJournal(game_dir).mark_repair_required(
                    reason=diagnosis.kind,
                    details={
                        "diagnostic_path": str(diagnostic_path),
                        "version": str(
                            getattr(version, "name", "")
                            or getattr(version, "id", "")
                            or "TensaCraft"
                        ),
                    },
                )

            coordinator = getattr(self.app, "instance_operations", None)
            if coordinator is None:
                mark_repair_required()
            else:
                coordinator.execute(
                    game_dir,
                    "diagnostic_repair_mark",
                    mark_repair_required,
                )
            Logger.warning(
                "Marked TensaCraft pack for repair sync after launch failure: "
                f"version={getattr(version, 'name', version)} reason={diagnosis.kind}"
            )
        except InstanceOperationBusy:
            _log_safely(
                "warning",
                f"Skipped repair marker while another instance operation is active: {game_dir}",
            )
        except Exception as exc:
            _log_safely("warning", f"Unable to mark TensaCraft repair sync: {exc!r}")

    @staticmethod
    def _is_tensacraft_version(version: object | None) -> bool:
        checker = getattr(version, "is_tensacraft", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _diagnose_launch_failure(
        self,
        paths: tuple[Path, ...],
        *,
        managed_pack: bool,
        launch_started_at: float | None,
    ) -> DiagnosticResult:
        artifacts = tuple(
            read_artifact(
                path,
                started_at=launch_started_at,
            )
            for path in paths
        )
        return analyze_launch_failure(
            DiagnosticCase(
                artifacts=artifacts,
                managed_pack=managed_pack,
            )
        )

    def _tail_text(self, path: Optional[Path], max_lines: int = LOG_TAIL_LINES) -> str:
        if path is None or max_lines <= 0:
            return ""
        try:
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - LOG_TAIL_MAX_BYTES))
                lines = handle.read(LOG_TAIL_MAX_BYTES).decode("utf-8", errors="replace").splitlines()
        except (FileNotFoundError, IsADirectoryError):
            return ""
        except OSError as exc:
            return f"Unable to read {path}: {exc!r}"
        return "\n".join(lines[-max_lines:]).strip()

    def _newest_crash_report(self, game_dir: Path, *, newer_than: float | None = None) -> Optional[Path]:
        return self._newest_file((game_dir / "crash-reports").glob("*"), newer_than)

    def _newest_hs_err_log(self, game_dir: Path, *, newer_than: float | None = None) -> Optional[Path]:
        return self._newest_file(game_dir.glob("hs_err_*.log"), newer_than)

    def _newest_file(self, candidates: Iterable[Path], newer_than: float | None) -> Optional[Path]:
        try:
            newest = max((path for path in candidates if path.is_file()), key=lambda path: path.stat().st_mtime, default=None)
            return self._fresh_file(newest, newer_than=newer_than)
        except OSError:
            return None

    @staticmethod
    def _fresh_file(path: Optional[Path], *, newer_than: float | None) -> Optional[Path]:
        if path is None or not path.is_file():
            return None
        if newer_than is None:
            return path
        try:
            return path if path.stat().st_mtime >= newer_than - 2.0 else None
        except OSError:
            return None

    @staticmethod
    def _unique_paths(paths: Iterable[Optional[Path]]) -> tuple[Path, ...]:
        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path is None:
                continue
            normalized = path.resolve(strict=False)
            if normalized not in seen:
                seen.add(normalized)
                unique.append(path)
        return tuple(unique)

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
