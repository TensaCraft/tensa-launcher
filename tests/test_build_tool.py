from __future__ import annotations

import hashlib
import importlib.util
import plistlib
import runpy
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

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
        company_name="Tensa",
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
    assert "setuptools>=80" in commands[0]
    assert commands[2][-1] == "import flet.cli, PyInstaller"
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


def test_typecheck_uses_current_python_for_import_resolution(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT_DIR / ".tools"))
    typecheck_tool = _load_module("run_typecheck_test", ROOT_DIR / ".tools" / "run_typecheck.py")
    calls: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(typecheck_tool.sys, "executable", "/repo/.venv/bin/python")

    def fake_run(cmd, *, cwd):
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(typecheck_tool.subprocess, "run", fake_run)

    assert typecheck_tool.main() == 0
    assert calls == [
        (
            [
                "/repo/.venv/bin/python",
                "-m",
                "pyright",
                "--project",
                "pyrightconfig.json",
                "--pythonpath",
                "/repo/.venv/bin/python",
            ],
            typecheck_tool.ROOT,
        )
    ]


def test_build_package_metadata_uses_tensalauncher_names():
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")

    metadata = build_tool.read_package_meta(ctx)

    assert metadata["app_name"] == "TensaLauncher"
    assert metadata["product_name"] == "TensaLauncher"
    assert metadata["company_name"] == "Tensa"
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


@pytest.mark.parametrize("target", ["windows", "linux", "macos"])
def test_flet_pack_does_not_override_patched_runtime_with_stock_archive(tmp_path, target):
    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target=target)
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
            artifact = build_tool._resolve_built_artifact_path(ctx, target=target)
            if target == "macos":
                artifact.mkdir()
            else:
                artifact.write_text("binary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    build_tool.build_base_artifact(ctx, target=target, lang_path=tmp_path)

    pack_command = commands[0]
    data_sep = ";" if target == "windows" else ":"
    data_entries = [pack_command[index + 1] for index, option in enumerate(pack_command) if option == "--add-data"]
    assert data_entries == [
        f"{ctx.assets_dir}{data_sep}launcher/assets",
        f"{tmp_path}{data_sep}transliterate/contrib/languages",
    ]


def test_flet_hook_keeps_patched_archive_and_matching_fingerprint(tmp_path, monkeypatch):
    hooks = pytest.importorskip("PyInstaller.utils.hooks")
    toc_utils = pytest.importorskip("PyInstaller.building.datastruct")
    build_utils = pytest.importorskip("PyInstaller.building.utils")
    hook_config = pytest.importorskip("flet_cli.__pyinstaller.config")
    hook_utils = pytest.importorskip("flet_cli.__pyinstaller.utils")

    patched_dir = tmp_path / "patched"
    patched_dir.mkdir()
    patched_archive = patched_dir / "flet-windows.zip"
    with zipfile.ZipFile(patched_archive, "w") as archive:
        archive.writestr("flet/flet.exe", b"client with launcher icon and metadata")
    payload = patched_archive.read_bytes()
    fingerprint = f"{hashlib.sha256(payload).hexdigest()} {len(payload)}"
    patched_archive.with_suffix(".zip.sha256").write_text(fingerprint, encoding="ascii")
    stock_archive = tmp_path / "flet-windows.zip"
    with zipfile.ZipFile(stock_archive, "w") as archive:
        archive.writestr("flet/flet.exe", b"stock client")

    build_tool = _load_build_tool()
    ctx = _build_context(build_tool, target="windows")
    ctx.assets_dir = tmp_path / "assets"
    ctx.assets_dir.mkdir()
    languages = tmp_path / "languages"
    languages.mkdir()
    monkeypatch.setattr(build_tool, "resolve_pack_icon", lambda *_args: tmp_path / "icon.ico")
    monkeypatch.setattr(build_tool, "resolve_flet_desktop_client_archive", lambda _ctx: stock_archive)
    monkeypatch.setattr(hook_config, "temp_bin_dir", str(patched_dir))
    monkeypatch.setattr(hooks, "collect_data_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hook_utils, "get_flet_bin_path", lambda: pytest.fail("Patched client must be bundled"))
    hook = runpy.run_path(str(Path(hook_config.__file__).with_name("hook-flet.py")))

    options = build_tool._shared_bundle_options(ctx, target="windows", lang_path=languages)
    cli_data = [options[index + 1].rsplit(";", 1) for index, option in enumerate(options) if option == "--add-data"]
    # Analysis appends hook data after explicit CLI data, then keeps the first destination.
    toc = [
        (destination, source, "DATA")
        for data in (cli_data, hook["datas"])
        for destination, source in build_utils.format_binaries_and_datas(data)
    ]
    bundled = {Path(destination).as_posix(): Path(source) for destination, source, _ in toc_utils.normalize_toc(toc)}

    assert bundled["flet_desktop/app/flet-windows.zip"] == patched_archive
    assert bundled["flet_desktop/app/flet-windows.zip"].read_bytes() == payload
    assert bundled["flet_desktop/app/flet-windows.zip.sha256"].read_text(encoding="ascii") == fingerprint


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
        assert "importlib.metadata.version('flet-desktop')" in code
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
    source = Image.new("RGBA", (1024, 1024))
    source.paste((0, 200, 160, 255), (0, 256, 1024, 768))
    source.save(ctx.assets_dir / "logo.png")

    windows_icon = icon_assets.resolve_pack_icon(ctx, "windows")
    linux_icon = icon_assets.resolve_pack_icon(ctx, "linux")
    macos_icon = icon_assets.resolve_pack_icon(ctx, "macos")

    assert windows_icon.name == "TensaLauncher.ico"
    assert windows_icon.is_file()
    assert linux_icon.name == "TensaLauncher.png"
    assert linux_icon.is_file()
    assert macos_icon.name == "TensaLauncher.icns"
    assert macos_icon.is_file()

    with Image.open(windows_icon) as icon:
        assert icon.ico.sizes() == set(icon_assets.WINDOWS_ICO_SIZES)
        for size in icon.ico.sizes():
            frame = icon.ico.getimage(size).convert("RGBA")
            assert frame.size == size
            assert frame.getchannel("A").getextrema() == (0, 255)
            assert frame.getpixel((0, 0))[3] == 0
            assert frame.getpixel((0, size[1] // 2))[3] == 255
            assert frame.getpixel((size[0] - 1, size[1] // 2))[3] == 255
    with Image.open(linux_icon) as icon:
        assert icon.size == (512, 512)
        assert icon.mode == "RGBA"
        assert icon.getpixel((0, 0))[3] == 0
        assert icon.getpixel((0, 256))[3] == 255
        assert icon.getpixel((511, 256))[3] == 255
    with Image.open(macos_icon) as icon:
        assert icon.size == (1024, 1024)
        assert {size[:2] for size in icon.info["sizes"]} >= {(16, 16), (128, 128), (512, 512)}
        assert icon.convert("RGBA").getpixel((0, 0))[3] == 0
        assert icon.convert("RGBA").getpixel((0, 512))[3] == 255
        assert icon.convert("RGBA").getpixel((1023, 512))[3] == 255


def test_icon_generator_preserves_non_square_source_proportions(tmp_path):
    from PIL import Image

    icon_assets = _load_module("icon_assets_aspect_test", ROOT_DIR / ".tools" / "icon_assets.py")
    source = tmp_path / "logo.png"
    Image.new("RGBA", (1024, 512), (10, 170, 120, 255)).save(source)

    icon = icon_assets._contained_image(source, (256, 256))

    assert icon.size == (256, 256)
    assert icon.getchannel("A").getbbox() == (0, 64, 256, 192)


@pytest.mark.parametrize("target,filename", [("windows", "logo.ico"), ("macos", "icon.icns")])
def test_bundled_icons_match_logo_source(tmp_path, target, filename):
    from PIL import Image

    icon_assets = _load_module("icon_assets_source_test", ROOT_DIR / ".tools" / "icon_assets.py")
    ctx = _build_context(_load_build_tool(), target=target)
    ctx.build_dir = tmp_path
    generated = icon_assets.resolve_pack_icon(ctx, target)

    with Image.open(ctx.assets_dir / filename) as bundled, Image.open(generated) as expected:
        if target == "windows":
            assert bundled.ico.sizes() == expected.ico.sizes()
            frames = [(bundled.ico.getimage(size), expected.ico.getimage(size)) for size in expected.ico.sizes()]
        else:
            assert bundled.info["sizes"] == expected.info["sizes"]
            frames = [(bundled.icns.getimage(size), expected.icns.getimage(size)) for size in expected.info["sizes"]]
        for actual, reference in frames:
            assert actual.size == reference.size
            assert actual.convert("RGBA").tobytes() == reference.convert("RGBA").tobytes()


def test_icon_regeneration_keeps_png_source_unchanged(tmp_path):
    from PIL import Image

    icon_assets = _load_module("icon_assets_regenerate_test", ROOT_DIR / ".tools" / "icon_assets.py")
    source = tmp_path / "logo.png"
    Image.new("RGBA", (1024, 1024), (10, 170, 120, 255)).save(source)
    original = source.read_bytes()

    icon_assets.update_bundled_icons(tmp_path)

    assert source.read_bytes() == original
    assert (tmp_path / "logo.ico").is_file()
    assert (tmp_path / "icon.icns").is_file()


def test_windows_installer_finds_per_user_inno_setup(monkeypatch, tmp_path):
    windows_builder = _load_module("build_windows_inno_test", ROOT_DIR / ".tools" / "build_windows.py")
    compiler = tmp_path / "Programs" / "Inno Setup 6" / "ISCC.exe"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(windows_builder.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "is_file", lambda path: path == compiler)

    assert windows_builder.find_iscc() == compiler


def test_windows_installer_shortcuts_follow_executable_icon(tmp_path):
    windows_builder = _load_module("build_windows_icon_test", ROOT_DIR / ".tools" / "build_windows.py")
    script = windows_builder.render_iss(
        exe_path=tmp_path / "TensaLauncher.exe",
        icon_path=tmp_path / "TensaLauncher.ico",
        output_dir=tmp_path,
        output_name="TensaLauncherInstaller",
    )

    assert "SetupIconFile=" in script
    assert r"UninstallDisplayIcon={app}\{#MyAppExeName}" in script
    assert script.count(r'IconFilename: "{app}\{#MyAppExeName}"') == 2
    assert "MyAppIconName" not in script
    assert '.ico"; DestDir:' not in script


def test_windows_build_emits_base_executable(tmp_path):
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


def test_windows_sandbox_helper_runs_exe():
    script = (ROOT_DIR / ".tools" / "run_exe_sandbox.ps1").read_text(encoding="utf-8")

    assert "TensaLauncher.exe" in script
    assert "TensaLauncher.wsb" in script


def test_windows_sandbox_helper_applies_application_control_workaround():
    script = (ROOT_DIR / ".tools" / "run_exe_sandbox.ps1").read_text(encoding="utf-8")

    assert "VerifiedAndReputablePolicyState" in script
    assert "EnableWebContentEvaluation" in script
    assert "CiTool.exe" in script
    assert "<ProtectedClient>Disable</ProtectedClient>" in script


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


@pytest.mark.parametrize("failure", [False, True])
def test_windows_signing_redacts_password_in_logs_and_errors(monkeypatch, tmp_path, capsys, failure):
    build_tool = _load_build_tool()
    builder = _load_module("build_windows_redaction_test", ROOT_DIR / ".tools" / "build_windows.py")
    ctx = _build_context(build_tool, target="windows")
    cert = tmp_path / "codesign.pfx"
    cert.write_bytes(b"cert")
    secret = "test-password-not-for-logs"
    monkeypatch.setenv("TENSALAUNCHER_WINDOWS_CERT_PATH", str(cert))
    monkeypatch.setenv("TENSALAUNCHER_WINDOWS_CERT_PASSWORD", secret)
    monkeypatch.setattr(builder, "find_windows_sdk_tool", lambda _name: tmp_path / "signtool.exe")

    def run(command, **kwargs):
        assert command[command.index("/p") + 1] == secret
        if failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_tool.subprocess, "run", run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError) as raised:
            builder.sign_windows_artifact_if_configured(ctx, tmp_path / "launcher.exe")
        assert secret not in str(raised.value)
    else:
        builder.sign_windows_artifact_if_configured(ctx, tmp_path / "launcher.exe")
    output = capsys.readouterr().out
    assert secret not in output
    assert "***" in output


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
    assert not any("flet_desktop/app" in option for option in commands[0])


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
    assert not any("flet_desktop/app" in option for option in commands[0])


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
        app_dir = desktop_entry.parent
        icon = app_dir / f"{ctx.app_name}.png"
        assert icon.read_bytes() == (app_dir / ".DirIcon").read_bytes()
        with Image.open(icon) as image:
            assert image.size == (512, 512)
            assert image.getpixel((0, 0))[3] == 0
        (ctx.target_output_dir / f"{ctx.executable_name}-x86_64.AppImage").write_text("artifact", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ctx.run = fake_run

    artifact = linux_builder.build_appimage(ctx, binary)

    assert artifact.name == "TensaLauncher-x86_64.AppImage"
    assert "TENSALAUNCHER_APP_BASE" not in captured["app_run"]
    assert "Name=TensaLauncher" in captured["desktop_entry"]
    assert "X-AppImage-Name=TensaLauncher" in captured["desktop_entry"]
    assert "Icon=TensaLauncher" in captured["desktop_entry"]
