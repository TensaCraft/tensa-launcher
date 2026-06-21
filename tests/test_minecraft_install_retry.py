from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from requests.exceptions import SSLError

from launcher.core.minecraft_install import (
    format_install_error,
    install_minecraft_version_with_retries,
    is_java_runtime_process_failure,
    is_retryable_install_error,
)


def test_minecraft_install_retries_transient_tls_eof(tmp_path):
    calls: list[tuple[str, str, object]] = []
    sleeps: list[float] = []

    def fake_install(*, version: str, minecraft_directory: str, callback=None) -> None:
        calls.append((version, minecraft_directory, callback))
        if len(calls) < 3:
            raise SSLError(
                "HTTPSConnectionPool(host='libraries.minecraft.net', port=443): "
                "Max retries exceeded with url: /com/azure/azure-json/1.4.0/azure-json-1.4.0.jar "
                "(Caused by SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred "
                "in violation of protocol (_ssl.c:1032)')))"
            )

    callback = {"setStatus": lambda _status: None}
    install_minecraft_version_with_retries(
        "1.21.1",
        tmp_path / "minecraft",
        callback=callback,
        attempts=3,
        retry_delay=0.25,
        sleep=sleeps.append,
        installer=fake_install,
    )

    assert calls == [
        ("1.21.1", str(tmp_path / "minecraft"), callback),
        ("1.21.1", str(tmp_path / "minecraft"), callback),
        ("1.21.1", str(tmp_path / "minecraft"), callback),
    ]
    assert sleeps == [0.25, 0.25]


def test_minecraft_install_creates_target_directory_before_installer(tmp_path):
    minecraft_dir = tmp_path / "fresh" / "minecraft"
    observed: list[bool] = []

    def fake_install(*, version: str, minecraft_directory: str, callback=None) -> None:
        observed.append(Path(minecraft_directory).is_dir())

    install_minecraft_version_with_retries(
        "26.1.2",
        minecraft_dir,
        attempts=1,
        installer=fake_install,
    )

    assert observed == [True]


def test_minecraft_install_does_not_retry_non_transient_errors(tmp_path):
    calls = 0

    def fake_install(*, version: str, minecraft_directory: str, callback=None) -> None:
        nonlocal calls
        calls += 1
        raise ValueError(f"No version data found for the specified version: {Path(version).name}")

    with pytest.raises(ValueError):
        install_minecraft_version_with_retries(
            "missing-version",
            tmp_path / "minecraft",
            attempts=3,
            retry_delay=0,
            installer=fake_install,
        )

    assert calls == 1


def test_minecraft_install_retries_missing_library_files():
    missing_library = (
        "C:/Users/Player/AppData/Local/TensaLauncher/minecraft/libraries/com/google/guava/"
        "listenablefuture/9999.0-empty-to-avoid-conflict-with-guava/"
        "listenablefuture-9999.0-empty-to-avoid-conflict-with-guava.jar"
    )

    assert is_retryable_install_error(FileNotFoundError(2, "No such file or directory", missing_library))


def test_java_runtime_process_failure_detects_windows_dll_loader_codes():
    assert is_java_runtime_process_failure(
        subprocess.CalledProcessError(
            3221225781,
            ["java.exe", "-version"],
        )
    )
    assert is_java_runtime_process_failure(
        subprocess.CalledProcessError(
            -1073741515,
            ["java.exe", "-version"],
        )
    )


def test_java_runtime_process_failure_detects_visual_cpp_runtime_message():
    assert is_java_runtime_process_failure(
        RuntimeError("The code execution cannot proceed because VCRUNTIME140.dll was not found.")
    )


def test_install_error_formatter_includes_external_process_streams():
    class ExternalProgramError(RuntimeError):
        pass

    exc = ExternalProgramError("installer failed")
    exc.output = b"installer stdout"
    exc.stderr = "installer stderr"

    formatted = format_install_error(exc)

    assert "installer stdout" in formatted
    assert "installer stderr" in formatted


def test_install_error_formatter_includes_external_program_stdout_attribute():
    class ExternalProgramError(RuntimeError):
        pass

    exc = ExternalProgramError("installer failed")
    exc.command = ["java.exe", "-jar", "fabric-installer.jar"]
    exc.stdout = b"fabric stdout"
    exc.stderr = b"fabric stderr"

    formatted = format_install_error(exc)

    assert "fabric-installer.jar" in formatted
    assert "fabric stdout" in formatted
    assert "fabric stderr" in formatted
