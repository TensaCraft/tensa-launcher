import flet as ft

from launcher import ui
from launcher.core.game import Game
from launcher.pages.launch_feedback import handle_launch_response
from launcher.pages.launch_profiles import launch_start_kwargs, launch_task_args, show_launch_profile_selector
from launcher.ui.core.page_runtime import run_blocking, run_task, schedule_update


class VersionsPage:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.trans = self.app.trans

        self.app.header.set_params(
            title=self.app.trans('builds_title'),
            actions=self._build_header_actions(),
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.versions = self.app.versions.all()
        self.lv = ui.ListView(expand=True, padding=self.app.theme.version_content_padding)

    def _build_header_actions(self):
        return [
            ui.Button(
                icon=ft.Icons.ADD,
                on_click=lambda _e: self.add_version_btn(),
                text=self.trans("add_version"),
                size="sm",
            ),
            ui.Button(
                icon=ft.Icons.CONSTRUCTION,
                on_click=lambda _e: self.app.show_minecraft_components_page(),
                text=self.trans("minecraft_components_nav"),
                size="sm",
            ),
            ui.Button(
                icon=ft.Icons.WEBHOOK,
                on_click=lambda _e: self.app.show_modpacks_page(),
                text=self.app.trans("modpacks_title"),
                size="sm",
            ),
            ui.Button(
                icon=ft.Icons.UPLOAD_FILE,
                on_click=lambda _e: self.import_curseforge_btn(),
                text=self.trans("import_curseforge"),
                size="sm",
            ),
        ]

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
        elif "forge" in client_lower:
            return ft.Icons.BUILD
        elif "fabric" in client_lower:
            return ft.Icons.EXTENSION
        elif "quilt" in client_lower:
            return ft.Icons.GRID_VIEW
        elif "neoforge" in client_lower:
            return ft.Icons.CONSTRUCTION
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
                            ),
                        ],
                        spacing=self.app.theme.spacing_sm
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
                            )
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

            self.lv.controls.append(version_card)

    def edit_version(self, e):
        self.app.show_version_settings_page(e.control.key)

    def add_version_btn(self):
        self.app.show_version_create_page()

    def import_curseforge_btn(self):
        self.app.curseforge_import_modal(self.app).show()

    def copy_version(self, version):
        from launcher.ui import VersionCopyModal
        VersionCopyModal(self.app, version).show()

    def manage_mods(self, version):
        self.app.show_mods_manager_page(version)

    def open_directory(self, version):
        target = version.path or version.version_id
        response = self.app.util.open_mc_dir(target)
        if response is not None:
            self.app.feedback.info(response)

    def handle_play(self, version, *, allow_duplicate: bool = False, profile_key: str | None = None):
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        if not allow_duplicate and self._confirm_duplicate_launch(version):
            return
        if profile_key is None and show_launch_profile_selector(
            self.app,
            version,
            lambda selected_key: self.handle_play(
                version,
                allow_duplicate=allow_duplicate,
                profile_key=selected_key,
            ),
        ):
            return
        try:
            args = launch_task_args(version, allow_duplicate, profile_key)
            run_task(self.page, self._handle_play_async, *args)
        except Exception:
            self.app.feedback.info(self.trans("installation_already_running"))
            raise

    def _confirm_duplicate_launch(self, version) -> bool:
        if not Game.is_game_dir_active(Game.version_game_dir(version)):
            return False

        def handle_response(response: bool) -> None:
            if response:
                self.handle_play(version, allow_duplicate=True)

        self.app.feedback.confirm(
            self.trans("version_already_running_confirm_title", version=version.name),
            self.trans("version_already_running_confirm_message", version=version.name),
            handle_response,
        )
        return True

    async def _handle_play_async(self, version, allow_duplicate: bool = False, profile_key: str | None = None):
        response = await run_blocking(version.start, **launch_start_kwargs(allow_duplicate, profile_key))
        handle_launch_response(self.app, response)
        schedule_update(self.app.page)
