from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from launcher import ui
from launcher.ui.forms.field_specs import FieldSpec, build_field
from launcher.ui.modals.version_component_modal import VersionComponentModal


def _dialog(fake_app, kind):
    if kind == "form":
        return ui.FormDialog(
            fake_app, "Profile",
            [FieldSpec("textfield", str(index), "Name") for index in range(4)],
            lambda _data: None,
        )
    if kind == "install":
        return ui.VersionInstallModal(fake_app)
    if kind == "copy":
        return ui.VersionCopyModal(fake_app, fake_app.versions.all()[0])
    if kind == "component":
        return VersionComponentModal(fake_app, fake_app.versions.all()[0], SimpleNamespace())
    if kind == "curseforge":
        return ui.CurseForgeImportModal(fake_app)
    modal = ui.ModpackInstallModal(fake_app, "example")
    modal.modpack_data = {"id": "example"}
    modal.versions = [{"id": "v1", "version_number": "1.0", "game_versions": ["1.20.1"]}]
    modal.update_view()
    return modal


MODALS = ["form", "install", "copy", "component", "curseforge", "modpack"]


@pytest.mark.parametrize("kind", MODALS)
@pytest.mark.parametrize("page_width", [320, 1920])
def test_modal_fields_fit_content_width_and_keep_desktop_proportions(fake_app, kind, page_width):
    fake_app.page.width = page_width
    dialog = _dialog(fake_app, kind).modal
    content = dialog.content
    available_width = page_width - 2 * fake_app.theme.padding_xl - 48

    assert content.width == min(fake_app.theme.modal_width, available_width)
    assert content.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
    fields = [control for control in content.controls if isinstance(control, (ft.TextField, ft.Dropdown))]
    assert fields
    assert all(control.width == content.width for control in fields)


@pytest.mark.parametrize("kind", MODALS)
def test_modal_body_can_scroll_without_moving_actions_into_content(fake_app, kind):
    fake_app.page.height = 360
    dialog = _dialog(fake_app, kind).modal

    assert dialog.scrollable or dialog.content.scroll in (ft.ScrollMode.AUTO, ft.ScrollMode.ALWAYS)
    assert len(dialog.actions) == 2
    assert all(action not in dialog.content.controls for action in dialog.actions)
    assert all(action.on_click is not None for action in dialog.actions)


@pytest.mark.parametrize("explicit_height", [None, 160])
def test_field_spec_preserves_multiline_intrinsic_or_explicit_height(fake_app, explicit_height):
    field = build_field(
        fake_app,
        FieldSpec("textfield", "notes", "Notes", height=explicit_height, props={"multiline": True, "min_lines": 4}),
    )

    assert field.height == explicit_height
    assert field.multiline
    assert field.min_lines == 4


@pytest.mark.parametrize("field_type", ["textfield", "dropdown"])
def test_single_line_field_spec_keeps_shared_control_height(fake_app, field_type):
    field = build_field(fake_app, FieldSpec(field_type, "name", "Name"))

    assert field.height == fake_app.theme.input_height


def test_field_height_props_keep_existing_precedence(fake_app):
    field = build_field(
        fake_app,
        FieldSpec("textfield", "notes", "Notes", height=100, props={"height": 180, "multiline": True}),
    )

    assert field.height == 180


def test_field_spec_does_not_force_height_on_helper_text(fake_app):
    field = build_field(fake_app, FieldSpec("textfield", "name", "Name", props={"helper_text": "Explanation"}))

    assert field.height is None


def test_form_dialog_preserves_custom_dimensions_and_submission(fake_app):
    fake_app.page.width = 900
    scheduled = []
    closed = []
    submitted = []
    fake_app.page.run_task = lambda handler, *args: scheduled.append((handler, args))
    dialog = ui.FormDialog(
        fake_app, "Profile", [FieldSpec("textfield", "username", "Username", value="Original")],
        submitted.append, on_close=lambda: closed.append(True), modal_width=360, modal_height=180,
    )
    assert dialog.modal.content.width == 360
    assert dialog.modal.content.height == 180
    assert dialog.inputs["username"].width == 360
    dialog.on_change(SimpleNamespace(control=SimpleNamespace(value="Edited")), "username")
    dialog.handle_submit()

    assert closed == [True]
    assert submitted == []
    handler, args = next((fn, args) for fn, args in scheduled if fn == dialog._submit_after_close)
    asyncio.run(handler(*args))
    assert submitted == [{"username": "Edited"}]


def test_install_description_can_scroll_inside_existing_panel(fake_app):
    modal = ui.VersionInstallModal(fake_app)
    panel = modal.tensacraft_description_panel

    assert panel.content.scroll == ft.ScrollMode.AUTO
    assert panel.height == max(108, fake_app.theme.input_height * 3)
    assert panel.content.controls[-1] is modal.tensacraft_description_text


def test_unmeasured_page_keeps_original_form_dimensions(fake_app):
    dialog = _dialog(fake_app, "form")

    assert dialog.modal.content.width == fake_app.theme.modal_width
    assert dialog.modal.content.height == fake_app.theme.modal_height // 2


def test_oversized_field_width_cannot_escape_narrow_form(fake_app):
    fake_app.page.width = 320
    dialog = ui.FormDialog(
        fake_app, "Profile", [FieldSpec("textfield", "name", "Name", width=1000)], lambda _data: None,
    )

    assert dialog.inputs["name"].width == dialog.modal.content.width
    assert dialog.inputs["name"].width < fake_app.page.width
