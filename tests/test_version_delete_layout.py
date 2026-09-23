import flet as ft

from launcher.pages.mods_manager import ModsManagerPage


def test_delete_settings_share_one_surface_without_nested_cards(fake_app):
    page = ModsManagerPage(fake_app, fake_app.versions.all()[0])
    panel = page._build_delete_version_panel()

    assert len(panel.controls) == 1
    surface = panel.controls[0]
    assert surface.bgcolor == fake_app.theme.bg_list
    assert surface.padding == fake_app.theme.section_padding
    rows = surface.content.controls
    assert rows[2] is page.delete_directory_toggle
    assert rows[4] is page.delete_backups_toggle
    assert all(isinstance(rows[index], ft.Divider) for index in (1, 3, 5))
    for row in (rows[2], rows[4]):
        assert row.bgcolor is None
        assert row.border is None
        assert row.content.controls[0].expand is True
        assert row.content.controls[1].tooltip
    button = rows[-1].controls[0]
    assert button.height == fake_app.theme.input_height
    assert button.icon == ft.Icons.DELETE_OUTLINE


def test_delete_layout_keeps_options_and_requires_confirmation(fake_app):
    page = ModsManagerPage(fake_app, fake_app.versions.all()[0])
    confirmations = []
    tasks = []
    fake_app.feedback.confirm = lambda *args: confirmations.append(args)
    page._run_session_task = lambda *args, **kwargs: tasks.append(args)
    panel = page._build_delete_version_panel()
    page.delete_backups_toggle.content.controls[1].value = True
    rebuilt = page._build_delete_version_panel()

    assert rebuilt.controls[0].content.controls[4] is page.delete_backups_toggle
    assert page._delete_toggle_value(page.delete_directory_toggle) is True
    assert page._delete_toggle_value(page.delete_backups_toggle) is True
    panel.controls[0].content.controls[-1].controls[0].on_click(None)
    assert len(confirmations) == 1
    assert tasks == []
    confirmations[0][-1](False)
    assert tasks == []
