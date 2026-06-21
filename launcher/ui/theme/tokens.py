from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from .palette import UiPalette
from .scale import UiScale


@dataclass(frozen=True, slots=True)
class UiTokens:
    scale: UiScale
    palette: UiPalette

    ui_scale: float
    control_height: int
    button_height: int
    search_height: int
    tab_height: int
    icon_size: int
    icon_size_sm: int
    icon_size_lg: int
    radius_sm: int
    radius_md: int
    radius_lg: int
    spacing_xs: int
    spacing_sm: int
    spacing_md: int
    spacing_lg: int
    spacing_xl: int
    padding_xs: int
    padding_sm: int
    padding_md: int
    padding_lg: int
    padding_xl: int
    padding_2xl: int
    input_padding_h: int
    input_padding_v: int
    section_padding: int
    shell_padding: int
    card_padding: int
    modal_padding: int
    header_height: int
    footer_height: int
    shell_action_height: int
    shell_avatar_size: int
    sidebar_width: int
    modal_width: int
    modal_width_md: int
    modal_height: int
    progress_bar_width: int
    progress_ring_size: int
    progress_button_size: int
    version_image_size_compact: int
    home_card_size: int
    home_spacing: int
    home_run_spacing: int
    modpacks_per_page: int
    jvm_args_min_lines: int
    jvm_args_max_lines: int
    badge_padding_h: int
    badge_padding_v: int
    stroke_width_sm: int
    snackbar_duration: int
    snackbar_elevation: int
    alpha_input: float
    alpha_hover: float
    alpha_shell: float
    alpha_modal_bg: float
    alpha_progress_bg: float
    alpha_progress_ring_bg: float
    text_size_small: int
    text_size_xs: int
    text_size_sm: int
    text_size_md: int
    text_size_lg: int
    text_size_xl: int
    text_size_2xl: int
    font_weight_medium: ft.FontWeight
    font_weight_semibold: ft.FontWeight
    font_weight_bold: ft.FontWeight
    animation_duration_fast: int
    animation_duration_normal: int
    animation_curve_default: ft.AnimationCurve

    @classmethod
    def build(cls, scale: UiScale, palette: UiPalette) -> "UiTokens":
        return cls(
            scale=scale,
            palette=palette,
            ui_scale=scale.ui_scale,
            control_height=scale.px(38),
            button_height=scale.px(30),
            search_height=scale.px(38),
            tab_height=scale.px(40),
            icon_size=scale.px(18),
            icon_size_sm=scale.px(16),
            icon_size_lg=scale.px(24),
            radius_sm=scale.px(10),
            radius_md=scale.px(14),
            radius_lg=scale.px(20),
            spacing_xs=scale.px(4),
            spacing_sm=scale.px(8),
            spacing_md=scale.px(12),
            spacing_lg=scale.px(16),
            spacing_xl=scale.px(24),
            padding_xs=scale.px(4),
            padding_sm=scale.px(8),
            padding_md=scale.px(12),
            padding_lg=scale.px(16),
            padding_xl=scale.px(20),
            padding_2xl=scale.px(24),
            input_padding_h=scale.px(12),
            input_padding_v=scale.px(8),
            section_padding=scale.px(20),
            shell_padding=scale.px(16),
            card_padding=scale.px(12),
            modal_padding=scale.px(24),
            header_height=scale.px(46),
            footer_height=scale.px(46),
            shell_action_height=scale.px(26),
            shell_avatar_size=scale.px(24),
            sidebar_width=scale.px(220),
            modal_width=scale.px(480),
            modal_width_md=scale.px(520),
            modal_height=scale.px(240),
            progress_bar_width=scale.px(500),
            progress_ring_size=scale.px(24),
            progress_button_size=scale.px(40),
            version_image_size_compact=scale.px(48),
            home_card_size=scale.px(150),
            home_spacing=scale.px(16),
            home_run_spacing=scale.px(16),
            modpacks_per_page=16,
            jvm_args_min_lines=12,
            jvm_args_max_lines=24,
            badge_padding_h=scale.px(8),
            badge_padding_v=scale.px(4),
            stroke_width_sm=scale.px(2),
            snackbar_duration=4000,
            snackbar_elevation=8,
            alpha_input=0.95,
            alpha_hover=0.12,
            alpha_shell=0.3,
            alpha_modal_bg=0.95,
            alpha_progress_bg=0.3,
            alpha_progress_ring_bg=0.8,
            text_size_small=scale.px(10),
            text_size_xs=scale.px(12),
            text_size_sm=scale.px(13),
            text_size_md=scale.px(14),
            text_size_lg=scale.px(16),
            text_size_xl=scale.px(18),
            text_size_2xl=scale.px(20),
            font_weight_medium=ft.FontWeight.W_500,
            font_weight_semibold=ft.FontWeight.W_600,
            font_weight_bold=ft.FontWeight.BOLD,
            animation_duration_fast=150,
            animation_duration_normal=300,
            animation_curve_default=ft.AnimationCurve.EASE_OUT,
        )
