import flet as ft

from launcher.pages.mods_manager import ModsManagerPage
from launcher.pages.settings import SettingsPage


def test_settings_and_build_use_same_primary_navigation(fake_app):
    settings = SettingsPage(fake_app)
    build = ModsManagerPage(fake_app, fake_app.versions.all()[0])
    for attribute in ("padding", "bgcolor", "border", "border_radius"):
        assert getattr(settings.settings_tabs, attribute) == getattr(build.tab_buttons, attribute)
    for bar in (settings.settings_tabs, build.tab_buttons):
        assert bar.content.spacing == 0
        assert all(tab.expand == 1 for tab in bar.content.controls)
        assert all(tab.height == fake_app.theme.tab_height for tab in bar.content.controls)


def test_settings_navigation_moves_active_indicator_and_content(fake_app):
    page = SettingsPage(fake_app)
    tabs = page.settings_tabs.content.controls
    initial_height = tabs[0].height
    assert tabs[0].content.controls[-1].bgcolor == fake_app.theme.primary
    assert tabs[1].content.controls[-1].bgcolor == ft.Colors.TRANSPARENT

    tabs[1].on_click(None)

    tabs = page.settings_tabs.content.controls
    assert page.active_tab == "backups"
    assert tabs[0].content.controls[-1].bgcolor == ft.Colors.TRANSPARENT
    assert tabs[1].content.controls[-1].bgcolor == fake_app.theme.primary
    assert tabs[1].height == initial_height
    assert page.tab_content.content.controls[0].content.controls[0].value == "world_backups"


def test_long_tab_labels_shrink_without_changing_height(fake_app):
    fake_app.trans = lambda key, **kwargs: key * 12
    page = SettingsPage(fake_app)
    for tab in page.settings_tabs.content.controls:
        label = tab.content.controls[0].content.controls[1]
        assert label.expand is True
        assert label.expand_loose is True
        assert label.max_lines == 1
        assert label.overflow == ft.TextOverflow.ELLIPSIS
        assert tab.tooltip == label.value
        assert tab.height == fake_app.theme.tab_height
