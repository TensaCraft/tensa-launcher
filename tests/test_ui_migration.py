from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
import inspect
import threading
import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from launcher.app import App
from launcher.application.feedback import FeedbackService
from launcher.shared.app_context import AppContext
from launcher import ui
from launcher.platform.system import SystemService
from launcher.ui.theme import font_config
from launcher.ui.core.page_runtime import close_dialog, invoke_on_ui, schedule_update, show_dialog
from launcher.pages.home import Home
from launcher.pages.modpacks import ModpacksPage
from launcher.pages.mods_manager import ModsManagerPage
from launcher.pages.profiles import ProfilesPage
from launcher.pages.settings import SettingsPage
from launcher.pages.version_settings import VersionSettingsPage
from launcher.pages.versions import VersionsPage


ROOT_DIR = Path(__file__).resolve().parents[1]


def _padding_tuple(padding) -> tuple[float, float, float, float]:
    return (padding.left, padding.top, padding.right, padding.bottom)


def test_form_controls_share_default_height_and_padding(fake_app):
    textfield = ui.build_field(
        fake_app,
        ui.FieldSpec(type="textfield", key="ram", label="RAM", value="4"),
        on_change=lambda e: None,
    )
    dropdown = ui.build_field(
        fake_app,
        ui.FieldSpec(
            type="dropdown",
            key="language",
            label="Language",
            value="uk_UA",
            options=[{"text": "Ukrainian", "key": "uk_UA"}],
        ),
        on_change=lambda e: None,
    )
    button = ui.Button(text="Browse")

    assert textfield.height == fake_app.theme.input_height
    assert dropdown.height == fake_app.theme.input_height
    assert button.height == fake_app.theme.button_height
    assert textfield.dense is True
    assert dropdown.dense is True
    assert textfield.fit_parent_size is True
    assert _padding_tuple(textfield.content_padding) == _padding_tuple(dropdown.content_padding)


def test_small_button_uses_compact_shell_height():
    button = ui.Button(text="Save", size="sm")
    theme = ui.current_theme()

    assert button.height == theme.shell_action_height


def test_wrapper_compat_smoke():
    dropdown = ui.Dropdown(
        label="Language",
        value="uk_UA",
        options=[ft.dropdown.Option(key="uk_UA", text="Ukrainian")],
        on_change=lambda e: None,
        prefix_style=ft.TextStyle(),
        suffix_style=ft.TextStyle(),
        counter_style=ft.TextStyle(),
        focused_color=ft.Colors.WHITE,
    )
    textfield = ui.TextField(label="RAM", value="4", suffix_text="G")
    button = ui.Button(text="Save", on_click=lambda e: None)
    fab = ui.FloatingActionButton(text="Go", on_click=lambda e: None)
    icon = ui.Icon(ft.Icons.SETTINGS)
    tooltip = ui.Tooltip(message="Open", border_radius=6)
    image = ui.Image(src_base64="aGVsbG8=")
    blank_image = ui.Image(error_content=ft.Text("fallback"))
    bottom_sheet = ui.BottomSheet(content=ft.Container(), enable_drag=True, is_scroll_controlled=True)
    container = ui.Container(image_src="https://example.com/test.png", image_fit=ft.BoxFit.COVER, image_opacity=0.5)
    grid = ui.GridView(on_scroll_interval=24)
    list_view = ui.ListView(on_scroll_interval=24)
    snack_bar = ui.SnackBar(content=ft.Text("Saved"), action="Retry", action_color=ft.Colors.WHITE)
    switch = ui.Switch(label="Enabled", label_style=ft.TextStyle())
    dialog = ui.AlertDialog(title=ft.Text("Title"), content=ft.Text("Body"))

    assert isinstance(dropdown, ft.Dropdown)
    assert textfield.suffix == "G"
    assert button.content == "Save"
    assert isinstance(button, ft.Button)
    assert fab.content == "Go"
    assert isinstance(icon, ft.Icon)
    assert isinstance(tooltip, ft.Tooltip)
    assert isinstance(image, ft.Image)
    assert blank_image.src == ""
    assert isinstance(bottom_sheet, ft.BottomSheet)
    assert isinstance(container.image, ft.DecorationImage)
    assert grid.scroll_interval == 24
    assert list_view.scroll_interval == 24
    assert isinstance(snack_bar.action, ft.SnackBarAction)
    assert switch.label_text_style is not None
    assert isinstance(dialog, ft.AlertDialog)


def test_ui_scale_derives_shared_theme_tokens():
    base = ui.UiTheme.build()
    compact = ui.UiTheme.build(ui_scale=0.75)
    expanded = ui.UiTheme.build(ui_scale=1.5)

    assert compact.control_height == 28
    assert expanded.control_height == 57
    assert compact.button_height == 22
    assert expanded.button_height == 45
    assert compact.padding_md == 9
    assert expanded.padding_md == 18
    assert compact.radius_sm == 8
    assert expanded.radius_sm == 15
    assert compact.icon_size == 14
    assert expanded.icon_size == 27
    assert compact.switch_scale() == round(compact.control_height / 44, 2)
    assert compact.switch_scale() < base.switch_scale()


def test_flet_theme_populates_global_subthemes():
    theme = ui.UiTheme.build()
    flet_theme = theme.flet_theme

    for attribute in (
        "button_theme",
        "icon_button_theme",
        "floating_action_button_theme",
        "checkbox_theme",
        "switch_theme",
        "dropdown_theme",
        "dialog_theme",
        "snackbar_theme",
        "tooltip_theme",
        "progress_indicator_theme",
        "text_theme",
        "color_scheme",
    ):
        assert getattr(flet_theme, attribute) is not None


def test_theme_build_uses_static_font_configuration(monkeypatch):
    monkeypatch.setattr(font_config, "APP_FONT_FAMILY", "Launcher Sans")
    monkeypatch.setattr(font_config, "APP_FONT_ASSETS", {"Launcher Sans": r"fonts\LauncherSans-Regular.ttf"})

    theme = ui.UiTheme.build()

    assert ui.configured_font_family() == "Launcher Sans"
    assert ui.configured_page_fonts() == {"Launcher Sans": "fonts/LauncherSans-Regular.ttf"}
    assert theme.font_family == "Launcher Sans"
    assert theme.flet_theme.font_family == "Launcher Sans"
    assert theme.text_style().font_family == "Launcher Sans"


def test_theme_build_accepts_explicit_font_family_override():
    theme = ui.UiTheme.build(font_family="Georgia")

    assert theme.font_family == "Georgia"
    assert theme.flet_theme.font_family == "Georgia"
    assert theme.text_style().font_family == "Georgia"
    assert theme.flet_theme.text_theme.body_medium.font_family == "Georgia"


def test_wrappers_apply_semantic_overrides(fake_app):
    button = ui.Button(text="Save", variant="ghost", tone="neutral", size="sm")
    textfield = ui.TextField(label="RAM", value="4", dense=False, suffix_text="G")
    dropdown = ui.Dropdown(
        label="Language",
        dense=False,
        options=[ft.dropdown.Option(key="uk_UA", text="Ukrainian")],
    )

    assert button.style.bgcolor[ft.ControlState.DEFAULT] == ft.Colors.TRANSPARENT
    assert button.style.color[ft.ControlState.DEFAULT] == fake_app.theme.text_color
    assert button.style.padding.left == fake_app.theme.padding_sm
    assert textfield.dense is False
    assert textfield.suffix == "G"
    assert dropdown.dense is False


def test_form_helpers_share_theme_dimensions(fake_app):
    form_section = ui.FormSection(fake_app)
    toggle = ui.ToggleField(label="Enabled", value=True, on_change=lambda e: None)
    file_input = ui.FileInputTrigger(text="Browse", on_click=lambda e: None)
    section = form_section.section(
        title="General",
        controls=[form_section.wrap_control(ui.TextField(label="Name"))],
    )
    dialog = ui.FormDialog(
        fake_app,
        title="Offline profile",
        fields=[{"type": "textfield", "label": "Username", "key": "username", "value": "Steve"}],
        on_submit=lambda _data: None,
    )

    assert toggle.height == fake_app.theme.input_height
    assert file_input.height == fake_app.theme.button_height
    assert section.padding.left == fake_app.theme.section_padding
    assert dialog.modal.content.width == fake_app.theme.modal_width
    assert dialog.inputs["username"].width == fake_app.theme.modal_width


def test_architecture_disallows_direct_shell_flet_controls_outside_ui():
    banned_controls = {"TextField", "Dropdown", "Button", "AlertDialog", "Tooltip", "Container"}
    violations: list[str] = []

    for path in (ROOT_DIR / "launcher").rglob("*.py"):
        if "launcher/ui" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "ft":
                continue
            if func.attr in banned_controls:
                violations.append(f"{path}:{node.lineno}: ft.{func.attr}")

    assert violations == []


def test_modpack_modal_schedules_async_load(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append(func)
    fake_app.feedback.is_busy = lambda: False

    modal = ui.ModpackInstallModal(fake_app, "example-pack")
    modal.show()

    assert scheduled[0] == modal.load_modpack_details
    assert any(inspect.iscoroutinefunction(func) for func in scheduled[1:])


def test_modpack_modal_defers_install_until_after_close(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args))
    fake_app.feedback.is_busy = lambda: False
    fake_app.versions.get_by_name = lambda _name: None

    modal = ui.ModpackInstallModal(fake_app, "example-pack")
    modal.version_name = "Create Plus"
    modal.versions = [{"id": "ver-1", "project_id": "project-1"}]
    modal.selected_version = "ver-1"
    modal.modpack_data = {"icon_url": "https://example.com/icon.png"}
    closed = []
    modal.close = lambda: closed.append(True)

    modal.install_modpack()

    assert closed == [True]
    assert scheduled == [
        (
            modal._start_install_after_close,
            ("project-1", "Create Plus", "ver-1", "https://example.com/icon.png"),
        )
    ]


def test_modal_fields_use_full_width(fake_app):
    source_version = fake_app.versions.all()[0]
    AppContext.set(fake_app)
    form_dialog = ui.FormDialog(
        fake_app,
        title="Offline profile",
        fields=[{"type": "textfield", "label": "Username", "key": "username", "value": "Steve"}],
        on_submit=lambda _data: None,
    )
    install_modal = ui.VersionInstallModal(fake_app)
    copy_modal = ui.VersionCopyModal(fake_app, source_version)
    modpack_modal = ui.ModpackInstallModal(fake_app, "example-pack")
    modpack_modal.modpack_data = {"id": "example-pack"}
    modpack_modal.versions = [{"id": "ver-1", "version_number": "1.0.0", "game_versions": ["1.20.1"]}]
    modpack_modal.update_view()
    curseforge_modal = ui.CurseForgeImportModal(fake_app)

    assert form_dialog.inputs["username"].width == fake_app.theme.modal_width
    assert install_modal.version_name.width == fake_app.theme.modal_width
    assert install_modal.type_select.width == fake_app.theme.modal_width
    assert install_modal.version_select.width == fake_app.theme.modal_width
    assert copy_modal.version_name.width == fake_app.theme.modal_width
    assert copy_modal.type_select.width == fake_app.theme.modal_width
    assert modpack_modal.name_input.width == fake_app.theme.modal_width
    assert modpack_modal.version_dropdown.width == fake_app.theme.modal_width
    assert curseforge_modal.name_input.width == fake_app.theme.modal_width


def test_form_dialog_closes_before_submit_callback(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args))
    events = []

    dialog = ui.FormDialog(
        fake_app,
        title="Offline profile",
        fields=[{"type": "textfield", "label": "Username", "key": "username", "value": "Steve"}],
        on_submit=lambda data: events.append(("submit", data)),
    )
    dialog.close = lambda: events.append("close")

    dialog.handle_submit()

    assert events == ["close"]
    assert len(scheduled) == 1

    callback, args = scheduled[0]
    asyncio.run(callback(*args))

    assert events == ["close", ("submit", {"username": "Steve"})]


def test_version_card_uses_hoverable_replacement_action_surface(fake_app):
    card = fake_app.version_card.create(
        title="Vanilla 1.20.1",
        subtitle="vanilla 1.20.1",
        image=None,
        on_action_click=lambda e: None,
    )

    assert isinstance(card, ft.GestureDetector)
    assert isinstance(card.content, ft.Container)
    assert isinstance(card.content.content.controls[1], ft.Text)
    card.on_enter(SimpleNamespace())
    assert isinstance(card.content.content.controls[2].content, ft.GestureDetector)


def test_version_card_constrains_preview_media_and_action_icon(fake_app):
    card = fake_app.version_card.create(
        title="Vanilla 1.20.1",
        subtitle="vanilla 1.20.1",
        image=None,
        on_action_click=lambda e: None,
    )

    body = card.content.content
    preview = body.controls[2]
    preview_control = preview.content
    card.on_enter(SimpleNamespace())
    action_button = preview.content.content

    assert preview.height >= fake_app.theme.version_image_size_compact
    assert preview_control.size <= fake_app.theme.version_image_size_compact
    assert action_button.width > fake_app.theme.button_height
    assert action_button.width <= preview.height
    assert action_button.height <= preview.height
    assert body.controls[3].height == fake_app.theme.spacing_sm


def test_version_card_reserves_two_line_title_area(fake_app):
    short_card = fake_app.version_card.create(
        title="Tensa",
        subtitle="TensaCraft 26.1.2",
        image=None,
    )
    long_card = fake_app.version_card.create(
        title="Aeronautics Voxy",
        subtitle="TensaCraft 1.21.1",
        image=None,
    )

    short_title_block = short_card.content.content.controls[0]
    long_title_block = long_card.content.content.controls[0]

    assert short_title_block.height == long_title_block.height
    assert short_title_block.height >= round(fake_app.theme.text_size_sm * 2.4)
    assert short_title_block.content.max_lines == 2


def test_modpacks_page_schedules_initial_async_load(fake_app):
    page = ModpacksPage(fake_app)
    page.view()

    called = []
    page._load_initial = lambda: called.append(True)
    page.after_show()

    assert called == [True]


def test_build_search_field_uses_full_width_compact_prefix_icon(fake_app):
    search = ui.build_search_field(
        fake_app,
        label="Search",
        value="fabric",
        on_submit=lambda e: None,
    )

    assert isinstance(search.row, ft.Row)
    assert search.field.width is None
    assert search.field.expand == 1
    assert search.field.label is None
    assert search.field.hint_text == "Search"
    assert search.field.prefix_icon == ft.Icons.SEARCH


def test_text_wrapper_uses_current_theme_font_family():
    previous_theme = ui.current_theme()
    themed = ui.UiTheme.build(font_family="Georgia")
    ui.set_current_theme(themed)

    try:
        text = ui.Text("Launcher")
    finally:
        ui.set_current_theme(previous_theme)

    assert text.font_family == "Georgia"


def test_app_configure_page_applies_configured_page_fonts(fake_app, monkeypatch):
    monkeypatch.setattr(font_config, "APP_FONT_FAMILY", "Launcher Sans")
    monkeypatch.setattr(font_config, "APP_FONT_ASSETS", {"Launcher Sans": "fonts/LauncherSans-Regular.ttf"})
    app = SimpleNamespace(page=fake_app.page, theme=ui.UiTheme.build(), util=fake_app.util)

    App._configure_page(app)

    assert app.page.fonts == {"Launcher Sans": "fonts/LauncherSans-Regular.ttf"}
    assert app.page.theme.font_family == "Launcher Sans"


def test_system_service_caches_avatar_locally(tmp_path, monkeypatch):
    calls = []

    class Response:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"avatar-bytes"

    def fake_get(_self, url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("launcher.platform.system.requests.Session.get", fake_get)
    path_service = SimpleNamespace(paths=SimpleNamespace(app_state_dir=tmp_path))
    service = SystemService(path_service)

    first = service.get_skin_url("Steve")
    second = service.get_skin_url("Steve")

    assert first is not None
    assert second == first
    assert Path(first).read_bytes() == b"avatar-bytes"
    assert len(calls) == 1


def test_theme_scale_derives_sizes_from_single_ui_scale():
    base = ui.UiTheme.build()
    scaled = ui.UiTheme.build(ui_scale=1.25)

    assert base.ui_scale == 1.0
    assert scaled.ui_scale == 1.25
    assert scaled.control_height == 48
    assert scaled.button_height == 38
    assert scaled.padding_md == 15
    assert scaled.radius_sm == 12
    assert scaled.icon_size == 22
    assert scaled.switch_scale() == 1.0
    assert scaled.control_height > base.control_height
    assert scaled.padding_md > base.padding_md
    assert scaled.radius_sm > base.radius_sm
    assert scaled.icon_size > base.icon_size
    assert scaled.switch_scale() >= base.switch_scale()


def test_theme_build_populates_global_flet_subthemes():
    theme = ui.UiTheme.build().flet_theme

    assert theme.button_theme is not None
    assert theme.icon_button_theme is not None
    assert theme.floating_action_button_theme is not None
    assert theme.dropdown_theme is not None
    assert theme.checkbox_theme is not None
    assert theme.switch_theme is not None
    assert theme.dialog_theme is not None
    assert theme.snackbar_theme is not None
    assert theme.tooltip_theme is not None
    assert theme.progress_indicator_theme is not None


def test_wrapper_semantic_overrides_affect_styles():
    default_button = ui.Button(text="Default")
    outline_button = ui.Button(text="Outline", variant="outline", tone="neutral", size="sm")
    relaxed_textfield = ui.TextField(label="Name", dense=False)
    relaxed_dropdown = ui.Dropdown(
        label="Language",
        options=[ft.dropdown.Option(key="uk_UA", text="Ukrainian")],
        dense=False,
    )

    assert outline_button.style.bgcolor[ft.ControlState.DEFAULT] != default_button.style.bgcolor[ft.ControlState.DEFAULT]
    assert outline_button.style.padding.left < default_button.style.padding.left
    assert relaxed_textfield.dense is False
    assert relaxed_dropdown.dense is False


def test_form_scaffolds_share_theme_tokens(fake_app):
    theme = fake_app.theme
    toggle = ui.ToggleField(label="Auto update", value=True, on_change=lambda e: None)
    file_input = ui.FileInputTrigger(text="Browse")
    section = ui.FormSection(fake_app).section(
        title="General",
        controls=[ui.TextField(label="Name")],
        description="Shared shell",
    )
    dialog = ui.FormDialog(
        fake_app,
        title="Offline profile",
        fields=[{"type": "textfield", "label": "Username", "key": "username", "value": "Steve"}],
        on_submit=lambda _data: None,
    )

    assert toggle.height == theme.input_height
    assert _padding_tuple(toggle.padding) == _padding_tuple(theme.field_shell_padding())
    assert file_input.height == theme.button_height
    assert _padding_tuple(section.padding) == (
        theme.section_padding,
        theme.section_padding,
        theme.section_padding,
        theme.section_padding,
    )
    assert dialog.modal.content.width == theme.modal_width
    assert dialog.modal.content.height == theme.modal_height // 2


def test_architecture_guard_disallows_direct_shell_flet_controls_outside_ui():
    banned = {"TextField", "Dropdown", "Button", "AlertDialog", "Tooltip", "Container"}
    violations: list[str] = []

    for path in (ROOT_DIR / "launcher").rglob("*.py"):
        rel_path = path.relative_to(ROOT_DIR).as_posix()
        if rel_path.startswith("launcher/ui/"):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_flet_names = set()
        flet_aliases = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "flet":
                        flet_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "flet":
                for alias in node.names:
                    imported_flet_names.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in ({"ft", "flet"} | flet_aliases) and func.attr in banned:
                    violations.append(f"{rel_path}:{node.lineno}: ft.{func.attr}")
            elif isinstance(func, ast.Name) and func.id in banned and func.id in imported_flet_names:
                violations.append(f"{rel_path}:{node.lineno}: {func.id}")

    assert violations == []


def test_pages_survive_recursive_before_update_smoke(fake_app, monkeypatch):
    version = fake_app.versions.all()[0]
    version.client = "fabric"
    version.loader = "fabric"
    version_root = fake_app.util.minecraft_dir / "versions" / version.version_id
    version_root.mkdir(parents=True, exist_ok=True)
    version.path = str(version_root)

    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))
    monkeypatch.setattr("minecraft_launcher_lib.utils.get_installed_versions", lambda _dir: [{"id": "fabric-loader-0.16"}])

    pages = [
        Home(fake_app),
        ModpacksPage(fake_app),
        ProfilesPage(fake_app),
        SettingsPage(fake_app),
        VersionSettingsPage(fake_app, version.version_id),
        VersionsPage(fake_app),
        ModsManagerPage(fake_app, version),
    ]

    visited: set[int] = set()

    def walk(control):
        if isinstance(control, ft.Control):
            control_id = id(control)
            if control_id in visited:
                return
            visited.add(control_id)
            control.before_update()
            if is_dataclass(control):
                for field in fields(control):
                    try:
                        value = getattr(control, field.name)
                    except Exception:
                        continue
                    walk(value)
            return
        if isinstance(control, dict):
            for value in control.values():
                walk(value)
        elif isinstance(control, (list, tuple, set)):
            for value in control:
                walk(value)

    for page in pages:
        visited.clear()
        walk(page.view())


def test_file_picker_schedules_async_directory_result(fake_app):
    events = []
    scheduled = []

    class FakeService:
        async def get_directory_path(self, **_kwargs):
            return "D:/Games/Minecraft"

    picker = ui.FilePicker(page=fake_app.page, on_result=lambda e: events.append(e))
    picker.service = FakeService()
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args, kwargs))

    result = picker.get_directory_path(dialog_title="Select directory")

    assert result is None
    assert len(scheduled) == 1

    callback, args, _kwargs = scheduled[0]
    asyncio.run(callback(*args))

    assert events[0].path == "D:/Games/Minecraft"


def test_file_picker_initial_directory_uses_existing_path(tmp_path):
    directory = tmp_path / "Minecraft"
    file_path = tmp_path / "Java" / "bin" / "java.exe"
    missing = tmp_path / "Missing"
    directory.mkdir()
    file_path.parent.mkdir(parents=True)
    file_path.write_text("java", encoding="utf-8")

    assert ui.initial_directory_from_path(str(directory)) == str(directory)
    assert ui.initial_directory_from_path(str(file_path)) == str(file_path.parent)
    assert ui.initial_directory_from_path(str(missing)) is None


def test_header_uses_compact_height_without_title_slot(fake_app):
    fake_app.header.set_params(title="Should not render", show_back_btn=True)
    header = fake_app.header.view()

    assert header.height == fake_app.theme.header_height


def test_invoke_on_ui_schedules_coroutine_with_page_run_task(fake_app):
    scheduled = []

    def fake_run_task(func, *args, **kwargs):
        scheduled.append(func)
        return None

    fake_app.page.run_task = fake_run_task

    worker = threading.Thread(target=lambda: invoke_on_ui(fake_app.page, lambda: None))
    worker.start()
    worker.join()

    assert len(scheduled) == 1
    assert inspect.iscoroutinefunction(scheduled[0])


def test_confirm_dialog_defers_callback_until_after_close(fake_app):
    events = []
    scheduled = []
    opened = []

    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args))
    fake_app.page.pop_dialog = lambda: events.append("pop")
    alert = ui.Alert(fake_app)
    alert.open_dialog = lambda dialog: opened.append(dialog)

    alert.show_confirm("Confirm", "Question", lambda response: events.append(("callback", response)))

    assert len(opened) == 1
    dialog = opened[0]

    dialog.actions[0].on_click(None)

    assert events == ["pop"]
    callback, args = scheduled[-1]
    asyncio.run(callback(*args))

    assert events == ["pop", ("callback", True)]


def test_schedule_update_prefers_immediate_update_on_page_loop(monkeypatch):
    updates = []
    page = SimpleNamespace(update=lambda: updates.append("update"), run_task=lambda *_args, **_kwargs: None)

    monkeypatch.setattr("launcher.ui.core.page_runtime._is_on_page_loop", lambda _page: True)

    schedule_update(page)

    assert updates == ["update"]


def test_schedule_update_uses_run_task_off_page_loop(monkeypatch):
    scheduled = []
    page = SimpleNamespace(
        update=lambda: scheduled.append("direct-update"),
        run_task=lambda func, *args, **kwargs: scheduled.append(func),
        schedule_update=lambda: scheduled.append("deferred"),
    )

    monkeypatch.setattr("launcher.ui.core.page_runtime._is_on_page_loop", lambda _page: False)

    schedule_update(page)

    assert len(scheduled) == 1
    assert inspect.iscoroutinefunction(scheduled[0])


def test_dialog_runtime_removes_closed_dialog_from_stack():
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))

    def pop_dialog():
        if dialog_stack.controls:
            dialog_stack.controls[-1].open = False
            dialog_stack.controls.pop()
            dialog_stack.update()

    page = SimpleNamespace(
        _dialogs=dialog_stack,
        show_dialog=lambda dialog: ft.Page.show_dialog(page, dialog),
        pop_dialog=pop_dialog,
        update=lambda: updates.append("page"),
        schedule_update=lambda: updates.append("schedule"),
    )
    dialog = ui.BottomSheet(content=ft.Container(), open=False)

    show_dialog(page, dialog)
    assert dialog in dialog_stack.controls
    assert dialog.open is True

    close_dialog(page, dialog)
    assert dialog not in dialog_stack.controls
    assert dialog.open is False

    show_dialog(page, dialog)
    assert dialog in dialog_stack.controls
    assert dialog.open is True


def test_dialog_runtime_removes_dialog_when_pop_finds_no_open_dialog():
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))
    pop_calls = []

    def pop_dialog():
        pop_calls.append("pop")
        return None

    page = SimpleNamespace(
        _dialogs=dialog_stack,
        show_dialog=lambda dialog: ft.Page.show_dialog(page, dialog),
        pop_dialog=pop_dialog,
        update=lambda: updates.append("page"),
        schedule_update=lambda: updates.append("schedule"),
    )
    dialog = ui.BottomSheet(content=ft.Container(), open=False)

    show_dialog(page, dialog)
    assert dialog in dialog_stack.controls
    assert dialog.open is True

    dialog.open = False
    close_dialog(page, dialog)

    assert pop_calls == ["pop"]
    assert dialog.open is False
    assert dialog not in dialog_stack.controls


def test_dialog_runtime_removes_dialog_when_flet_pop_only_closes_it():
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))

    def pop_dialog():
        if not dialog_stack.controls:
            return None
        dialog_stack.controls[-1].open = False
        return dialog_stack.controls[-1]

    page = SimpleNamespace(
        _dialogs=dialog_stack,
        show_dialog=lambda dialog: ft.Page.show_dialog(page, dialog),
        pop_dialog=pop_dialog,
        update=lambda: updates.append("page"),
        schedule_update=lambda: updates.append("schedule"),
    )
    dialog = ui.BottomSheet(content=ft.Container(), open=False)

    show_dialog(page, dialog)
    assert dialog in dialog_stack.controls
    assert dialog.open is True

    close_dialog(page, dialog)

    assert dialog.open is False
    assert dialog not in dialog_stack.controls


def test_dialog_runtime_keeps_real_flet_dialog_mounted_until_dismiss():
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))

    def pop_dialog():
        if not dialog_stack.controls:
            return None
        dialog_stack.controls[-1].open = False
        return dialog_stack.controls[-1]

    def remove_dialog(dialog):
        dialog_stack.controls.remove(dialog)
        dialog_stack.update()

    page = SimpleNamespace(
        _dialogs=dialog_stack,
        _remove_dialog=remove_dialog,
        _wrap_dialog_on_dismiss=lambda _dialog: None,
        pop_dialog=pop_dialog,
        update=lambda: updates.append("page"),
        schedule_update=lambda: updates.append("schedule"),
    )
    dialog = ui.BottomSheet(content=ft.Container(), open=True)
    dialog_stack.controls.append(dialog)

    close_dialog(page, dialog)

    assert dialog.open is False
    assert dialog in dialog_stack.controls
    assert updates == ["page"]

    remove_dialog(dialog)
    assert dialog not in dialog_stack.controls


def test_dialog_runtime_forces_dialog_closed_when_page_close_is_noop():
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))
    close_calls = []
    page = SimpleNamespace(
        _dialogs=dialog_stack,
        show_dialog=lambda dialog: ft.Page.show_dialog(page, dialog),
        close=lambda dialog: close_calls.append(dialog),
        update=lambda: updates.append("page"),
        schedule_update=lambda: updates.append("schedule"),
    )
    dialog = ui.BottomSheet(content=ft.Container(), open=False)

    show_dialog(page, dialog)
    assert dialog.open is True

    close_dialog(page, dialog)

    assert close_calls == [dialog]
    assert dialog.open is False
    assert dialog not in dialog_stack.controls

    show_dialog(page, dialog)
    assert dialog.open is True

    close_dialog(page, dialog)

    assert close_calls == [dialog, dialog]
    assert dialog.open is False
    assert dialog not in dialog_stack.controls


def test_progress_overlay_reopens_for_new_install_cycle(fake_app):
    overlay = fake_app.progressbar

    overlay._show_impl("Installing A", 5, 100, True)
    assert overlay.bottom_sheet.open is True
    assert overlay.open_button.visible is True

    overlay.toggle_bottom_sheet()
    assert overlay.bottom_sheet.open is False
    assert overlay.closed_manually is True

    overlay._hide_impl(manual=False)
    assert overlay.open_button.visible is False
    assert overlay.closed_manually is False

    overlay.start_cycle()
    overlay._show_impl("Installing B", 0, 100, True)
    assert overlay.bottom_sheet.open is True
    assert overlay.open_button.visible is True


def test_install_session_can_keep_progress_sheet_closed_until_user_opens(fake_app):
    overlay = fake_app.progressbar

    def run_task_immediately(func, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(func(*args, **kwargs))
            return None
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    fake_app.page.run_task = run_task_immediately

    operation = fake_app.feedback.begin_operation("Syncing updates", auto_open=False)

    assert overlay.open_button.visible is True
    assert overlay.bottom_sheet.open is False

    operation.update("Downloading files", progress=1, total=2, auto_open=False)

    assert overlay.open_button.visible is True
    assert overlay.bottom_sheet.open is False

    overlay.toggle_bottom_sheet()
    assert overlay.bottom_sheet.open is True

    operation.update("Finalizing", progress=2, total=2, auto_open=False)
    assert overlay.bottom_sheet.open is True

    operation.finish("Done")

    assert overlay.open_button.visible is False
    assert overlay.bottom_sheet.open is False


def test_progress_overlay_hide_accepts_manual_keyword(fake_app):
    fake_app.progressbar.hide(manual=False)


def test_progress_overlay_hide_forces_sheet_closed_when_page_close_is_noop(fake_app):
    overlay = fake_app.progressbar
    overlay.bottom_sheet.open = True
    fake_app.page.close = lambda _dialog: None

    overlay._hide_impl(manual=False)

    assert overlay.bottom_sheet.open is False
    assert overlay.open_button.visible is False


def test_progress_overlay_removes_managed_sheet_when_dismiss_event_is_missing(fake_app):
    overlay = fake_app.progressbar
    updates = []
    dialog_stack = SimpleNamespace(controls=[], update=lambda: updates.append("stack"))

    def show_managed_dialog(dialog):
        dialog.open = True
        if dialog not in dialog_stack.controls:
            dialog_stack.controls.append(dialog)

    def pop_dialog():
        dialog = next((item for item in reversed(dialog_stack.controls) if item.open), None)
        if dialog is None:
            return None
        dialog.open = False
        return dialog

    def remove_dialog(dialog):
        if dialog in dialog_stack.controls:
            dialog_stack.controls.remove(dialog)
            dialog_stack.update()

    fake_app.page._dialogs = dialog_stack
    fake_app.page.show_dialog = show_managed_dialog
    fake_app.page.pop_dialog = pop_dialog
    fake_app.page._remove_dialog = remove_dialog
    fake_app.page._wrap_dialog_on_dismiss = lambda _dialog: None
    fake_app.page._restore_dialog_on_dismiss = lambda _dialog: None

    overlay._show_impl("Synchronizing updates", 1, 2, force_open=True, auto_open=True)
    assert overlay.bottom_sheet in dialog_stack.controls
    assert overlay.bottom_sheet.open is True

    overlay._hide_impl(manual=False)

    assert overlay.bottom_sheet.open is False
    assert overlay.bottom_sheet not in dialog_stack.controls
    assert overlay.open_button.visible is False
    assert overlay._dismiss_pending is False


def test_progress_overlay_hide_closes_dialog_when_open_state_lags(fake_app):
    overlay = fake_app.progressbar
    closed = []

    overlay.bottom_sheet.open = False
    fake_app.page.close = lambda dialog: closed.append(dialog)

    overlay._hide_impl(manual=False)

    assert closed == [overlay.bottom_sheet]
    assert overlay.open_button.visible is False


def test_progress_overlay_does_not_stack_duplicate_sheets_when_open_state_lags(fake_app):
    overlay = fake_app.progressbar
    opened = []

    def open_without_mutating_state(dialog):
        opened.append(dialog)

    fake_app.page.show_dialog = open_without_mutating_state

    overlay._show_impl("Installation started", 0, 100, force_open=True, auto_open=True)
    overlay._show_impl("Downloading sodium.jar", 1, 10, force_open=False, auto_open=True)

    assert opened == [overlay.bottom_sheet]


def test_progress_overlay_keeps_sheet_hidden_after_user_dismiss(fake_app):
    overlay = fake_app.progressbar
    opened = []

    def record_open(dialog):
        opened.append(dialog)
        dialog.open = True

    fake_app.page.show_dialog = record_open

    overlay._show_impl("Installing Minecraft", 1, 100, force_open=True, auto_open=True)
    assert overlay.bottom_sheet.open is True

    overlay.bottom_sheet.open = False
    overlay._on_bottom_sheet_dismiss(None)

    assert overlay.closed_manually is True

    overlay._show_impl("Downloading tiny file", 2, 100, force_open=False, auto_open=True)

    assert opened == [overlay.bottom_sheet]
    assert overlay.bottom_sheet.open is False


def test_progress_overlay_ignores_stale_queued_show_after_hide(fake_app):
    overlay = fake_app.progressbar
    queued = []

    def queue_task(func, *args, **kwargs):
        queued.append((func, args, kwargs))

    fake_app.page.run_task = queue_task

    overlay._start_cycle_impl()
    overlay.show("Downloading sodium-extra-neoforge.jar", 10, 10, force_open=True, auto_open=True)
    overlay._hide_impl(manual=False)

    assert queued
    while queued:
        task, args, kwargs = queued.pop(0)
        result = task(*args, **kwargs)
        if inspect.isawaitable(result):
            asyncio.run(result)

    assert overlay.bottom_sheet.open is False
    assert overlay.open_button.visible is False


def test_progress_overlay_waits_for_programmatic_dismiss(fake_app):
    overlay = fake_app.progressbar

    overlay._show_impl("Installing", 5, 100, force_open=True, auto_open=True)

    async def wait_for_hide():
        waiter = asyncio.create_task(overlay.wait_until_hidden(timeout=1))
        await asyncio.sleep(0)
        assert waiter.done() is False
        overlay._hide_impl(manual=False)
        await asyncio.sleep(0)
        assert waiter.done() is False
        overlay._on_bottom_sheet_dismiss(None)
        return await waiter

    asyncio.run(wait_for_hide())

    assert overlay.closed_manually is False
    assert overlay.bottom_sheet.open is False
    assert overlay.open_button.visible is False


def test_launcher_code_does_not_call_legacy_progress_api():
    forbidden = (
        ".feedback.update_current_operation",
        ".progressbar.update_progress",
        ".progressbar.installation_complete",
        "installation_complete(",
        "update_progress(",
    )
    offenders: list[str] = []

    for path in (ROOT_DIR / "launcher").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT_DIR)}: {token}")

    assert offenders == []


def test_feedback_shutdown_can_skip_ui_hide():
    hidden = []
    feedback = FeedbackService(SimpleNamespace(log=SimpleNamespace(debug=lambda *_args, **_kwargs: None)))
    feedback.attach(
        progress_overlay=SimpleNamespace(
            start_cycle=lambda: None,
            show=lambda *_args, **_kwargs: None,
            hide=lambda **kwargs: hidden.append(kwargs),
        )
    )

    feedback.begin_operation("Checking updates")
    feedback.shutdown(update_ui=False)

    assert hidden == []


def test_feedback_operation_can_close_without_completion_message():
    completed = []
    hidden = []
    feedback = FeedbackService(
        SimpleNamespace(log=SimpleNamespace(debug=lambda *_args, **_kwargs: None)),
        auto_close_delay=0,
    )
    feedback.attach(
        progress_overlay=SimpleNamespace(
            start_cycle=lambda: None,
            show=lambda *_args, **_kwargs: None,
            installation_complete=lambda *args, **kwargs: completed.append((args, kwargs)),
            hide=lambda **kwargs: hidden.append(kwargs),
        )
    )

    operation = feedback.begin_operation("Checking updates")
    operation.finish(show_success=False)

    assert completed == []
    assert hidden == [{"manual": False}]


def test_app_window_lifecycle_does_not_intercept_native_close():
    page = SimpleNamespace(
        window=SimpleNamespace(prevent_close=True, on_event="legacy-handler"),
        on_disconnect=None,
    )
    app = SimpleNamespace(page=page, _handle_disconnect=lambda: None)

    App._configure_window_lifecycle(app)

    assert page.window.prevent_close is False
    assert page.window.on_event is None
    assert callable(page.on_disconnect)


def test_app_close_window_async_awaits_window_destroy():
    called = []
    window = SimpleNamespace(prevent_close=True)

    async def destroy():
        called.append("destroy")

    window.destroy = destroy
    window.close = lambda: called.append("close")
    app = SimpleNamespace(page=SimpleNamespace(window=window), log=SimpleNamespace(debug=lambda *_args, **_kwargs: None))

    asyncio.run(App._close_window_async(app))

    assert window.prevent_close is False
    assert called == ["destroy"]


def test_feedback_operations_keep_root_visible_until_children_finish():
    completed = []
    hidden = []
    shown = []

    feedback = FeedbackService(
        SimpleNamespace(log=SimpleNamespace(debug=lambda *_args, **_kwargs: None)),
        auto_close_delay=0,
    )
    feedback.attach(
        progress_overlay=SimpleNamespace(
            start_cycle=lambda: None,
            show=lambda *args, **kwargs: shown.append((args, kwargs)),
            installation_complete=lambda *_args, **_kwargs: completed.append(True),
            hide=lambda **kwargs: hidden.append(kwargs),
        ),
    )

    root = feedback.begin_operation("Installing")
    child = feedback.begin_operation("Installing loader")

    assert feedback.is_busy() is True

    child.finish()
    assert feedback.is_busy() is True
    assert completed == []

    root.finish("Done")
    assert feedback.is_busy() is False
    assert completed == []
    assert hidden == [{"manual": False}]
    assert shown


def test_version_install_modal_defers_install_until_after_close(fake_app):
    scheduled = []
    fake_app.page.run_task = lambda func, *args, **kwargs: scheduled.append((func, args))
    fake_app.feedback.is_busy = lambda: False
    fake_app.versions.get_by_name = lambda _name: None
    AppContext.set(fake_app)

    modal = ui.VersionInstallModal(fake_app)
    modal.version_name.value = "Build 1"
    modal.type_select.value = "fabric"
    modal.version_select.value = "1.20.1"
    closed = []
    modal.close = lambda: closed.append(True)

    modal.create_version(None)

    assert closed == [True]
    assert scheduled[-1] == (
        modal._start_install_after_close,
        ("Build 1", "fabric", "1.20.1"),
    )
