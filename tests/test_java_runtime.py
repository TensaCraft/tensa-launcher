from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess

from launcher.application.java_runtime import JavaRuntimeService
from launcher.platform.java_process import java_subprocess_kwargs, launcher_java_path


class DummyLogger:
    def debug(self, *_args, **_kwargs) -> None:
        return None

    info = debug
    warning = debug
    error = debug


def _patch_runtime_lookup(monkeypatch, runtime_name: str, java_path: Path) -> None:
    monkeypatch.setattr(
        "minecraft_launcher_lib.runtime.get_version_runtime_information",
        lambda *_args, **_kwargs: {"name": runtime_name},
    )
    monkeypatch.setattr(
        "minecraft_launcher_lib.runtime.get_executable_path",
        lambda *_args, **_kwargs: str(java_path) if java_path.exists() else None,
    )


def _write_runtime_manifest(platform_dir: Path, runtime_name: str, include_jawt: bool) -> None:
    runtime_dir = platform_dir / runtime_name
    lines = [_runtime_manifest_line(runtime_dir, "bin/java.exe")]
    if include_jawt:
        lines.append(_runtime_manifest_line(runtime_dir, "lib/jawt.lib"))
    (platform_dir / f"{runtime_name}.sha1").write_text("".join(lines), encoding="utf-8")


def _runtime_manifest_line(runtime_dir: Path, relative_path: str, sha1: str | None = None) -> str:
    file_path = runtime_dir / relative_path
    digest = sha1
    if digest is None:
        digest = hashlib.sha1(file_path.read_bytes()).hexdigest() if file_path.exists() else "0" * 40
    return f"{relative_path} /#// {digest} 1\n"


def _write_windows_loader_dlls(bin_dir: Path) -> None:
    (bin_dir / "jli.dll").write_bytes(b"dll")
    (bin_dir / "vcruntime140.dll").write_bytes(b"dll")


def test_java_runtime_extracts_minecraft_versions():
    service = JavaRuntimeService(".", DummyLogger())

    assert service.extract_minecraft_version("1.20.1") == "1.20.1"
    assert service.extract_minecraft_version("fabric-loader-0.15.11-1.20.1") == "1.20.1"
    assert service.extract_minecraft_version("fabric-loader-0.17.0-beta-25w20a") == "25w20a"
    assert service.extract_minecraft_version("quilt-loader-0.30.0-beta.7-26.2-snapshot-7") == "26.2-snapshot-7"
    assert service.extract_minecraft_version("1.20.1-forge-47.2.0") == "1.20.1"
    assert service.extract_minecraft_version("neoforge-20.4.170-beta") == "20.4.170-beta"


def test_java_runtime_finds_java_executable(tmp_path: Path):
    runtime_dir = tmp_path / "runtime" / "java-runtime" / "bin"
    runtime_dir.mkdir(parents=True)
    java_path = runtime_dir / "java.exe"
    java_path.write_bytes(b"exe")

    found = JavaRuntimeService.find_java_executable(tmp_path)

    assert found == java_path


def test_managed_java_processes_ignore_global_java_options(monkeypatch, tmp_path: Path):
    java_path = tmp_path / "runtime" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Djavax.net.ssl.trustStore=NUL")
    monkeypatch.setenv("_JAVA_OPTIONS", "-Dbroken=true")

    kwargs = java_subprocess_kwargs(java_path)

    assert "JAVA_TOOL_OPTIONS" not in kwargs["env"]
    assert "_JAVA_OPTIONS" not in kwargs["env"]

    with launcher_java_path(java_path):
        assert "JAVA_TOOL_OPTIONS" not in os.environ
        assert "_JAVA_OPTIONS" not in os.environ

    assert os.environ["JAVA_TOOL_OPTIONS"] == "-Djavax.net.ssl.trustStore=NUL"
    assert os.environ["_JAVA_OPTIONS"] == "-Dbroken=true"


def test_java_runtime_uses_fallback_scan_when_library_path_missing(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime"
    runtime_dir = tmp_path / "runtime" / runtime_name / "bin"
    runtime_dir.mkdir(parents=True)
    java_path = runtime_dir / "javaw.exe"
    java_path.write_bytes(b"exe")

    monkeypatch.setattr(
        "minecraft_launcher_lib.runtime.get_executable_path",
        lambda *_args, **_kwargs: None,
    )

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.get_executable_path(runtime_name) == str(java_path)


def test_java_runtime_presence_rejects_existing_executable_without_sha1_manifest(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-epsilon"
    runtime_dir = tmp_path / "runtime" / runtime_name / "windows-x64" / runtime_name / "bin"
    runtime_dir.mkdir(parents=True)
    java_path = runtime_dir / "java.exe"
    java_path.write_bytes(b"exe")

    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.has_runtime("26.1.2", "26.1.2") is False


def test_java_runtime_presence_rejects_existing_executable_with_missing_manifest_file(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-epsilon"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    java_path = bin_dir / "java.exe"
    java_path.write_bytes(b"exe")
    _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)
    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.has_runtime("26.1.2", "26.1.2") is False


def test_java_runtime_presence_uses_existing_executable_with_hash_mismatch(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-delta"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    java_path = bin_dir / "java.exe"
    java_path.write_bytes(b"exe")
    (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
    (platform_dir / f"{runtime_name}.sha1").write_text(
        "".join(
            [
                _runtime_manifest_line(runtime_dir, "bin/java.exe", sha1="f" * 40),
                _runtime_manifest_line(runtime_dir, "lib/jawt.lib"),
            ]
        ),
        encoding="utf-8",
    )
    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.runtime_is_complete(runtime_name, java_path) is False
    assert service.has_runtime("1.21.1", "1.21.1") is False


def test_java_runtime_reinstalls_existing_executable_with_incomplete_manifest(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-epsilon"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    java_path = bin_dir / "java.exe"
    java_path.write_bytes(b"exe")

    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    installs = []

    def fake_install(_runtime_name, _minecraft_dir, callback=None):
        installs.append(_runtime_name)
        bin_dir.mkdir(parents=True, exist_ok=True)
        java_path.write_bytes(b"exe")
        _write_windows_loader_dlls(bin_dir)
        (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
        _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    monkeypatch.setattr("minecraft_launcher_lib.runtime.install_jvm_runtime", fake_install)
    monkeypatch.setattr(
        JavaRuntimeService,
        "_java_executable_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("java process validation must not run")),
    )

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.ensure_runtime("26.1.2", "26.1.2") == str(java_path)
    assert installs == [runtime_name]


def test_java_runtime_install_succeeds_when_installer_creates_executable(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-epsilon"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    java_path = bin_dir / "java.exe"

    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    installs = []

    def fake_install(_runtime_name, _minecraft_dir, callback=None):
        installs.append(_runtime_name)
        bin_dir.mkdir(parents=True, exist_ok=True)
        java_path.write_bytes(b"exe")
        _write_windows_loader_dlls(bin_dir)
        if len(installs) == 1:
            _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)
            return
        (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
        _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    monkeypatch.setattr("minecraft_launcher_lib.runtime.install_jvm_runtime", fake_install)
    monkeypatch.setattr(
        JavaRuntimeService,
        "_java_executable_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("java process validation must not run")),
    )

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.install_runtime("26.1.2", "26.1.2") is True
    assert installs == [runtime_name, runtime_name]


def test_java_runtime_removes_empty_runtime_before_reinstall(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-epsilon"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    (runtime_dir / "stale.txt").write_text("partial", encoding="utf-8")
    java_path = bin_dir / "java.exe"

    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)
    installs = []

    def fake_install(_runtime_name, _minecraft_dir, callback=None):
        installs.append(_runtime_name)
        assert not (runtime_dir / "stale.txt").exists()
        bin_dir.mkdir(parents=True, exist_ok=True)
        java_path.write_bytes(b"exe")
        _write_windows_loader_dlls(bin_dir)
        (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
        _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    monkeypatch.setattr("minecraft_launcher_lib.runtime.install_jvm_runtime", fake_install)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.ensure_runtime("26.1.2", "26.1.2") == str(java_path)
    assert installs == [runtime_name]


def test_java_runtime_reuses_existing_executable_without_process_validation(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-delta"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    java_path = bin_dir / "java.exe"
    java_path.write_bytes(b"exe")
    _write_windows_loader_dlls(bin_dir)
    (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
    _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    _patch_runtime_lookup(monkeypatch, runtime_name, java_path)

    installs = []

    def fake_install(_runtime_name, _minecraft_dir, callback=None):
        installs.append(_runtime_name)
        bin_dir.mkdir(parents=True, exist_ok=True)
        java_path.write_bytes(b"exe")
        _write_windows_loader_dlls(bin_dir)
        (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
        _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    monkeypatch.setattr("minecraft_launcher_lib.runtime.install_jvm_runtime", fake_install)
    monkeypatch.setattr(
        JavaRuntimeService,
        "_java_executable_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ensure_runtime must not execute java.exe during normal install flow")
        ),
    )

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.ensure_runtime("1.21.1", "1.21.1") == str(java_path)
    assert installs == []


def test_java_runtime_validation_suppresses_windows_error_dialogs(tmp_path: Path, monkeypatch):
    java_path = tmp_path / "runtime" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    events = []

    @contextmanager
    def fake_suppress():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fake_run(*args, **_kwargs):
        events.append("run")
        return subprocess.CompletedProcess(args[0], 0, b"", b"java version")

    monkeypatch.setattr("launcher.application.java_runtime.suppress_windows_error_dialogs", fake_suppress)
    monkeypatch.setattr("launcher.application.java_runtime.subprocess.run", fake_run)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service._java_executable_runs(java_path) is True
    assert events == ["enter", "run", "exit"]


def test_java_runtime_validation_runs_from_java_bin_with_local_path_first(tmp_path: Path, monkeypatch):
    java_path = tmp_path / "runtime" / "java-runtime-delta" / "windows-x64" / "java-runtime-delta" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, b"", b"java version")

    monkeypatch.setattr("launcher.application.java_runtime.subprocess.run", fake_run)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service._java_executable_runs(java_path) is True
    assert Path(captured["kwargs"]["cwd"]) == java_path.parent
    assert captured["kwargs"]["env"]["PATH"].split(os.pathsep)[0] == str(java_path.parent)


def test_java_runtime_validation_exports_java_home_and_server_path(tmp_path: Path, monkeypatch):
    java_path = tmp_path / "runtime" / "java-runtime-delta" / "windows-x64" / "java-runtime-delta" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, b"", b"java version")

    monkeypatch.setattr("launcher.application.java_runtime.subprocess.run", fake_run)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service._java_executable_runs(java_path) is True
    path_entries = captured["kwargs"]["env"]["PATH"].split(os.pathsep)
    assert path_entries[:2] == [str(java_path.parent), str(java_path.parent / "server")]
    assert captured["kwargs"]["env"]["JAVA_HOME"] == str(java_path.parent.parent)


def test_windows_java_runtime_rejects_missing_local_loader_dlls(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-delta"
    platform_dir = tmp_path / "runtime" / runtime_name / "windows-x64"
    runtime_dir = platform_dir / runtime_name
    bin_dir = runtime_dir / "bin"
    bin_dir.mkdir(parents=True)
    java_path = bin_dir / "java.exe"
    java_path.write_bytes(b"exe")
    (runtime_dir / "lib").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "lib" / "jawt.lib").write_bytes(b"lib")
    _write_runtime_manifest(platform_dir, runtime_name, include_jawt=True)

    monkeypatch.setattr("launcher.application.java_runtime.sys.platform", "win32")
    monkeypatch.setattr(JavaRuntimeService, "_java_executable_runs", lambda *_args, **_kwargs: True)

    service = JavaRuntimeService(tmp_path, DummyLogger())

    assert service.runtime_is_usable(runtime_name, java_path) is False

    (bin_dir / "jli.dll").write_bytes(b"dll")
    assert service.runtime_is_usable(runtime_name, java_path) is False

    (bin_dir / "vcruntime140.dll").write_bytes(b"dll")
    assert service.runtime_is_usable(runtime_name, java_path) is True
