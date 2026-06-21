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

        self.app.header.set_params(
            title=self.app.trans('profile_title'),
            actions=self._build_header_actions(),
        )
        self.app.footer.set_params(center_btn=None, left_btn=False, right_btn=False)

        self.active_profile_key = None
        self.profiles = self.app.profiles.get_all_profiles()
        self.microsoft_auth_in_progress = False

        self.lv = ui.ListView(expand=True, padding=self.app.theme.profile_content_padding)
        self.switches = {}

    def _build_header_actions(self):
        return [
            ui.Button(
                icon=ft.Icons.ADD,
                on_click=lambda _e: self.add_offline_modal(),
                text=self.trans("offline_account"),
                size="sm",
            ),
            ui.Button(
                icon=ft.Icons.ADD,
                on_click=lambda _e: self.add_microsoft_profile(),
                text=self.trans("microsoft_account"),
                size="sm",
            ),
        ]

    def view(self):
        self.update_list_view()
        return self.lv

    def after_show(self) -> None:
        if self._initial_action_handled:
            return
        self._initial_action_handled = True
        if self.initial_action == "offline":
            self.add_offline_modal()
        elif self.initial_action == "microsoft":
            self.add_microsoft_profile()

    def _refresh_avatar_views(self):
        self.update_list_view()
        self.app.refresh_shell()
        schedule_update(self.page)

    def _avatar_src(self, profile: dict) -> str | None:
        avatar_id = profile.get("id") or profile.get("name")
        self.app.util.prefetch_skin(
            avatar_id,
            on_ready=lambda _src: invoke_on_ui(self.page, self._refresh_avatar_views),
        )
        return self.app.util.get_cached_skin_url(avatar_id)

    def update_list_view(self):
        self.lv.controls.clear()
        self.switches.clear()

        for key, profile in self.profiles.items():
            if profile.get("default", False):
                self.active_profile_key = key
            switch = ui.Switch(
                value=self.active_profile_key == key,
                key=key,
                data=profile,
                on_change=self.on_switch_change,
                tooltip=self.trans("set_as_default"),
            )
            self.switches[key] = switch

            access_token = profile.get("access_token", "")
            profile_type = self.trans("offline") if access_token == "offline" else self.trans("microsoft")
            badge_color = self.app.theme.color_red if access_token == "offline" else self.app.theme.color_green
            reauth_check = access_token != "offline"

            reauth_button = ui.Button(
                text=self.trans("reauthorize"),
                on_click=lambda e: self.add_microsoft_profile(),
                visible=False,
                height=self.app.theme.button_height,
            )

            if reauth_check:
                try:
                    requires_reauth = getattr(self.app.auth, "profile_requires_reauth", lambda _profile: False)
                    reauth_button.visible = bool(requires_reauth(profile))
                except Exception as exc:
                    self.app.log.error(f"Profile status check failed: {exc!r}")

            # Замінено Badge на Container
            badge_container = ui.Container(
                content=ui.Text(
                    profile_type,
                    size=self.app.theme.text_size_xs,
                    color=self.app.theme.color_white,
                ),
                bgcolor=badge_color,
                padding=ft.Padding.symmetric(
                    horizontal=self.app.theme.badge_padding_h,
                    vertical=self.app.theme.badge_padding_v
                ),
                border_radius=ft.BorderRadius.all(self.app.theme.badge_radius),
                alignment=ft.Alignment.CENTER,
                expand=False
            )

            # Компактний дизайн як у версіях
            profile_row = ui.Row(
                controls=[
                    # Ліва частина: аватар + ім'я + тип
                    ui.Row(
                        controls=[
                            ui.Image(
                                src=self._avatar_src(profile),
                                width=40,
                                height=40,
                                fit=ft.BoxFit.COVER,
                                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
                                error_content=ui.Icon(
                                    ft.Icons.ACCOUNT_CIRCLE,
                                    size=32,
                                    color=self.app.theme.text_secondary,
                                ),
                            ),
                            ui.Column(
                                controls=[
                                    ui.Text(
                                        profile.get("name"),
                                        size=self.app.theme.text_size_medium,
                                        weight=self.app.theme.font_weight_semibold,
                                        color=self.app.theme.text_color
                                    ),
                                    ui.Row([
                                        badge_container,
                                        reauth_button if reauth_check else ui.Container(),
                                    ], spacing=self.app.theme.spacing_xs),
                                ],
                                spacing=2,
                            ),
                        ],
                        spacing=self.app.theme.spacing_sm
                    ),
                    # Права частина: switch + кнопка видалення
                    ui.Row(
                        controls=[
                            switch,
                            ui.FloatingActionButton(
                                icon=ft.Icons.DELETE,
                                on_click=self.delete_profile,
                                key=key,
                                tooltip=self.trans("delete"),
                                mini=True,
                            )
                        ],
                        spacing=self.app.theme.spacing_xs
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )

            profile_card = ui.Container(
                content=profile_row,
                bgcolor=self.app.theme.bg_list,
                border=ft.Border.all(1, self.app.theme.border_color),
                border_radius=ft.BorderRadius.all(self.app.theme.radius_sm),
                padding=self.app.theme.padding_md,
                margin=ft.Margin.only(left=6, right=6, bottom=4),
            )
            self.lv.controls.append(profile_card)

    def delete_profile(self, e):
        def handle_response(response):
            if response:
                self.app.profiles.delete_profile(e.control.key)
                self.app.feedback.info(self.trans("profile_deleted"))
            self.profiles = self.app.profiles.get_all_profiles()
            self.update_list_view()
            schedule_update(self.page)

        self.app.feedback.confirm(self.trans("confirmation"), self.trans("are_you_sure"), handle_response)

    def on_switch_change(self, e):
        if e.control.value:
            self.active_profile_key = e.control.key
            self.app.profiles.set_default_profile(self.active_profile_key)
            self.app.feedback.info(self.trans("profile_set_as_default", profile=e.control.data.get('name')))
        self.profiles = self.app.profiles.get_all_profiles()
        self.update_list_view()
        # Оновлюємо header в content area
        if self.app._content_area and len(self.app._content_area.controls) >= 1:
            self.app._content_area.controls[0] = self.app.header.view()
        schedule_update(self.page)

    def add_offline_modal(self):
        def on_submit(data):
            name = data.get("username")
            resp = self.app.profiles.create_profile(name,
                                                    {"name": name, "access_token": "offline",
                                                     "refresh_token": "offline"})
            if resp:
                self.app.feedback.info(resp.get("text"))
            self.profiles = self.app.profiles.get_all_profiles()
            self.update_list_view()
            schedule_update(self.page)

        fields = [{"type": "textfield", "label": self.trans('enter_nickname'), "key": "username", "value": ""}]
        self.app.form_modal(self.app, title=self.trans('create_offline_profile'), fields=fields,
                            on_submit=on_submit, modal_height=(self.app.theme.modal_height / 4)).open()

    def add_microsoft_profile(self):
        if self.microsoft_auth_in_progress:
            return
        self.microsoft_auth_in_progress = True
        self.app.feedback.info(self.trans("microsoft_auth_starting"))

        run_task(self.page, self._add_microsoft_profile_async)

    async def _add_microsoft_profile_async(self):
        try:
            authenticate = getattr(self.app.auth, "authenticate", self.app.auth.authenticate_with_device_code)
            result = await run_blocking(authenticate)
        finally:
            self.microsoft_auth_in_progress = False

        if isinstance(result, dict):
            self.profiles = self.app.profiles.get_all_profiles()
            self.update_list_view()
            schedule_update(self.page)
            self.app.feedback.info(self.trans("profile_created"))
