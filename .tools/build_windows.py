#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from icon_assets import resolve_pack_icon  # noqa: E402

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


def _env(name: str, legacy_name: str, default: str = "") -> str:
    return os.environ.get(name) or os.environ.get(legacy_name) or default


def find_windows_sdk_tool(name: str) -> Path:
    from_path = shutil.which(name)
    if from_path:
        return Path(from_path)

    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Kits" / "10" / "bin",
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        candidates.extend(root.glob(f"*/*/{name}"))
        candidates.extend((root / version / "x64" / name for version in root.iterdir() if version.is_dir()))

    existing = sorted((path for path in candidates if path.is_file()), reverse=True)
    if existing:
        return existing[0]

    raise FileNotFoundError(f"{name} was not found. Install the Windows SDK to sign Windows artifacts.")


def find_iscc() -> Path:
    from_path = shutil.which("iscc")
    if from_path:
        return Path(from_path)

    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError("Inno Setup compiler (ISCC.exe) not found.")


def render_iss(*, exe_path: Path, icon_path: Path, output_dir: Path, output_name: str) -> str:
    exe_src = str(exe_path).replace("/", "\\")
    icon_src = str(icon_path).replace("/", "\\")
    icon_name = icon_path.name
    out_dir = str(output_dir).replace("/", "\\")

    return textwrap.dedent(
        f"""\
        #define MyAppName "{exe_path.stem}"
        #define MyAppVersion "*"
        #define MyAppPublisher "TensaCraft"
        #define MyAppURL "https://tensa.co.ua/"
        #define MyAppExeName "{exe_path.name}"
        #define MyAppIconName "{icon_name}"
        #define MyAppAssocName MyAppName + " File"
        #define MyAppAssocExt ".myp"
        #define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

        [Setup]
        PrivilegesRequired=admin
        AppId={{{{915E978C-BCB8-4C17-A0E2-E9B8A8A4E380}}}}
        AppName={{#MyAppName}}
        AppVersion={{#MyAppVersion}}
        AppPublisher={{#MyAppPublisher}}
        AppPublisherURL={{#MyAppURL}}
        AppSupportURL={{#MyAppURL}}
        AppUpdatesURL={{#MyAppURL}}
        DefaultDirName=C:\\Games\\{{#MyAppName}}
        ChangesAssociations=yes
        DefaultGroupName={{#MyAppName}}
        AllowNoIcons=no
        OutputDir={out_dir}
        OutputBaseFilename={output_name}
        Compression=lzma
        SolidCompression=yes
        WizardStyle=modern
        SetupIconFile={icon_src}

        [Languages]
        Name: "ukrainian"; MessagesFile: "compiler:Languages\\Ukrainian.isl"

        [Tasks]
        Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked

        [Files]
        Source: "{exe_src}"; DestDir: "{{app}}"; Flags: ignoreversion
        Source: "{icon_src}"; DestDir: "{{app}}"; Flags: ignoreversion

        [Registry]
        Root: HKA; Subkey: "Software\\Classes\\{{#MyAppAssocExt}}\\OpenWithProgids"; ValueType: string; ValueName: "{{#MyAppAssocKey}}"; ValueData: ""; Flags: uninsdeletevalue
        Root: HKA; Subkey: "Software\\Classes\\{{#MyAppAssocKey}}"; ValueType: string; ValueName: ""; ValueData: "{{#MyAppAssocName}}"; Flags: uninsdeletekey
        Root: HKA; Subkey: "Software\\Classes\\{{#MyAppAssocKey}}\\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{{app}}\\{{#MyAppExeName}},0"
        Root: HKA; Subkey: "Software\\Classes\\{{#MyAppAssocKey}}\\shell\\open\\command"; ValueType: string; ValueName: ""; ValueData: \"\"\"{{app}}\\{{#MyAppExeName}}\"\" \"\"%1\"\"\"
        Root: HKA; Subkey: "Software\\Classes\\Applications\\{{#MyAppExeName}}\\SupportedTypes"; ValueType: string; ValueName: ".myp"; ValueData: ""

        [Icons]
        Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; IconFilename: "{{app}}\\{{#MyAppIconName}}"
        Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon; IconFilename: "{{app}}\\{{#MyAppIconName}}"

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#StringChange(MyAppName, '&', '&&')}}}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
        """
    )


def build_installer(ctx, exe_path: Path) -> Path:
    icon_path = resolve_pack_icon(ctx, "windows")

    try:
        iscc = find_iscc()
    except FileNotFoundError as exc:
        raise ctx.error(str(exc)) from exc

    output_name = ctx.installer_name
    iss_path = ctx.target_output_dir / "_installer.iss"
    iss_path.write_text(
        render_iss(exe_path=exe_path, icon_path=icon_path, output_dir=ctx.target_output_dir, output_name=output_name),
        encoding="utf-8",
    )

    ctx.log(f"ISCC: {iscc}")
    ctx.run([str(iscc), str(iss_path)])

    installer_path = ctx.target_output_dir / f"{output_name}.exe"
    if not installer_path.exists():
        raise ctx.error(f"Installer was not created: {installer_path}")

    return installer_path


def sign_windows_artifact_if_configured(ctx, artifact_path: Path) -> None:
    cert_path = _env("TENSALAUNCHER_WINDOWS_CERT_PATH", "TCL_WINDOWS_CERT_PATH")
    if not cert_path:
        return

    cert = Path(cert_path).expanduser()
    if not cert.is_file():
        raise ctx.error(f"Windows signing certificate was not found: {cert}")

    signtool = find_windows_sdk_tool("signtool.exe")
    password = _env("TENSALAUNCHER_WINDOWS_CERT_PASSWORD", "TCL_WINDOWS_CERT_PASSWORD")
    timestamp_url = _env("TENSALAUNCHER_WINDOWS_TIMESTAMP_URL", "TCL_WINDOWS_TIMESTAMP_URL", DEFAULT_TIMESTAMP_URL)

    command = [
        str(signtool),
        "sign",
        "/fd",
        "SHA256",
        "/f",
        str(cert),
    ]
    if password:
        command.extend(["/p", password])
    command.extend(["/tr", timestamp_url, "/td", "SHA256", str(artifact_path)])
    ctx.run(command)


def build_target(ctx, args, base_artifact: Path) -> list[Path]:
    exe_artifact = ctx.copy_to_target(base_artifact, name=f"{ctx.executable_name}.exe")
    sign_windows_artifact_if_configured(ctx, exe_artifact)
    artifacts = [exe_artifact]

    if getattr(args, "with_windows_installer", False):
        installer = build_installer(ctx, artifacts[0])
        sign_windows_artifact_if_configured(ctx, installer)
        artifacts.append(installer)

    return artifacts
