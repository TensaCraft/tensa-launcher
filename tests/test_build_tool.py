from __future__ import annotations

import importlib.util
import plistlib
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BUILD_TOOL_PATH = ROOT_DIR / ".tools" / "build.py"


def _load_build_tool():
    spec = importlib.util.spec_from_file_location("build_tool", BUILD_TOOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_context(build_tool, *, target: str) -> object:
    return build_tool.BuildContext(
        root_dir=ROOT_DIR,
        assets_dir=ROOT_DIR / "launcher" / "assets",
        dist_dir=ROOT_DIR / "dist",
        build_dir=ROOT_DIR / "build",
        pyproject_file=ROOT_DIR / "pyproject.toml",
        output_root=ROOT_DIR / ".build",
        target_output_dir=ROOT_DIR / ".build" / target,
        python_bin="python",
        target=target,
        app_name="TensaLauncher",
        product_name="TensaLauncher",
        company_name="TensaCraft",
        executable_name="TensaLauncher",
        installer_name="TensaLauncherInstaller",
    )


def test_build_install_dependencies_does_not_reinstall_current_project():
    build_tool = _load_build_tool()
    commands: list[list[str]] = []

    ctx = _build_context(build_tool, target="windows")

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    build_tool.install_dependencies(ctx)

    assert len(commands) == 3
    assert commands[0][:5] == ["python", "-m", "pip", "install", "--upgrade"]
    assert commands[1][:4] == ["python", "-m", "pip", "install"]
    assert "-e" not in commands[1]
    assert ".[build]" not in commands[1]
    project = build_tool.read_project_metadata(ctx)
    expected_requirements = [
        *project["dependencies"],
        *project["optional-dependencies"]["build"],
    ]
    for requirement in expected_requirements:
        assert requirement in commands[1]


def test_pyproject_pins_project_to_python_313_runtime():
    with (ROOT_DIR / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert pyproject["project"]["requires-python"] == ">=3.13,<3.14"


def test_build_resolves_project_python_when_current_python_is_not_313(monkeypatch):
    build_tool = _load_build_tool()

    monkeypatch.setattr(build_tool.sys, "executable", r"C:\Python314\python.exe")
    monkeypatch.setattr(build_tool, "ensure_python", lambda value: value)
    monkeypatch.setattr(
        build_tool,
        "python_version",
        lambda value: (3, 13) if value == r"C:\Python313\python.exe" else (3, 14),
    )
    monkeypatch.setattr(
        build_tool,
        "candidate_project_python_bins",
        lambda: [r"C:\Python313\python.exe"],
    )

    assert build_tool.resolve_build_python(None) == r"C:\Python313\python.exe"


def test_build_rejects_explicit_non_project_python(monkeypatch):
    build_tool = _load_build_tool()

    monkeypatch.setattr(build_tool, "ensure_python", lambda value: value)
    monkeypatch.setattr(build_tool, "python_version", lambda _value: (3, 14))

    try:
        build_tool.resolve_build_python(r"C:\Python314\python.exe")
    except build_tool.BuildError as exc:
        assert "Python 3.13" in str(exc)
    else:
        raise AssertionError("Expected explicit non-3.13 Python to be rejected")


def test_python_runtime_reexec_preserves_child_exit_and_output_path(monkeypatch):
    runtime = _load_module("python_runtime_test", ROOT_DIR / ".tools" / "python_runtime.py")
    calls = {}

    monkeypatch.setattr(runtime, "is_current_project_python", lambda: False)
    monkeypatch.setattr(runtime, "resolve_project_python", lambda: r"C:\Python313\python.exe")
    monkeypatch.setattr(runtime.sys, "argv", ["tool.py", "--check"])
    monkeypatch.delenv(runtime.REEXEC_ENV, raising=False)

    def fake_run(cmd, *, env):
        calls["cmd"] = cmd
        calls["env"] = env
        return subprocess.CompletedProcess(cmd, 9, "", "")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    try:
        runtime.reexec_if_needed()
    except SystemExit as exc:
        assert exc.code == 9
    else:
        raise AssertionError("Expected re-exec to exit with child status")

    assert calls["cmd"] == [r"C:\Python313\python.exe", "tool.py", "--check"]
    assert calls["env"][runtime.REEXEC_ENV] == "1"


def test_build_package_metadata_uses_tensalauncher_names():
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")

    metadata = build_tool.read_package_meta(ctx)

    assert metadata["app_name"] == "TensaLauncher"
    assert metadata["product_name"] == "TensaLauncher"
    assert metadata["executable_name"] == "TensaLauncher"
    assert metadata["installer_name"] == "TensaLauncherInstaller"


def test_build_target_cleanup_removes_generated_output(tmp_path):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")
    ctx.target_output_dir = tmp_path / "windows"
    ctx.target_output_dir.mkdir()
    (ctx.target_output_dir / "TensaLauncher.exe").write_text("old", encoding="utf-8")
    generated_dir = ctx.target_output_dir / "generated"
    generated_dir.mkdir()
    (generated_dir / "artifact.txt").write_text("old", encoding="utf-8")

    build_tool.reset_target_output_dir(ctx)

    assert not (ctx.target_output_dir / "TensaLauncher.exe").exists()
    assert not generated_dir.exists()


def test_clean_removes_build_root_contents(tmp_path):
    clean_tool = _load_module("clean_tool_test", ROOT_DIR / ".tools" / "clean.py")
    build_root = tmp_path / ".build"
    windows_dir = build_root / "windows"
    linux_dir = build_root / "linux"
    build_root.mkdir()
    windows_dir.mkdir()
    linux_dir.mkdir()
    (windows_dir / "TensaLauncher.exe").write_text("old", encoding="utf-8")
    (linux_dir / "TensaLauncher").write_text("old", encoding="utf-8")

    clean_tool.remove_build_root(build_root)

    assert not build_root.exists()
    assert not windows_dir.exists()
    assert not linux_dir.exists()


def test_release_workflow_uses_current_installer_name():
    workflow = (ROOT_DIR / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    assert "TensaLauncherInstaller.exe" in workflow
    assert "TCLInstaller.exe" not in workflow


def test_build_base_artifact_uses_internal_pack_name(tmp_path):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="linux")
    ctx.dist_dir = tmp_path / "dist"
    ctx.build_dir = tmp_path / "build"
    ctx.dist_dir.mkdir()
    ctx.build_dir.mkdir()
    client_archive = tmp_path / "flet-linux-ubuntu24.04-light-amd64.tar.gz"
    client_archive.write_bytes(b"flet")

    build_tool.detect_flet_command = lambda _ctx: ["flet"]
    build_tool.resolve_flet_desktop_client_archive = lambda _ctx: client_archive

    def fake_run(cmd, **_kwargs):
        if "pack" in cmd:
            (ctx.dist_dir / ctx.app_name).write_text("binary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    artifact = build_tool.build_base_artifact(
        ctx,
        target="linux",
        lang_path=tmp_path,
    )

    assert artifact == ctx.dist_dir / "TensaLauncher"


def test_linux_build_bundles_flet_desktop_client_archive(tmp_path):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="linux")
    ctx.dist_dir = tmp_path / "dist"
    ctx.build_dir = tmp_path / "build"
    ctx.dist_dir.mkdir()
    ctx.build_dir.mkdir()
    client_archive = tmp_path / "flet-linux-ubuntu24.04-light-amd64.tar.gz"
    client_archive.write_bytes(b"flet")
    commands: list[list[str]] = []

    build_tool.detect_flet_command = lambda _ctx: ["flet"]
    build_tool.resolve_flet_desktop_client_archive = lambda _ctx: client_archive

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if "pack" in cmd:
            (ctx.dist_dir / ctx.app_name).write_text("binary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    build_tool.build_base_artifact(ctx, target="linux", lang_path=tmp_path)

    pack_command = commands[0]
    assert "--add-data" in pack_command
    assert f"{client_archive}:flet_desktop/app" in pack_command


def test_flet_desktop_release_uses_public_artifact_api():
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")

    def fake_run(cmd, **kwargs):
        code = cmd[2]
        if "__get_artifact_filename" in code:
            raise subprocess.CalledProcessError(
                1,
                cmd,
                output="",
                stderr="AttributeError: module 'flet_desktop' has no attribute '__get_artifact_filename'",
            )
        assert "get_artifact_filename()" in code
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(cmd, 0, "0.85.1\nflet-windows.zip\n", "")

    ctx.run = fake_run

    assert build_tool._resolve_flet_desktop_release(ctx) == (
        "0.85.1",
        "flet-windows.zip",
    )


def test_build_icon_resolver_generates_platform_specific_icons(tmp_path):
    icon_assets = _load_module("icon_assets_resolver_test", ROOT_DIR / ".tools" / "icon_assets.py")
    build_tool = _load_build_tool()
    from PIL import Image

    ctx = _build_context(build_tool, target="windows")
    ctx.assets_dir = tmp_path / "assets"
    ctx.output_root = tmp_path / ".build"
    ctx.build_dir = tmp_path / "build"
    ctx.assets_dir.mkdir(parents=True)
    ctx.build_dir.mkdir()
    Image.new("RGBA", (256, 256), (0, 200, 160, 255)).save(ctx.assets_dir / "logo.png")

    windows_icon = icon_assets.resolve_pack_icon(ctx, "windows")
    linux_icon = icon_assets.resolve_pack_icon(ctx, "linux")
    macos_icon = icon_assets.resolve_pack_icon(ctx, "macos")

    assert windows_icon.name == "TensaLauncher.ico"
    assert windows_icon.is_file()
    assert linux_icon.name == "TensaLauncher.png"
    assert linux_icon.is_file()
    assert macos_icon.name == "TensaLauncher.icns"
    assert macos_icon.is_file()


def test_windows_build_emits_base_executable_without_msix(tmp_path):
    build_tool = _load_build_tool()
    windows_builder = _load_module("build_windows_exe_test", ROOT_DIR / ".tools" / "build_windows.py")

    ctx = _build_context(build_tool, target="windows")
    ctx.target_output_dir = tmp_path / "windows"
    ctx.target_output_dir.mkdir()
    base_binary = tmp_path / "TensaLauncher.exe"
    base_binary.write_text("binary", encoding="utf-8")

    def fake_copy_to_target(source, name=None):
        target = ctx.target_output_dir / (name or source.name)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    ctx.copy_to_target = fake_copy_to_target

    artifacts = windows_builder.build_target(
        ctx,
        type("Args", (), {"with_windows_installer": False})(),
        base_binary,
    )

    assert [artifact.name for artifact in artifacts] == ["TensaLauncher.exe"]


def test_windows_sandbox_helper_runs_exe_without_app_package_installer():
    script = (ROOT_DIR / ".tools" / "run_exe_sandbox.ps1").read_text(encoding="utf-8")

    assert "TensaLauncher.exe" in script
    assert "TensaLauncher.wsb" in script
    assert ".msix" not in script.lower()
    assert "Add-AppxPackage" not in script
    assert "Remove-AppxPackage" not in script


def test_windows_signing_uses_timestamp_when_configured(monkeypatch, tmp_path):
    build_tool = _load_build_tool()
    windows_builder = _load_module("build_windows_signing_test", ROOT_DIR / ".tools" / "build_windows.py")
    ctx = _build_context(build_tool, target="windows")
    cert = tmp_path / "codesign.pfx"
    cert.write_bytes(b"cert")
    artifact = tmp_path / "TensaLauncher.exe"
    artifact.write_text("binary", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setenv("TENSALAUNCHER_WINDOWS_CERT_PATH", str(cert))
    monkeypatch.setenv("TENSALAUNCHER_WINDOWS_CERT_PASSWORD", "secret")
    monkeypatch.setenv("TENSALAUNCHER_WINDOWS_TIMESTAMP_URL", "https://timestamp.example.test")
    windows_builder.find_windows_sdk_tool = lambda _name: tmp_path / "signtool.exe"
    ctx.run = lambda cmd, **_kwargs: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")

    windows_builder.sign_windows_artifact_if_configured(ctx, artifact)

    assert commands == [
        [
            str(tmp_path / "signtool.exe"),
            "sign",
            "/fd",
            "SHA256",
            "/f",
            str(cert),
            "/p",
            "secret",
            "/tr",
            "https://timestamp.example.test",
            "/td",
            "SHA256",
            str(artifact),
        ]
    ]


def test_build_base_artifact_packs_root_bootstrap(tmp_path):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")
    ctx.dist_dir = tmp_path / "dist"
    ctx.build_dir = tmp_path / "build"
    ctx.dist_dir.mkdir()
    ctx.build_dir.mkdir()
    client_archive = tmp_path / "flet-windows.zip"
    client_archive.write_bytes(b"flet")
    commands: list[list[str]] = []

    build_tool.detect_flet_command = lambda _ctx: ["flet"]
    build_tool.resolve_flet_desktop_client_archive = lambda _ctx: client_archive

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if "pack" in cmd:
            (ctx.dist_dir / f"{ctx.app_name}.exe").write_text("binary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    build_tool.build_base_artifact(ctx, target="windows", lang_path=tmp_path)

    assert commands
    assert commands[0][2] == str(ROOT_DIR / "launcher" / "main.py")
    assert f"{client_archive};flet_desktop/app" in commands[0]


def test_build_base_artifact_macos_uses_flet_pack(tmp_path):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="macos")
    ctx.dist_dir = tmp_path / "dist"
    ctx.build_dir = tmp_path / "build"
    ctx.dist_dir.mkdir()
    ctx.build_dir.mkdir()
    client_archive = tmp_path / "flet-macos.tar.gz"
    client_archive.write_bytes(b"flet")
    commands: list[list[str]] = []

    build_tool.detect_flet_command = lambda _ctx: ["flet"]
    build_tool.resolve_flet_desktop_client_archive = lambda _ctx: client_archive

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if "pack" in cmd:
            (ctx.dist_dir / f"{ctx.app_name}.app").mkdir()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    artifact = build_tool.build_base_artifact(ctx, target="macos", lang_path=tmp_path)

    assert artifact == ctx.dist_dir / "TensaLauncher.app"
    assert commands
    assert commands[0][0] == "flet"
    assert commands[0][1] == "pack"
    assert "--product-name" in commands[0]
    assert "--hidden-import" in commands[0]
    assert "AVFoundation" in commands[0]
    assert f"{client_archive}:flet_desktop/app" in commands[0]


def test_platform_builders_emit_release_artifact_names(tmp_path):
    build_tool = _load_build_tool()
    linux_builder = _load_module("build_linux_test", ROOT_DIR / ".tools" / "build_linux.py")
    macos_builder = _load_module("build_macos_test", ROOT_DIR / ".tools" / "build_macos.py")

    linux_ctx = _build_context(build_tool, target="linux")
    linux_ctx.target_output_dir = tmp_path / "linux"
    linux_ctx.target_output_dir.mkdir()
    base_binary = tmp_path / "TensaLauncher"
    base_binary.write_text("binary", encoding="utf-8")

    linux_ctx.copy_to_target = lambda source, name=None: linux_ctx.target_output_dir / (name or source.name)
    linux_builder.build_appimage = lambda _ctx, _base: linux_ctx.target_output_dir / "TensaLauncher-x86_64.AppImage"

    linux_artifacts = linux_builder.build_target(
        linux_ctx,
        type("Args", (), {"linux_format": "appimage"})(),
        base_binary,
    )

    assert linux_artifacts[0].name == "TensaLauncher"
    assert linux_artifacts[1].name == "TensaLauncher-x86_64.AppImage"

    mac_ctx = _build_context(build_tool, target="macos")
    mac_ctx.target_output_dir = tmp_path / "macos"
    mac_ctx.target_output_dir.mkdir()
    app_bundle = tmp_path / "TensaLauncher.app"
    app_bundle.mkdir()
    copied = {}

    def fake_copy_to_target(source, name=None):
        target = mac_ctx.target_output_dir / (name or source.name)
        copied["app"] = target
        contents_dir = target / "Contents"
        contents_dir.mkdir(parents=True, exist_ok=True)
        with (contents_dir / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleName": "TensaLauncher"}, handle)
        return target

    mac_ctx.copy_to_target = fake_copy_to_target
    macos_builder.build_dmg = lambda _ctx, app: mac_ctx.target_output_dir / "TensaLauncher.dmg" if app == copied["app"] else None

    dmg_artifacts = macos_builder.build_target(
        mac_ctx,
        type("Args", (), {"skip_dmg": False})(),
        app_bundle,
    )
    app_artifacts = macos_builder.build_target(
        mac_ctx,
        type("Args", (), {"skip_dmg": True})(),
        app_bundle,
    )

    assert dmg_artifacts[0].name == "TensaLauncher.dmg"
    assert app_artifacts[0].name == "TensaLauncher.app"
    assert copied["app"].name == "TensaLauncher.app"


def test_macos_build_adds_microphone_permission_metadata(tmp_path):
    build_tool = _load_build_tool()
    macos_builder = _load_module("build_macos_permissions_test", ROOT_DIR / ".tools" / "build_macos.py")
    ctx = _build_context(build_tool, target="macos")
    app_bundle = tmp_path / "TensaLauncher.app"
    contents_dir = app_bundle / "Contents"
    contents_dir.mkdir(parents=True)
    info_plist = contents_dir / "Info.plist"
    with info_plist.open("wb") as handle:
        plistlib.dump({"CFBundleName": "TensaLauncher"}, handle)

    macos_builder.prepare_macos_app_bundle(ctx, app_bundle)

    with info_plist.open("rb") as handle:
        metadata = plistlib.load(handle)
    assert metadata["CFBundleIdentifier"] == "ua.co.tensa.TensaLauncher"
    assert "microphone" in metadata["NSMicrophoneUsageDescription"].lower()


def test_smoke_packaged_resolves_macos_app_binary(tmp_path):
    smoke_tool = _load_module("smoke_packaged_tool", ROOT_DIR / ".tools" / "smoke_packaged.py")
    app_bundle = tmp_path / "TensaLauncher.app"
    binary = app_bundle / "Contents" / "MacOS" / "TensaLauncher"
    binary.parent.mkdir(parents=True)
    binary.write_text("echo", encoding="utf-8")

    command = smoke_tool._resolve_command("macos", app_bundle)

    assert command == [str(binary), "--smoke-test"]


def test_linux_appimage_apprun_does_not_force_sidecar_app_base(tmp_path):
    build_tool = _load_build_tool()
    linux_builder = _load_module("build_linux_apprun_test", ROOT_DIR / ".tools" / "build_linux.py")

    ctx = _build_context(build_tool, target="linux")
    ctx.output_root = tmp_path / "output"
    ctx.target_output_dir = tmp_path / "linux"
    ctx.assets_dir = tmp_path / "assets"
    ctx.build_dir = tmp_path / "build"
    ctx.target_output_dir.mkdir(parents=True)
    ctx.assets_dir.mkdir(parents=True)
    ctx.build_dir.mkdir()
    from PIL import Image

    Image.new("RGBA", (64, 64), (0, 200, 160, 255)).save(ctx.assets_dir / "logo.png")
    ctx.ensure_executable = lambda _path: None

    binary = tmp_path / "TensaLauncher"
    binary.write_text("binary", encoding="utf-8")
    captured = {}

    linux_builder.resolve_appimagetool = lambda _ctx: tmp_path / "appimagetool"

    def fake_run(cmd, **_kwargs):
        app_run = ctx.target_output_dir / f"{ctx.app_name}.AppDir" / "AppRun"
        captured["app_run"] = app_run.read_text(encoding="utf-8")
        desktop_entry = ctx.target_output_dir / f"{ctx.app_name}.AppDir" / f"{ctx.app_name}.desktop"
        captured["desktop_entry"] = desktop_entry.read_text(encoding="utf-8")
        (ctx.target_output_dir / f"{ctx.executable_name}-x86_64.AppImage").write_text("artifact", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    artifact = linux_builder.build_appimage(ctx, binary)

    assert artifact.name == "TensaLauncher-x86_64.AppImage"
    assert "TENSALAUNCHER_APP_BASE" not in captured["app_run"]
    assert "Name=TensaLauncher" in captured["desktop_entry"]
    assert "X-AppImage-Name=TensaLauncher" in captured["desktop_entry"]
