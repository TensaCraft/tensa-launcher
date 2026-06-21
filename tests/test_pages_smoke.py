from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from launcher.application.modrinth_mods import ModInstallFile
from launcher.application.installed_components import InstalledComponent
from launcher.application.version_creation import VersionCreateOption
from launcher.pages.activity import ActivityPage, ActivityPanel
from launcher.pages.home import Home
from launcher.pages.modpacks import ModpacksPage
from launcher.pages.minecraft_components import MinecraftComponentsPage
from launcher.pages.mods_manager import ModsManagerPage
from launcher.pages.profiles import ProfilesPage
from launcher.pages.settings import SettingsPage
from launcher.pages.version_create import VersionCreatePage
from launcher.pages.version_settings import VersionSettingsPage
from launcher.pages.versions import VersionsPage
from launcher.pages.launch_feedback import handle_launch_response
from launcher.pages.launch_profiles import show_launch_profile_selector
from launcher.presentation.mods_manager_cards import ModsManagerCards


def _run_task_immediately(func, *args, **kwargs):
    result = func(*args, **kwargs)
    if not inspect.isawaitable(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(result)
    return loop.create_task(result)


def _flatten_text_values(control) -> list[str]:
    if isinstance(control, str):
        return [control]
    values: list[str] = []
    value = getattr(control, "value", None)
    if isinstance(value, str):
        values.append(value)
    content = getattr(control, "content", None)
    if content is not None:
        values.extend(_flatten_text_values(content))
    for child in getattr(control, "controls", []) or []:
        values.extend(_flatten_text_values(child))
    return values


def _flatten_controls(control) -> list:
    if control is None or isinstance(control, str):
        return []
    controls = [control]
    content = getattr(control, "content", None)
    if content is not None:
        controls.extend(_flatten_controls(content))
    for child in getattr(control, "controls", []) or []:
        controls.extend(_flatten_controls(child))
    return controls


async def _run_blocking_immediately(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def test_navigation_shell_builds(fake_app):
    fake_app.navigation.set_destinations(
        [
            {"icon": ft.Icons.HOME_OUTLINED, "selected_icon": ft.Icons.HOME, "label": "Home", "on_click": lambda e: None},
            {"icon": ft.Icons.SETTINGS_OUTLINED, "selected_icon": ft.Icons.SETTINGS, "label": "Settings", "on_click": lambda e: None},
        ]
    )
    fake_app.header.set_params(title="Settings", show_back_btn=True, show_profile=True)
    fake_app.footer.set_params(center_btn={"icon": ft.Icons.SAVE, "on_click": lambda e: None})

    assert isinstance(fake_app.header.view(), ft.Container)
    assert isinstance(fake_app.footer.view(), ft.Container)
    assert isinstance(fake_app.navigation.view(), ft.Container)


def test_sidebar_collapsed_mode_uses_icon_only_nav_with_tooltips(fake_app):
    fake_app.config.set("compact_sidebar", "yes")
    fake_app.navigation.set_destinations(
        [
            {"icon": ft.Icons.HOME_OUTLINED, "selected_icon": ft.Icons.HOME, "label": "Home", "on_click": lambda e: None},
            {"icon": ft.Icons.SETTINGS_OUTLINED, "selected_icon": ft.Icons.SETTINGS, "label": "Settings", "on_click": lambda e: None},
        ]
    )

    sidebar = fake_app.navigation.view()
    nav_button = sidebar.content.controls[1].content.controls[0].content
    nav_row = nav_button.content
    user_button = sidebar.content.controls[-1].content.content

    assert sidebar.width == fake_app.navigation.current_width()
    assert len(nav_row.controls) == 1
    assert isinstance(nav_row.controls[0], ft.Icon)
    assert nav_button.tooltip == "Home"
    assert user_button.tooltip == "PlayerOne"


def test_settings_page_builds(fake_app):
    page = SettingsPage(fake_app)

    assert isinstance(page.view(), ft.Control)
    assert isinstance(fake_app.footer.center_control, ft.Button)
    assert fake_app.header.title == "settings_title"
    expected_version_label = (
        "launcher_version_header_with_update "
        f"(launcher={fake_app.util.launcher_name}, version={fake_app.util.launcher_version}, "
        "channel=launcher_update_channel_stable)"
    )
    assert fake_app.header.subtitle == ""
    assert fake_app.header.actions is not None
    assert fake_app.header.actions[0].value == expected_version_label
    assert not hasattr(page, "font_select")
    assert page.language_select.height == fake_app.theme.input_height
    assert page.auto_update_toggle.height == fake_app.theme.input_height
    assert page.close_on_game_toggle.height == fake_app.theme.input_height
    assert page.ask_profile_on_launch_toggle.height == fake_app.theme.input_height
    assert page.show_tensacraft_toggle.height == fake_app.theme.input_height
    assert page.compact_sidebar_toggle.height == fake_app.theme.input_height
    assert isinstance(page.default_max_ram_slider, ft.Slider)
    assert page.default_max_ram_slider.value >= 1
    assert page.report_contact.height == fake_app.theme.input_height
    assert page.minecraft_game_dir_browse.height == fake_app.theme.input_height
    assert isinstance(page.custom_java_browse, ft.IconButton)
    assert page.custom_java_browse.icon == ft.Icons.FOLDER_OPEN
    assert page.custom_java_browse.tooltip is None
    assert not hasattr(page, "activity_list")
    assert page.active_tab == "launcher"
    assert [tab.content for tab in page.settings_tabs.controls] == [
        "settings_tab_launcher",
        "settings_tab_backups",
        "settings_tab_java_performance",
        "activity_center",
    ]
    launcher_values = _flatten_text_values(page.tab_content)
    assert "launcher_behavior" in launcher_values
    assert "ask_profile_on_launch" in launcher_values
    assert "interface" in launcher_values
    assert "ui_click_sound_enabled" in launcher_values
    assert page.ui_click_sound_select.label == "ui_click_sound_variant"
    assert page.ui_click_sound_select.value == "gate_latch_click"
    assert [option.key for option in page.ui_click_sound_select.options] == [
        "typewriter_soft_click",
        "gate_latch_click",
        "plastic_bubble_click",
    ]
    assert "minecraft_storage" in launcher_values
    assert page.world_backups_toggle.height == fake_app.theme.input_height
    assert page.world_backups_keep_count.height == fake_app.theme.input_height
    assert page.world_backups_dir_browse.height == fake_app.theme.input_height
    assert page.check_updates_button.content == "check_updates_now"
    assert page.launcher_update_status_label == "launcher_update_status_unknown"


def test_settings_path_pickers_start_in_current_existing_paths(fake_app, tmp_path: Path):
    page = SettingsPage(fake_app)
    minecraft_root = tmp_path / "Minecraft"
    backup_root = tmp_path / "Backups"
    java_root = tmp_path / "Java" / "bin"
    java_path = java_root / "java.exe"
    minecraft_root.mkdir(exist_ok=True)
    backup_root.mkdir(exist_ok=True)
    java_root.mkdir(parents=True, exist_ok=True)
    java_path.write_text("java", encoding="utf-8")

    captured_minecraft: dict[str, object] = {}
    captured_backups: dict[str, object] = {}
    captured_java: dict[str, object] = {}
    page.minecraft_dir_picker.get_directory_path = lambda **kwargs: captured_minecraft.update(kwargs)
    page.world_backups_dir_picker.get_directory_path = lambda **kwargs: captured_backups.update(kwargs)
    page.custom_java_picker.pick_files = lambda **kwargs: captured_java.update(kwargs)

    page.minecraft_game_dir.value = str(minecraft_root)
    page.world_backups_dir.value = str(backup_root)
    page.custom_java_path.value = str(java_path)

    page._browse_minecraft_dir()
    page._browse_world_backups_dir()
    page._browse_custom_java()

    assert captured_minecraft["initial_directory"] == str(minecraft_root)
    assert captured_backups["initial_directory"] == str(backup_root)
    assert captured_java["initial_directory"] == str(java_root)


def test_settings_path_pickers_omit_missing_initial_directories(fake_app, tmp_path: Path):
    page = SettingsPage(fake_app)
    captured_minecraft: dict[str, object] = {}
    captured_backups: dict[str, object] = {}
    captured_java: dict[str, object] = {}
    page.minecraft_dir_picker.get_directory_path = lambda **kwargs: captured_minecraft.update(kwargs)
    page.world_backups_dir_picker.get_directory_path = lambda **kwargs: captured_backups.update(kwargs)
    page.custom_java_picker.pick_files = lambda **kwargs: captured_java.update(kwargs)

    page.minecraft_game_dir.value = str(tmp_path / "missing-minecraft")
    page.world_backups_dir.value = str(tmp_path / "missing-backups")
    page.custom_java_path.value = str(tmp_path / "missing-java.exe")

    page._browse_minecraft_dir()
    page._browse_world_backups_dir()
    page._browse_custom_java()

    assert captured_minecraft == {"dialog_title": "select_directory"}
    assert captured_backups == {"dialog_title": "select_directory"}
    assert captured_java == {"allow_multiple": False}


def test_settings_page_shows_macos_microphone_permissions(fake_app):
    requested = []
    reset = []
    messages = []
    fake_app.util.is_macos = lambda: True
    fake_app.util.request_macos_microphone_access = lambda: requested.append(True) or "authorized"
    fake_app.util.reset_macos_microphone_access = lambda: reset.append(True) or True
    fake_app.feedback.info = lambda message, **_kwargs: messages.append(message)
    fake_app.page.run_task = _run_task_immediately

    page = SettingsPage(fake_app)
    launcher_values = _flatten_text_values(page.tab_content)

    assert "macos_microphone_permissions" in launcher_values
    assert "request_macos_microphone_access" in launcher_values
    assert "reset_macos_microphone_access" in launcher_values
    assert requested == []

    page.on_open_macos_microphone_settings(None)

    assert requested == [True]
    assert reset == []
    assert messages == ["macos_microphone_permission_authorized"]


def test_settings_page_resets_macos_microphone_permission(fake_app):
    requested = []
    reset = []
    messages = []
    fake_app.util.is_macos = lambda: True
    fake_app.util.request_macos_microphone_access = lambda: requested.append(True) or "authorized"
    fake_app.util.reset_macos_microphone_access = lambda: reset.append(True) or True
    fake_app.feedback.info = lambda message, **_kwargs: messages.append(message)
    fake_app.page.run_task = _run_task_immediately

    page = SettingsPage(fake_app)
    page.on_reset_macos_microphone_access(None)

    assert reset == [True]
    assert requested == [True]
    assert messages == [
        "macos_microphone_permission_reset",
        "macos_microphone_permission_authorized",
    ]


def test_settings_page_switches_tabs_and_embeds_activity(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs))
    page = SettingsPage(fake_app)
    fake_app.current_page = page

    page.show_tab("activity")

    assert page.active_tab == "activity"
    assert isinstance(page.tab_content.content, ft.Control)
    assert scheduled
    assert scheduled[0][0] == page.activity_panel._refresh_loop

    page.show_tab("performance")

    assert page.active_tab == "java_performance"
    assert page.activity_panel._is_active is False


def test_settings_page_manual_update_check_opens_update_dialog(fake_app):
    shown = []
    fake_app.updater = SimpleNamespace(
        check_for_updates=lambda: {"version": "9.0.0", "changelog": "notes", "download_url": "https://example.com"},
        show_update_dialog=lambda update_info: shown.append(update_info),
    )
    page = SettingsPage(fake_app)

    asyncio.run(page._check_updates_now_async())

    assert shown == [{"version": "9.0.0", "changelog": "notes", "download_url": "https://example.com"}]
    assert page.launcher_update_status_label == "launcher_update_status_update_available (version=9.0.0)"
    expected_version_label = (
        "launcher_version_header_with_update "
        f"(launcher={fake_app.util.launcher_name}, version={fake_app.util.launcher_version}, "
        "channel=launcher_update_channel_stable)"
    )
    assert fake_app.header.subtitle == ""
    assert fake_app.header.actions is not None
    assert fake_app.header.actions[0].value == expected_version_label


def test_activity_page_builds_with_feedback_snapshot(fake_app):
    operation = fake_app.feedback.begin_operation("Install", kind="install", status="Downloading")
    fake_app.feedback.warning("Network is slow", allow_report=False)

    page = ActivityPage(fake_app)
    view = page.view()

    assert isinstance(view, ft.Control)
    assert fake_app.header.title == "activity_center"
    assert fake_app.header.subtitle == "activity_center_desc"
    assert fake_app.header.actions is not None
    assert fake_app.header.actions[0].content == "activity_refresh"
    assert isinstance(fake_app.footer.center_control, type(None))
    assert view.content.content.controls[0] is not None
    assert view.content.content.controls[1] is not None

    operation.finish(show_success=False)


def test_activity_page_shows_only_leaf_active_operations(fake_app):
    root = fake_app.feedback.begin_operation("Install started", kind="install", status="Install started")
    child = fake_app.feedback.begin_operation("Install started", kind="install", status="Downloaded: config.toml")

    page = ActivityPage(fake_app)
    operations = fake_app.feedback.snapshot()["active_operations"]
    leaf_operations = page._leaf_operations(operations)

    assert [operation["id"] for operation in leaf_operations] == [child.operation_id]

    child.finish(show_success=False)
    root.finish(show_success=False)


def test_activity_panel_collapses_duplicate_active_operations(fake_app):
    panel = ActivityPanel(fake_app)
    operations = [
        {
            "id": 1,
            "title": "Install started",
            "kind": "install",
            "status": "Install started",
            "parent_id": None,
        },
        {
            "id": 2,
            "title": "Install started",
            "kind": "install",
            "status": "Downloaded file.jar",
            "parent_id": None,
        },
    ]

    visible = panel._display_operations(operations)

    assert [operation["id"] for operation in visible] == [2]


def test_activity_page_formats_recent_events_compactly(fake_app):
    panel = ActivityPanel(fake_app)
    row = panel._activity_row(
        {
            "level": "success",
            "event": "finish",
            "kind": "sync",
            "message": "Update complete",
            "timestamp": 123.456,
        }
    )

    content = row.content

    assert isinstance(content, ft.Row)
    assert row.padding.left == fake_app.theme.padding_sm
    assert row.padding.top == fake_app.theme.padding_xs
    assert len(content.controls) == 3
    assert content.controls[1].value == "Update complete"
    assert content.controls[2].value == "sync / finish"


def test_activity_page_starts_live_refresh_loop(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs))
    page = ActivityPage(fake_app)
    fake_app.current_page = page

    page.after_show()

    assert scheduled
    assert scheduled[0][0] == page._refresh_loop

    page.before_hide()
    assert page._is_active is False


def test_activity_panel_refresh_loop_updates_until_stopped(fake_app, monkeypatch):
    panel = ActivityPanel(fake_app)
    refreshes = []

    async def fake_sleep(_seconds):
        refreshes.append("sleep")
        panel.before_hide()

    monkeypatch.setattr("launcher.pages.activity.asyncio.sleep", fake_sleep)
    panel.after_show()

    asyncio.run(panel._refresh_loop())

    assert refreshes == ["sleep"]


def test_settings_page_sidebar_toggle_updates_config(fake_app):
    page = SettingsPage(fake_app)

    page.on_compact_sidebar_change(type("Event", (), {"control": type("Control", (), {"value": True})()})())
    assert fake_app.config.get("compact_sidebar") == "yes"

    page.on_compact_sidebar_change(type("Event", (), {"control": type("Control", (), {"value": False})()})())
    assert fake_app.config.get("compact_sidebar") == "no"


def test_settings_page_saves_custom_minecraft_dir_to_config(fake_app, tmp_path):
    page = SettingsPage(fake_app)
    custom_dir = tmp_path.parent / f"{tmp_path.name}-portable-minecraft"
    captured = {}

    fake_app.util.set_minecraft_dir_override = lambda value: captured.setdefault("override", value)
    page.minecraft_game_dir.value = str(custom_dir)

    assert page._save_game_dir_setting() is True
    assert fake_app.config.get("minecraft_game_dir") == str(custom_dir)
    assert page.minecraft_game_dir.value == str(custom_dir)
    assert captured["override"] == str(custom_dir)


def test_settings_page_adds_custom_java_to_config(fake_app, tmp_path):
    page = SettingsPage(fake_app)
    java_path = tmp_path / "jdk-21" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")

    page.custom_java_name.value = "Custom Java 21"
    page.custom_java_path.value = str(java_path)
    page.on_add_custom_java(None)

    assert fake_app.config.get("custom_java_versions") == [{"Custom Java 21": str(java_path.resolve())}]


def test_settings_page_scans_and_adds_custom_java(fake_app, monkeypatch, tmp_path):
    page = SettingsPage(fake_app)
    java_path = tmp_path / "temurin-21" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")
    fake_app.util.get_all_java = lambda: [{"Temurin 21": str(java_path)}]

    fake_app.page.run_task = _run_task_immediately
    monkeypatch.setattr("launcher.pages.settings.run_blocking", _run_blocking_immediately)
    messages = []
    fake_app.feedback.info = lambda message, **_kwargs: messages.append(message)

    page.on_scan_custom_java(None)

    assert fake_app.config.get("custom_java_versions") == [{"Temurin 21": str(java_path.resolve())}]
    assert page.custom_java_scan.disabled is False
    assert messages == [
        "custom_java_scan_started",
        "custom_java_scan_added (count=1)",
    ]


def test_versions_page_builds(fake_app):
    page = VersionsPage(fake_app)
    view = page.view()

    assert isinstance(view, ft.ListView)
    assert fake_app.header.title == "builds_title"
    assert len(view.controls) >= 1
    assert fake_app.header.actions is not None
    assert len(fake_app.header.actions) == 4
    assert [action.content for action in fake_app.header.actions[:2]] == [
        "add_version",
        "minecraft_components_nav",
    ]
    first_card = view.controls[0]
    action_row = first_card.content.controls[1]
    assert [action.icon for action in action_row.controls] == [
        ft.Icons.PLAY_ARROW,
        ft.Icons.COPY,
        ft.Icons.EXTENSION,
        ft.Icons.FOLDER,
    ]


def test_versions_page_add_button_opens_create_page(fake_app):
    opened = []
    fake_app.show_version_create_page = lambda: opened.append(True)
    page = VersionsPage(fake_app)

    page.add_version_btn()

    assert opened == [True]


def test_versions_page_components_button_opens_component_manager(fake_app):
    opened = []
    fake_app.show_minecraft_components_page = lambda: opened.append(True)
    VersionsPage(fake_app)

    fake_app.header.actions[1].on_click(None)

    assert opened == [True]


def test_versions_page_card_opens_version_workspace(fake_app):
    opened = []
    fake_app.show_mods_manager_page = lambda version: opened.append(version)
    page = VersionsPage(fake_app)
    view = page.view()

    view.controls[0].on_click(None)

    assert opened == [fake_app.versions.all()[0]]


def test_versions_page_confirms_before_launching_duplicate_game_dir(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    page = VersionsPage(fake_app)
    scheduled = []
    confirms = []

    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True))
    monkeypatch.setattr(
        "launcher.pages.versions.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )
    fake_app.feedback.confirm = lambda title, question, callback: confirms.append((title, question)) or callback(True)

    page.handle_play(version)

    assert confirms == [
        (
            "version_already_running_confirm_title (version=Vanilla 1.20.1)",
            "version_already_running_confirm_message (version=Vanilla 1.20.1)",
        )
    ]
    assert scheduled == [(page._handle_play_async, (version, True))]


def test_minecraft_components_page_lists_installed_components(fake_app):
    version_dir = fake_app.util.minecraft_dir / "versions" / "1.20.1"
    version_dir.mkdir(parents=True)
    (version_dir / "1.20.1.json").write_text(
        '{"id":"1.20.1","type":"release","mainClass":"Main","libraries":[]}',
        encoding="utf-8",
    )

    page = MinecraftComponentsPage(fake_app)
    view = page.view()
    values = _flatten_text_values(view)

    assert isinstance(view, ft.Control)
    assert fake_app.header.title == "minecraft_components_title"
    assert "minecraft_components_installed_tab" in values
    assert "minecraft_components_install_tab" in values
    assert "1.20.1" in values
    assert any("minecraft_components_used_by (versions=Vanilla 1.20.1)" in value for value in values)


def test_minecraft_components_page_uses_one_global_unstable_filter(fake_app):
    page = MinecraftComponentsPage(fake_app)

    for tab_key, _label, _icon in page.INSTALL_TABS:
        page.active_mode = "install"
        page.active_install_tab = tab_key
        values = set(_flatten_text_values(page._build_filter_bar()))

        assert "version_create_filter_unstable_versions" in values
        assert "version_create_filter_snapshots" not in values
        assert "version_create_filter_unstable" not in values


def test_minecraft_components_page_places_filters_in_install_tab_row(fake_app):
    page = MinecraftComponentsPage(fake_app)
    page.active_mode = "install"
    page.active_install_tab = "quilt"
    page._rebuild_content()

    tab_values = _flatten_text_values(page.install_tabs)
    filter_bar_values = _flatten_text_values(page.filter_bar)

    assert "Quilt" in tab_values
    assert "version_create_filter_unstable_versions" in tab_values
    assert "version_create_filter_unstable_versions" not in filter_bar_values
    assert "version_create_filter_snapshots" not in tab_values + filter_bar_values
    assert "version_create_filter_unstable" not in tab_values + filter_bar_values


def test_minecraft_components_page_maps_global_unstable_filter_by_loader(fake_app):
    page = MinecraftComponentsPage(fake_app)

    assert page._tab_state_key("minecraft") == ("minecraft", False, False)
    page.set_unstable_versions_enabled(True)

    assert page._tab_state_key("minecraft") == ("minecraft", True, False)
    assert page._tab_state_key("fabric") == ("fabric", True, False)
    assert page._tab_state_key("forge") == ("forge", False, False)
    assert page._tab_state_key("neoforge") == ("neoforge", False, True)
    assert page._tab_state_key("quilt") == ("quilt", True, True)


def test_minecraft_components_page_clears_stale_tab_options_when_unstable_filter_changes(fake_app):
    page = MinecraftComponentsPage(fake_app)
    stable = VersionCreateOption(
        id="neoforge:1.21.1:21.1.230",
        name="NeoForge 1.21.1",
        minecraft_version="1.21.1",
        loader_id="neoforge",
        loader_name="NeoForge",
        loader_version="21.1.230",
    )
    beta = VersionCreateOption(
        id="neoforge:1.21.9:21.9.16-beta",
        name="NeoForge 1.21.9",
        minecraft_version="1.21.9",
        loader_id="neoforge",
        loader_name="NeoForge",
        loader_version="21.9.16-beta",
        unstable_loader=True,
    )
    page.active_mode = "install"
    page.active_install_tab = "minecraft"
    page.include_unstable_versions = True
    page.options_by_tab["neoforge"] = [beta]
    page._options_cache[("neoforge", False, False)] = [stable]

    page.set_unstable_versions_enabled(False)
    page.show_install_tab("neoforge")

    assert page.options_by_tab["neoforge"] == [stable]


def test_minecraft_components_page_rebuilds_install_list_from_cache(fake_app):
    page = MinecraftComponentsPage(fake_app)
    option = VersionCreateOption(
        id="minecraft:1.21.1",
        name="Minecraft 1.21.1",
        minecraft_version="1.21.1",
        loader_id="minecraft",
        loader_name="Minecraft",
    )
    page.active_mode = "install"
    page.active_install_tab = "minecraft"
    page._options_cache[("minecraft", False, False)] = [option]
    page.options_by_tab.pop("minecraft", None)
    page._rebuild_content()

    page._load_install_tab("minecraft")

    values = _flatten_text_values(page.content_list)
    assert "Minecraft 1.21.1" in values


def test_minecraft_components_page_installs_selected_loader_build(fake_app):
    page = MinecraftComponentsPage(fake_app)
    option = VersionCreateOption(
        id="fabric:1.21.1:0.17.3",
        name="Fabric 1.21.1",
        minecraft_version="1.21.1",
        loader_id="fabric",
        loader_name="Fabric",
        loader_version="0.17.3",
        loader_versions=("0.17.3", "0.16.14"),
    )
    captured = []
    page.selected_loader_builds[option.id] = "0.16.14"
    page.service.install_component = lambda loader_id, minecraft_version, **kwargs: captured.append(
        (loader_id, minecraft_version, kwargs.get("loader_version"))
    )
    operation = SimpleNamespace(fail=lambda *_args, **_kwargs: None, finish=lambda *_args, **_kwargs: None)

    asyncio.run(page._install_option_async(option, operation))

    assert captured == [("fabric", "1.21.1", "0.16.14")]


def test_minecraft_components_page_selects_loader_build_in_install_dialog(fake_app, monkeypatch):
    page = MinecraftComponentsPage(fake_app)
    option = VersionCreateOption(
        id="fabric:1.21.1:0.17.3",
        name="Fabric 1.21.1",
        minecraft_version="1.21.1",
        loader_id="fabric",
        loader_name="Fabric",
        loader_version="0.17.3",
        loader_versions=("0.17.3", "0.16.14"),
    )
    row = page._build_install_option_row(option, set())
    dialogs = []
    monkeypatch.setattr("launcher.pages.minecraft_components.show_dialog", lambda _page, dialog: dialogs.append(dialog), raising=False)

    assert not any(isinstance(control, ft.Dropdown) for control in _flatten_controls(row))

    page.confirm_install_option(option)

    assert dialogs
    dropdowns = [control for control in _flatten_controls(dialogs[0]) if isinstance(control, ft.Dropdown)]
    assert len(dropdowns) == 1
    assert dropdowns[0].value == "0.17.3"


def test_minecraft_components_page_shows_badges_for_unstable_install_options(fake_app):
    page = MinecraftComponentsPage(fake_app)
    snapshot = VersionCreateOption(
        id="minecraft:25w20a",
        name="Minecraft 25w20a",
        minecraft_version="25w20a",
        loader_id="minecraft",
        loader_name="Minecraft",
        snapshot=True,
    )
    beta = VersionCreateOption(
        id="neoforge:1.21.9:21.9.16-beta",
        name="NeoForge 1.21.9",
        minecraft_version="1.21.9",
        loader_id="neoforge",
        loader_name="NeoForge",
        loader_version="21.9.16-beta",
        unstable_loader=True,
    )

    snapshot_values = _flatten_text_values(page._build_install_option_row(snapshot, set()))
    beta_values = _flatten_text_values(page._build_install_option_row(beta, set()))

    assert "version_create_snapshot_badge" in snapshot_values
    assert "version_create_unstable_loader_badge" in beta_values


def test_minecraft_components_page_renders_install_options_in_chunks(fake_app):
    page = MinecraftComponentsPage(fake_app)
    page.active_mode = "install"
    page.active_install_tab = "minecraft"
    state_key = page._tab_state_key("minecraft")
    page.loaded_state_by_tab["minecraft"] = state_key
    page.options_by_tab["minecraft"] = [
        VersionCreateOption(
            id=f"minecraft:1.21.{index}",
            name=f"Minecraft 1.21.{index}",
            minecraft_version=f"1.21.{index}",
            loader_id="minecraft",
            loader_name="Minecraft",
        )
        for index in range(page.OPTION_RENDER_LIMIT + 5)
    ]

    controls = page._build_install_controls()

    assert len(controls) == page.OPTION_RENDER_LIMIT + 1
    assert "load_more" in _flatten_text_values(controls[-1])

    page._load_more_install_options()

    assert len(page._build_install_controls()) == page.OPTION_RENDER_LIMIT + 5


def test_minecraft_components_page_ignores_stale_install_tab_result(fake_app, monkeypatch):
    page = MinecraftComponentsPage(fake_app)
    option = VersionCreateOption(
        id="minecraft:25w20a",
        name="Minecraft 25w20a",
        minecraft_version="25w20a",
        loader_id="minecraft",
        loader_name="Minecraft",
        snapshot=True,
    )

    async def fake_run_blocking(*_args, **_kwargs):
        return [option]

    monkeypatch.setattr("launcher.pages.minecraft_components.run_blocking", fake_run_blocking, raising=False)
    page.load_generation = 2

    asyncio.run(page._load_install_tab_async("minecraft", True, False, 1))

    assert "minecraft" not in page.options_by_tab


def test_minecraft_components_verify_repairs_invalid_component(fake_app):
    page = MinecraftComponentsPage(fake_app)
    component = InstalledComponent(
        version_id="1.21.1",
        kind="minecraft",
        loader_name="Minecraft",
        minecraft_version="1.21.1",
        loader_version=None,
        inherits_from=None,
        path=fake_app.util.minecraft_dir / "versions" / "1.21.1",
        size_bytes=0,
        modified_at=None,
        used_by=(),
        dependent_components=(),
    )
    calls = []
    infos = []
    warnings = []
    page.service.verify_component = lambda _component: {"valid": False, "issues": ["Some libraries are missing or corrupted"]}
    page.service.reinstall_component = lambda selected, _operation=None: calls.append(selected.version_id)
    fake_app.feedback.info = lambda message, **_kwargs: infos.append(message)
    fake_app.feedback.warning = lambda message, **_kwargs: warnings.append(message)

    asyncio.run(page._verify_component_async(component))

    assert calls == ["1.21.1"]
    assert infos == ["minecraft_components_repair_complete (version=1.21.1)"]
    assert warnings == []


def test_version_create_page_defaults_to_tensacraft_catalog(fake_app):
    page = VersionCreatePage(fake_app)

    view = page.view()

    assert isinstance(view, ft.Control)
    assert fake_app.header.title == "create_version_title"
    assert fake_app.header.back_action == fake_app.show_versions_page
    assert page.active_tab == "tensacraft"
    tab_values = _flatten_text_values(page.tab_bar)
    assert tab_values[:2] == ["TensaCraft", "Minecraft"]


def test_version_create_page_uses_one_global_unstable_filter(fake_app):
    page = VersionCreatePage(fake_app)

    for tab_key, _label, _icon in page.TABS:
        page.active_tab = tab_key
        values = set(_flatten_text_values(page._build_filter_bar()))

        assert "version_create_filter_unstable_versions" in values
        assert "version_create_filter_snapshots" not in values
        assert "version_create_filter_unstable" not in values


def test_version_create_page_places_filters_in_tab_row(fake_app):
    page = VersionCreatePage(fake_app)
    page.active_tab = "quilt"
    page._rebuild_active_content()

    tab_values = _flatten_text_values(page.tab_bar)
    filter_bar_values = _flatten_text_values(page.filter_bar)

    assert "Quilt" in tab_values
    assert "version_create_filter_unstable_versions" in tab_values
    assert "version_create_filter_unstable_versions" not in filter_bar_values
    assert "version_create_filter_snapshots" not in tab_values + filter_bar_values
    assert "version_create_filter_unstable" not in tab_values + filter_bar_values


def test_version_create_page_maps_global_unstable_filter_by_loader(fake_app):
    page = VersionCreatePage(fake_app)

    assert page._tab_state_key("minecraft") == ("minecraft", False, False)
    page._set_unstable_versions_enabled(True)

    assert page._tab_state_key("minecraft") == ("minecraft", True, False)
    assert page._tab_state_key("fabric") == ("fabric", True, False)
    assert page._tab_state_key("forge") == ("forge", False, False)
    assert page._tab_state_key("neoforge") == ("neoforge", False, True)
    assert page._tab_state_key("quilt") == ("quilt", True, True)


def test_version_create_page_clears_stale_tab_options_when_unstable_filter_changes(fake_app):
    page = VersionCreatePage(fake_app)
    stable = VersionCreateOption(
        id="neoforge:1.21.1:21.1.230",
        name="NeoForge 1.21.1",
        minecraft_version="1.21.1",
        loader_id="neoforge",
        loader_name="NeoForge",
        loader_version="21.1.230",
    )
    beta = VersionCreateOption(
        id="neoforge:1.21.9:21.9.16-beta",
        name="NeoForge 1.21.9",
        minecraft_version="1.21.9",
        loader_id="neoforge",
        loader_name="NeoForge",
        loader_version="21.9.16-beta",
        unstable_loader=True,
    )
    page.active_tab = "minecraft"
    page.include_unstable_versions = True
    page.options_by_tab["neoforge"] = [beta]
    page._options_cache[("neoforge", False, False)] = [stable]

    page._set_unstable_versions_enabled(False)
    page.show_tab("neoforge")

    assert page.options_by_tab["neoforge"] == [stable]


def test_version_create_page_reloads_when_snapshot_filter_changes(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs))
    page = VersionCreatePage(fake_app)
    page.show_tab("minecraft")
    scheduled.clear()

    page._on_snapshots_toggle(SimpleNamespace(control=SimpleNamespace(value=True)))

    assert page.include_unstable_versions is True
    assert "minecraft" in page.loading_tabs
    load_calls = [call for call in scheduled if call[0] == page._load_tab_async]
    assert load_calls
    assert load_calls[0][1][0] == "minecraft"
    assert load_calls[0][1][1] is True


def test_version_create_page_uses_cached_filter_state(fake_app, monkeypatch):
    scheduled = []
    fake_app.page.run_task = lambda task, *args, **kwargs: scheduled.append((task, args, kwargs))
    page = VersionCreatePage(fake_app)
    page.show_tab("minecraft")
    page.loading_tabs.clear()
    page.options_by_tab["minecraft"] = [
        VersionCreateOption(
            id="minecraft:1.21.1",
            name="Minecraft 1.21.1",
            minecraft_version="1.21.1",
            loader_id="minecraft",
            loader_name="Minecraft",
        )
    ]
    page._options_cache[("minecraft", False, False)] = list(page.options_by_tab["minecraft"])
    scheduled.clear()

    page._on_snapshots_toggle(SimpleNamespace(control=SimpleNamespace(value=True)))
    page.loading_tabs.clear()
    page.options_by_tab["minecraft"] = [
        VersionCreateOption(
            id="minecraft:25w20a",
            name="Minecraft 25w20a",
            minecraft_version="25w20a",
            loader_id="minecraft",
            loader_name="Minecraft",
            snapshot=True,
        )
    ]
    page._options_cache[("minecraft", True, False)] = list(page.options_by_tab["minecraft"])
    scheduled.clear()

    page._on_snapshots_toggle(SimpleNamespace(control=SimpleNamespace(value=False)))

    assert page.include_unstable_versions is False
    assert page.options_by_tab["minecraft"][0].minecraft_version == "1.21.1"
    assert not [call for call in scheduled if call[0] == page._load_tab_async]


def test_version_create_page_ignores_stale_tab_result(fake_app, monkeypatch):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:25w20a",
        name="Minecraft 25w20a",
        minecraft_version="25w20a",
        loader_id="minecraft",
        loader_name="Minecraft",
        snapshot=True,
    )

    async def fake_run_blocking(*_args, **_kwargs):
        return [option]

    monkeypatch.setattr("launcher.pages.version_create.run_blocking", fake_run_blocking, raising=False)
    page.load_generation = 2

    asyncio.run(page._load_tab_async("minecraft", True, False, 1))

    assert "minecraft" not in page.options_by_tab


def test_version_create_page_renders_options_in_chunks(fake_app):
    page = VersionCreatePage(fake_app)
    page.active_tab = "minecraft"
    state_key = page._tab_state_key("minecraft")
    page.loaded_state_by_tab["minecraft"] = state_key
    page.options_by_tab["minecraft"] = [
        VersionCreateOption(
            id=f"minecraft:1.21.{index}",
            name=f"Minecraft 1.21.{index}",
            minecraft_version=f"1.21.{index}",
            loader_id="minecraft",
            loader_name="Minecraft",
        )
        for index in range(page.OPTION_RENDER_LIMIT + 5)
    ]

    controls = page._build_option_controls()

    assert len(controls) == page.OPTION_RENDER_LIMIT + 1
    assert "load_more" in _flatten_text_values(controls[-1])

    page._load_more_options()

    assert len(page._build_option_controls()) == page.OPTION_RENDER_LIMIT + 5


def test_version_create_page_confirms_install_with_editable_name(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:1.21.1",
        name="Minecraft 1.21.1",
        minecraft_version="1.21.1",
        loader_id="minecraft",
        loader_name="Minecraft",
    )
    started = []
    page._start_install = lambda install_option, name=None: started.append((install_option, name))

    page._handle_install_click(option)
    page.install_name.value = "Custom Vanilla"
    page._confirm_install_dialog()

    assert started == [(option, "Custom Vanilla")]


def test_version_create_page_selects_loader_build_only_in_install_dialog(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="quilt:1.21.1:0.30.0",
        name="Quilt 1.21.1",
        minecraft_version="1.21.1",
        loader_id="quilt",
        loader_name="Quilt",
        loader_version="0.30.0",
        loader_versions=("0.30.0", "0.29.2"),
    )
    row = page._build_option_row(option)

    assert not any(isinstance(control, ft.Dropdown) for control in _flatten_controls(row))

    page._handle_install_click(option)

    assert page.install_dialog is not None
    dropdowns = [control for control in _flatten_controls(page.install_dialog) if isinstance(control, ft.Dropdown)]
    assert len(dropdowns) == 1
    assert dropdowns[0].value == "0.30.0"


def test_version_create_page_rejects_empty_install_name(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:1.21.1",
        name="Minecraft 1.21.1",
        minecraft_version="1.21.1",
        loader_id="minecraft",
        loader_name="Minecraft",
    )
    warnings = []
    fake_app.feedback.warning = lambda message, **_kwargs: warnings.append(message)
    page._start_install = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not install"))

    page._handle_install_click(option)
    page.install_name.value = " "
    page._confirm_install_dialog()

    assert warnings == ["empty_version_name"]


def test_version_create_page_installs_non_tensacraft_through_component_service(fake_app, monkeypatch):
    installed = []
    saved = []

    class FakeComponentService:
        def __init__(self, *_args, **_kwargs):
            return None

        def install_game_build(self, version, *, operation=None):
            installed.append((version.client, version.version, version.loader_version, operation))
            version.loader = "fabric-loader-0.16.14-1.21.1"
            version.client = "Fabric"
            version.path = str(fake_app.util.minecraft_dir / "games" / version.version_id)
            version.save()
            return version

    class CapturedVersion:
        def __init__(self, version_id, data):
            self.version_id = version_id
            self.id = version_id
            self.name = data["name"]
            self.version = data["version"]
            self.client = data["client"]
            self.loader = data.get("loader")
            self.loader_version = data.get("loader_version")
            self.options = data.get("options", {})
            self.path = data.get("path")

        def save(self):
            saved.append(self)

        def install(self):
            raise AssertionError("non-TensaCraft installs must go through InstalledComponentsService")

    monkeypatch.setattr("launcher.pages.version_create.InstalledComponentsService", FakeComponentService)
    monkeypatch.setattr("launcher.pages.version_create.Version", CapturedVersion)
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="fabric:1.21.1:0.17.3",
        name="Fabric 1.21.1",
        minecraft_version="1.21.1",
        loader_id="fabric",
        loader_name="Fabric",
        loader_version="0.17.3",
        loader_versions=("0.17.3", "0.16.14"),
    )
    page.selected_loader_builds[option.id] = "0.16.14"
    operation = SimpleNamespace()

    page._install_option(option, "Custom Fabric", operation=operation)

    assert installed == [("fabric", "1.21.1", "0.16.14", operation)]
    assert saved[0].name == "Custom Fabric"
    assert saved[0].loader == "fabric-loader-0.16.14-1.21.1"


def test_version_create_page_does_not_duplicate_loader_description(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:1.21.1",
        name="Minecraft 1.21.1",
        minecraft_version="1.21.1",
        loader_id="minecraft",
        loader_name="Minecraft",
    )
    subtitle = page._option_subtitle(option)

    values = _flatten_text_values(page._build_option_row(option))

    assert values.count(subtitle) == 1


def test_version_create_page_uses_aligned_badge_column(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:26.2-snapshot-8",
        name="Minecraft 26.2-snapshot-8",
        minecraft_version="26.2-snapshot-8",
        loader_id="minecraft",
        loader_name="Minecraft",
        snapshot=True,
    )

    card = page._build_option_row(option)
    content_row = card.content.controls[0]
    badge_slot = content_row.controls[2]
    title_column = content_row.controls[1]
    title_values = _flatten_text_values(title_column.controls[0])

    assert badge_slot.key == "version-create-badge-slot"
    assert badge_slot.width == page.BADGE_COLUMN_WIDTH
    assert badge_slot.alignment == ft.Alignment.CENTER
    assert "version_create_snapshot_badge" in _flatten_text_values(badge_slot)
    assert "version_create_snapshot_badge" not in title_values


def test_version_create_page_keeps_tensacraft_description(fake_app):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="aeronautics",
        name="Aeronautics",
        minecraft_version="1.21.1",
        loader_id="tensacraft",
        loader_name="TensaCraft",
        description="Create focused TensaCraft pack",
    )

    values = _flatten_text_values(page._build_option_row(option))

    assert "Create focused TensaCraft pack" in values


def test_version_create_install_waits_for_progress_overlay_before_navigation(fake_app, monkeypatch):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="minecraft:1.21.1",
        name="Minecraft 1.21.1",
        minecraft_version="1.21.1",
        loader_id="minecraft",
        loader_name="Minecraft",
    )
    events = []

    async def run_blocking_immediately(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def wait_until_hidden(*_args, **_kwargs):
        events.append("wait")

    monkeypatch.setattr("launcher.pages.version_create.run_blocking", run_blocking_immediately)
    fake_app.progressbar.wait_until_hidden = wait_until_hidden
    fake_app.feedback.info = lambda *_args, **_kwargs: events.append("info")
    fake_app.show_versions_page = lambda: events.append("nav")
    page._install_option = lambda *_args, **_kwargs: events.append("install")
    operation = SimpleNamespace(
        finish=lambda *_args, **_kwargs: events.append("finish"),
        fail=lambda *_args, **_kwargs: events.append("fail"),
    )

    asyncio.run(page._install_option_async(option, "Custom Vanilla", operation))

    assert events == ["install", "finish", "wait", "info", "nav"]


def test_missing_profile_launch_response_shows_profile_actions(fake_app, monkeypatch):
    dialogs = []
    opened_actions = []
    fake_app.show_profiles_page = lambda action=None: opened_actions.append(action)
    monkeypatch.setattr("launcher.pages.launch_feedback.show_dialog", lambda _page, dialog: dialogs.append(dialog))

    handle_launch_response(
        fake_app,
        {"status": False, "text": "no_default_profile", "reason": "missing_profile"},
    )

    assert dialogs
    values = _flatten_text_values(dialogs[0].content)
    action_values = [action.content for action in dialogs[0].actions]
    assert dialogs[0].title.value == "profile_required_title"
    assert "profile_required_message" in values
    assert "microsoft_account" in action_values
    assert "offline_account" in action_values

    dialogs[0].actions[0].on_click(None)

    assert opened_actions == ["microsoft"]


def test_profiles_page_opens_requested_initial_action(fake_app):
    opened = []
    fake_app.form_modal = lambda *_args, **_kwargs: SimpleNamespace(open=lambda: opened.append("offline"))

    page = ProfilesPage(fake_app, initial_action="offline")
    page.view()
    page.after_show()

    assert opened == ["offline"]


def test_version_settings_page_builds(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )

    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)

    assert isinstance(page.view(), ft.Control)
    assert isinstance(fake_app.footer.center_control, ft.Button)
    assert fake_app.header.back_action == fake_app.show_versions_page
    assert page.name.height == fake_app.theme.input_height
    assert page.java_select.height == fake_app.theme.input_height
    assert isinstance(page.max_ram_slider, ft.Slider)
    assert page.max_ram_slider.value >= 1
    assert page.file_picker_button.height == fake_app.theme.input_height
    assert page.open_latest_log_button.height == fake_app.theme.button_height
    assert page.open_latest_crash_button.height == fake_app.theme.button_height
    assert fake_app.footer.center_control.height == fake_app.theme.shell_action_height
    page.show_tab("runtime")
    runtime_values = _flatten_text_values(page.tab_content)
    assert "graphics_preset_label" not in runtime_values


def test_version_settings_page_uses_section_tabs(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )

    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)

    assert page.active_tab == "general"
    assert [button.content for button in page.version_tabs.controls] == [
        "version_section_general",
        "version_section_runtime",
        "version_section_arguments",
    ]
    assert _control_tree_contains(page.tab_content.content, page.file_picker_button)

    page.show_tab("diagnostics")

    assert page.active_tab == "general"
    assert [button.content for button in page.version_tabs.controls][-1] == "version_section_arguments"


def _control_tree_contains(root, target):
    if root is target:
        return True
    controls = getattr(root, "controls", None)
    if controls and any(_control_tree_contains(child, target) for child in controls):
        return True
    content = getattr(root, "content", None)
    return content is not None and _control_tree_contains(content, target)


def test_version_settings_page_includes_custom_java_entries(fake_app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )
    java_path = tmp_path / "jdk-21" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")
    fake_app.config.set("custom_java_versions", [{"Custom Java 21": str(java_path)}])

    version = fake_app.versions.all()[0]
    page = VersionSettingsPage(fake_app, version.version_id)
    option_keys = [option.key for option in page.java_select.options]

    assert str(java_path) in option_keys


def test_version_settings_page_shows_auto_java_selector_without_custom_java(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )
    monkeypatch.setattr(
        "launcher.pages.version_settings.JavaRuntimeService.get_runtime_name",
        lambda _self, _mc_version: "java-runtime-delta",
    )
    monkeypatch.setattr(
        "launcher.pages.version_settings.JavaRuntimeService.get_executable_path",
        lambda _self, _runtime_name: "D:/Games/TensaLauncher/minecraft/runtime/java-runtime-delta/bin/java.exe",
    )
    fake_app.java_versions = []
    fake_app.config.set("custom_java_versions", [])

    version = fake_app.versions.all()[0]
    version.options.pop("executablePath", None)
    page = VersionSettingsPage(fake_app, version.version_id)
    option_keys = [option.key for option in page.java_select.options]

    assert page.java_select.visible is not False
    assert page.java_select.value == VersionSettingsPage.AUTO_JAVA_VALUE
    assert option_keys == [VersionSettingsPage.AUTO_JAVA_VALUE]
    assert page.java_help_text.value == "java_selection_hint"
    assert page.java_path_display.value == "D:/Games/TensaLauncher/minecraft/runtime/java-runtime-delta/bin/java.exe"


def test_version_settings_page_hides_scanned_java_when_not_custom(fake_app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )
    java_path = tmp_path / "scanned" / "bin" / "java.exe"
    java_path.parent.mkdir(parents=True)
    java_path.write_text("", encoding="utf-8")
    fake_app.java_versions = [{"Scanned Java": str(java_path)}]
    fake_app.config.set("custom_java_versions", [])

    version = fake_app.versions.all()[0]
    version.options.pop("executablePath", None)
    page = VersionSettingsPage(fake_app, version.version_id)
    option_keys = [option.key for option in page.java_select.options]

    assert option_keys == [VersionSettingsPage.AUTO_JAVA_VALUE]


def test_version_settings_page_saves_auto_java_as_launcher_default(fake_app, monkeypatch):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )
    fake_app.java_versions = []
    fake_app.config.set("custom_java_versions", [])

    version = fake_app.versions.all()[0]
    version.save = lambda: None
    page = VersionSettingsPage(fake_app, version.version_id)
    page.java_select.value = VersionSettingsPage.AUTO_JAVA_VALUE
    page.save()

    assert "executablePath" not in version.options


def test_version_settings_page_opens_diagnostic_paths(fake_app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )

    version = fake_app.versions.all()[0]
    version_root = tmp_path / "instance"
    logs_dir = version_root / "logs"
    crash_dir = version_root / "crash-reports"
    logs_dir.mkdir(parents=True)
    crash_dir.mkdir()
    latest_log = logs_dir / "latest.log"
    launch_log = logs_dir / "tensalauncher-launch.log"
    latest_crash = crash_dir / "crash-2026-04-28.txt"
    latest_log.write_text("latest", encoding="utf-8")
    launch_log.write_text("launch", encoding="utf-8")
    latest_crash.write_text("crash", encoding="utf-8")
    version.path = str(version_root)

    opened = []
    alerts = []
    fake_app.util.open_mc_dir = lambda path: opened.append(path) or None
    fake_app.feedback.warning = lambda message, **kwargs: alerts.append((message, kwargs))

    page = VersionSettingsPage(fake_app, version.version_id)
    page.open_instance_button.on_click(None)
    page.open_logs_button.on_click(None)
    page.open_latest_log_button.on_click(None)
    page.open_crash_reports_button.on_click(None)
    page.open_latest_crash_button.on_click(None)
    page.open_launch_log_button.on_click(None)

    assert opened == [
        str(version_root),
        str(logs_dir),
        str(latest_log),
        str(crash_dir),
        str(latest_crash),
        str(launch_log),
    ]
    assert alerts == []


def test_version_settings_page_sends_manual_report_with_launch_logs(fake_app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "minecraft_launcher_lib.utils.get_installed_versions",
        lambda _dir: [{"id": "fabric-loader-0.16"}],
    )

    version = fake_app.versions.all()[0]
    version_root = tmp_path / "instance"
    logs_dir = version_root / "logs"
    crash_dir = version_root / "crash-reports"
    logs_dir.mkdir(parents=True)
    crash_dir.mkdir()
    latest_log = logs_dir / "latest.log"
    launch_log = logs_dir / "tensalauncher-launch.log"
    latest_crash = crash_dir / "crash-2026-04-28.txt"
    hs_err = version_root / "hs_err_pid123.log"
    latest_log.write_text("latest", encoding="utf-8")
    launch_log.write_text("launch diagnostics", encoding="utf-8")
    latest_crash.write_text("crash", encoding="utf-8")
    hs_err.write_text("fatal java error", encoding="utf-8")
    version.path = str(version_root)

    captured = {"dialogs": [], "reports": [], "alerts": []}
    fake_app.page.show_dialog = lambda dialog: captured["dialogs"].append(dialog)
    fake_app.feedback.info = lambda message, **kwargs: captured["alerts"].append((message, "info", kwargs))
    fake_app.feedback.warning = lambda message, **kwargs: captured["alerts"].append((message, "warning", kwargs))

    class FakeReporter:
        def submit_report_async(self, **kwargs):
            captured["reports"].append(kwargs)
            kwargs["on_success"]({"ok": True, "report_id": "version-report-1"})

    fake_app.reporter = FakeReporter()

    page = VersionSettingsPage(fake_app, version.version_id)
    page.send_version_report_button.on_click(None)
    page.version_report_contact.value = "client@example.com"
    page.version_report_message.value = "The game freezes after pressing Play"
    page._submit_version_report()

    assert captured["dialogs"]
    assert len(captured["reports"]) == 1
    report = captured["reports"][0]
    assert report["report_type"] == "error"
    assert report["severity"] == "error"
    assert report["title"] == "version_report_title (version=Vanilla 1.20.1)"
    assert report["message"] == "The game freezes after pressing Play"
    assert report["metadata"]["action"] == "manual_version_report"
    assert report["metadata"]["version_id"] == version.version_id
    assert report["metadata"]["version_name"] == version.name
    assert report["metadata"]["loader"] == version.loader
    assert report["metadata"]["minecraft"] == version.version
    assert report["metadata"]["java_path"] == "C:/Java/bin/javaw.exe"
    assert report["metadata"]["contact"] == "client@example.com"
    assert report["attachments"] == [latest_log, launch_log, latest_crash, hs_err]


def test_profiles_page_builds(fake_app):
    page = ProfilesPage(fake_app)
    view = page.view()

    assert isinstance(view, ft.ListView)
    assert len(view.controls) >= 1
    assert fake_app.header.actions is not None
    assert len(fake_app.header.actions) == 2
    assert all(action.height == fake_app.theme.shell_action_height for action in fake_app.header.actions)


def test_profiles_page_uses_local_reauth_state_after_default_switch(fake_app):
    class ProfilesRepo:
        def __init__(self):
            self.decrypted = {
                "one": {
                    "id": "one-id",
                    "name": "One",
                    "type": "microsoft",
                    "access_token": "access-one",
                    "refresh_token": "refresh-one",
                    "default": True,
                },
                "two": {
                    "id": "two-id",
                    "name": "Two",
                    "type": "microsoft",
                    "access_token": "access-two",
                    "refresh_token": "refresh-two",
                    "default": False,
                },
            }

        def get_all_profiles(self):
            return {key: dict(profile) for key, profile in self.decrypted.items()}

        def load(self):
            return {
                key: {
                    **profile,
                    "access_token": f"enc::{profile['access_token']}",
                    "refresh_token": f"enc::{profile['refresh_token']}",
                }
                for key, profile in self.decrypted.items()
            }

        def set_default_profile(self, key):
            for profile_key, profile in self.decrypted.items():
                profile["default"] = profile_key == key

    checked_profiles = []
    fake_app.profiles = ProfilesRepo()
    fake_app.auth = SimpleNamespace(
        verify=lambda _profile: (_ for _ in ()).throw(AssertionError("profile page must not verify over network")),
        profile_requires_reauth=lambda profile: checked_profiles.append(dict(profile)) and False,
    )
    page = ProfilesPage(fake_app)
    page.view()
    checked_profiles.clear()

    event = type(
        "Event",
        (),
        {
            "control": type(
                "Control",
                (),
                {"value": True, "key": "two", "data": {"name": "Two"}},
            )()
        },
    )()
    page.on_switch_change(event)

    assert checked_profiles
    assert all(not profile["access_token"].startswith("enc::") for profile in checked_profiles)
    assert all(not profile["refresh_token"].startswith("enc::") for profile in checked_profiles)


def test_home_page_builds(fake_app, monkeypatch):
    page = Home(fake_app)
    monkeypatch.setattr(page, "_load_tensacraft_versions_async", lambda: None)
    view = page.view()

    assert isinstance(view, ft.GridView)
    assert view.child_aspect_ratio == 0.84
    assert len(view.controls) == 1


def test_home_page_confirms_before_launching_duplicate_game_dir(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    page = Home(fake_app)
    scheduled = []
    confirms = []

    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True))
    monkeypatch.setattr(
        "launcher.pages.home.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )
    fake_app.feedback.confirm = lambda title, question, callback: confirms.append((title, question)) or callback(True)

    page.start_version(version)

    assert confirms == [
        (
            "version_already_running_confirm_title (version=Vanilla 1.20.1)",
            "version_already_running_confirm_message (version=Vanilla 1.20.1)",
        )
    ]
    assert scheduled == [(page._start_version_async, (version, True))]


def test_home_page_prompts_for_launch_profile_when_enabled(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    fake_app.config.set("ask_profile_on_launch", "yes")
    page = Home(fake_app)
    prompts = []
    scheduled = []

    monkeypatch.setattr(
        "launcher.pages.home.show_launch_profile_selector",
        lambda _app, _version, callback: prompts.append(callback) or True,
    )
    monkeypatch.setattr(
        "launcher.pages.home.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )

    page.start_version(version)

    assert prompts
    assert scheduled == []

    prompts[0]("second")

    assert scheduled == [(page._start_version_async, (version, False, "second"))]


def test_launch_profile_selector_builds_profile_dialog(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    fake_app.config.set("ask_profile_on_launch", "yes")
    dialogs = []
    selected = []

    monkeypatch.setattr("launcher.pages.launch_profiles.show_dialog", lambda _page, dialog: dialogs.append(dialog))

    handled = show_launch_profile_selector(fake_app, version, selected.append)

    assert handled is True
    assert dialogs
    values = _flatten_text_values(dialogs[0].content)
    assert "launch_profile_select_message (version=Vanilla 1.20.1)" in values
    profile_row = dialogs[0].content.content.controls[1].controls[0]
    profile_row.on_click(None)
    assert selected == ["default"]


def test_modpacks_page_builds(fake_app, monkeypatch):
    page = ModpacksPage(fake_app)
    monkeypatch.setattr(page, "_load_initial", lambda: None)
    view = page.view()

    assert isinstance(view, ft.Container)
    assert page.search_input.height == fake_app.theme.search_input_height
    assert page.search_input.width is None
    assert page.search_input.expand == 1
    assert page.search_input.prefix_icon == ft.Icons.SEARCH
    assert page.search_input.on_change is not None
    assert isinstance(page.results_container, ft.ListView)
    assert page.results_container.scroll == ft.ScrollMode.AUTO
    assert page.pagination_container is not None
    assert fake_app.footer.center_control is page.pagination_container
    assert page.prev_button.height == fake_app.theme.shell_action_height
    assert page.next_button.height == fake_app.theme.shell_action_height


def test_modpacks_page_keeps_search_input_active_while_loading(fake_app, monkeypatch):
    page = ModpacksPage(fake_app)
    page.view()

    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.daemon))

    monkeypatch.setattr("launcher.pages.modpacks.threading.Thread", FakeThread)

    page.search_input.disabled = False

    page._load_page(0)

    assert page.search_input.disabled is False
    assert len(started) == 1


def test_modpacks_page_card_includes_open_on_site_action(fake_app):
    opened = []
    fake_app.auth.device_ui = type("DeviceUi", (), {"open_url": lambda _self, url: opened.append(url) or True})()
    page = ModpacksPage(fake_app)

    card = page.create_card(
        {
            "slug": "better-mc",
            "title": "Better MC",
            "author": "LunaPixel",
            "description": "Large RPG modpack",
            "downloads": 123456,
            "icon_url": None,
        }
    )
    action_column = card.content.controls[2]

    assert len(action_column.controls) == 2
    assert action_column.controls[1].content == "open_on_site"

    action_column.controls[1].on_click(None)

    assert opened == ["https://modrinth.com/modpack/better-mc"]


def test_mods_manager_page_builds(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    view = page.view()

    assert isinstance(view, ft.Column)
    assert isinstance(view.controls[0], ft.Container)
    assert view.controls[0].padding.left == fake_app.theme.shell_padding
    assert view.controls[0].padding.right == fake_app.theme.shell_padding
    assert page.search_input.height == fake_app.theme.input_height
    assert page.search_input.width is None
    assert page.search_input.expand == 1
    assert page.search_input.prefix_icon == ft.Icons.SEARCH
    assert page.search_input.on_change is not None
    assert isinstance(page.search_bar, ft.Row)
    assert isinstance(page.search_results_container, ft.ListView)
    assert page.search_results_container.scroll == ft.ScrollMode.AUTO
    assert page.search_pagination_container is not None
    assert fake_app.footer.center_control is page.search_pagination_container
    assert page.search_prev_button.height == fake_app.theme.shell_action_height
    assert page.search_next_button.height == fake_app.theme.shell_action_height
    assert all(tab.height == fake_app.theme.tab_height for tab in page.tab_buttons.content.controls)
    assert page.tab_buttons.content.spacing == 0


def test_mods_manager_header_shows_launch_action(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"
    launches = []
    version.start = lambda *, allow_duplicate=False: launches.append(allow_duplicate) or None

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    async def run_blocking_immediately(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("launcher.pages.mods_manager.run_blocking", run_blocking_immediately)

    fake_app.page.run_task = _run_task_immediately

    ModsManagerPage(fake_app, version)

    assert fake_app.header.actions is not None
    launch_button = fake_app.header.actions[0]
    assert launch_button.content == "play"
    assert launch_button.icon == ft.Icons.PLAY_ARROW_ROUNDED

    launch_button.on_click(None)

    assert launches == [False]


def test_mods_manager_page_uses_content_tabs_without_datapacks(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)

    assert [tab["key"] for tab in page.content_tabs_data] == [
        "mods",
        "resourcepacks",
        "shaders",
        "backups",
        "screenshots",
        "settings",
        "diagnostics",
        "delete",
    ]
    assert "datapacks" not in [tab["key"] for tab in page.content_tabs_data]
    assert [button.content for button in page.inner_tab_buttons.controls] == [
        "installed_content_tab",
        "modrinth_content_tab",
    ]
    assert page.minecraft_version_select is None
    view = page.view()
    assert view is not None
    header_row = view.controls[1].content
    assert header_row.controls[0] is page.inner_tab_buttons
    assert header_row.controls[1] is page.filter_info_container


def test_mods_manager_vanilla_mods_tab_hides_modrinth_search(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.name = "Minecraft 26.1.2"
    version.client = "minecraft"
    version.loader = ""
    version.version = "26.1.2"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page.current_inner_tab = "modrinth"
    page.search_state.total_results = 4200
    page.search_state.current_page_size = page.search_state.limit

    page._update_tab_content()
    page._update_search_pagination()

    assert page.current_content_key == "mods"
    assert page.current_inner_tab == "installed"
    assert [button.content for button in page.inner_tab_buttons.controls] == ["installed_content_tab"]
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    assert page.search_pagination_container.visible is False


def test_mods_manager_vanilla_shaders_tab_hides_modrinth_search(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.name = "Minecraft 26.1.2"
    version.client = "minecraft"
    version.loader = ""
    version.version = "26.1.2"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("shaders")
    page.current_inner_tab = "modrinth"
    page.search_state.total_results = 4200
    page.search_state.current_page_size = page.search_state.limit

    page._update_tab_content()
    page._update_search_pagination()

    assert page.current_content_key == "shaders"
    assert page.current_inner_tab == "installed"
    assert [button.content for button in page.inner_tab_buttons.controls] == ["installed_content_tab"]
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    assert page.search_pagination_container.visible is False


def test_mods_manager_delete_tab_shows_warning_and_options(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("delete")

    values = _flatten_text_values(page.tab_content)

    assert "version_delete_warning_title" in values
    assert "version_delete_warning_desc" in values
    assert "version_delete_directory" in values
    assert "version_delete_backups" in values
    assert "delete_version_action" in values
    assert page.delete_directory_toggle.content.controls[1].value is True
    assert page.delete_backups_toggle.content.controls[1].value is False
    delete_panel = page.tab_content.content.content
    delete_button = delete_panel.controls[3].content.controls[0]
    assert delete_button.style.color[ft.ControlState.DEFAULT] == fake_app.theme.color_white
    assert delete_button.style.icon_color[ft.ControlState.DEFAULT] == fake_app.theme.color_white


def test_mods_manager_delete_tab_removes_selected_directory_and_backups(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    backup_root = fake_app.util.minecraft_dir / "backups" / "worlds" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    (version_root / "options.txt").write_text("", encoding="utf-8")
    (backup_root / "backup.zip").write_bytes(b"zip")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("delete")
    page.delete_directory_toggle.content.controls[1].value = True
    page.delete_backups_toggle.content.controls[1].value = True

    page._delete_version_worker(delete_directory=True, delete_backups=True)

    assert fake_app.versions.get(version.version_id) is None
    assert not version_root.exists()
    assert not backup_root.exists()


def test_mods_manager_delete_tab_can_keep_directory_and_backups(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    backup_root = fake_app.util.minecraft_dir / "backups" / "worlds" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    (version_root / "options.txt").write_text("", encoding="utf-8")
    (backup_root / "backup.zip").write_bytes(b"zip")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._delete_version_worker(delete_directory=False, delete_backups=False)

    assert fake_app.versions.get(version.version_id) is None
    assert version_root.exists()
    assert backup_root.exists()


def test_mods_manager_installed_tabs_are_loaded_lazily(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    mods_dir = version_root / "mods"
    resourcepacks_dir = version_root / "resourcepacks"
    mods_dir.mkdir(parents=True, exist_ok=True)
    resourcepacks_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "example.jar").write_bytes(b"not a real jar")
    (resourcepacks_dir / "pack.zip").write_bytes(b"pack")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))
    calls = {"mods": 0, "resourcepacks": 0}
    original_scan_mods = fake_app.content.scan_installed_mods
    original_scan_resourcepacks = fake_app.content.scan_installed_resourcepacks

    def scan_mods(path):
        calls["mods"] += 1
        return original_scan_mods(path)

    def scan_resourcepacks(path):
        calls["resourcepacks"] += 1
        return original_scan_resourcepacks(path)

    fake_app.content.scan_installed_mods = scan_mods
    fake_app.content.scan_installed_resourcepacks = scan_resourcepacks

    page = ModsManagerPage(fake_app, version)

    assert calls == {"mods": 1, "resourcepacks": 0}

    page._switch_content_tab("resourcepacks")
    page._switch_content_tab("mods")
    page._switch_content_tab("resourcepacks")

    assert calls == {"mods": 1, "resourcepacks": 1}


def test_mods_manager_settings_tab_embeds_version_settings(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("settings")

    assert page.current_content_key == "settings"
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    assert page.version_settings_page is not None
    assert page.version_settings_page.embedded is True
    settings_values = _flatten_text_values(page.tab_content)
    assert "version_section_general" in settings_values
    assert fake_app.footer.center_control is page.version_settings_page.footer_save_button


def test_mods_manager_diagnostics_tab_is_top_level(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("diagnostics")

    assert page.current_content_key == "diagnostics"
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    assert fake_app.footer.center_control is None
    diagnostics_values = _flatten_text_values(page.tab_content)
    assert "version_section_diagnostics" in diagnostics_values
    assert "open_latest_log" in diagnostics_values


def test_mods_manager_screenshots_tab_lists_version_screenshots(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    screenshots_dir = version_root / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    (screenshots_dir / "2026-05-21_12.00.00.png").write_bytes(b"png")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("screenshots")

    assert page.current_content_key == "screenshots"
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    screenshot_values = _flatten_text_values(page.tab_content)
    assert "2026-05-21_12.00.00.png" in screenshot_values
    assert "version_screenshots_content_tab" not in screenshot_values
    assert str(screenshots_dir) not in screenshot_values
    first_card = page.installed_containers["screenshots"].controls[0]
    action_icons = [action.icon for action in first_card.content.controls[2].controls]
    assert ft.Icons.FOLDER_OPEN not in action_icons
    dialogs = []
    monkeypatch.setattr("launcher.pages.mods_manager.show_dialog", lambda _page, dialog: dialogs.append(dialog), raising=False)

    thumbnail = first_card.content.controls[0]
    assert callable(thumbnail.on_click)
    thumbnail.on_click(None)

    assert dialogs
    assert dialogs[0].title.value == "2026-05-21_12.00.00.png"
    assert dialogs[0].modal is False
    assert dialogs[0].content.width == fake_app.theme.modal_width_md
    assert dialogs[0].content.height == 460
    assert callable(dialogs[0].on_dismiss)
    dialogs[0].on_dismiss(None)
    assert page.screenshot_preview_dialog is None
    assert any(action.icon == ft.Icons.OPEN_IN_NEW for action in dialogs[0].actions)


def test_mods_manager_world_backups_tab_shows_worlds_without_modrinth_search(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    world_dir = version_root / "saves" / "Survival"
    world_dir.mkdir(parents=True, exist_ok=True)
    (world_dir / "level.dat").write_bytes(b"level")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("backups")

    assert page.current_content_key == "backups"
    assert page.inner_tab_buttons.visible is False
    assert page.filter_info_container.visible is False
    assert page.search_input.value == ""
    backup_values = _flatten_text_values(page.tab_content)
    assert "Survival" in backup_values
    assert str(world_dir) in backup_values
    first_card = page.installed_containers["backups"].controls[0]
    assert callable(first_card.on_click)
    first_card_actions = first_card.content.controls[1].controls
    assert [getattr(action, "icon", None) for action in first_card_actions] == [ft.Icons.FOLDER_OPEN]

    first_card.on_click(None)

    detail_values = _flatten_text_values(page.tab_content)
    assert page.selected_backup_world_path == world_dir
    assert "world_backups_for_world (world=Survival)" not in detail_values
    assert not any(value.startswith("world_backups_world_details") for value in detail_values)
    assert "world_backups_empty" in detail_values
    assert str(world_dir) not in detail_values
    detail_action_row = page.installed_containers["backups"].controls[0].controls[0].content
    detail_actions = detail_action_row.controls
    assert detail_action_row.alignment == ft.MainAxisAlignment.START
    assert [getattr(control, "content", None) for control in detail_actions] == [
        "world_backups_back_to_worlds",
        "world_backups_create_now",
    ]
    assert [getattr(control, "icon", None) for control in detail_actions] == [
        ft.Icons.ARROW_BACK,
        ft.Icons.ADD,
    ]
    assert not any(
        getattr(control, "tooltip", None) == "open_directory"
        for control in detail_actions
    )


def test_mods_manager_world_backup_create_action_refreshes_list(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    world_dir = version_root / "saves" / "Survival"
    world_dir.mkdir(parents=True, exist_ok=True)
    (world_dir / "level.dat").write_bytes(b"level")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    world = fake_app.world_backups.scan_worlds(version)[0]

    asyncio.run(page._create_world_backup_async(world))

    backups = fake_app.world_backups.scan_backups(version, world.path)
    assert len(backups) == 1
    assert backups[0].kind == "manual"
    detail = page.installed_containers["backups"].controls[0]
    backup_card = detail.controls[1].controls[0]
    assert backup_card.bgcolor == fake_app.theme.bg_list
    assert backup_card.border == ft.Border.all(1, fake_app.theme.border_color)
    action_icons = [action.icon for action in backup_card.content.controls[1].controls]
    assert action_icons == [ft.Icons.RESTORE, ft.Icons.DELETE_OUTLINE]


def test_mods_manager_clears_modrinth_search_when_switching_content_tabs(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page.current_inner_tab = "modrinth"
    page.search_input.value = "sodium"
    page.search_state.query = "sodium"
    page.search_results_container.controls.append(ft.Text("Sodium"))

    page._switch_content_tab("resourcepacks")

    assert page.search_input.value == ""
    assert page.search_state.query == ""
    assert page.search_results_container.controls == []


def test_mods_manager_shader_toggle_hidden_without_iris(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    shaderpacks_dir = version_root / "shaderpacks"
    shaderpacks_dir.mkdir(parents=True, exist_ok=True)
    (shaderpacks_dir / "Complementary.zip").write_bytes(b"shader")
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("shaders")
    card = page.installed_containers["shaders"].controls[0]
    actions = card.content.controls[1].controls

    assert len(actions) == 1
    assert actions[0].icon == ft.Icons.DELETE


def test_mods_manager_action_buttons_use_neutral_state_colors(fake_app):
    cards = ModsManagerCards(fake_app)
    enabled_card = cards.installed_content_card(
        {
            "filename": "Faithful.zip",
            "name": "Faithful",
            "enabled": True,
            "size": 1024,
        },
        icon=ft.Icons.PALETTE,
        enable_tooltip="enable",
        disable_tooltip="disable",
        on_toggle=lambda _e: None,
        on_delete=lambda _e: None,
    )
    enabled_actions = enabled_card.content.controls[1].controls

    assert enabled_actions[0].bgcolor == fake_app.theme.bg_primary
    assert enabled_actions[0].foreground_color == fake_app.theme.success
    assert enabled_actions[1].bgcolor == fake_app.theme.bg_primary
    assert enabled_actions[1].foreground_color == fake_app.theme.error

    disabled_card = cards.installed_content_card(
        {
            "filename": "Complementary.zip",
            "name": "Complementary",
            "enabled": False,
            "size": 1024,
        },
        icon=ft.Icons.WB_SUNNY_OUTLINED,
        enable_tooltip="enable",
        disable_tooltip="disable",
        on_toggle=lambda _e: None,
        on_delete=lambda _e: None,
    )
    disabled_actions = disabled_card.content.controls[1].controls

    assert disabled_actions[0].bgcolor == fake_app.theme.bg_primary
    assert disabled_actions[0].foreground_color == fake_app.theme.text_disabled

    resourcepack_card = cards.resourcepack_card(
        {
            "filename": "Ukrainian Translate.zip",
            "type": "resourcepack_archive",
            "enabled": True,
            "size": 1024,
        },
        on_toggle=lambda _e: None,
        on_open_folder=lambda _e: None,
        on_delete=lambda _e: None,
    )
    resourcepack_actions = resourcepack_card.content.controls[1].controls

    assert resourcepack_actions[0].foreground_color == fake_app.theme.success
    assert resourcepack_actions[1].foreground_color == fake_app.theme.text_secondary
    assert resourcepack_actions[2].foreground_color == fake_app.theme.error

    mod_card = cards.installed_mod_card(
        {
            "filename": "sodium.jar",
            "name": "Sodium",
            "enabled": False,
            "size": 1024,
            "update_available": True,
        },
        has_backup=True,
        on_update=lambda _e: None,
        on_restore=lambda _e: None,
        on_toggle=lambda _e: None,
        on_delete=lambda _e: None,
    )
    mod_actions = mod_card.content.controls[1].controls

    assert [action.foreground_color for action in mod_actions] == [
        fake_app.theme.info,
        fake_app.theme.primary,
        fake_app.theme.text_disabled,
        fake_app.theme.error,
    ]


def test_mods_manager_modrinth_install_uses_alerts_without_progress_dialog(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    page = ModsManagerPage(fake_app, version)
    page._switch_content_tab("resourcepacks")
    fake_app.modrinth_mods.find_latest_version = lambda *_args, **_kwargs: {
        "id": "version-id",
        "version_number": "1.0.0",
        "files": [],
    }
    fake_app.modrinth_mods.select_primary_file = lambda _version_data: ModInstallFile(
        url="https://example.com/faithful.zip",
        filename="faithful.zip",
        version_number="1.0.0",
    )

    def fake_download(_url, download_path, *, progress_callback=None):
        assert progress_callback is None
        Path(download_path).write_bytes(b"pack")
        return download_path

    monkeypatch.setattr("launcher.pages.mods_manager_search.ModrinthAPI.download_mod_file", fake_download)
    monkeypatch.setattr("launcher.pages.mods_manager_search.run_blocking", _run_blocking_immediately)
    fake_app.feedback.begin_operation = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Modrinth content installs must not open progress dialogs")
    )
    fake_app.feedback.is_busy = lambda: False
    infos = []
    warnings = []
    fake_app.feedback.info = lambda message, **_kwargs: infos.append(message)
    fake_app.feedback.warning = lambda message, **_kwargs: warnings.append(message)

    fake_app.page.run_task = _run_task_immediately

    page._install_mod({"project_id": "faithful-project", "slug": "faithful", "title": "Faithful"})

    assert warnings == []
    assert infos[0] == "installing_resourcepack (name=Faithful)"
    assert infos[-1] == "resourcepack_installed (name=Faithful)"
    assert page.content_installing is False


def test_mods_manager_installed_tab_does_not_auto_check_mod_updates(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"

    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "example.jar").write_bytes(b"not a real jar")
    version.path = str(version_root)
    scheduled = []

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))
    monkeypatch.setattr(
        "launcher.pages.mods_manager_installed.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )

    page = ModsManagerPage(fake_app, version)
    page.is_loading = False
    page._rebuild_installed_mods()

    assert scheduled == []
    assert page.is_loading is False
