from __future__ import annotations

from types import SimpleNamespace
import threading

import flet as ft

from launcher import ui
from launcher.application.catalog import CatalogState
from launcher.ui.core.page_runtime import invoke_on_ui, schedule_update


class ModpacksPage:
    def __init__(self, app):
        self.app = app
        self.search_input = None
        self.search_bar = None
        self.container = None
        self.results_container = None
        self.content_area = None
        self.loading_container = None
        self.pagination_container = None
        self.prev_button = None
        self.next_button = None
        self.page_label = None
        self._search_timer = None

        self.catalog_state = CatalogState(limit=max(self.app.theme.modpacks_per_page, 20))
        self._is_active = False

        # header/footer
        self.app.header.set_params(title=self.app.trans("modpacks_title"))
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

    def view(self):
        search_parts = ui.build_search_field(
            self.app,
            label=self.app.trans("search_modpacks"),
            value=self.catalog_state.query,
            on_submit=self.on_search,
            on_change=self.on_search_change,
            autofocus=True,
            height=self.app.theme.search_input_height,
        )
        self.search_input = search_parts.field
        self.search_bar = search_parts.row

        self.results_container = ui.ListView(
            auto_scroll=False,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            build_controls_on_demand=True,
            item_extent=112,
            cache_extent=700,
            spacing=8,
            padding=0,
        )

        self.loading_container = ui.Container(
            content=ui.ProgressRing(),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
        self.content_area = ui.Container(content=self.loading_container, expand=True)
        self.prev_button = ui.Button(
            text=self.app.trans("previous_page"),
            on_click=lambda _e: self._go_previous_page(),
            size="sm",
            height=self.app.theme.shell_action_height,
        )
        self.next_button = ui.Button(
            text=self.app.trans("next_page"),
            on_click=lambda _e: self._go_next_page(),
            size="sm",
            height=self.app.theme.shell_action_height,
        )
        self.page_label = ui.Text(
            self.app.trans("page_indicator", current_page=1, total_pages=1),
            size=self.app.theme.text_size_xs,
            color=self.app.theme.text_secondary,
        )
        self.pagination_container = ui.Container(
            content=ui.Row(
                [self.prev_button, self.page_label, self.next_button],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            padding=ft.Padding.only(top=self.app.theme.padding_xs, bottom=self.app.theme.padding_xs),
            visible=False,
        )
        self.app.footer.set_params(center_control=self.pagination_container, left_btn=False, right_btn=False)

        self.container = ui.Column(
            [
                self.search_bar,
                self.content_area,
            ],
            spacing=8,
            expand=True,
        )
        return ui.Container(
            content=self.container,
            padding=self.app.theme.version_content_padding,
            expand=True,
        )

    def after_show(self):
        self._is_active = True
        self._load_initial()

    def before_hide(self):
        self._is_active = False
        self.catalog_state.cancel()
        if self._search_timer is not None:
            self._search_timer.cancel()
            self._search_timer = None

    def _load_initial(self):
        self._load_page(0)

    def _load_page(self, offset: int):
        self.results_container.controls.clear()
        load_token = self.catalog_state.begin(self.catalog_state.query, offset)

        self.content_area.content = self.loading_container
        self._update_pagination()
        schedule_update(self.app.page)
        threading.Thread(
            target=self._load_page_worker,
            args=(load_token, self.catalog_state.query, self.catalog_state.offset),
            daemon=True,
        ).start()

    def _load_page_worker(self, load_token: int, query: str, offset: int):
        try:
            modpacks_data = self.app.catalog.search_modpacks(
                query,
                offset=offset,
                limit=self.catalog_state.limit,
            )
        except Exception as exc:
            self.app.log.error(f"Error loading modpacks: {exc}")
            invoke_on_ui(self.app.page, self._apply_page_error, load_token)
            return

        invoke_on_ui(self.app.page, self._apply_page_results, load_token, modpacks_data)

    def _apply_page_error(self, load_token: int):
        if load_token != self.catalog_state.token or not self._is_active:
            return

        self.catalog_state.fail(clear_results=True)
        self.search_input.disabled = False
        self.content_area.content = self._create_message_state(
            self.app.trans("no_modpacks_found"),
            color=self.app.theme.error,
        )
        self._update_pagination()
        schedule_update(self.app.page)

    def _apply_page_results(self, load_token: int, modpacks_data):
        if load_token != self.catalog_state.token or not self._is_active:
            return

        self.results_container.controls.clear()
        self.catalog_state.apply(modpacks_data)
        if modpacks_data.items:
            self.results_container.controls.extend(self.create_card(modpack) for modpack in modpacks_data.items)
            self.content_area.content = self.results_container
        else:
            self.content_area.content = self._create_message_state(self.app.trans("no_modpacks_found"))

        self.search_input.disabled = False
        self._update_pagination()
        schedule_update(self.app.page)

    def _create_message_state(self, message: str, *, color: str | None = None):
        return ui.Container(
            content=ui.Text(
                message,
                size=self.app.theme.text_size_sm,
                color=color or self.app.theme.text_secondary,
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    def create_card(self, modpack):
        def on_download_click(_):
            self.app.modpack_install_modal(self.app, modpack["slug"], modpack["title"]).show()

        def on_open_click(_):
            self._open_modpack_page(modpack)

        title = modpack.get("title", "Unknown")
        author = modpack.get("author", "Unknown")
        description = modpack.get("description", "")
        downloads = modpack.get("downloads", 0)
        icon_url = modpack.get("icon_url")
        action_width = max(156, self.app.theme.button_height * 4 + self.app.theme.padding_md)

        image_control = (
            ui.Image(src=icon_url, width=48, height=48, fit=ft.BoxFit.COVER, border_radius=ft.BorderRadius.all(8))
            if icon_url
            else ui.Icon(ft.Icons.INVENTORY_2, size=48, color=self.app.theme.text_secondary)
        )

        action_column = ui.Column(
            controls=[
                ui.Button(
                    text=self.app.trans("install"),
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    on_click=on_download_click,
                    height=self.app.theme.button_height,
                    width=action_width,
                ),
                ui.Button(
                    text=self.app.trans("open_on_site"),
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                    on_click=on_open_click,
                    height=self.app.theme.button_height,
                    width=action_width,
                    variant="outline",
                    tone="neutral",
                    size="sm",
                ),
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.END,
            tight=True,
            width=action_width,
        )

        return ui.Container(
            content=ui.Row(
                [
                    image_control,
                    ui.Column(
                        [
                            ui.Text(
                                title,
                                size=self.app.theme.text_size_medium,
                                weight=ft.FontWeight.W_600,
                                color=self.app.theme.text_color,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ui.Text(
                                f"by {author} • {downloads:,} downloads",
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_secondary,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ]
                        + ([
                            ui.Text(
                                description[:160] + ("..." if len(description) > 160 else ""),
                                size=self.app.theme.text_size_xs,
                                color=self.app.theme.text_tertiary,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            )
                        ] if description else []),
                        spacing=4,
                        expand=True,
                    ),
                    action_column,
                ],
                spacing=12,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=self.app.theme.bg_list,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            padding=self.app.theme.padding_md,
        )

    def _open_modpack_page(self, modpack: dict):
        identifier = (modpack.get("slug") or modpack.get("project_id") or modpack.get("id") or "").strip()
        if not identifier:
            self.app.feedback.warning(self.app.trans("modpack_open_failed"))
            return
        opener = getattr(getattr(self.app, "auth", SimpleNamespace()), "device_ui", None)
        if opener is not None and callable(getattr(opener, "open_url", None)):
            if opener.open_url(f"https://modrinth.com/modpack/{identifier}"):
                return
        self.app.feedback.warning(self.app.trans("modpack_open_failed"))

    def on_search(self, _event=None):
        self.catalog_state.query = (self.search_input.value or "").strip()
        self._load_initial()

    def on_search_change(self, e):
        self.catalog_state.query = (e.control.value or "").strip()
        if self._search_timer is not None:
            self._search_timer.cancel()
        self._search_timer = threading.Timer(0.35, self._trigger_search_from_timer)
        self._search_timer.daemon = True
        self._search_timer.start()

    def _trigger_search_from_timer(self):
        if not self._is_active:
            return
        invoke_on_ui(self.app.page, self._load_initial)

    def _go_previous_page(self):
        if self.catalog_state.loading or not self.catalog_state.has_previous:
            return
        self._load_page(self.catalog_state.previous_offset)

    def _go_next_page(self):
        if self.catalog_state.loading or not self.catalog_state.has_next:
            return
        self._load_page(self.catalog_state.next_offset)

    def _update_pagination(self):
        if (
            self.prev_button is None
            or self.next_button is None
            or self.page_label is None
            or self.pagination_container is None
        ):
            return

        self.prev_button.disabled = self.catalog_state.loading or not self.catalog_state.has_previous
        self.next_button.disabled = self.catalog_state.loading or not self.catalog_state.has_next
        self.page_label.value = self.app.trans(
            "page_indicator",
            current_page=self.catalog_state.current_page,
            total_pages=self.catalog_state.total_pages,
        )
        self.pagination_container.visible = self.catalog_state.show_pagination
