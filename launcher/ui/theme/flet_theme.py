from __future__ import annotations

import flet as ft

from .palette import UiPalette
from .tokens import UiTokens


def _op(alpha: float, color: str) -> str:
    return ft.Colors.with_opacity(alpha, color)


def build_flet_theme(tokens: UiTokens, palette: UiPalette, *, font_family: str | None = None) -> ft.Theme:
    def text_style(
        *,
        size: int,
        color: str,
        weight: ft.FontWeight | None = None,
    ) -> ft.TextStyle:
        return ft.TextStyle(
            size=size,
            color=color,
            weight=weight,
            font_family=font_family,
        )

    field_border = ft.BorderSide(1, _op(0.6, palette.text_tertiary))
    field_radius = ft.RoundedRectangleBorder(radius=tokens.radius_sm)
    button_style = ft.ButtonStyle(
        bgcolor={
            ft.ControlState.DEFAULT: palette.primary,
            ft.ControlState.HOVERED: palette.primary_dark,
            ft.ControlState.DISABLED: _op(0.35, palette.primary),
        },
        color={ft.ControlState.DEFAULT: palette.bg_app},
        padding=ft.Padding.symmetric(horizontal=tokens.padding_md, vertical=0),
        side={ft.ControlState.DEFAULT: ft.BorderSide(1, palette.border_light)},
        shape={ft.ControlState.DEFAULT: field_radius},
        text_style={
            ft.ControlState.DEFAULT: text_style(
                size=tokens.text_size_sm,
                color=palette.bg_app,
                weight=tokens.font_weight_semibold,
            )
        },
        icon_color={ft.ControlState.DEFAULT: palette.bg_app},
        icon_size={ft.ControlState.DEFAULT: tokens.icon_size},
        visual_density=ft.VisualDensity.COMPACT,
    )
    icon_button_style = ft.ButtonStyle(
        bgcolor={
            ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
            ft.ControlState.HOVERED: _op(tokens.alpha_hover * 2, palette.primary),
        },
        shape={ft.ControlState.DEFAULT: field_radius},
        icon_color={ft.ControlState.DEFAULT: palette.text},
        icon_size={ft.ControlState.DEFAULT: tokens.icon_size},
        padding=ft.Padding.all(tokens.padding_xs),
        visual_density=ft.VisualDensity.COMPACT,
    )
    tooltip_decoration = ft.BoxDecoration(
        bgcolor=palette.bg_shell,
        border=ft.Border.all(1, _op(0.45, palette.border_light)),
        border_radius=ft.BorderRadius.all(tokens.radius_sm),
    )
    color_scheme = ft.ColorScheme(
        primary=palette.primary,
        on_primary=palette.bg_app,
        secondary=palette.primary_light,
        on_secondary=palette.bg_app,
        error=palette.error,
        on_error=palette.white,
        surface=palette.bg_card,
        on_surface=palette.text,
        on_surface_variant=palette.text_secondary,
        outline=palette.border,
        outline_variant=palette.border_light,
        shadow=_op(0.4, ft.Colors.BLACK),
        surface_container=palette.bg_panel,
        surface_container_high=palette.bg_card,
        surface_container_low=palette.bg_page,
    )
    return ft.Theme(
        use_material3=True,
        font_family=font_family,
        color_scheme=color_scheme,
        scaffold_bgcolor=palette.bg_app,
        canvas_color=palette.bg_page,
        card_bgcolor=palette.bg_card,
        divider_color=palette.border,
        highlight_color=_op(tokens.alpha_hover, palette.primary),
        hover_color=_op(tokens.alpha_hover, palette.primary),
        focus_color=_op(tokens.alpha_hover * 1.2, palette.primary),
        disabled_color=palette.text_disabled,
        text_theme=ft.TextTheme(
            body_small=text_style(size=tokens.text_size_xs, color=palette.text_secondary),
            body_medium=text_style(size=tokens.text_size_sm, color=palette.text),
            body_large=text_style(size=tokens.text_size_md, color=palette.text),
            title_medium=text_style(
                size=tokens.text_size_lg,
                color=palette.text,
                weight=tokens.font_weight_semibold,
            ),
            title_large=text_style(
                size=tokens.text_size_xl,
                color=palette.text,
                weight=tokens.font_weight_bold,
            ),
        ),
        button_theme=ft.ButtonTheme(style=button_style),
        icon_button_theme=ft.IconButtonTheme(style=icon_button_style),
        floating_action_button_theme=ft.FloatingActionButtonTheme(
            bgcolor=palette.bg_app,
            foreground_color=palette.text,
            hover_color=_op(tokens.alpha_hover * 2, palette.primary),
            elevation=6,
            focus_elevation=8,
            shape=ft.CircleBorder(),
            size_constraints=ft.BoxConstraints(
                min_width=tokens.button_height,
                min_height=tokens.button_height,
            ),
            text_style=text_style(
                size=tokens.text_size_sm,
                color=palette.text,
                weight=tokens.font_weight_semibold,
            ),
        ),
        checkbox_theme=ft.CheckboxTheme(
            fill_color={
                ft.ControlState.DEFAULT: _op(0.3, palette.primary),
                ft.ControlState.SELECTED: palette.primary,
            },
            check_color=palette.bg_app,
            overlay_color={ft.ControlState.HOVERED: _op(tokens.alpha_hover, palette.primary)},
            border_side=field_border,
            shape=field_radius,
            visual_density=ft.VisualDensity.COMPACT,
        ),
        switch_theme=ft.SwitchTheme(
            thumb_color={
                ft.ControlState.DEFAULT: palette.text,
                ft.ControlState.SELECTED: palette.bg_app,
            },
            track_color={
                ft.ControlState.DEFAULT: _op(0.25, palette.text_tertiary),
                ft.ControlState.SELECTED: _op(0.5, palette.primary),
            },
            overlay_color={ft.ControlState.HOVERED: _op(tokens.alpha_hover, palette.primary)},
            track_outline_color={ft.ControlState.DEFAULT: _op(0.45, palette.border_light)},
            track_outline_width=1,
            padding=ft.Padding.all(0),
        ),
        dropdown_theme=ft.DropdownTheme(
            text_style=text_style(size=tokens.text_size_sm, color=palette.text),
            menu_style=ft.MenuStyle(
                bgcolor=palette.bg_card,
                shadow_color=_op(0.45, ft.Colors.BLACK),
                elevation=8,
                padding=ft.Padding.all(tokens.padding_sm),
                side=ft.BorderSide(1, palette.border_light),
                shape=field_radius,
                visual_density=ft.VisualDensity.COMPACT,
            ),
        ),
        dialog_theme=ft.DialogTheme(
            bgcolor=_op(tokens.alpha_modal_bg, palette.bg_card),
            elevation=10,
            shape=ft.RoundedRectangleBorder(radius=tokens.radius_md),
            title_text_style=text_style(
                size=tokens.text_size_xl,
                color=palette.text,
                weight=tokens.font_weight_bold,
            ),
            content_text_style=text_style(size=tokens.text_size_sm, color=palette.text_secondary),
            actions_padding=ft.Padding.all(tokens.padding_lg),
            inset_padding=ft.Padding.all(tokens.padding_xl),
            barrier_color=_op(0.65, ft.Colors.BLACK),
        ),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=_op(tokens.alpha_modal_bg, palette.bg_card),
            elevation=tokens.snackbar_elevation,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=tokens.radius_sm),
            content_text_style=text_style(
                size=tokens.text_size_sm,
                color=palette.text,
                weight=tokens.font_weight_medium,
            ),
        ),
        tooltip_theme=ft.TooltipTheme(
            text_style=text_style(size=tokens.text_size_small, color=palette.text),
            padding=ft.Padding.symmetric(
                horizontal=tokens.padding_sm,
                vertical=tokens.padding_xs,
            ),
            decoration=tooltip_decoration,
            wait_duration=300,
            show_duration=2500,
        ),
        progress_indicator_theme=ft.ProgressIndicatorTheme(
            color=palette.primary,
            circular_track_color=_op(0.2, palette.primary),
            linear_track_color=_op(0.2, palette.primary),
            linear_min_height=6,
            border_radius=ft.BorderRadius.all(tokens.radius_sm),
            stroke_width=4,
        ),
    )
