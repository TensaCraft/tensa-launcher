from __future__ import annotations

from launcher.core.auth.device_ui import DeviceCodeUI


def test_device_ui_uses_run_task_for_async_page_apis_when_browser_fallback_needed(fake_app):
    scheduled = []

    async def launch_url(url: str) -> None:
        return None

    async def set_clipboard(text: str) -> None:
        return None

    fake_app.page.launch_url = launch_url
    fake_app.page.set_clipboard = set_clipboard
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args))

    ui = DeviceCodeUI(fake_app)
    ui._open_browser = lambda _url: False

    assert ui.open_url("https://example.com") is True
    assert ui.copy_to_clipboard("code-123") is True
    assert scheduled == [
        (launch_url, ("https://example.com",)),
        (set_clipboard, ("code-123",)),
    ]


def test_device_ui_prefers_system_browser_before_page_api(fake_app):
    launched = []

    async def launch_url(url: str) -> None:
        launched.append(url)

    fake_app.page.launch_url = launch_url
    ui = DeviceCodeUI(fake_app)
    ui._open_browser = lambda url: launched.append(f"browser:{url}") or True

    assert ui.open_url("https://example.com") is True
    assert launched == ["browser:https://example.com"]
