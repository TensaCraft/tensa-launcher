from collections.abc import Callable

import flet as ft


def ContextMenu(
    content: ft.Control,
    items: list[ft.PopupMenuItem],
    *,
    is_active: Callable[[], bool],
    key: str | None = None,
    expand: bool = False,
) -> ft.ContextMenu:
    async def open_menu(event):
        if is_active():
            try:
                await menu.open(global_position=event.global_position)
            except RuntimeError:
                if is_active():
                    raise

    # Gesture arbitration lets a card menu win over its surrounding page menu.
    menu = ft.ContextMenu(
        content=ft.GestureDetector(content=content, on_secondary_tap_up=open_menu),
        items=items,
        secondary_trigger=None,
        tertiary_trigger=None,
        key=key,
        expand=expand,
    )
    return menu
