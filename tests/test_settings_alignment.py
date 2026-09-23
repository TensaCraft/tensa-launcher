from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from launcher.application.version_creation import VersionCreateOption
from launcher.pages.settings import SettingsPage
from launcher.pages.version_create import VersionCreatePage
from launcher.pages.version_settings import VersionSettingsPage


def _walk(control):
    yield control
    for child in getattr(control, "controls", ()):
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _cell(root, target):
    for control in _walk(root):
        if isinstance(control, ft.ResponsiveRow):
            for cell in control.controls:
                if any(child is target for child in _walk(cell)):
                    return cell
    raise AssertionError("Control is missing from its responsive form grid")


def _version_settings(fake_app):
    return VersionSettingsPage(fake_app, fake_app.versions.all()[0].version_id)


@pytest.mark.parametrize("breakpoint", ["sm", "md", "lg"])
def test_custom_java_path_and_browse_remain_in_one_responsive_cell(fake_app, breakpoint):
    page = SettingsPage(fake_app)
    section = page._build_java_performance_tab()
    path_cell = _cell(section, page.custom_java_path)

    assert path_cell is _cell(section, page.custom_java_browse)
    assert path_cell.col[breakpoint] == {"sm": 12, "md": 8, "lg": 6}[breakpoint]
    assert page.custom_java_path_picker.controls == [page.custom_java_path, page.custom_java_browse]
    assert page.custom_java_path.expand is True
    assert page.custom_java_browse.expand is not True
    assert page.custom_java_browse.width == page.custom_java_browse.height == fake_app.theme.input_height
    assert page.custom_java_path_picker.vertical_alignment == ft.CrossAxisAlignment.CENTER


@pytest.mark.parametrize("breakpoint", ["sm", "md", "lg"])
def test_custom_java_grid_uses_proportional_rows(fake_app, breakpoint):
    page = SettingsPage(fake_app)
    section = page._build_java_performance_tab()
    controls = [page.custom_java_name, page.custom_java_path_picker, page.custom_java_add, page.custom_java_scan]
    actual = [_cell(section, control).col[breakpoint] for control in controls]
    assert actual == {"sm": [12, 12, 12, 12], "md": [4, 8, 6, 6], "lg": [3, 6, 3, 12]}[breakpoint]
    assert page.custom_java_list.horizontal_alignment == ft.CrossAxisAlignment.STRETCH


@pytest.mark.parametrize("breakpoint", ["md", "lg"])
def test_backup_fields_fill_the_row_without_an_unused_third_column(fake_app, breakpoint):
    page = SettingsPage(fake_app)
    body = page._build_backups_tab()
    assert _cell(body, page.world_backups_toggle).col[breakpoint] == 6
    assert _cell(body, page.world_backups_keep_count).col[breakpoint] == 6
    assert _cell(body, page.world_backups_dir).col[breakpoint] == 8
    assert _cell(body, page.world_backups_dir_browse).col[breakpoint] == 4


@pytest.mark.parametrize("breakpoint", ["sm", "md", "lg"])
def test_version_runtime_selectors_share_equal_width(fake_app, breakpoint):
    page = _version_settings(fake_app)
    body = page._build_runtime_tab()
    expected = 12 if breakpoint == "sm" else 6
    assert _cell(body, page.java_select).col[breakpoint] == expected
    assert _cell(body, page.gpu_mode_select).col[breakpoint] == expected
    assert page.java_select.height == page.gpu_mode_select.height == fake_app.theme.input_height


@pytest.mark.parametrize("kind", ["launcher", "version"])
def test_memory_slider_stays_full_width_and_long_label_can_wrap(fake_app, kind):
    original_trans = fake_app.trans
    fake_app.trans = lambda key, **kwargs: (
        "Long localized memory label " * 6
        if key in {"default_max_ram_label", "max_ram_label"}
        else original_trans(key, **kwargs)
    )
    if kind == "launcher":
        page = SettingsPage(fake_app)
        body = page._build_java_performance_tab()
        field, slider, value = page.default_max_ram, page.default_max_ram_slider, page.default_max_ram_value
    else:
        page = _version_settings(fake_app)
        body = page._build_runtime_tab()
        field, slider, value = page.max_ram, page.max_ram_slider, page.max_ram_value

    assert _cell(body, field).col.get("sm") == 12
    assert all(span == 12 for span in _cell(body, field).col.values())
    assert field.width is None
    assert field.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    assert slider.width is None
    assert slider.padding == 0
    label_row = field.content.controls[0]
    label = label_row.controls[0]
    assert label.expand is True
    assert label.max_lines is None
    assert label_row.controls[1] is value
    assert value.text_align == ft.TextAlign.END


@pytest.mark.parametrize("kind", ["launcher", "version"])
def test_scrolling_form_content_uses_natural_vertical_size(fake_app, kind):
    page = SettingsPage(fake_app) if kind == "launcher" else _version_settings(fake_app)
    assert page.tab_content.expand is not True
    assert page.content.content.scroll == ft.ScrollMode.AUTO
    assert page.content.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH


def test_icon_picker_stretches_to_match_name_field(fake_app):
    page = _version_settings(fake_app)
    body = page._build_general_tab()
    name_cell = _cell(body, page.name)
    picker_cell = _cell(body, page.file_picker_button)
    assert picker_cell.col == name_cell.col
    assert picker_cell is name_cell
    picker = next(control for control in _walk(picker_cell) if isinstance(control, ft.Column))
    assert picker.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    assert picker.tight is True
    assert page.change_component_button.height == page.loaders_select.height == fake_app.theme.input_height


def test_general_fields_and_secondary_actions_use_matching_columns(fake_app):
    page = _version_settings(fake_app)
    body = page._build_general_tab()
    left = _cell(body, page.name)
    right = _cell(body, page.loaders_select)

    assert left is _cell(body, page.file_picker_button)
    assert right is _cell(body, page.change_component_button)
    assert left.col == right.col
    assert left.content.spacing == right.content.spacing
    assert page.file_picker_button.height == page.change_component_button.height
    assert page.file_picker_button.style.bgcolor == page.change_component_button.style.bgcolor
    assert page.file_picker_button.style.side == page.change_component_button.style.side


def test_report_dialog_fields_follow_available_width(fake_app):
    page = _version_settings(fake_app)
    page._open_version_report_dialog()

    assert page.version_report_dialog.scrollable is True
    assert page.version_report_contact.width is None
    assert page.version_report_message.width is None
    assert page.version_report_dialog.content.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    assert page.version_report_message.height == 180


@pytest.mark.parametrize("loader", ["minecraft", "fabric", "tensacraft"])
def test_install_dialog_fields_fill_content_and_match_heights(fake_app, loader):
    page = VersionCreatePage(fake_app)
    option = VersionCreateOption(
        id="build-id",
        name="Build name",
        minecraft_version="1.21.1",
        loader_id=loader,
        loader_name=loader.title(),
        description="Long localized description " * 10,
        loader_version="0.16.0" if loader == "fabric" else None,
        loader_versions=("0.16.0", "0.16.1") if loader == "fabric" else (),
    )

    page._open_install_dialog(option)

    assert page.install_dialog.scrollable is True
    assert page.install_dialog.content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    assert page.install_name.width is None
    assert page.install_name.height == fake_app.theme.input_height
    assert all(getattr(control, "width", None) is None for control in page.install_dialog.content.controls)
    if loader == "fabric":
        assert page.install_loader_build.width is None
        assert page.install_loader_build.height == page.install_name.height
        page.install_loader_build.on_select(SimpleNamespace(control=SimpleNamespace(value="0.16.1")))
        assert page._selected_loader_version(option) == "0.16.1"


def test_page_alignment_changes_preserve_section_surface_colors(fake_app):
    settings = SettingsPage(fake_app)
    version = _version_settings(fake_app)
    for body in (settings._build_launcher_tab(), settings._build_java_performance_tab(), version._build_runtime_tab()):
        for section in body.controls:
            assert section.bgcolor == fake_app.theme.bg_list
            assert section.border.left.color == fake_app.theme.border_color
