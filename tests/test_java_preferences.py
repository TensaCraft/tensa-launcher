from __future__ import annotations

from pathlib import Path

from launcher.application.java_preferences import JavaPreferencesService


def test_add_custom_java_normalizes_label_and_path(tmp_path: Path):
    java_path = tmp_path / "jdk-21" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")

    entries = JavaPreferencesService.add_custom_java([], "  Custom Java 21  ", str(java_path))

    assert entries == [{"Custom Java 21": str(java_path.resolve())}]


def test_add_custom_java_replaces_existing_path_label(tmp_path: Path):
    java_path = tmp_path / "jdk" / "bin" / "javaw.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")
    existing = [{"Old label": str(java_path)}]

    entries = JavaPreferencesService.add_custom_java(existing, "New label", str(java_path))

    assert entries == [{"New label": str(java_path.resolve())}]


def test_add_custom_java_rejects_non_java_executable(tmp_path: Path):
    tool_path = tmp_path / "tool.exe"
    tool_path.write_text("", encoding="utf-8")

    try:
        JavaPreferencesService.add_custom_java([], "Tool", str(tool_path))
    except ValueError as exc:
        assert str(exc) == "invalid_java_executable"
    else:
        raise AssertionError("Expected invalid_java_executable")


def test_merge_java_entries_keeps_launcher_and_custom_entries(tmp_path: Path):
    launcher_path = tmp_path / "runtime" / "bin" / "java.exe"
    custom_path = tmp_path / "jdk" / "bin" / "javaw.exe"
    launcher_path.parent.mkdir(parents=True)
    custom_path.parent.mkdir(parents=True)
    launcher_path.write_text("", encoding="utf-8")
    custom_path.write_text("", encoding="utf-8")

    entries = JavaPreferencesService.merge_java_entries(
        [{"Launcher Java": str(launcher_path)}],
        [{"Custom Java": str(custom_path)}],
    )

    assert entries == [
        {"Launcher Java": str(launcher_path)},
        {"Custom Java": str(custom_path)},
    ]


def test_import_discovered_java_adds_only_new_valid_paths(tmp_path: Path):
    existing_path = tmp_path / "existing" / "bin" / "javaw.exe"
    discovered_path = tmp_path / "temurin-21" / "bin" / "java.exe"
    invalid_path = tmp_path / "not-java.exe"
    existing_path.parent.mkdir(parents=True)
    discovered_path.parent.mkdir(parents=True)
    existing_path.write_text("", encoding="utf-8")
    discovered_path.write_text("", encoding="utf-8")
    invalid_path.write_text("", encoding="utf-8")

    entries, added_count = JavaPreferencesService.import_discovered_java(
        [{"Existing Java": str(existing_path)}],
        [
            {"Duplicate Java": str(existing_path)},
            {"Temurin 21": str(discovered_path)},
            {"Invalid": str(invalid_path)},
        ],
    )

    assert added_count == 1
    assert entries == [
        {"Existing Java": str(existing_path)},
        {"Temurin 21": str(discovered_path.resolve())},
    ]


def test_detects_legacy_launcher_runtime_labels():
    assert JavaPreferencesService.has_raw_launcher_runtime_labels(
        [{"java-runtime-delta": "C:/Minecraft/runtime/java-runtime-delta/bin/java.exe"}]
    )
    assert not JavaPreferencesService.has_raw_launcher_runtime_labels(
        [{"Launcher Java 21.0.7 (java-runtime-delta)": "C:/Minecraft/runtime/java-runtime-delta/bin/java.exe"}]
    )
