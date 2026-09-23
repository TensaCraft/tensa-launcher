from types import SimpleNamespace

import flet as ft
import pytest


@pytest.mark.parametrize("compact", ["yes", "no"])
@pytest.mark.parametrize("opened", [True, False])
def test_support_button_opens_requested_discord_invite(fake_app, compact, opened):
    calls = []
    warnings = []
    fake_app.config.set("compact_sidebar", compact)
    fake_app.auth.device_ui = SimpleNamespace(open_url=lambda url: calls.append(url) or opened)
    fake_app.feedback.warning = warnings.append
    sidebar = fake_app.navigation
    button = sidebar.view().content.controls[-2].content

    button.on_click(None)

    assert button.tooltip == "discord_support"
    assert button.content.controls[0].icon == ft.Icons.SUPPORT_AGENT
    assert button.content.controls[0].color == fake_app.theme.primary
    assert button.bgcolor == ft.Colors.TRANSPARENT
    assert button.border is None
    assert button.content.spacing == (0 if compact == "yes" else 12)
    assert len(button.content.controls) == (1 if compact == "yes" else 2)
    if compact == "no":
        assert button.content.controls[1].color == fake_app.theme.text_color
    assert calls == ["https://discord.com/invite/mftAjQA4Pp"]
    assert warnings == ([] if opened else ["support_open_failed"])
