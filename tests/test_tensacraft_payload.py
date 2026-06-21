from __future__ import annotations

from types import SimpleNamespace

import pytest

from launcher.application.tensacraft_payload import TensaCraftPayloadService


def test_tensacraft_payload_applies_install_payload():
    version = SimpleNamespace(id="old", version="old", loader_version=None, force_update=False, image=None, options={})
    client_data = {
        "loader": "fabric",
        "version": "1.20.1",
        "loader_version": "0.16.0",
        "force_update": True,
        "image": "img",
        "options": {"foo": "bar"},
        "jvm_arguments": ["-Xmx4G"],
    }

    loader_name = TensaCraftPayloadService().apply_install_payload(version, version_key="tensa", client_data=client_data)

    assert loader_name == "fabric"
    assert version.id == "tensa"
    assert version.version == "1.20.1"
    assert version.loader_version == "0.16.0"
    assert version.force_update is True
    assert version.options["jvmArguments"] == ["-Xmx4G"]


def test_tensacraft_payload_enables_sync_when_force_update_endpoint_exists():
    version = SimpleNamespace(id="old", version="old", loader_version=None, force_update=False, image=None, options={})
    client_data = {
        "loader_id": "fabric",
        "minecraft_version": "1.21.11",
        "loader_version": "0.19.2",
        "force_update_endpoint": "https://gigabait.uk/api/mods/tensa-lite/force-update",
    }

    TensaCraftPayloadService().apply_install_payload(version, version_key="tensa-lite", client_data=client_data)

    assert version.force_update is True


def test_tensacraft_payload_detects_loader_change():
    version = SimpleNamespace(version="1.20.1", loader_version="0.15.0", loader="fabric")
    client_data = {"version": "1.20.1", "loader_version": "0.16.0", "loader": "fabric"}

    assert TensaCraftPayloadService().loader_changed(version, client_data) is True


def test_tensacraft_payload_accepts_installed_loader_identifier():
    version = SimpleNamespace(
        version="1.20.1",
        loader_version="0.16.0",
        loader="fabric-loader-0.16.0-1.20.1",
        client="TensaCraft",
    )
    client_data = {"version": "1.20.1", "loader_version": "0.16.0", "loader": "fabric"}

    assert TensaCraftPayloadService().loader_changed(version, client_data) is False


def test_tensacraft_payload_applies_api_defaults_when_requested():
    version = SimpleNamespace(options={"server": {"host": "existing"}}, image=None, force_update=False)
    client_data = {
        "options": {"server": {"host": "api"}, "memory": "4g"},
        "jvm_arguments": "-Xmx4G\n-XX:+UseG1GC",
        "image": "img",
        "force_update": True,
    }

    TensaCraftPayloadService().merge_sync_payload(
        version,
        client_data,
        java_path="C:/Java/bin/javaw.exe",
        apply_api_defaults=True,
    )

    assert version.options["server"] == {"host": "existing"}
    assert version.options["memory"] == "4g"
    assert version.options["jvmArguments"] == ["-Xmx4G", "-XX:+UseG1GC"]
    assert version.options["executablePath"] == "C:/Java/bin/javaw.exe"
    assert version.force_update is True


def test_tensacraft_payload_sync_preserves_user_version_options():
    version = SimpleNamespace(
        options={
            "gpuMode": "igpu",
            "jvmArguments": ["-Xmx2G", "-XX:+UseG1GC"],
            "executablePath": "D:/CustomJava/bin/javaw.exe",
        },
        image="custom-icon",
        force_update=False,
    )
    client_data = {
        "server_host": "play.tensa.example",
        "server_port": 25565,
        "gpu_preference": "discrete",
        "jvm_arguments": ["-Xmx8G"],
        "image": "api-icon",
        "force_update": True,
        "options": {
            "server": {"host": "api-server"},
            "memory": "8g",
            "jvmArguments": ["-Xmx10G"],
        },
    }

    TensaCraftPayloadService().merge_sync_payload(
        version,
        client_data,
        java_path="C:/LauncherJava/bin/javaw.exe",
    )

    assert "server" not in version.options
    assert "memory" not in version.options
    assert version.options["gpuMode"] == "igpu"
    assert version.options["jvmArguments"] == ["-Xmx2G", "-XX:+UseG1GC"]
    assert version.options["executablePath"] == "D:/CustomJava/bin/javaw.exe"
    assert version.image == "custom-icon"
    assert version.force_update is True


def test_tensacraft_payload_forces_requested_profile_fields_on_sync():
    version = SimpleNamespace(
        version="1.20.1",
        loader_version="0.16",
        options={
            "server": {"host": "custom.example", "port": 24454},
            "gpuMode": "igpu",
            "jvmArguments": ["-Xmx2G"],
            "executablePath": "D:/CustomJava/bin/javaw.exe",
        },
        image="custom-icon",
        force_update=False,
    )
    client_data = {
        "minecraft_version": "1.21.1",
        "loader_id": "neoforge",
        "loader_version": "21.1.229",
        "server_host": "auro.tensa.co.ua",
        "server_port": "25565",
        "gpu_preference": "discrete",
        "jvm_arguments": ["-Xmx6G"],
        "image": "api-icon",
        "force_update_profile_fields": [
            "minecraft_version",
            "loader_id",
            "loader_version",
            "server_host",
            "server_port",
            "gpu_preference",
            "jvm_arguments",
            "image",
        ],
    }

    TensaCraftPayloadService().merge_sync_payload(version, client_data)

    assert version.version == "1.21.1"
    assert version.loader_version == "21.1.229"
    assert version.options["server"] == {"host": "auro.tensa.co.ua", "port": 25565}
    assert version.options["gpuMode"] == "dgpu"
    assert version.options["jvmArguments"] == ["-Xmx6G"]
    assert version.options["executablePath"] == "D:/CustomJava/bin/javaw.exe"
    assert version.image == "api-icon"
    assert "force_update_profile_fields" not in version.options
    assert not hasattr(version, "force_update_profile_fields")


def test_tensacraft_payload_only_forces_listed_profile_fields_on_sync():
    version = SimpleNamespace(
        version="1.20.1",
        loader_version="0.16",
        options={
            "server": {"host": "custom.example", "port": 24454},
            "gpuMode": "igpu",
        },
        image="custom-icon",
        force_update=False,
    )
    client_data = {
        "minecraft_version": "1.21.1",
        "loader_id": "neoforge",
        "loader_version": "21.1.229",
        "server_host": "auro.tensa.co.ua",
        "server_port": 25565,
        "gpu_preference": "discrete",
        "image": "api-icon",
        "force_update_profile_fields": [
            "minecraft_version",
            "loader_id",
            "loader_version",
        ],
    }

    TensaCraftPayloadService().merge_sync_payload(version, client_data)

    assert version.version == "1.21.1"
    assert version.loader_version == "21.1.229"
    assert version.options["server"] == {"host": "custom.example", "port": 24454}
    assert version.options["gpuMode"] == "igpu"
    assert version.image == "custom-icon"


def test_tensacraft_payload_requires_client_data():
    with pytest.raises(ValueError, match="No version data found"):
        TensaCraftPayloadService().get_client_data({}, "tensa")

    with pytest.raises(ValueError, match="missing in API response"):
        TensaCraftPayloadService().get_client_data({"foo": "bar"}, "tensa")


def test_tensacraft_payload_applies_new_api_client_fields():
    version = SimpleNamespace(id="old", version="old", loader_version=None, force_update=False, image=None, options={})
    client_data = {
        "loader_id": "fabric",
        "minecraft_version": "1.21.11",
        "loader_version": "0.18.4",
        "force_update": True,
        "image": "img",
        "server_host": "tensa.co.ua",
        "server_port": 25565,
        "gpu_preference": "discrete",
        "jvm_arguments": ["-Xmx4G"],
    }

    loader_name = TensaCraftPayloadService().apply_install_payload(version, version_key="tensa-lite", client_data=client_data)

    assert loader_name == "fabric"
    assert version.id == "tensa-lite"
    assert version.version == "1.21.11"
    assert version.options["server"] == {"host": "tensa.co.ua", "port": 25565}
    assert version.options["gpuMode"] == "dgpu"
    assert version.options["jvmArguments"] == ["-Xmx4G"]
