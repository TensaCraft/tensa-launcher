from types import SimpleNamespace

import flet as ft
import pytest

from launcher import ui


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_input_and_select_align_at_each_size(size):
    text = ui.TextField(label="Name", size=size)
    select = ui.Dropdown(label="Choice", size=size)
    assert text.height == select.height
    assert text.content_padding == select.content_padding


@pytest.mark.parametrize("detail", ["helper", "error", "counter"])
def test_field_details_can_grow_without_clipping(detail):
    control = ui.TextField(label="Name", **{detail: "A long validation message"})
    assert control.height is None
    assert control.size_constraints.min_height == ui.current_theme().input_height


@pytest.mark.parametrize("detail", ["helper_text", "error_text"])
def test_select_details_can_grow_without_clipping(detail):
    control = ui.Dropdown(label="Name", **{detail: "A long validation message"})
    assert control.height is None
    assert getattr(control, detail) == "A long validation message"


def test_multiline_input_does_not_use_single_line_height():
    field = ui.TextField(label="Notes", multiline=True, min_lines=4, max_lines=8)
    assert field.height is None
    assert field.text_vertical_align == ft.VerticalAlignment.START
    assert not field.fit_parent_size
    assert field.min_lines == 4
    assert field.content_padding.top < ui.current_theme().input_height


def test_explicit_multiline_height_remains_supported():
    field = ui.TextField(multiline=True, height=180)
    assert field.height == 180
    assert field.text_vertical_align == ft.VerticalAlignment.START


def test_form_buttons_match_inputs_without_changing_toolbar_buttons():
    layout = ui.FormSection(SimpleNamespace())
    form_button = ui.Button(text="Browse")
    toolbar_button = ui.Button(text="Play")
    wrapped = layout.wrap_control(form_button, {"md": 4, "lg": 3})
    assert form_button.height == ui.current_theme().input_height
    assert toolbar_button.height == ui.current_theme().button_height
    assert wrapped.col == {"xs": 12, "md": 4, "lg": 3}


def test_long_toggle_label_keeps_switch_inside_field():
    toggle = ui.ToggleField(label="A long localized setting label", value=True, on_change=lambda e: None)
    label = toggle.content.controls[0].content
    assert label.max_lines == 1
    assert label.overflow == ft.TextOverflow.ELLIPSIS
    assert label.tooltip == "A long localized setting label"
