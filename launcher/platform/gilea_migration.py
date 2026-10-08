from __future__ import annotations

import os
import platform
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

from launcher.application.gilea_migration.models import MigrationError, MigrationResult, MigrationRoots
from launcher.platform.instance_shortcuts import ShortcutCommand, _powershell, _windows_link, desktop_directory


def supported_platform() -> bool:
    return sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}


def has_webview() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    subkey = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                parts = str(value).split(".")
                if len(parts) == 4 and all(p.isdigit() for p in parts) and any(int(p) for p in parts):
                    return True
            except OSError:
                continue
    return False


def conflicting_processes(roots: MigrationRoots) -> bool:
    try:
        result = _powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$root = ($env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json).root; "
            "$busy = @(Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -like 'GileaLauncher*' -or "
            "($_.Name -match '^(java|javaw|minecraftjava)(.exe)?$' -and "
            "(-not $_.CommandLine -or $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0))"
            "}); if ($busy.Count -gt 0) { 'busy' } else { 'idle' }",
            {"root": str(roots.minecraft)},
        ).strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise MigrationError("process_check_failed") from None
    if result not in {"idle", "busy"}:
        raise MigrationError("process_check_failed")
    return result == "busy"


def check_prerequisites(roots: MigrationRoots) -> None:
    if not supported_platform():
        raise MigrationError("unsupported_platform")
    if not has_webview():
        raise MigrationError("webview_missing")
    if conflicting_processes(roots):
        raise MigrationError("running_process")


def protect_directory(path: Path) -> None:
    from launcher.application.gilea_migration.files import safe_path

    safe_path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        _powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$path = ($env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json).path; "
            "$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User; "
            "$acl = New-Object Security.AccessControl.DirectorySecurity; "
            "$acl.SetOwner($sid); $acl.SetAccessRuleProtection($true, $false); "
            "$rule = New-Object Security.AccessControl.FileSystemAccessRule("
            "$sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'); "
            "$acl.AddAccessRule($rule); [IO.Directory]::SetAccessControl($path, $acl); "
            "$check = [IO.Directory]::GetAccessControl($path); "
            "$rules = @($check.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])); "
            "if (-not $check.AreAccessRulesProtected -or $rules.Count -ne 1 -or "
            "$rules[0].IdentityReference.Value -ne $sid.Value -or "
            "$rules[0].AccessControlType -ne 'Allow') { throw 'ACL protection failed' }",
            {"path": str(path)},
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise MigrationError("backup_protection") from None


def default_roots(source_state: Path, minecraft: Path) -> MigrationRoots:
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        raise MigrationError("unsafe_path")
    return MigrationRoots(source_state, minecraft, Path(local) / "GileaLauncher",
                          Path(local) / "Programs/GileaLauncher", source_state / "gilea-migration")


@contextmanager
def read_guard(paths: list[Path]):
    """Deny concurrent Windows writes/deletes while the source snapshot is used."""
    if sys.platform != "win32":
        raise MigrationError("unsupported_platform")
    import ctypes
    from ctypes import wintypes

    from launcher.application.gilea_migration.files import safe_path

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    with ExitStack() as stack:
        for path in paths:
            safe_path(path)
            handle = create(str(path), 0x80000000, 1, None, 3, 0x80, None)
            if handle == wintypes.HANDLE(-1).value:
                raise MigrationError("source_busy")
            stack.callback(close, handle)
        yield


def recovery_command() -> ShortcutCommand:
    from launcher.platform.paths import is_frozen

    if is_frozen():
        return ShortcutCommand(sys.executable, ("--stay-on-tensa",), Path(sys.executable).parent)
    return ShortcutCommand(sys.executable, ("-m", "launcher.main", "--stay-on-tensa"),
                           Path(__file__).resolve().parents[2])


def _gilea_running(executable: Path) -> bool:
    try:
        result = _powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$exe = ($env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json).exe; "
            "$running = @(Get-CimInstance Win32_Process | Where-Object { "
            "$_.ExecutablePath -and [String]::Equals($_.ExecutablePath, $exe, "
            "[StringComparison]::OrdinalIgnoreCase) }); "
            "if ($running.Count -gt 0) { 'running' } else { 'stopped' }",
            {"exe": str(executable)},
        ).strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise MigrationError("process_check_failed") from None
    if result not in {"running", "stopped"}:
        raise MigrationError("process_check_failed")
    return result == "running"


def launch_gilea(result: MigrationResult, *, version_id: str | None = None,
                 initial: bool = False) -> subprocess.Popen[bytes]:
    from launcher.application.gilea_migration.files import safe_path
    from launcher.application.gilea_migration.release import verify_binary

    exe = safe_path(result.executable)
    if not exe.is_file():
        raise MigrationError("launch_failed")
    if initial:
        verify_binary(exe)
    command = [str(exe)]
    if version_id is not None:
        folder = result.build_aliases.get(version_id)
        if not folder:
            raise MigrationError("missing_build")
        command.append(f"--launch-version={folder}")
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("TENSALAUNCHER_", "FLET_", "_PYI", "_MEI"))
                   and k not in {"LAUNCHER_APP_BASE", "PYTHONPATH", "PYTHONHOME"}}
    already_running = _gilea_running(exe)
    try:
        process = subprocess.Popen(command, cwd=exe.parent, env=environment,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            code = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return process
        if code == 0 and already_running:
            # Gilea's single-instance helper exits after delivering the request.
            return process
        raise MigrationError("launch_failed")
    except OSError:
        raise MigrationError("launch_failed") from None


def create_gilea_shortcuts(result: MigrationResult, *, recovery_command: ShortcutCommand) -> tuple[Path, ...]:
    from launcher.application.gilea_migration.files import fingerprint, read_object, safe_path
    from launcher.storage.atomic import atomic_write_json

    desktop = safe_path(desktop_directory())
    manifest = safe_path(result.executable.parent / "shortcuts.json")
    owned = read_object(manifest, missing=True)
    commands = {
        "GileaLauncher.lnk": ShortcutCommand(str(result.executable), (), result.executable.parent),
        "TensaLauncher Recovery.lnk": recovery_command,
    }
    created = []
    for name, command in commands.items():
        path = safe_path(desktop / name)
        if path.exists():
            if owned.get(name) != fingerprint(path):
                raise MigrationError("shortcut_conflict")
        else:
            encoded = _windows_link(command, Path(command.executable))
            with path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            owned[name] = fingerprint(path)
            atomic_write_json(manifest, owned)
        created.append(path)
    return tuple(created)
