from __future__ import annotations

import base64
import hashlib
import io
import json
import ntpath
import os
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image, ImageOps

from launcher.platform.paths import LauncherPaths, is_frozen
from launcher.platform.resources import PACKAGE_ASSETS_DIR
from launcher.storage.atomic import path_lock

MAX_ICON_BYTES = 8 * 1024 * 1024
MAX_ICON_PIXELS = 16 * 1024 * 1024


@dataclass(frozen=True)
class ShortcutCommand:
    executable: str
    arguments: tuple[str, ...]
    working_directory: Path


def validate_version_id(value: str) -> str:
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A non-empty version ID without control characters is required")
    return value


def launcher_command(version_id: str) -> ShortcutCommand:
    argument = f"--launch-version={validate_version_id(version_id)}"
    executable = Path(sys.executable).absolute()
    if is_frozen():
        if sys.platform.startswith("linux") and os.environ.get("APPIMAGE"):
            executable = Path(os.environ["APPIMAGE"]).expanduser().absolute()
        return ShortcutCommand(str(executable), (argument,), executable.parent)

    if sys.platform.startswith("win"):
        windowed_python = executable.with_name("pythonw.exe")
        if windowed_python.is_file():
            executable = windowed_python
    return ShortcutCommand(
        str(executable),
        ("-m", "launcher.main", argument),
        Path(__file__).resolve().parents[2],
    )


def _powershell(script: str, payload: dict | None = None) -> str:
    env = os.environ.copy()
    if payload is not None:
        env["TENSALAUNCHER_SHORTCUT_DATA"] = json.dumps(payload, ensure_ascii=True)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.strip()


def desktop_directory() -> Path:
    if sys.platform.startswith("win"):
        value = _powershell(
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Environment]::GetFolderPath('DesktopDirectory')"
        )
        if not value:
            raise OSError("Windows Desktop directory is unavailable")
        desktop = Path(value)
    elif sys.platform.startswith("linux"):
        try:
            completed = subprocess.run(
                ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=True, timeout=5
            )
            desktop = Path(completed.stdout.strip())
        except FileNotFoundError:
            desktop = Path.home() / "Desktop"
    elif sys.platform == "darwin":
        desktop = Path.home() / "Desktop"
    else:
        raise OSError(f"Desktop shortcuts are unsupported on {sys.platform}")
    if not desktop.is_absolute() or not desktop.is_dir():
        raise OSError("Desktop directory is unavailable")
    return desktop


def _desktop_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _desktop_argument(value: str) -> str:
    # Exec quoting is applied before the desktop-entry string escaping layer.
    escaped = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return _desktop_string('"' + escaped + '"')


def _read_icon(source: str) -> bytes:
    if source.startswith(("https://", "http://")):
        try:
            with requests.get(source, stream=True, timeout=(5, 15)) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_ICON_BYTES:
                        raise ValueError("Shortcut icon exceeds the download limit")
                return bytes(data)
        except requests.RequestException as exc:
            raise OSError("Unable to download the instance shortcut icon") from exc
    if source.startswith("data:"):
        header, separator, source = source.partition(",")
        if not separator or not header.startswith("data:image/") or not header.endswith(";base64"):
            raise ValueError("Unsupported shortcut icon data URL")
    elif len(source) < 4096:
        path = Path(source)
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file:
            with path.open("rb") as stream:
                data = stream.read(MAX_ICON_BYTES + 1)
            if len(data) > MAX_ICON_BYTES:
                raise ValueError("Shortcut icon exceeds the file size limit")
            return data
    if len(source) > 4 * ((MAX_ICON_BYTES + 2) // 3):
        raise ValueError("Shortcut icon exceeds the encoded size limit")
    return base64.b64decode(source, validate=True)


def _persistent_icon(version_id: str, source: str | None, directory: Path | None, extension: str) -> Path:
    data = _read_icon(source or str(PACKAGE_ASSETS_DIR / "logo.png"))
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as opened:
                if opened.width * opened.height > MAX_ICON_PIXELS:
                    raise ValueError("Shortcut icon exceeds the pixel limit")
                picture = ImageOps.exif_transpose(opened).convert("RGBA")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("Shortcut icon exceeds the pixel limit") from exc
    size = {".ico": 256, ".png": 512, ".icns": 1024}[extension]
    resized = ImageOps.contain(picture, (size, size), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (size, size))
    square.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    buffer = io.BytesIO()
    if extension == ".ico":
        square.save(buffer, format="ICO", sizes=[(value, value) for value in (16, 24, 32, 48, 64, 128, 256)])
    else:
        square.save(buffer, format="PNG" if extension == ".png" else "ICNS")
    encoded = buffer.getvalue()
    identity = hashlib.sha256(version_id.encode("utf-8")).hexdigest()[:12]
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    directory = directory if directory is not None else LauncherPaths.detect().app_state_dir / "shortcut-icons"
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identity}-{digest}{extension}"
    with path_lock(directory):
        if not _icon_matches(path, encoded):
            _write_icon(path, encoded)
    return path


def _icon_matches(path: Path, encoded: bytes) -> bool:
    try:
        stored = path.lstat()
        if not stat.S_ISREG(stored.st_mode):
            raise OSError("Shortcut icon must be a regular file, not a symbolic link")
        with open(
            path, "rb", opener=lambda name, flags: os.open(name, flags | getattr(os, "O_NOFOLLOW", 0)),
        ) as stream:
            # On Windows, validate the opened file identity before reading any bytes.
            if not os.path.samestat(stored, os.fstat(stream.fileno())):
                raise OSError("Shortcut icon changed while opening its cache entry")
            return stream.read(len(encoded) + 1) == encoded
    except FileNotFoundError:
        return False


def _write_icon(path: Path, encoded: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise OSError("Shortcut icon must not be a symbolic link")
        # Replace the entry, never open the cached target: old partial files are repairable.
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _windows_link(command: ShortcutCommand, icon: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="tensalauncher-shortcut-") as directory:
        target = Path(directory) / "instance.lnk"
        # All user-controlled values are data, never interpolated into PowerShell.
        _powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$data = $env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json; "
            "$shell = New-Object -ComObject WScript.Shell; "
            "$link = $shell.CreateShortcut($data.path); "
            "$link.TargetPath = $data.executable; "
            "$link.Arguments = $data.arguments; "
            "$link.WorkingDirectory = $data.directory; "
            "$link.IconLocation = $data.icon + ',0'; "
            "$link.Save()",
            {
                "path": str(target),
                "executable": command.executable,
                "arguments": subprocess.list2cmdline(command.arguments),
                "directory": str(command.working_directory),
                "icon": str(icon),
            },
        )
        return target.read_bytes()


def _macos_bundle(target: Path, command: ShortcutCommand, icon: Path, name: str, identity: str) -> None:
    contents = target / "Contents"
    executable_directory = contents / "MacOS"
    resources = contents / "Resources"
    executable_directory.mkdir(parents=True)
    resources.mkdir()
    script = executable_directory / "launch"
    script.write_text(
        "#!/bin/sh\n"
        f"cd {shlex.quote(str(command.working_directory))} || exit 1\n"
        f"exec {shlex.join((command.executable, *command.arguments))}\n",
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o700)
    (resources / "instance.icns").write_bytes(icon.read_bytes())
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": name,
        "CFBundleIdentifier": f"org.tensacraft.instance.{identity}",
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "launch",
        "CFBundleIconFile": "instance.icns",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleVersion": "1.0",
        "LSUIElement": True,
    }))


def create_desktop_shortcut(
    version_id: str,
    name: str,
    *,
    desktop: Path | None = None,
    command: ShortcutCommand | None = None,
    icon_source: str | None = None,
    icon_directory: Path | None = None,
) -> Path:
    validate_version_id(version_id)
    command = command or launcher_command(version_id)
    for value in (command.executable, *command.arguments, str(command.working_directory)):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Shortcut commands cannot contain control characters")
    desktop = desktop if desktop is not None else desktop_directory()
    if not desktop.is_absolute() or not desktop.is_dir():
        raise OSError("Desktop directory is unavailable")
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .")[:64] or "instance"
    if ntpath.isreserved(safe_name):
        safe_name = f"_{safe_name}"
    identity = hashlib.sha256(version_id.encode("utf-8")).hexdigest()[:12]
    if sys.platform.startswith("win"):
        icon = _persistent_icon(version_id, icon_source, icon_directory, ".ico")
        extension, data = ".lnk", _windows_link(command, icon)
    elif sys.platform.startswith("linux"):
        icon = _persistent_icon(version_id, icon_source, icon_directory, ".png")
        exec_line = " ".join(_desktop_argument(value) for value in (command.executable, *command.arguments))
        extension = ".desktop"
        data = (
            "[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name={_desktop_string(safe_name)}\n"
            f"Exec={exec_line}\nPath={_desktop_string(str(command.working_directory))}\n"
            f"Icon={_desktop_string(str(icon))}\n"
            "Terminal=false\nCategories=Game;\n"
        ).encode("utf-8")
    elif sys.platform == "darwin":
        icon = _persistent_icon(version_id, icon_source, icon_directory, ".icns")
        extension, data = ".app", b""
    else:
        raise OSError(f"Desktop shortcuts are unsupported on {sys.platform}")

    # Exclusive creation avoids overwriting unrelated files or following symlinks.
    for index in range(100):
        suffix = f" ({index + 1})" if index else ""
        target = desktop / f"{safe_name}{suffix}{extension}"
        if extension == ".app":
            try:
                target.mkdir()
            except FileExistsError:
                continue
            _macos_bundle(target, command, icon, safe_name, identity)
            return target
        try:
            stream = target.open("xb")
        except FileExistsError:
            continue
        with stream:
            stream.write(data)
        if not sys.platform.startswith("win"):
            target.chmod(0o700)
        return target
    raise FileExistsError("Too many shortcuts already exist for this instance")
