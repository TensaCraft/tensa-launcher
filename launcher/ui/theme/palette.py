from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UiPalette:
    primary: str = "#17C3A2"
    primary_dark: str = "#0F8D77"
    primary_light: str = "#7BE3D2"
    success: str = "#4CAF50"
    error: str = "#F44336"
    info: str = "#1FB5D7"
    bg_app: str = "#0B1412"
    bg_shell: str = "#0E1917"
    bg_page: str = "#0E1B18"
    bg_card: str = "#101B19"
    bg_panel: str = "#11211D"
    bg_action: str = "#2B6B56"
    text: str = "#E6F6F2"
    text_secondary: str = "#BED7D2"
    text_tertiary: str = "#8DA7A2"
    text_disabled: str = "#59706C"
    border: str = "#1C2825"
    border_light: str = "#243530"
    white: str = "#FFFFFF"
    transparent: str = "transparent"
