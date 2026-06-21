import flet as ft

from launcher import ui
from launcher.application.tensacraft_catalog import TensaCraftCatalogService
from launcher.application.tensacraft_install_state import mark_pending, pending_pack_ids, unmark_pending
from launcher.core.api import TensaCraftAPI
from launcher.core.game import Game
from launcher.core.versions import Version
from launcher.pages.launch_feedback import handle_launch_response
from launcher.pages.launch_profiles import launch_start_kwargs, launch_task_args, show_launch_profile_selector
from launcher.ui.core.page_runtime import close_dialog, run_blocking, run_task, schedule_update, show_dialog


class Home:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.trans = self.app.trans
        self.catalog = TensaCraftCatalogService()
        self.cards_data = []
        self.grid = None
        self._tensacraft_seen = set()
        self._tensacraft_install_dialog = None
        self._tensacraft_install_target = None
        self._tensacraft_description_text = None
        self.app.header.set_params(title=self.trans('home_title'))
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

    def view(self):
        self.grid = ui.GridView(
            expand=True,
            auto_scroll=False,
            spacing=self.app.theme.home_spacing,
            run_spacing=self.app.theme.home_run_spacing,
            max_extent=self.app.theme.home_card_size,
            child_aspect_ratio=0.84,
        )

        self.cards_data = self.catalog.filter_local_versions(
            self.app.versions.all(),
            show_tensacraft=self._should_show_tensacraft(),
        )
        for version in self.cards_data:
            self.grid.controls.append(self.create_card(version))

        if self._should_show_tensacraft():
            self._load_tensacraft_versions_async()

        return self.grid

    def create_card(self, version: Version):
        """Створює картку версії використовуючи VersionCard компонент."""
        is_remote = bool(getattr(version, "is_remote", False))

        def on_play_click(_):
            self.start_version(version)

        card = self.app.version_card.create(
            title=version.name,
            subtitle=f"{version.client} {version.version}",
            image=version.get_image(),
            on_action_click=on_play_click,
            action_icon=ft.Icons.DOWNLOAD_ROUNDED if is_remote else ft.Icons.PLAY_ARROW_ROUNDED,
        )
        if is_remote:
            pack_id = getattr(version, "remote_pack_id", None) or version.id or version.version
            if pack_id:
                card.key = self._remote_card_key(pack_id)
        return card

    def start_version(
        self,
        version: Version,
        *,
        allow_duplicate: bool = False,
        profile_key: str | None = None,
    ):
        """Запускає версію Minecraft."""
        if getattr(version, "is_remote", False):
            self._install_and_launch_tensacraft(version)
            return

        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return

        if not allow_duplicate and self._confirm_duplicate_launch(version):
            return

        if profile_key is None and show_launch_profile_selector(
            self.app,
            version,
            lambda selected_key: self.start_version(
                version,
                allow_duplicate=allow_duplicate,
                profile_key=selected_key,
            ),
        ):
            return

        try:
            args = launch_task_args(version, allow_duplicate, profile_key)
            run_task(self.page, self._start_version_async, *args)
        except Exception:
            self.app.feedback.info(self.trans("installation_already_running"))
            raise

    def _confirm_duplicate_launch(self, version: Version) -> bool:
        if not Game.is_game_dir_active(Game.version_game_dir(version)):
            return False

        def handle_response(response: bool) -> None:
            if response:
                self.start_version(version, allow_duplicate=True)

        self.app.feedback.confirm(
            self.trans("version_already_running_confirm_title", version=version.name),
            self.trans("version_already_running_confirm_message", version=version.name),
            handle_response,
        )
        return True

    async def _start_version_async(
        self,
        version: Version,
        allow_duplicate: bool = False,
        profile_key: str | None = None,
    ) -> None:
        resp = await run_blocking(version.start, **launch_start_kwargs(allow_duplicate, profile_key))
        handle_launch_response(self.app, resp)

    def _should_show_tensacraft(self) -> bool:
        return self.app.config.get("show_tensacraft_versions", "yes") == "yes"

    def _load_tensacraft_versions_async(self) -> None:
        run_task(self.page, self._load_tensacraft_versions)

    async def _load_tensacraft_versions(self) -> None:
        if not self.grid:
            return
        try:
            packs = await run_blocking(TensaCraftAPI().list_versions)
        except Exception as exc:
            self.app.log.error(f"Failed to fetch TensaCraft versions: {exc}")
            return

        local_ids = self.catalog.local_pack_ids(self.app.versions.all())
        excluded_ids = local_ids | set(pending_pack_ids(self.app))

        added = 0
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            pack_id = self.catalog.pack_id(pack)
            if not pack_id or pack_id in excluded_ids or pack_id in self._tensacraft_seen:
                continue
            stub = self.catalog.build_stub(pack, pack_id)
            self._tensacraft_seen.add(pack_id)
            self.grid.controls.append(self.create_card(stub))
            added += 1

        if added:
            schedule_update(self.page)

    def _find_local_tensacraft(self, pack_id: str) -> Version | None:
        return self.catalog.find_local(self.app.versions.all(), pack_id)

    def _install_and_launch_tensacraft(self, version: Version) -> None:
        if self.app.feedback.is_busy():
            self.app.feedback.info(self.trans("installation_already_running"))
            return

        pack_id = (
            getattr(version, "remote_pack_id", None)
            or version.id
            or version.version
        )
        if not pack_id:
            self.app.feedback.warning(self.trans("version_not_found"))
            return

        existing = self._find_local_tensacraft(pack_id)
        if existing:
            self.start_version(existing)
            return

        self._show_tensacraft_install_confirmation(
            version.name,
            pack_id,
            self._tensacraft_description(version),
        )

    def _tensacraft_description(self, version: Version) -> str:
        options = getattr(version, "options", {}) or {}
        candidates = (
            getattr(version, "description", None),
            options.get("description") if isinstance(options, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _show_tensacraft_install_confirmation(self, version_name: str, pack_id: str, description: str) -> None:
        theme = self.app.theme
        self._tensacraft_install_target = {
            "version_name": version_name,
            "pack_id": pack_id,
        }
        self._tensacraft_description_text = ui.Text(
            description or self.trans("tensacraft_description_unavailable"),
            color=theme.text_secondary,
            size=theme.text_size_sm,
        )
        description_panel = ui.Container(
            content=ui.Column(
                [
                    ui.Text(
                        self.trans("tensacraft_description_title"),
                        color=theme.text_color,
                        weight=theme.font_weight_semibold,
                    ),
                    self._tensacraft_description_text,
                ],
                spacing=theme.spacing_sm,
                tight=True,
            ),
            width=theme.modal_width,
            padding=theme.padding_md,
            border=ft.Border.all(1, theme.border_color),
            border_radius=theme.radius_md,
            bgcolor=theme.overlay(theme.alpha_input, theme.bg_shell),
        )
        self._tensacraft_install_dialog = ui.AlertDialog(
            title=ui.Text(
                self.trans("tensacraft_install_confirm_title", version=version_name),
                color=theme.text_color,
                weight=theme.font_weight_bold,
            ),
            modal=True,
            content=ui.Column(
                [
                    ui.Text(
                        self.trans("tensacraft_install_confirm_message"),
                        color=theme.text_secondary,
                    ),
                    description_panel,
                ],
                width=theme.modal_width,
                spacing=theme.spacing_md,
                tight=True,
            ),
            actions=[
                ui.Button(text=self.trans("install"), on_click=self._confirm_tensacraft_install),
                ui.Button(
                    text=self.trans("cancel"),
                    variant="outline",
                    tone="neutral",
                    on_click=lambda _e: self._close_tensacraft_install_dialog(),
                ),
            ],
        )
        show_dialog(self.page, self._tensacraft_install_dialog)
        schedule_update(self.page)

    def _confirm_tensacraft_install(self, _e) -> None:
        target = self._tensacraft_install_target
        if not target:
            return
        version_name = target["version_name"]
        pack_id = target["pack_id"]
        self._close_tensacraft_install_dialog()
        self._start_tensacraft_install(version_name, pack_id)

    def _close_tensacraft_install_dialog(self) -> None:
        if self._tensacraft_install_dialog is not None:
            close_dialog(self.page, self._tensacraft_install_dialog)
        self._tensacraft_install_dialog = None
        self._tensacraft_install_target = None
        schedule_update(self.page)

    def _start_tensacraft_install(self, version_name: str, pack_id: str) -> None:
        self.app.feedback.info(
            self.trans("version_not_installed_message", version=version_name)
        )
        mark_pending(self.app, pack_id)
        self.hide_pending_tensacraft_pack(pack_id)
        if self.app.feedback.is_busy():
            unmark_pending(self.app, pack_id)
            self.app.feedback.info(self.trans("installation_already_running"))
            return
        operation = self.app.feedback.begin_operation(
            self.trans("installation_started"),
            kind="install",
            status=self.trans("installation_started"),
        )
        try:
            run_task(self.page, self._install_and_launch_tensacraft_async, version_name, pack_id, operation)
        except Exception:
            unmark_pending(self.app, pack_id)
            operation.fail(self.trans("installation_failed"), notify=False)
            raise

    @staticmethod
    def _remote_card_key(pack_id: str) -> str:
        return f"tensacraft:{pack_id}"

    def hide_pending_tensacraft_pack(self, pack_id: str) -> None:
        mark_pending(self.app, pack_id)
        if not self.grid:
            return
        card_key = self._remote_card_key(pack_id)
        self.grid.controls = [control for control in self.grid.controls if getattr(control, "key", None) != card_key]
        schedule_update(self.page)

    async def _install_and_launch_tensacraft_async(self, version_name: str, pack_id: str, operation) -> None:
        final_message = self.trans("installation_complete")
        installed = None
        try:
            installed = await run_blocking(self._install_tensacraft_version, version_name, pack_id)
        except Exception as exc:
            self.app.log.error(f"Failed to install TensaCraft {pack_id}: {exc}")
            final_message = self.app.trans(
                "version_install_error",
                client="tensacraft",
                version=version_name,
                error=str(exc),
            )
            self.app.feedback.warning(
                final_message,
                report_title=f"TensaCraft install failed: {version_name}",
                report_metadata={
                    "screen": "Home",
                    "action": "tensacraft_install",
                    "pack_id": pack_id,
                    "version_name": version_name,
                    "exception": repr(exc),
                },
            )
            operation.fail(final_message, notify=False)
            return
        finally:
            unmark_pending(self.app, pack_id)

        operation.finish(final_message, show_success=False)

        if show_launch_profile_selector(
            self.app,
            installed,
            lambda selected_key: self.start_version(installed, profile_key=selected_key),
        ):
            return

        resp = await run_blocking(installed.start)
        handle_launch_response(self.app, resp)

    def _install_tensacraft_version(self, version_name: str, pack_id: str) -> Version:
        new_version = Version(
            version_name,
            {"name": version_name, "version": pack_id, "client": "tensacraft"},
        )
        new_version.install()
        return (
            self._find_local_tensacraft(pack_id)
            or self.app.versions.get_by_name(version_name)
            or new_version
        )
