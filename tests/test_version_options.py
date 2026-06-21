from __future__ import annotations

from types import SimpleNamespace

import pytest

from launcher.application.memory_preferences import MemoryLimits, MemoryPreferencesService
from launcher.application.version_options import VersionOptionsPayload, VersionOptionsService


def test_version_options_parses_and_extracts_jvm_arguments():
    service = VersionOptionsService()

    xmx, xms = service.parse_jvm_arguments(["-Xmx4096M", "-Xms2G", "-XX:+UseG1GC"])
    custom = service.extract_custom_arguments(["-Xmx4096M", "-Xms2G", "-XX:+UseG1GC"])

    assert xmx == 4
    assert xms == 2
    assert custom == ["-XX:+UseG1GC"]


def test_version_options_builds_presets():
    service = VersionOptionsService()

    options, preset_map = service.build_preset_options(lambda key: key.upper())

    assert options[0] == {"text": "JVM_PRESET_KEEP", "key": "keep"}
    assert preset_map["performance"][0] == "-XX:+UseG1GC"


def test_version_options_applies_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        MemoryPreferencesService,
        "detect_limits",
        classmethod(lambda _cls: MemoryLimits(total_gb=16, available_gb=10, min_heap_gb=1, max_heap_gb=14, recommended_heap_gb=6)),
    )
    image_path = tmp_path / "icon.png"
    image_path.write_bytes(b"image")
    version = SimpleNamespace(
        options={"graphicsPreset": "sodium_legacy"},
        loader="",
        name="Old",
        image=None,
    )
    payload = VersionOptionsPayload(
        name="Updated",
        java_path="C:/Java/bin/javaw.exe",
        loader_id="fabric",
        min_ram="2",
        max_ram="8",
        custom_args_text="-XX:+UseG1GC\n-XX:+UseG1GC\n",
        server_host="example.com",
        server_port="25565",
        gpu_mode="igpu",
        image_path=str(image_path),
    )

    VersionOptionsService().apply(version, payload)

    assert version.name == "Updated"
    assert version.loader == "fabric"
    assert version.options["executablePath"] == "C:/Java/bin/javaw.exe"
    assert version.options["gpuMode"] == "igpu"
    assert "graphicsPreset" not in version.options
    assert version.options["server"] == {"host": "example.com", "port": 25565}
    assert version.options["jvmArguments"] == ["-Xmx8G", "-XX:+UseG1GC"]
    assert isinstance(version.image, str)


def test_version_options_rejects_invalid_port():
    version = SimpleNamespace(options={}, loader="", name="Version", image=None)
    payload = VersionOptionsPayload(server_host="example.com", server_port="abc")

    with pytest.raises(ValueError, match="invalid_port"):
        VersionOptionsService().apply(version, payload)
