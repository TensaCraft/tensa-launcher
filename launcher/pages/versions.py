import flet as ft

from launcher import ui
from launcher.pages.version_actions import VersionActions


class VersionsPage(VersionActions):
    def __init__(self, app):
        super().__init__(app)
        self.app.header.set_params(
            title=self.app.trans('builds_title'),
            actions=self._build_header_actions(),
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.versions = self.app.versions.all()
        self.lv = ui.ListView(expand=True, padding=self.app.theme.version_content_padding)

    def view(self):
        self.rebuild_versions_list()
        return self.lv

    def _build_version_visual(self, version):
        size = self.app.theme.version_image_size_compact
        image = version.get_image()

        if image:
            return ui.Image(
                src=image,
                width=size,
                height=size,
                fit=ft.BoxFit.COVER,
                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            )

        return ui.Container(
            content=ui.Icon(
                self.get_version_icon(version.client),
                size=20,
                color=self.app.theme.text_secondary,
            ),
            width=size,
            height=size,
            bgcolor=self.app.theme.bg_card,
            border=ft.Border.all(1, self.app.theme.border_color),
            border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
            alignment=ft.Alignment.CENTER,
        )

    def get_version_icon(self, client):
        """Визначити іконку на основі типу клієнта."""
        client_lower = (client or "").lower()

        if "vanilla" in client_lower:
            return ft.Icons.LAYERS
        elif "neoforge" in client_lower:
            return ft.Icons.CONSTRUCTION
        elif "forge" in client_lower:
            return ft.Icons.BUILD
        elif "fabric" in client_lower:
            return ft.Icons.EXTENSION
        elif "quilt" in client_lower:
            return ft.Icons.GRID_VIEW
        elif "tensacraft" in client_lower or "tensa" in client_lower:
            return ft.Icons.ROCKET_LAUNCH
        else:
            return ft.Icons.VIDEOGAME_ASSET

    def rebuild_versions_list(self):
        self.lv.controls.clear()
        self.versions = self.app.versions.all()
        for version in self.versions:
            version_image = self._build_version_visual(version)

            # Компактний одно-рядковий дизайн
            version_row = ui.Row(
                controls=[
                    # Ліва частина: картинка + назва + версія
                    ui.Row(
                        controls=[
                            version_image,
                            ui.Column(
                                controls=[
                                    ui.Text(
                                        version.name,
                                        size=self.app.theme.text_size_medium,
                                        weight=self.app.theme.font_weight_semibold,
                                        color=self.app.theme.text_color
                                    ),
                                    ui.Text(
                                        f"{version.client} {version.version}",
                                        size=self.app.theme.text_size_xs,
                                        color=self.app.theme.text_secondary
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        spacing=self.app.theme.spacing_sm,
                        expand=True,
                    ),
                    # Права частина: кнопки дій
                    ui.Row(
                        controls=[
                            ui.FloatingActionButton(
                                icon=ft.Icons.PLAY_ARROW,
                                on_click=lambda e, ver=version: self.handle_play(ver),
                                key=version.version_id,
                                tooltip=self.trans("play"),
                                mini=True,
                            ),
                            ui.FloatingActionButton(
                                icon=ft.Icons.COPY,
                                on_click=lambda e, ver=version: self.copy_version(ver),
                                key=version.version_id,
                                tooltip=self.trans("copy"),
                                mini=True,
                            ),
                            ui.FloatingActionButton(
                                icon=ft.Icons.EXTENSION,
                                on_click=lambda e, ver=version: self.manage_mods(ver),
                                key=version.version_id,
                                tooltip=self.trans("manage_mods"),
                                mini=True,
                            ),
                            ui.FloatingActionButton(
                                icon=ft.Icons.FOLDER,
                                on_click=lambda e, ver=version: self.open_directory(ver),
                                key=version.version_id,
                                tooltip=self.trans("open_directory"),
                                data=version,
                                mini=True,
                            ),
                        ],
                        spacing=self.app.theme.spacing_xs
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )

            version_card = ui.Container(
                content=version_row,
                bgcolor=self.app.theme.bg_list,
                border=ft.Border.all(1, self.app.theme.border_color),
                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
                padding=self.app.theme.padding_md,
                margin=ft.Margin.only(left=6, right=6, bottom=4),
                on_click=lambda _e, ver=version: self.manage_mods(ver),
            )

            self.lv.controls.append(self._context_menu(version_card, self._build_version_menu_items(version)))
