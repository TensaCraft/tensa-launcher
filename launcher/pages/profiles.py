import flet as ft

from launcher import ui
from launcher.ui.core.page_runtime import invoke_on_ui, run_blocking, run_task, schedule_update


class ProfilesPage:
    def __init__(self, app, initial_action: str | None = None):
        self.app = app
        self.page = app.page
        self.trans = self.app.trans
        self.initial_action = initial_action
        self._initial_action_handled = False
        self._closed = False

        self.app.header.set_params(
            title=self.app.trans('profile_title'),
            actions=self._build_header_actions(),
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.active_profile_key = None
        self.profiles = self.app.profiles.get_all_profiles()
        self.microsoft_auth_in_progress = False

        self.lv = ui.ListView(
            expand=True,
            padding=self.app.theme.profile_content_padding,
            spacing=self.app.theme.spacing_sm,
        )

    def _build_header_actions(self, *, size="sm"):
        return [
            ui.Button(
                icon=ft.Icons.ADD,
                on_click=self._action(self.add_offline_modal),
                text=self.trans("offline_account"),
                size=size,
            ),
            ui.Button(
                icon=ft.Icons.ADD,
                on_click=self._action(self.add_microsoft_profile),
                text=self.trans("microsoft_account"),
                size=size,
            ),
        ]

    def view(self):
        self.update_list_view()
        return ui.ContextMenu(
            content=self.lv,
            items=[
                self._menu_item("offline_account", ft.Icons.PERSON_ADD, self.add_offline_modal),
                self._menu_item("microsoft_account", ft.Icons.ACCOUNT_CIRCLE, self.add_microsoft_profile),
            ],
            is_active=self._session_open,
            expand=True,
        )

    def before_hide(self):
        self._closed = True

    def _session_open(self):
        if self._closed or getattr(self.app, "_terminating", False):
            return False
        try:
            getattr(self.page, "session", None)
        except RuntimeError:
            return False
        return True

    def _action(self, callback):
        async def select(_event):
            if self._session_open():
                callback()
        return select

    def _menu_item(self, label_key, icon, callback, *, disabled=False):
        return ft.PopupMenuItem(
            key=label_key,
            content=ui.Text(self.trans(label_key)),
            icon=icon,
            height=36,
            disabled=disabled,
            on_click=self._action(callback),
        )

    def _profile_menu_items(self, key, *, online):
        items = [
            self._menu_item(
                "set_as_default", ft.Icons.CHECK_CIRCLE_OUTLINE,
                lambda: self.set_default_profile(key),
                disabled=key == self.active_profile_key,
            ),
        ]
        if online:
            items.append(self._menu_item(
                "profile_sign_in_again", ft.Icons.LOGIN, self.add_microsoft_profile,
                disabled=self.microsoft_auth_in_progress,
            ))
        items.extend([
            ft.PopupMenuItem(),
            self._menu_item("delete", ft.Icons.DELETE_OUTLINE, lambda: self.confirm_delete_profile(key)),
        ])
        return items

    def after_show(self) -> None:
        if self._initial_action_handled:
            return
        self._initial_action_handled = True
        if self.initial_action == "offline":
            self.add_offline_modal()
        elif self.initial_action == "microsoft":
            self.add_microsoft_profile()

    def _refresh_avatar_views(self):
        if not self._session_open():
            return
        self.update_list_view()
        self.app.refresh_shell()
        schedule_update(self.page)

    def _refresh_profiles(self):
        self.profiles = self.app.profiles.get_all_profiles()
        self._refresh_avatar_views()

    def _avatar_src(self, profile: dict) -> str | None:
        avatar_id = profile.get("id") or profile.get("name")
        self.app.util.prefetch_skin(
            avatar_id,
            on_ready=lambda _src: invoke_on_ui(self.page, self._refresh_avatar_views),
        )
        return self.app.util.get_cached_skin_url(avatar_id)

    def update_list_view(self):
        self.lv.controls.clear()
        self.active_profile_key = next(
            (key for key, profile in self.profiles.items() if profile.get("default")), None,
        )
        if not self.profiles:
            self.lv.controls.append(ui.Container(
                padding=self.app.theme.padding_2xl,
                content=ui.Column(
                    [
                        ui.Icon(ft.Icons.PERSON_OUTLINE, size=48, color=self.app.theme.text_secondary),
                        ui.Text(self.trans("profiles_empty"), size=self.app.theme.text_size_xl),
                        ui.Row(self._build_header_actions(size="md"), wrap=True, alignment=ft.MainAxisAlignment.CENTER),
                    ],
                    spacing=self.app.theme.spacing_md,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ))
            return

        for key, profile in self.profiles.items():
            online = profile.get("access_token") != "offline" and profile.get("type") != "offline"
            self.lv.controls.append(ui.ContextMenu(
                content=self._profile_card(key, profile, online=online),
                items=self._profile_menu_items(key, online=online),
                is_active=self._session_open,
            ))

    def _profile_card(self, key, profile, *, online):
        theme = self.app.theme
        active = key == self.active_profile_key
        name = str(profile.get("name") or key)
        needs_reauth = False
        if online:
            try:
                requires_reauth = getattr(self.app.auth, "profile_requires_reauth", lambda _profile: False)
                needs_reauth = bool(requires_reauth(profile))
            except Exception as exc:
                self.app.log.error(f"Profile status check failed: {exc!r}")

        details = [
            ui.Text(
                name, size=theme.text_size_lg, weight=theme.font_weight_semibold,
                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=name,
            ),
            ui.Row(
                [
                    ui.Icon(ft.Icons.VERIFIED_USER_OUTLINED if online else ft.Icons.PERSON_OUTLINE,
                            size=theme.icon_size_sm, color=theme.text_secondary),
                    ui.Text(self.trans("microsoft_account" if online else "offline_account"), color=theme.text_secondary),
                ],
                spacing=theme.spacing_xs,
                wrap=True,
            ),
        ]
        if needs_reauth:
            details.append(ui.Text(self.trans("profile_reauth_required"), color=theme.error, size=theme.text_size_xs))

        actions = []
        if active:
            actions.append(ui.Row(
                [
                    ui.Icon(ft.Icons.CHECK_CIRCLE, size=theme.icon_size_sm, color=theme.primary),
                    ui.Text(self.trans("profile_active"), color=theme.primary, weight=theme.font_weight_semibold),
                ],
                spacing=theme.spacing_xs,
                tight=True,
            ))
        else:
            actions.append(ui.Button(
                text=self.trans("profile_use"),
                icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                variant="outline", tone="neutral",
                tooltip=self.trans("set_as_default"),
                on_click=self._action(lambda: self.set_default_profile(key)),
            ))
        if online:
            actions.append(ui.IconButton(
                icon=ui.Icon(ft.Icons.LOGIN, color=theme.error if needs_reauth else theme.text_secondary),
                tooltip=self.trans("profile_sign_in_again"),
                width=theme.button_height, height=theme.button_height,
                disabled=self.microsoft_auth_in_progress,
                on_click=self._action(self.add_microsoft_profile),
            ))
        actions.append(ui.IconButton(
            icon=ui.Icon(ft.Icons.DELETE_OUTLINE, color=theme.text_secondary), tooltip=self.trans("delete"),
            width=theme.button_height, height=theme.button_height,
            on_click=self._action(lambda: self.confirm_delete_profile(key)),
        ))

        return ui.Container(
            content=ui.ResponsiveRow(
                [
                    ui.Row(
                        [
                            ui.Image(
                                src=self._avatar_src(profile), width=48, height=48, fit=ft.BoxFit.COVER,
                                border_radius=theme.radius(),
                                error_content=ui.Icon(ft.Icons.ACCOUNT_CIRCLE, size=48, color=theme.text_secondary),
                            ),
                            ui.Column(details, spacing=theme.spacing_xs, tight=True, expand=True),
                        ],
                        spacing=theme.spacing_md,
                        col={"xs": 12, "md": 7, "lg": 8},
                    ),
                    ui.Row(
                        actions, spacing=theme.spacing_sm, wrap=True,
                        alignment=ft.MainAxisAlignment.END,
                        col={"xs": 12, "md": 5, "lg": 4},
                    ),
                ],
                run_spacing=theme.spacing_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=theme.bg_list,
            border=ft.Border.all(1, theme.primary if active else theme.border_light),
            border_radius=theme.radius(),
            padding=theme.padding_lg,
        )

    def confirm_delete_profile(self, key):
        def handle_response(response):
            if not self._session_open():
                return
            if response:
                if self.app.profiles.delete_profile(key):
                    self.app.feedback.info(self.trans("profile_deleted"))
                else:
                    self.app.feedback.warning(self.trans("profile_save_failed"))
            self._refresh_profiles()

        self.app.feedback.confirm(self.trans("confirmation"), self.trans("are_you_sure"), handle_response)

    def set_default_profile(self, key):
        profile = self.profiles.get(key)
        if profile is not None:
            if self.app.profiles.set_default_profile(key):
                self.app.feedback.info(self.trans("profile_set_as_default", profile=profile.get('name')))
            else:
                self.app.feedback.warning(self.trans("profile_save_failed"))
        self._refresh_profiles()

    def add_offline_modal(self):
        def on_submit(data):
            name = data.get("username")
            resp = self.app.profiles.create_profile(name,
                                                    {"name": name, "access_token": "offline",
                                                     "refresh_token": "offline"})
            if resp:
                self.app.feedback.info(resp.get("text"))
            self._refresh_profiles()

        fields = [{"type": "textfield", "label": self.trans('enter_nickname'), "key": "username", "value": ""}]
        self.app.form_modal(self.app, title=self.trans('create_offline_profile'), fields=fields,
                            on_submit=on_submit, modal_height=(self.app.theme.modal_height / 4)).open()

    def add_microsoft_profile(self):
        if self.microsoft_auth_in_progress:
            return
        self.microsoft_auth_in_progress = True
        self.app.feedback.info(self.trans("microsoft_auth_starting"))
        self.update_list_view()
        schedule_update(self.page)

        run_task(self.page, self._add_microsoft_profile_async)

    async def _add_microsoft_profile_async(self):
        try:
            authenticate = getattr(self.app.auth, "authenticate", self.app.auth.authenticate_with_device_code)
            result = await run_blocking(authenticate)
        finally:
            self.microsoft_auth_in_progress = False
            if self._session_open():
                self._refresh_profiles()

        if isinstance(result, dict) and self._session_open():
            self.app.feedback.info(self.trans("profile_created"))
