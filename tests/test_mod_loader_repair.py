from __future__ import annotations

from contextlib import contextmanager
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

import launcher.core.integrity as integrity_module
from launcher.application.java_runtime import JavaRuntimeService
from launcher.core.loaders.minecraft import MinecraftLoader
from launcher.core.loaders.mod_loader import NeoForgeLoader
from launcher.core.versions import Version
from launcher.shared.app_context import AppContext


def _write_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_integrity_skips_libraries_excluded_by_minecraft_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(
        integrity_module.minecraft_launcher_helper.platform,
        "architecture",
        lambda: ("64bit", ""),
    )
    version_dir = tmp_path / "versions" / "1.21.1"
    _write_json(
        version_dir / "1.21.1.json",
        """
        {
          "id": "1.21.1",
          "type": "release",
          "mainClass": "net.minecraft.client.main.Main",
          "libraries": [
            {
              "name": "example:client32:1.0.0",
              "rules": [
                {"action": "allow", "os": {"arch": "x86"}}
              ],
              "downloads": {
                "artifact": {
                  "path": "example/client32/1.0.0/client32-1.0.0.jar"
                }
              }
            }
          ]
        }
        """,
    )

    assert integrity_module.IntegrityChecker(tmp_path)._check_libraries("1.21.1") is True


def test_integrity_requires_libraries_included_by_minecraft_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(
        integrity_module.minecraft_launcher_helper.platform,
        "architecture",
        lambda: ("32bit", ""),
    )
    version_dir = tmp_path / "versions" / "1.21.1"
    _write_json(
        version_dir / "1.21.1.json",
        """
        {
          "id": "1.21.1",
          "type": "release",
          "mainClass": "net.minecraft.client.main.Main",
          "libraries": [
            {
              "name": "example:client32:1.0.0",
              "rules": [
                {"action": "allow", "os": {"arch": "x86"}}
              ],
              "downloads": {
                "artifact": {
                  "path": "example/client32/1.0.0/client32-1.0.0.jar"
                }
              }
            }
          ]
        }
        """,
    )

    assert integrity_module.IntegrityChecker(tmp_path)._check_libraries("1.21.1") is False


def test_neoforge_verify_does_not_rerun_installer_when_generated_client_artifact_is_missing(
    fake_app,
    monkeypatch,
    tmp_path,
):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    version_id = "neoforge-21.1.228"
    mc_version = "1.21.1"
    neoform_version = "20240808.144430"
    version_dir = minecraft_dir / "versions" / version_id
    version_dir.mkdir(parents=True)
    (version_dir / f"{version_id}.jar").write_bytes(b"client jar")
    _write_json(
        version_dir / f"{version_id}.json",
        """
        {
          "id": "neoforge-21.1.228",
          "type": "release",
          "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
          "inheritsFrom": "1.21.1",
          "libraries": [],
          "arguments": {
            "game": [
              "--fml.neoForgeVersion",
              "21.1.228",
              "--fml.mcVersion",
              "1.21.1",
              "--fml.neoFormVersion",
              "20240808.144430",
              "--launchTarget",
              "forgeclient"
            ],
            "jvm": []
          }
        }
        """,
    )

    base_dir = minecraft_dir / "versions" / mc_version
    base_dir.mkdir(parents=True)
    (base_dir / f"{mc_version}.jar").write_bytes(b"base client jar")
    _write_json(
        base_dir / f"{mc_version}.json",
        """
        {
          "id": "1.21.1",
          "type": "release",
          "mainClass": "net.minecraft.client.main.Main",
          "libraries": []
        }
        """,
    )

    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _minecraft_dir: [{"id": version_id}, {"id": mc_version}],
    )

    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/LauncherJava/bin/java.exe"
    monkeypatch.setattr(loader.integrity_checker, "_check_java_runtime", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(loader.integrity_checker, "check_version", lambda *_args, **_kwargs: {"valid": True})

    install_calls = []

    def fake_install(**kwargs):
        install_calls.append(kwargs)
        minecraft_directory = kwargs["minecraft_directory"]
        generated_dir = Path(minecraft_directory) / "libraries" / "net" / "neoforged" / "neoforge" / "21.1.228"
        generated_dir.mkdir(parents=True, exist_ok=True)
        (generated_dir / "neoforge-21.1.228-client.jar").write_bytes(b"patched client")
        mc_dir = (
            Path(minecraft_directory)
            / "libraries"
            / "net"
            / "minecraft"
            / "client"
            / f"{mc_version}-{neoform_version}"
        )
        mc_dir.mkdir(parents=True, exist_ok=True)
        (mc_dir / f"client-{mc_version}-{neoform_version}-extra.jar").write_bytes(b"client extra")

    monkeypatch.setattr(loader.loader, "install", fake_install)

    assert loader.verify_and_repair_version(version_id, mc_version) is True
    assert install_calls == []


def test_install_mod_loader_uses_requested_neoforge_version_instead_of_latest(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            raise AssertionError("latest loader version should not be used when API pins a build")

        def get_installed_version(self, mc_version, loader_version):
            assert mc_version == "1.21.1"
            assert loader_version == "21.1.228"
            return "neoforge-21.1.228"

        def install(self, **kwargs):
            install_calls.append(kwargs)

    install_calls = []
    install_minecraft_calls = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(
        loader,
        "_install_minecraft_if_needed",
        lambda *args, **kwargs: install_minecraft_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())

    installed_version, loader_version = loader._install_mod_loader(
        "1.21.1",
        "neoforge",
        requested_loader_version="21.1.228",
    )

    assert installed_version == "neoforge-21.1.228"
    assert loader_version == "21.1.228"
    assert install_minecraft_calls == [(("1.21.1",), {"force_check": False})]
    assert install_calls == [
        {
            "minecraft_version": "1.21.1",
            "minecraft_directory": str(minecraft_dir),
            "loader_version": "21.1.228",
            "callback": {},
            "java": "C:/Java/bin/java.exe",
        }
    ]


def test_version_install_does_not_pass_saved_java_to_loader_installer(monkeypatch):
    install_calls = []

    class FakeLoader:
        def install(self, *args, **kwargs):
            install_calls.append((args, kwargs))

    monkeypatch.setattr("launcher.core.Launcher.get_loader", lambda _loader_name: FakeLoader())
    version = Version(
        "demo",
        {
            "client": "NeoForge",
            "version": "1.21.1",
            "loader_version": "21.1.228",
            "options": {"executablePath": "C:/SystemJava/bin/java.exe"},
        },
    )

    version.install()

    assert len(install_calls) == 1
    assert install_calls[0][1] == {"loader_version": "21.1.228"}


def test_install_mod_loader_ignores_external_java_path_for_installer(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    fake_app.util.minecraft_dir = minecraft_dir
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.228"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.228"

        def install(self, **kwargs):
            install_calls.append(kwargs)

    install_calls = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/LauncherJava/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    loader._install_mod_loader(
        "1.21.1",
        "neoforge",
        java_path="C:/SystemJava/bin/java.exe",
    )

    assert [call["java"] for call in install_calls] == ["C:/LauncherJava/bin/java.exe"]


def test_install_mod_loader_creates_minecraft_directory_before_fabric_installer(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "fresh" / "minecraft"
    games_dir = tmp_path / "games"
    games_dir.mkdir(exist_ok=True)
    fake_app.util.minecraft_dir = minecraft_dir
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "0.19.2"

        def get_installed_version(self, _mc_version, _loader_version):
            return "fabric-loader-0.19.2-26.1.2"

        def install(self, **_kwargs):
            install_calls.append(_kwargs)
            assert minecraft_dir.is_dir()

    install_calls = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    loader._install_mod_loader("26.1.2", "fabric", requested_loader_version="0.19.2")

    assert len(install_calls) == 1


def test_fabric_installer_process_output_is_captured(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeBase:
        def get_installer_url(self, _mc_version, _loader_version):
            return "https://example.invalid/fabric-installer.jar"

    class FakeModLoader:
        _base = FakeBase()

        def get_installed_version(self, _mc_version, _loader_version):
            return "fabric-loader-0.19.2-26.1.2"

    observed_kwargs = {}

    def fake_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"installer")

    def fake_run(command, **kwargs):
        observed_kwargs.update(kwargs)
        raise subprocess.CalledProcessError(
            1,
            command,
            output=b"fabric stdout",
            stderr=b"fabric stderr",
        )

    loader = NeoForgeLoader()
    loader.MOD_LOADER_INSTALL_ATTEMPTS = 1
    loader.install_callback = lambda: {}
    monkeypatch.setattr("launcher.core.loaders.base.download_file", fake_download)
    monkeypatch.setattr("launcher.core.loaders.base.subprocess.run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        loader._run_mod_loader_install(
            FakeModLoader(),
            mc_version="26.1.2",
            loader_name="fabric",
            loader_version="0.19.2",
            java_path="C:/Java/bin/java.exe",
        )

    assert observed_kwargs["stdout"] is subprocess.PIPE
    assert observed_kwargs["stderr"] is subprocess.PIPE
    formatted = str(exc_info.value)
    assert "fabric stdout" in formatted
    assert "fabric stderr" in formatted


def test_fabric_quilt_installer_command_uses_managed_java_path(tmp_path):
    command = NeoForgeLoader._fabric_quilt_installer_command(
        loader_name="fabric",
        java_path="C:/Java/bin/java.exe",
        installer_path=tmp_path / "fabric-installer.jar",
        minecraft_directory=tmp_path / "minecraft",
        mc_version="1.21.1",
        loader_version="0.16.14",
    )

    assert command[0] == "C:/Java/bin/java.exe"
    assert command[1] == "-jar"


def test_neoforge_installer_prefetches_libraries_and_uses_isolated_java_env(
    fake_app,
    monkeypatch,
    tmp_path,
):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    java_path = tmp_path / "runtime" / "java-runtime-delta" / "bin" / "java.exe"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Djavax.net.ssl.trustStore=NUL")
    monkeypatch.setenv("_JAVA_OPTIONS", "-Dbroken=true")

    library = {
        "name": "cpw.mods:bootstraplauncher:2.0.2",
        "downloads": {
            "artifact": {
                "url": "https://example.invalid/bootstraplauncher.jar",
                "sha1": "0" * 40,
                "path": "cpw/mods/bootstraplauncher/2.0.2/bootstraplauncher-2.0.2.jar",
            }
        },
    }

    class FakeModLoader:
        def get_installer_url(self, _mc_version, _loader_version):
            return "https://example.invalid/neoforge-installer.jar"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.232"

        def install(self, **_kwargs):
            raise AssertionError("real NeoForge must use the controlled installer path")

    def fake_download(_url, path, **_kwargs):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "install_profile.json",
                json.dumps(
                    {
                        "json": "/version.json",
                        "libraries": [library, library],
                    }
                ),
            )
            archive.writestr(
                "version.json",
                json.dumps(
                    {
                        "id": "neoforge-21.1.232",
                        "type": "release",
                        "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
                        "inheritsFrom": "1.21.1",
                        "libraries": [library],
                    }
                ),
            )

    library_calls = []
    install_calls = []
    run_calls = []

    def fake_install_libraries(profile_id, libraries, minecraft_directory, callback):
        library_calls.append((profile_id, list(libraries), Path(minecraft_directory), callback))

    def fake_install_minecraft(version_id, minecraft_directory, **kwargs):
        install_calls.append((version_id, Path(minecraft_directory), kwargs))

    def fake_run(command, **kwargs):
        run_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    loader = NeoForgeLoader()
    loader.install_callback = lambda *_args, **_kwargs: {}
    monkeypatch.setattr("launcher.core.loaders.base.download_file", fake_download)
    monkeypatch.setattr("launcher.core.loaders.base.install_libraries", fake_install_libraries)
    monkeypatch.setattr("launcher.core.loaders.base.install_minecraft_version_with_retries", fake_install_minecraft)
    monkeypatch.setattr("launcher.core.loaders.base.subprocess.run", fake_run)

    loader._run_mod_loader_install(
        FakeModLoader(),
        mc_version="1.21.1",
        loader_name="neoforge",
        loader_version="21.1.232",
        java_path=str(java_path),
    )

    profile_path = minecraft_dir / "versions" / "neoforge-21.1.232" / "neoforge-21.1.232.json"
    assert profile_path.is_file()
    assert json.loads(profile_path.read_text(encoding="utf-8"))["inheritsFrom"] == "1.21.1"
    assert len(library_calls) == 1
    assert len(library_calls[0][1]) == 1
    assert install_calls == [
        (
            "neoforge-21.1.232",
            minecraft_dir,
            {"callback": {}, "attempts": loader.MINECRAFT_INSTALL_ATTEMPTS},
        )
    ]
    assert len(run_calls) == 1
    command, run_kwargs = run_calls[0]
    assert command[:2] == [str(java_path), "-jar"]
    assert run_kwargs["stdout"] is subprocess.PIPE
    assert run_kwargs["stderr"] is subprocess.PIPE
    assert run_kwargs["env"]["JAVA_HOME"] == str(java_path.parent.parent)
    assert "JAVA_TOOL_OPTIONS" not in run_kwargs["env"]
    assert "_JAVA_OPTIONS" not in run_kwargs["env"]
    version_dir = minecraft_dir / "versions" / "neoforge-21.1.232"
    assert (version_dir / ".tensalauncher-installed").is_file()
    assert not (version_dir / ".tensalauncher-installing").exists()


def test_fabric_installer_uses_python_profile_after_pkix_without_keytool_trust_store(
    fake_app,
    monkeypatch,
    tmp_path,
):
    assert not hasattr(JavaRuntimeService, "certifi_trust_store_args")

    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))

    class FakeBase:
        def get_installer_url(self, _mc_version, _loader_version):
            return "https://example.invalid/fabric-installer.jar"

    class FakeModLoader:
        _base = FakeBase()

        def get_installed_version(self, _mc_version, _loader_version):
            return "fabric-loader-0.19.2-26.1.2"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "fabric-loader-0.19.2-26.1.2",
                "inheritsFrom": "26.1.2",
                "libraries": [],
            }

    run_commands = []
    install_calls = []

    def fake_run(command, **kwargs):
        run_commands.append(command)
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr=b"PKIX path building failed: unable to find valid certification path to requested target",
        )

    def fake_download(_url, path, **_kwargs):
        Path(path).write_bytes(b"installer")

    def fake_install(version_id, minecraft_directory, **kwargs):
        install_calls.append((version_id, Path(minecraft_directory), kwargs))

    loader = NeoForgeLoader()
    loader.install_callback = lambda *_args, **_kwargs: {}
    monkeypatch.setattr("launcher.core.loaders.base.download_file", fake_download)
    monkeypatch.setattr("launcher.core.loaders.base.subprocess.run", fake_run)
    monkeypatch.setattr("launcher.core.loaders.base.requests.get", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr("launcher.core.loaders.base.install_minecraft_version_with_retries", fake_install)

    loader._install_fabric_quilt_loader_with_capture(
        FakeModLoader(),
        mc_version="26.1.2",
        loader_name="fabric",
        loader_version="0.19.2",
        java_path="C:/Java/bin/java.exe",
    )

    assert len(run_commands) == 1
    assert run_commands[0][1] == "-jar"
    profile_path = (
        minecraft_dir
        / "versions"
        / "fabric-loader-0.19.2-26.1.2"
        / "fabric-loader-0.19.2-26.1.2.json"
    )
    assert len(run_commands) == 1
    assert profile_path.is_file()
    assert json.loads(profile_path.read_text(encoding="utf-8"))["inheritsFrom"] == "26.1.2"
    assert install_calls == [
        (
            "fabric-loader-0.19.2-26.1.2",
            minecraft_dir,
            {"callback": {}, "attempts": loader.MINECRAFT_INSTALL_ATTEMPTS},
        )
    ]


def test_install_mod_loader_retries_incomplete_read_during_library_install(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.228"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.228"

        def install(self, **_kwargs):
            install_calls.append(_kwargs)
            if len(install_calls) == 1:
                raise IncompleteRead(b"partial", 42)

    install_calls = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    installed_version, loader_version = loader._install_mod_loader("1.21.1", "neoforge")

    assert installed_version == "neoforge-21.1.228"
    assert loader_version == "21.1.228"
    assert len(install_calls) == 2


def test_install_minecraft_installs_without_post_integrity_gate(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))

    install_calls = []
    check_calls = []

    def fake_install(version, minecraft_directory, **_kwargs):
        install_calls.append((version, Path(minecraft_directory)))

    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    monkeypatch.setattr(loader, "loader_exists", lambda _mc_version: False)
    monkeypatch.setattr("launcher.core.loaders.base.install_minecraft_version_with_retries", fake_install)
    monkeypatch.setattr(
        loader.integrity_checker,
        "check_version",
        lambda version_id, **kwargs: check_calls.append((version_id, kwargs)) or {"valid": True, "issues": []},
    )

    loader._install_minecraft_if_needed("1.21.1")

    assert install_calls == [("1.21.1", minecraft_dir)]
    assert check_calls == []


def test_vanilla_verify_trusts_installed_version_without_library_check(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))

    loader = MinecraftLoader()
    monkeypatch.setattr(loader.integrity_checker, "_is_version_installed", lambda _version_id: True)
    check_calls = []
    repairs = []

    def fake_check_version(version_id, mc_version=None, **kwargs):
        check_calls.append((version_id, mc_version, kwargs))
        raise AssertionError("launch-time verify must not scan all Minecraft libraries")

    monkeypatch.setattr(loader.integrity_checker, "check_version", fake_check_version)
    monkeypatch.setattr(
        loader,
        "_install_minecraft_if_needed",
        lambda version, *, force_check=False, operation=None: repairs.append((version, force_check, operation)),
    )

    assert loader.verify_and_repair_version("1.21.1") is True
    assert check_calls == []
    assert repairs == []


def test_install_mod_loader_repairs_base_minecraft_after_missing_library(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.232"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.232"

        def install(self, **_kwargs):
            install_calls.append(_kwargs)
            if len(install_calls) == 1:
                missing_library = (
                    minecraft_dir
                    / "libraries"
                    / "com"
                    / "google"
                    / "guava"
                    / "listenablefuture"
                    / "9999.0-empty-to-avoid-conflict-with-guava"
                    / "listenablefuture-9999.0-empty-to-avoid-conflict-with-guava.jar"
                )
                raise FileNotFoundError(2, "No such file or directory", str(missing_library))

    install_calls = []
    minecraft_repairs = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(
        loader,
        "_install_minecraft_if_needed",
        lambda *args, **kwargs: minecraft_repairs.append((args, kwargs)),
    )

    installed_version, loader_version = loader._install_mod_loader("1.21.1", "neoforge")

    assert installed_version == "neoforge-21.1.232"
    assert loader_version == "21.1.232"
    assert len(install_calls) == 2
    assert minecraft_repairs == [
        (("1.21.1",), {"force_check": False}),
        (("1.21.1",), {"force_check": True, "operation": None}),
    ]


def test_install_mod_loader_retries_neoforge_installer_process_failure(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.228"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.228"

        def install(self, **_kwargs):
            install_calls.append(_kwargs)
            if len(install_calls) == 1:
                raise subprocess.CalledProcessError(
                    1,
                    ["java.exe", "-jar", "neoforge-installer.jar", "--install-client"],
                    output=b"installer stdout",
                    stderr=b"installer stderr",
                )

    install_calls = []
    warnings = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("launcher.core.loaders.base.Logger.warning", lambda message: warnings.append(message))

    installed_version, loader_version = loader._install_mod_loader("1.21.1", "neoforge")

    assert installed_version == "neoforge-21.1.228"
    assert loader_version == "21.1.228"
    assert len(install_calls) == 2
    assert any("installer stderr" in message for message in warnings)


def test_install_mod_loader_refreshes_existing_neoforge_with_missing_libraries(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    version_dir = minecraft_dir / "versions" / "neoforge-21.1.232"
    version_dir.mkdir(parents=True)
    games_dir.mkdir(exist_ok=True)
    _write_json(
        version_dir / "neoforge-21.1.232.json",
        """
        {
          "id": "neoforge-21.1.232",
          "type": "release",
          "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
          "libraries": [
            {
              "name": "cpw.mods:bootstraplauncher:2.0.2",
              "downloads": {
                "artifact": {
                  "url": "https://example.invalid/bootstraplauncher.jar",
                  "sha1": "0000000000000000000000000000000000000000",
                  "path": "cpw/mods/bootstraplauncher/2.0.2/bootstraplauncher-2.0.2.jar"
                }
              }
            }
          ]
        }
        """,
    )
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.232"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.232"

        def install(self, **kwargs):
            install_calls.append(kwargs)

    install_calls = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    installed_version, loader_version = loader._install_mod_loader("1.21.1", "neoforge")

    assert installed_version == "neoforge-21.1.232"
    assert loader_version == "21.1.232"
    assert len(install_calls) == 1


def test_install_mod_loader_requires_managed_java_before_installer(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.228"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.228"

        def install(self, **_kwargs):
            raise AssertionError("loader installer must not run without Java")

    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: None
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="required Java runtime"):
        loader._install_mod_loader("1.21.1", "neoforge")


def test_install_mod_loader_repairs_managed_java_after_windows_loader_failure(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.230"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.230"

        def install(self, **kwargs):
            install_calls.append(kwargs)
            if len(install_calls) == 1:
                raise subprocess.CalledProcessError(
                    3221225781,
                    [kwargs["java"], "-jar", "neoforge-installer.jar", "--install-client"],
                )

    install_calls = []
    runtime_repairs = []
    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loader, "_get_version_java_path", lambda *_args, **_kwargs: "C:/broken/java.exe")
    monkeypatch.setattr(
        loader.runtime,
        "repair_runtime",
        lambda *args, **kwargs: runtime_repairs.append((args, kwargs)) or "C:/repaired/java.exe",
    )

    installed_version, loader_version = loader._install_mod_loader("1.21.1", "neoforge")

    assert installed_version == "neoforge-21.1.230"
    assert loader_version == "21.1.230"
    assert [call["java"] for call in install_calls] == ["C:/broken/java.exe", "C:/repaired/java.exe"]
    assert runtime_repairs


def test_install_mod_loader_suppresses_windows_error_dialogs(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    events = []

    @contextmanager
    def fake_suppress():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.230"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.230"

        def install(self, **_kwargs):
            events.append("install")

    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: "C:/Java/bin/java.exe"
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("launcher.core.loaders.base.suppress_windows_error_dialogs", fake_suppress)

    loader._install_mod_loader("1.21.1", "neoforge")

    assert events == ["enter", "install", "exit"]


def test_install_mod_loader_exposes_launcher_java_bin_to_installer_process(fake_app, monkeypatch, tmp_path):
    minecraft_dir = tmp_path / "minecraft"
    games_dir = tmp_path / "games"
    java_path = tmp_path / "runtime" / "java-runtime-delta" / "bin" / "java.exe"
    minecraft_dir.mkdir(exist_ok=True)
    games_dir.mkdir(exist_ok=True)
    java_path.parent.mkdir(parents=True)
    java_path.write_bytes(b"exe")
    AppContext.set(fake_app)
    monkeypatch.setattr("launcher.core.loaders.base.util.minecraft_dir", str(minecraft_dir))
    monkeypatch.setattr("launcher.core.loaders.base.util.games_path", str(games_dir))
    integrity_module.IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}

    observed_env = []

    class FakeModLoader:
        def get_latest_loader_version(self, _mc_version):
            return "21.1.230"

        def get_installed_version(self, _mc_version, _loader_version):
            return "neoforge-21.1.230"

        def install(self, **_kwargs):
            observed_env.append(
                {
                    "path": os.environ.get("PATH", "").split(os.pathsep)[:2],
                    "java_home": os.environ.get("JAVA_HOME"),
                }
            )

    loader = NeoForgeLoader()
    loader.install_callback = lambda: {}
    loader.get_version_java_path = lambda _mc_version: str(java_path)
    monkeypatch.setattr(loader, "_get_mod_loader_instance", lambda _loader_name: FakeModLoader())
    monkeypatch.setattr(loader, "_install_minecraft_if_needed", lambda *_args, **_kwargs: None)

    loader._install_mod_loader("1.21.1", "neoforge")

    assert observed_env == [
        {
            "path": [str(java_path.parent), str(java_path.parent / "server")],
            "java_home": str(java_path.parent.parent),
        }
    ]
