from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from launcher.platform.system import SystemService


def test_system_java_scan_includes_java_home_and_path(tmp_path: Path, monkeypatch):
    java_home = tmp_path / "jdk-21"
    path_java = tmp_path / "path-jdk" / "bin" / "javaw.exe"
    java_home_java = java_home / "bin" / "java.exe"
    java_home_java.parent.mkdir(parents=True)
    path_java.parent.mkdir(parents=True)
    java_home_java.write_text("", encoding="utf-8")
    path_java.write_text("", encoding="utf-8")

    monkeypatch.setenv("JAVA_HOME", str(java_home))
    monkeypatch.setenv("PATH", str(path_java.parent))
    monkeypatch.setattr("minecraft_launcher_lib.runtime.get_installed_jvm_runtimes", lambda *_args: [])
    monkeypatch.setattr(SystemService, "_common_java_roots", lambda _self: [])
    path_service = SimpleNamespace(
        paths=SimpleNamespace(
            app_dir=tmp_path / "app",
            app_state_dir=tmp_path,
            minecraft_dir=tmp_path / "minecraft",
        )
    )

    found = SystemService(path_service).get_all_java()
    found_paths = {Path(path) for entry in found for path in entry.values()}

    assert java_home_java in found_paths
    assert path_java in found_paths


def test_system_java_scan_accepts_case_insensitive_executable_names(tmp_path: Path, monkeypatch):
    java_home = tmp_path / "jdk-21"
    java_home_java = java_home / "bin" / "Java.EXE"
    java_home_java.parent.mkdir(parents=True)
    java_home_java.write_text("", encoding="utf-8")

    monkeypatch.setenv("JAVA_HOME", str(java_home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("minecraft_launcher_lib.runtime.get_installed_jvm_runtimes", lambda *_args: [])
    monkeypatch.setattr(SystemService, "_common_java_roots", lambda _self: [])
    path_service = SimpleNamespace(
        paths=SimpleNamespace(
            app_dir=tmp_path / "app",
            app_state_dir=tmp_path,
            minecraft_dir=tmp_path / "minecraft",
        )
    )

    found = SystemService(path_service).get_all_java()
    found_paths = {Path(path) for entry in found for path in entry.values()}

    assert java_home_java in found_paths


def test_system_java_scan_generates_readable_launcher_runtime_label(tmp_path: Path, monkeypatch):
    runtime_name = "java-runtime-delta"
    java_home = tmp_path / "minecraft" / "runtime" / runtime_name / "windows-x64" / runtime_name
    java_path = java_home / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")
    (java_home / "release").write_text(
        'IMPLEMENTOR="Microsoft"\nJAVA_VERSION="21.0.7"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("JAVA_HOME", "")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("minecraft_launcher_lib.runtime.get_installed_jvm_runtimes", lambda *_args: [runtime_name])
    monkeypatch.setattr("minecraft_launcher_lib.runtime.get_executable_path", lambda *_args: str(java_path))
    monkeypatch.setattr(
        "minecraft_launcher_lib.runtime.get_jvm_runtime_information",
        lambda *_args: {"name": runtime_name},
    )
    monkeypatch.setattr(SystemService, "_common_java_roots", lambda _self: [])
    path_service = SimpleNamespace(
        paths=SimpleNamespace(
            app_dir=tmp_path / "app",
            app_state_dir=tmp_path,
            minecraft_dir=tmp_path / "minecraft",
        )
    )

    found = SystemService(path_service).get_all_java()

    assert found == [{"Launcher Java 21.0.7 (java-runtime-delta)": str(java_path.resolve())}]
