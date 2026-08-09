from __future__ import annotations

from typing import Any

import flet as ft

from launcher.pages.settings import SettingsPage
from launcher.pages.version_settings import VersionSettingsPage


def _section_titles(body: ft.Control) -> list[str]:
    sections = getattr(body, "controls", [])
    return [section.content.controls[0].value for section in sections]


def _contains_control(root: Any, target: ft.Control) -> bool:
    if root is target:
        return True
    controls = getattr(root, "controls", None)
    if controls and any(_contains_control(control, target) for control in controls):
        return True
    content = getattr(root, "content", None)
    return content is not None and _contains_control(content, target)


def test_settings_page_preserves_section_order(fake_app):
    page = SettingsPage(fake_app)

    assert _section_titles(page._build_launcher_tab()) == [
        "launcher_behavior",
        "interface",
        "minecraft_storage",
    ]
    assert _section_titles(page._build_backups_tab()) == ["world_backups"]
    assert _section_titles(page._build_java_performance_tab()) == [
        "custom_java_section",
        "java_performance_section",
    ]


def test_settings_page_sections_reuse_public_control_references(fake_app):
    page = SettingsPage(fake_app)

    launcher_tab = page._build_launcher_tab()
    backups_tab = page._build_backups_tab()
    java_tab = page._build_java_performance_tab()

    for control in (
        page.language_select,
        page.auto_update_toggle,
        page.report_contact,
        page.ui_click_sound_select,
        page.minecraft_game_dir,
    ):
        assert _contains_control(launcher_tab, control)
    for control in (
        page.world_backups_toggle,
        page.world_backups_dir,
        page.world_backups_dir_browse,
    ):
        assert _contains_control(backups_tab, control)
    for control in (
        page.custom_java_name,
        page.custom_java_list,
        page.default_max_ram,
        page.gpu_mode_select,
    ):
        assert _contains_control(java_tab, control)


def test_settings_page_preserves_control_callbacks(fake_app, monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        SettingsPage,
        "on_language_change",
        lambda _self, event: calls.append(("language", event)),
    )
    monkeypatch.setattr(
        SettingsPage,
        "on_default_ram_change",
        lambda _self, event: calls.append(("memory", event)),
    )
    monkeypatch.setattr(
        SettingsPage,
        "on_game_dir_pick_result",
        lambda _self, event: calls.append(("game_dir", event)),
    )
    monkeypatch.setattr(
        SettingsPage,
        "on_check_updates_click",
        lambda _self, event: calls.append(("updates", event)),
    )

    page = SettingsPage(fake_app)
    event = object()

    page.language_select.on_select(event)
    page.default_max_ram_slider.on_change(event)
    assert callable(page.minecraft_dir_picker.on_result)
    page.minecraft_dir_picker.on_result(event)
    page.check_updates_button.on_click(event)

    assert calls == [
        ("language", event),
        ("memory", event),
        ("game_dir", event),
        ("updates", event),
    ]


def test_version_settings_page_preserves_section_order(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _directory: [{"id": "fabric-loader-0.16"}],
    )
    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)

    assert _section_titles(page._build_general_tab()) == [
        "version_section_general",
        "version_server_section",
    ]
    assert _section_titles(page._build_runtime_tab()) == ["version_section_runtime"]
    assert _section_titles(page._build_arguments_tab()) == ["version_section_arguments"]
    assert _section_titles(page.diagnostics_view()) == ["version_section_diagnostics"]


def test_version_settings_sections_reuse_public_control_references(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _directory: [{"id": "fabric-loader-0.16"}],
    )
    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)

    general_tab = page._build_general_tab()
    runtime_tab = page._build_runtime_tab()
    arguments_tab = page._build_arguments_tab()
    diagnostics_tab = page.diagnostics_view()

    for control in (
        page.name,
        page.loaders_select,
        page.file_picker_button,
        page.server_host,
        page.server_port,
    ):
        assert _contains_control(general_tab, control)
    for control in (
        page.java_select,
        page.gpu_mode_select,
        page.java_path_display,
        page.max_ram,
    ):
        assert _contains_control(runtime_tab, control)
    for control in (page.jvm_preset_select, page.custom_args_group):
        assert _contains_control(arguments_tab, control)
    for control in (
        page.open_instance_button,
        page.send_version_report_button,
        page.scan_mods_button,
    ):
        assert _contains_control(diagnostics_tab, control)


def test_version_settings_page_preserves_control_callbacks(fake_app, monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _directory: [{"id": "fabric-loader-0.16"}],
    )
    monkeypatch.setattr(
        VersionSettingsPage,
        "on_java_change",
        lambda _self, event: calls.append(("java", event)),
    )
    monkeypatch.setattr(
        VersionSettingsPage,
        "on_max_ram_change",
        lambda _self, event: calls.append(("memory", event)),
    )
    monkeypatch.setattr(
        VersionSettingsPage,
        "on_file_picker_result",
        lambda _self, event: calls.append(("icon", event)),
    )
    monkeypatch.setattr(
        VersionSettingsPage,
        "_open_version_report_dialog",
        lambda _self: calls.append(("report", event)),
    )

    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)
    event = object()

    page.java_select.on_select(event)
    page.max_ram_slider.on_change(event)
    assert callable(page.file_picker.on_result)
    page.file_picker.on_result(event)
    page.send_version_report_button.on_click(event)

    assert calls == [
        ("java", event),
        ("memory", event),
        ("icon", event),
        ("report", event),
    ]
