from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from . import font_config
from .flet_theme import build_flet_theme
from .palette import UiPalette
from .scale import UiScale
from .tokens import UiTokens


_CURRENT_THEME: "UiTheme | None" = None


@dataclass(slots=True)
class UiTheme:
    scale: UiScale
    palette: UiPalette
    tokens: UiTokens
    font_family: str | None
    flet_theme: ft.Theme

    @classmethod
    def build(cls, ui_scale: float = 1.0, font_family: str | None = None) -> "UiTheme":
        scale = UiScale(ui_scale=ui_scale)
        palette = UiPalette()
        tokens = UiTokens.build(scale, palette)
        resolved_font_family = configured_font_family() if font_family is None else str(font_family or "").strip() or None
        return cls(
            scale=scale,
            palette=palette,
            tokens=tokens,
            font_family=resolved_font_family,
            flet_theme=build_flet_theme(tokens, palette, font_family=resolved_font_family),
        )

    def overlay(self, alpha: float, color: str) -> str:
        return ft.Colors.with_opacity(alpha, color)

    def radius(self, *, lg: bool = False, md: bool = False) -> ft.BorderRadius:
        value = self.radius_lg if lg else self.radius_md if md else self.radius_sm
        return ft.BorderRadius.all(value)

    def text_style(
        self,
        *,
        size: int | None = None,
        color: str | None = None,
        weight: ft.FontWeight | None = None,
    ) -> ft.TextStyle:
        return ft.TextStyle(
            size=size or self.text_size_sm,
            color=color or self.text_color,
            weight=weight,
            font_family=self.font_family,
        )

    def field_padding(self, *, dense: bool = True) -> ft.Padding:
        line_height = self.text_size_sm * 1.3
        free_vertical = max(self.control_height - line_height, 0)
        vertical = max(4, free_vertical / 2 + (0 if dense else 2))
        return ft.Padding.symmetric(horizontal=self.input_padding_h, vertical=vertical)

    def field_shell_padding(self) -> ft.Padding:
        return ft.Padding.symmetric(horizontal=self.padding_md, vertical=0)

    def button_style(
        self,
        *,
        variant: str = "filled",
        tone: str = "primary",
        size: str = "md",
    ) -> ft.ButtonStyle:
        bgcolor = self.primary if tone == "primary" else self.bg_shell if tone == "neutral" else self.info
        hover = self.primary_dark if tone == "primary" else self.overlay(self.alpha_hover * 2, self.primary)
        text_color = self.bg_app if tone == "primary" else self.text_color
        border = self.border_light if tone == "primary" else self.border
        padding_h = self.padding_md if size == "md" else self.padding_sm
        if variant == "ghost":
            bgcolor = ft.Colors.TRANSPARENT
            hover = self.overlay(self.alpha_hover * 2, self.primary)
            border = ft.Colors.TRANSPARENT
        elif variant == "outline":
            bgcolor = self.overlay(0.04, self.bg_shell)
            hover = self.overlay(self.alpha_hover, self.primary)
        return ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: bgcolor,
                ft.ControlState.HOVERED: hover,
                ft.ControlState.DISABLED: self.overlay(0.2, bgcolor if bgcolor != ft.Colors.TRANSPARENT else self.primary),
            },
            color={ft.ControlState.DEFAULT: text_color},
            side={ft.ControlState.DEFAULT: ft.BorderSide(1, border)},
            shape={ft.ControlState.DEFAULT: ft.RoundedRectangleBorder(radius=self.radius_sm)},
            padding=ft.Padding.symmetric(horizontal=padding_h, vertical=0),
            text_style={
                ft.ControlState.DEFAULT: self.text_style(
                    size=self.text_size_sm,
                    color=text_color,
                    weight=self.font_weight_semibold,
                )
            },
            icon_color={ft.ControlState.DEFAULT: text_color},
            icon_size={ft.ControlState.DEFAULT: self.icon_size},
            visual_density=ft.VisualDensity.COMPACT,
        )

    def icon_button_style(self, *, active: bool = False) -> ft.ButtonStyle:
        return ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: self.overlay(0.1, self.primary) if active else ft.Colors.TRANSPARENT,
                ft.ControlState.HOVERED: self.overlay(self.alpha_hover * 2, self.primary),
            },
            shape={ft.ControlState.DEFAULT: ft.RoundedRectangleBorder(radius=self.radius_sm)},
            icon_color={
                ft.ControlState.DEFAULT: self.primary if active else self.text_color,
            },
            icon_size={ft.ControlState.DEFAULT: self.icon_size},
            padding=ft.Padding.all(self.padding_xs),
            visual_density=ft.VisualDensity.COMPACT,
        )

    def switch_scale(self) -> float:
        return min(round(self.control_height / 44, 2), 1.0)

    def button_height_for_size(self, size: str = "md") -> int:
        if size == "sm":
            return self.shell_action_height
        if size == "lg":
            return max(self.button_height + self.padding_xs, self.control_height)
        return self.button_height

    @property
    def primary(self) -> str:
        return self.palette.primary

    @property
    def primary_dark(self) -> str:
        return self.palette.primary_dark

    @property
    def primary_light(self) -> str:
        return self.palette.primary_light

    @property
    def success(self) -> str:
        return self.palette.success

    @property
    def error(self) -> str:
        return self.palette.error

    @property
    def info(self) -> str:
        return self.palette.info

    @property
    def bg_app(self) -> str:
        return self.palette.bg_app

    @property
    def bg_shell(self) -> str:
        return self.palette.bg_shell

    @property
    def bg_primary(self) -> str:
        return self.palette.bg_app

    @property
    def bg_header_footer(self) -> str:
        return self.palette.bg_shell

    @property
    def bg_pages(self) -> str:
        return self.palette.bg_page

    @property
    def bg_card(self) -> str:
        return self.palette.bg_card

    @property
    def bg_list(self) -> str:
        return self.palette.bg_panel

    @property
    def bg_play_btn(self) -> str:
        return self.palette.bg_action

    @property
    def bg_tooltip(self) -> str:
        return self.palette.bg_shell

    @property
    def text_color(self) -> str:
        return self.palette.text

    @property
    def text_secondary(self) -> str:
        return self.palette.text_secondary

    @property
    def text_tertiary(self) -> str:
        return self.palette.text_tertiary

    @property
    def text_disabled(self) -> str:
        return self.palette.text_disabled

    @property
    def border_color(self) -> str:
        return self.palette.border

    @property
    def border_light_color(self) -> str:
        return self.palette.border_light

    @property
    def border(self) -> str:
        return self.palette.border

    @property
    def border_light(self) -> str:
        return self.palette.border_light

    @property
    def color_white(self) -> str:
        return self.palette.white

    @property
    def color_transparent(self) -> str:
        return self.palette.transparent

    @property
    def color_red(self) -> str:
        return self.palette.error

    @property
    def color_green(self) -> str:
        return self.palette.success

    @property
    def ui_scale(self) -> float:
        return self.tokens.ui_scale

    @property
    def control_height(self) -> int:
        return self.tokens.control_height

    @property
    def input_height(self) -> int:
        return self.tokens.control_height

    @property
    def button_height(self) -> int:
        return self.tokens.button_height

    @property
    def shell_action_height(self) -> int:
        return self.tokens.shell_action_height

    @property
    def shell_avatar_size(self) -> int:
        return self.tokens.shell_avatar_size

    @property
    def search_input_height(self) -> int:
        return self.tokens.search_height

    @property
    def search_height(self) -> int:
        return self.tokens.search_height

    @property
    def tab_height(self) -> int:
        return self.tokens.tab_height

    @property
    def padding_xs(self) -> int:
        return self.tokens.padding_xs

    @property
    def padding_sm(self) -> int:
        return self.tokens.padding_sm

    @property
    def padding_md(self) -> int:
        return self.tokens.padding_md

    @property
    def padding_lg(self) -> int:
        return self.tokens.padding_lg

    @property
    def padding_xl(self) -> int:
        return self.tokens.padding_xl

    @property
    def padding_2xl(self) -> int:
        return self.tokens.padding_2xl

    @property
    def radius_sm(self) -> int:
        return self.tokens.radius_sm

    @property
    def radius_md(self) -> int:
        return self.tokens.radius_md

    @property
    def radius_lg(self) -> int:
        return self.tokens.radius_lg

    @property
    def radius_xl(self) -> int:
        return self.tokens.radius_lg

    @property
    def spacing_xs(self) -> int:
        return self.tokens.spacing_xs

    @property
    def spacing_sm(self) -> int:
        return self.tokens.spacing_sm

    @property
    def spacing_md(self) -> int:
        return self.tokens.spacing_md

    @property
    def spacing_lg(self) -> int:
        return self.tokens.spacing_lg

    @property
    def spacing_xl(self) -> int:
        return self.tokens.spacing_xl

    @property
    def input_padding_h(self) -> int:
        return self.tokens.input_padding_h

    @property
    def input_padding_v(self) -> int:
        return self.tokens.input_padding_v

    @property
    def input_shell_padding(self) -> ft.Padding:
        return self.field_shell_padding()

    @property
    def input_content_padding(self) -> ft.Padding:
        return self.field_padding()

    @property
    def profile_content_padding(self) -> ft.Padding:
        return ft.Padding.only(left=self.shell_padding, right=self.shell_padding)

    @property
    def profile_btn_padding(self) -> ft.Padding:
        return ft.Padding.only(left=self.padding_sm, bottom=self.padding_sm)

    @property
    def version_content_padding(self) -> ft.Padding:
        return ft.Padding.only(left=self.shell_padding, right=self.shell_padding)

    @property
    def version_btn_padding(self) -> ft.Padding:
        return ft.Padding.only(left=self.padding_sm, bottom=self.padding_sm)

    @property
    def modal_title_padding(self) -> ft.Padding:
        return ft.Padding.only(top=self.padding_xl, left=self.padding_xl, right=self.padding_xl)

    @property
    def modal_padding(self) -> ft.Padding:
        return ft.Padding.all(self.modal_inner_padding)

    @property
    def modal_actions_padding(self) -> ft.Padding:
        return ft.Padding.all(self.padding_lg)

    @property
    def header_height(self) -> int:
        return self.tokens.header_height

    @property
    def footer_height(self) -> int:
        return self.tokens.footer_height

    @property
    def sidebar_width(self) -> int:
        return self.tokens.sidebar_width

    @property
    def modal_width(self) -> int:
        return self.tokens.modal_width

    @property
    def modal_width_md(self) -> int:
        return self.tokens.modal_width_md

    @property
    def modal_height(self) -> int:
        return self.tokens.modal_height

    @property
    def modal_inner_padding(self) -> int:
        return self.tokens.modal_padding

    @property
    def progress_bar_width(self) -> int:
        return self.tokens.progress_bar_width

    @property
    def progress_ring_size(self) -> int:
        return self.tokens.progress_ring_size

    @property
    def progress_button_size(self) -> int:
        return self.tokens.progress_button_size

    @property
    def version_image_size_compact(self) -> int:
        return self.tokens.version_image_size_compact

    @property
    def home_card_size(self) -> int:
        return self.tokens.home_card_size

    @property
    def home_spacing(self) -> int:
        return self.tokens.home_spacing

    @property
    def home_run_spacing(self) -> int:
        return self.tokens.home_run_spacing

    @property
    def modpacks_per_page(self) -> int:
        return self.tokens.modpacks_per_page

    @property
    def jvm_args_min_lines(self) -> int:
        return self.tokens.jvm_args_min_lines

    @property
    def jvm_args_max_lines(self) -> int:
        return self.tokens.jvm_args_max_lines

    @property
    def badge_padding_h(self) -> int:
        return self.tokens.badge_padding_h

    @property
    def badge_padding_v(self) -> int:
        return self.tokens.badge_padding_v

    @property
    def badge_radius(self) -> int:
        return self.radius_sm

    @property
    def stroke_width_sm(self) -> int:
        return self.tokens.stroke_width_sm

    @property
    def icon_size(self) -> int:
        return self.tokens.icon_size

    @property
    def icon_size_sm(self) -> int:
        return self.tokens.icon_size_sm

    @property
    def icon_size_lg(self) -> int:
        return self.tokens.icon_size_lg

    @property
    def text_size_small(self) -> int:
        return self.tokens.text_size_small

    @property
    def text_size_xs(self) -> int:
        return self.tokens.text_size_xs

    @property
    def text_size_sm(self) -> int:
        return self.tokens.text_size_sm

    @property
    def text_size_medium(self) -> int:
        return self.tokens.text_size_md

    @property
    def text_size_md(self) -> int:
        return self.tokens.text_size_md

    @property
    def text_size_large(self) -> int:
        return self.tokens.text_size_lg

    @property
    def text_size_lg(self) -> int:
        return self.tokens.text_size_lg

    @property
    def text_size_xl(self) -> int:
        return self.tokens.text_size_xl

    @property
    def text_size_2xl(self) -> int:
        return self.tokens.text_size_2xl

    @property
    def font_weight_medium(self) -> ft.FontWeight:
        return self.tokens.font_weight_medium

    @property
    def font_weight_semibold(self) -> ft.FontWeight:
        return self.tokens.font_weight_semibold

    @property
    def font_weight_bold(self) -> ft.FontWeight:
        return self.tokens.font_weight_bold

    @property
    def animation_duration_fast(self) -> int:
        return self.tokens.animation_duration_fast

    @property
    def animation_duration_normal(self) -> int:
        return self.tokens.animation_duration_normal

    @property
    def animation_curve_default(self) -> ft.AnimationCurve:
        return self.tokens.animation_curve_default

    @property
    def snackbar_duration(self) -> int:
        return self.tokens.snackbar_duration

    @property
    def snackbar_elevation(self) -> int:
        return self.tokens.snackbar_elevation

    @property
    def alpha_input(self) -> float:
        return self.tokens.alpha_input

    @property
    def alpha_hover(self) -> float:
        return self.tokens.alpha_hover

    @property
    def alpha_header_footer(self) -> float:
        return self.tokens.alpha_shell

    @property
    def alpha_modal_bg(self) -> float:
        return self.tokens.alpha_modal_bg

    @property
    def alpha_progress_bg(self) -> float:
        return self.tokens.alpha_progress_bg

    @property
    def alpha_progress_ring_bg(self) -> float:
        return self.tokens.alpha_progress_ring_bg

    @property
    def shell_padding(self) -> int:
        return self.tokens.shell_padding

    @property
    def section_padding(self) -> int:
        return self.tokens.section_padding

    @property
    def card_padding(self) -> int:
        return self.tokens.card_padding


def set_current_theme(theme: UiTheme) -> UiTheme:
    global _CURRENT_THEME
    _CURRENT_THEME = theme
    return theme


def configured_font_family() -> str | None:
    font_family = str(font_config.APP_FONT_FAMILY or "").strip()
    return font_family or None


def configured_page_fonts() -> dict[str, str]:
    fonts: dict[str, str] = {}
    for family, source in font_config.APP_FONT_ASSETS.items():
        normalized_family = str(family or "").strip()
        normalized_source = str(source or "").strip().replace("\\", "/")
        if normalized_family and normalized_source:
            fonts[normalized_family] = normalized_source
    return fonts


def current_theme() -> UiTheme:
    global _CURRENT_THEME
    if _CURRENT_THEME is None:
        _CURRENT_THEME = UiTheme.build()
    return _CURRENT_THEME


__all__ = [
    "current_theme",
    "set_current_theme",
    "configured_font_family",
    "configured_page_fonts",
    "font_config",
    "UiPalette",
    "UiScale",
    "UiTokens",
    "UiTheme",
    "build_flet_theme",
]
