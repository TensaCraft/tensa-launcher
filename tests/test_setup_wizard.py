from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import flet as ft

import launcher.platform.paths as paths_module
from launcher.application.setup_wizard import (
    SETUP_WIZARD_COMPLETED_KEY,
    SetupWizardService,
)
from launcher.pages.setup_wizard import SetupWizardPage, maybe_show_setup_wizard
from launcher.storage.config_store import Config


def _windows_default_app_state_dir(local_app_data: Path) -> Path:
    return local_app_data / "TensaLauncher"


def _windows_default_minecraft_dir(local_app_data: Path) -> Path:
    if local_app_data.name.casefold() == "local" and local_app_data.parent.name.casefold() == "appdata":
        return local_app_data.parent / "Roaming" / "TensaLauncher"
    return local_app_data.parent / "AppData" / "Roaming" / "TensaLauncher"


class MemoryConfig:
    def __init__(self, initial: dict | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def update(self, values: dict) -> None:
        self.data.update(values)

    def keys(self):
        return tuple(self.data.keys())


def test_setup_wizard_opens_for_fresh_config() -> None:
    service = SetupWizardService()

    assert service.should_open(MemoryConfig(), []) is True


def test_setup_wizard_stays_closed_after_completed() -> None:
    service = SetupWizardService()
    config = MemoryConfig({SETUP_WIZARD_COMPLETED_KEY: "yes", "lang": "uk_UA"})

    assert service.should_open(config, []) is False


def test_setup_wizard_reopens_when_storage_has_permission_issues() -> None:
    service = SetupWizardService()
    config = MemoryConfig({SETUP_WIZARD_COMPLETED_KEY: "yes"})

    assert service.should_open(config, ["Minecraft data: denied"]) is True


def test_setup_wizard_does_not_interrupt_existing_config_without_issues() -> None:
    service = SetupWizardService()
    config = MemoryConfig({"lang": "uk_UA", "minecraft_game_dir": "minecraft"})

    assert service.should_open(config, []) is False


def test_setup_wizard_normalizes_default_minecraft_directory(tmp_path: Path) -> None:
    service = SetupWizardService()
    app_dir = tmp_path / "app"
    target = app_dir / "minecraft"

    normalized = service.normalize_minecraft_dir(app_dir=app_dir, raw_value=str(target))

    assert normalized.path == target
    assert normalized.stored_value is None


def test_setup_wizard_normalizes_custom_minecraft_directory(tmp_path: Path) -> None:
    service = SetupWizardService()
    app_dir = tmp_path / "app"
    target = tmp_path / "data" / "minecraft"

    normalized = service.normalize_minecraft_dir(app_dir=app_dir, raw_value=str(target))

    assert normalized.path == target
    assert normalized.stored_value == str(target)


def test_setup_wizard_rebases_non_empty_storage_root_to_launcher_directory(tmp_path: Path) -> None:
    service = SetupWizardService()
    selected = tmp_path / "Games"
    selected.mkdir()
    (selected / "other-file.txt").write_text("keep", encoding="utf-8")

    normalized = service.normalize_app_state_dir(current_dir=tmp_path / "state", raw_value=str(selected))

    assert normalized == selected / "TensaLauncher"


def test_setup_wizard_rebases_non_empty_minecraft_root_to_derived_launcher_directory(tmp_path: Path) -> None:
    service = SetupWizardService()
    app_dir = tmp_path / "state"
    selected = tmp_path / "Games"
    selected.mkdir()
    (selected / "other-file.txt").write_text("keep", encoding="utf-8")

    normalized = service.normalize_minecraft_dir(app_dir=app_dir, raw_value=str(selected))

    assert normalized.path == selected / "TensaLauncher" / "minecraft"
    assert normalized.stored_value == str(selected / "TensaLauncher" / "minecraft")


def test_setup_wizard_storage_validation_accepts_missing_creatable_paths_without_creating_them(tmp_path: Path) -> None:
    service = SetupWizardService()
    root = tmp_path / "missing" / "TensaLauncher"

    issues = service.storage_issues(app_state_dir=root, minecraft_dir=root / "minecraft")

    assert issues == []
    assert not root.exists()


def test_setup_wizard_page_opens_for_fresh_install(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    fake_app.util.app_state_dir = tmp_path / "state"
    fake_app.util.minecraft_dir = tmp_path / "minecraft"
    opened_pages = []
    fake_app.show_page = lambda page: opened_pages.append(page)

    assert maybe_show_setup_wizard(fake_app) is True
    assert len(opened_pages) == 1
    assert isinstance(opened_pages[0], SetupWizardPage)
    assert opened_pages[0].title_text.value == "setup_wizard_title"


def test_setup_wizard_page_prefers_storage_layout_paths(fake_app, tmp_path: Path) -> None:
    layout_state = tmp_path / "LocalAppData" / "TensaLauncher"
    layout_minecraft = layout_state / "minecraft"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"

    fake_app.config = MemoryConfig()
    fake_app.paths = SimpleNamespace(app_state_dir=layout_state, minecraft_dir=layout_minecraft)
    fake_app.util.app_state_dir = package_dir
    fake_app.util.minecraft_dir = package_dir / "minecraft"

    page = SetupWizardPage(fake_app, storage_issues=["Launcher data: unsafe package directory"])

    assert page.launcher_data_dir.value == str(layout_state)
    assert page._field_minecraft_dir() == layout_minecraft


def test_setup_wizard_page_centers_setup_content(fake_app) -> None:
    fake_app.config = MemoryConfig()

    page = SetupWizardPage(fake_app)
    view = page.view()
    column = view.content

    assert view.alignment == ft.Alignment(0, 0)
    assert column.alignment == ft.MainAxisAlignment.CENTER
    assert column.horizontal_alignment == ft.CrossAxisAlignment.CENTER


def test_setup_wizard_page_defaults_to_english_for_fresh_config(fake_app) -> None:
    fake_app.config = MemoryConfig()

    page = SetupWizardPage(fake_app)

    assert page.language_select.value == "en_US"


def test_setup_wizard_page_language_select_is_full_width(fake_app) -> None:
    fake_app.config = MemoryConfig()

    page = SetupWizardPage(fake_app)
    page.view()

    assert page.language_select.width is None
    assert page.language_select.expand is True


def test_setup_wizard_page_only_shows_primary_path_input(fake_app) -> None:
    fake_app.config = MemoryConfig()

    page = SetupWizardPage(fake_app)
    page.view()

    assert not hasattr(page, "desktop_shortcut_select")
    assert not hasattr(page, "paths_preview_title")
    assert not hasattr(page, "launcher_data_preview_value")


def test_setup_wizard_page_relabels_when_language_changes(fake_app) -> None:
    fake_app.config = MemoryConfig({"lang": "uk_UA"})
    fake_app.trans = lambda key, **_placeholders: f"{fake_app.config.get('lang', 'en_US')}:{key}"
    page = SetupWizardPage(fake_app)
    page.view()

    assert page.title_text.value == "uk_UA:setup_wizard_title"
    assert page.launcher_data_dir.label == "uk_UA:setup_wizard_launcher_data"
    assert page.save_button.content == "uk_UA:setup_wizard_save"

    page.language_select.value = "en_US"
    page.language_select.on_select(SimpleNamespace(control=page.language_select))

    assert fake_app.config.get("lang") == "en_US"
    assert page.title_text.value == "en_US:setup_wizard_title"
    assert page.launcher_data_dir.label == "en_US:setup_wizard_launcher_data"
    assert page.save_button.content == "en_US:setup_wizard_save"


def test_setup_wizard_page_opens_picker_at_existing_launcher_data_dir(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    selected_root = tmp_path / "TensaLauncher"
    selected_root.mkdir()
    captured: dict[str, object] = {}

    page = SetupWizardPage(fake_app)
    page.launcher_data_dir.value = str(selected_root)
    page.launcher_data_picker.get_directory_path = lambda **kwargs: captured.update(kwargs)

    page._open_launcher_data_picker(None)

    assert captured["initial_directory"] == str(selected_root)
    assert captured["dialog_title"] == "select_directory"


def test_setup_wizard_page_omits_picker_initial_directory_when_current_path_is_missing(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    captured: dict[str, object] = {}

    page = SetupWizardPage(fake_app)
    page.launcher_data_dir.value = str(tmp_path / "missing")
    page.launcher_data_picker.get_directory_path = lambda **kwargs: captured.update(kwargs)

    page._open_launcher_data_picker(None)

    assert captured == {"dialog_title": "select_directory"}


def test_setup_wizard_page_keeps_selected_custom_directory(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    selected_root = tmp_path / "SelectedRoot"
    selected_root.mkdir()

    page = SetupWizardPage(fake_app)
    page._on_launcher_data_dir_result(SimpleNamespace(path=str(selected_root)))

    assert page.launcher_data_dir.value == str(selected_root)


def test_setup_wizard_page_refreshes_storage_status_after_path_edit(
    fake_app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "Users" / "WDAGUtilityAccount"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    safe_root = user_home / "AppData" / "Local" / "TensaLauncher"
    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(package_dir / "TensaLauncher.exe"), platform="win32", frozen=True),
    )
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setenv("LOCALAPPDATA", str(user_home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(user_home / "AppData" / "Roaming"))
    fake_app.config = MemoryConfig()
    fake_app.paths = SimpleNamespace(app_state_dir=package_dir, minecraft_dir=package_dir / "minecraft")
    fake_app.util.app_state_dir = package_dir
    fake_app.util.minecraft_dir = package_dir / "minecraft"
    page = SetupWizardPage(fake_app, storage_issues=["Launcher data: unsafe package directory"])
    page.view()

    assert page.launcher_data_dir.value == str(safe_root)
    assert page.storage_issues == []
    assert page.storage_issues_box.visible is False

    page.launcher_data_dir.value = str(safe_root)
    page.launcher_data_dir.on_change(SimpleNamespace(control=page.launcher_data_dir))

    assert page.storage_issues == []
    assert page.storage_issues_box.visible is False


def test_setup_wizard_storage_check_prefers_storage_layout_paths(fake_app, tmp_path: Path) -> None:
    layout_state = tmp_path / "LocalAppData" / "TensaLauncher"
    layout_minecraft = layout_state / "minecraft"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    opened_pages = []

    fake_app.config = MemoryConfig({SETUP_WIZARD_COMPLETED_KEY: "yes", "lang": "uk_UA"})
    fake_app.paths = SimpleNamespace(app_state_dir=layout_state, minecraft_dir=layout_minecraft)
    fake_app.util.app_state_dir = package_dir
    fake_app.util.minecraft_dir = package_dir / "minecraft"

    fake_app.show_page = lambda page: opened_pages.append(page)

    assert maybe_show_setup_wizard(fake_app) is False
    assert opened_pages == []


def test_setup_wizard_page_saves_derived_paths_and_marks_completed(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    overrides = []
    restarts = []
    fake_app.util.app_dir = tmp_path / "state"
    fake_app.util.app_state_dir = tmp_path / "state"
    fake_app.util.set_minecraft_dir_override = lambda value: overrides.append(value)
    fake_app.restart = lambda: restarts.append(True)

    page = SetupWizardPage(fake_app)
    page.language_select.value = "en_US"
    page.launcher_data_dir.value = str(tmp_path / "state")

    page._save_and_continue()

    assert fake_app.config.get("lang") == "en_US"
    assert fake_app.config.get("minecraft_game_dir") is None
    assert fake_app.config.get("world_backups_dir") is None
    assert fake_app.config.get(SETUP_WIZARD_COMPLETED_KEY) == "yes"
    assert overrides == [None]
    assert restarts == [True]


def test_setup_wizard_page_create_paths_prepares_derived_directories(fake_app, tmp_path: Path) -> None:
    fake_app.config = MemoryConfig()
    root = tmp_path / "state"
    page = SetupWizardPage(fake_app)
    page.view()
    page.launcher_data_dir.value = str(root)

    page._create_paths()

    assert root.is_dir()
    assert (root / "minecraft").is_dir()
    assert (root / "minecraft" / "backups" / "worlds").is_dir()
    assert page.storage_issues == []
    assert page.storage_ready is True
    assert page.storage_issues_box.visible is True


def test_setup_wizard_page_create_paths_prepares_windows_local_and_roaming_defaults(
    fake_app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "Users" / "TensaUser"
    local_app_data = user_home / "AppData" / "Local"
    roaming_app_data = user_home / "AppData" / "Roaming"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    root = local_app_data / "TensaLauncher"
    minecraft_root = roaming_app_data / "TensaLauncher"

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(package_dir / "TensaLauncher.exe"), platform="win32", frozen=True),
    )
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("APPDATA", str(roaming_app_data))

    fake_app.config = MemoryConfig()
    fake_app.paths = SimpleNamespace(app_state_dir=root, minecraft_dir=minecraft_root)
    fake_app.util.app_state_dir = root
    fake_app.util.minecraft_dir = minecraft_root

    page = SetupWizardPage(fake_app)
    page.view()
    page.launcher_data_dir.value = str(root)

    page._create_paths()

    assert root.is_dir()
    assert minecraft_root.is_dir()
    assert (minecraft_root / "backups" / "worlds").is_dir()
    assert not (root / "minecraft").exists()
    assert page.storage_issues == []
    assert page.storage_ready is True


def test_setup_wizard_moves_launcher_state_to_selected_root(tmp_path: Path) -> None:
    service = SetupWizardService()
    old_root = tmp_path / "old-state"
    new_root = tmp_path / "selected-state"
    old_root.mkdir()
    (old_root / "profiles.json").write_text('{"default": {}}', encoding="utf-8")
    (old_root / "versions.json").write_text('{"vanilla": {}}', encoding="utf-8")

    config = Config(storage_dir=old_root)
    config.set("lang", "uk_UA")
    app_state_overrides: list[tuple[str | None, bool]] = []
    minecraft_overrides: list[str | None] = []
    fake_app = SimpleNamespace(
        config=config,
        paths=SimpleNamespace(app_state_dir=old_root),
        util=SimpleNamespace(
            app_state_dir=old_root,
            app_dir=old_root,
            set_app_state_dir_override=lambda value, persist=True: app_state_overrides.append((value, persist)),
            set_minecraft_dir_override=lambda value: minecraft_overrides.append(value),
        ),
    )

    service.apply_paths(
        fake_app,
        app_state_dir=str(new_root),
        minecraft_dir=str(new_root / "minecraft"),
        backups_dir=str(new_root / "minecraft" / "backups" / "worlds"),
    )

    moved_config = Config(storage_dir=new_root)
    assert moved_config.get("lang") == "uk_UA"
    assert moved_config.get(SETUP_WIZARD_COMPLETED_KEY) == "yes"
    assert moved_config.get("minecraft_game_dir") is None
    assert moved_config.get("world_backups_dir") is None
    assert (new_root / "profiles.json").read_text(encoding="utf-8") == '{"default": {}}'
    assert (new_root / "versions.json").read_text(encoding="utf-8") == '{"vanilla": {}}'
    assert app_state_overrides == [(str(new_root), True)]
    assert minecraft_overrides == [None]


def test_setup_wizard_repairs_package_directory_defaults(monkeypatch, tmp_path: Path) -> None:
    service = SetupWizardService()
    user_home = tmp_path / "Users" / "TensaUser"
    local_app_data = user_home / "AppData" / "Local"
    roaming_app_data = user_home / "AppData" / "Roaming"
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    package_dir.mkdir(parents=True)

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(package_dir / "TensaLauncher.exe"), platform="win32", frozen=True),
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("APPDATA", str(roaming_app_data))

    normalized_state = service.normalize_app_state_dir(
        current_dir=package_dir,
        raw_value=str(package_dir),
    )
    normalized_minecraft = service.normalize_minecraft_dir(
        app_dir=normalized_state,
        raw_value=str(package_dir / "minecraft"),
    )

    expected_root = _windows_default_app_state_dir(local_app_data)
    assert normalized_state == expected_root
    assert normalized_minecraft.path == _windows_default_minecraft_dir(local_app_data)
    assert normalized_minecraft.stored_value is None


def test_setup_wizard_storage_issues_do_not_probe_package_dirs(tmp_path: Path, monkeypatch) -> None:
    service = SetupWizardService()
    package_dir = tmp_path / "Program Files" / "TensaLauncher"
    probed: list[Path] = []

    monkeypatch.setattr(
        paths_module,
        "sys",
        SimpleNamespace(executable=str(package_dir / "TensaLauncher.exe"), platform="win32", frozen=True),
    )
    monkeypatch.setattr(service, "ensure_writable", lambda directory: probed.append(directory))

    issues = service.storage_issues(
        app_state_dir=package_dir,
        minecraft_dir=package_dir / "minecraft",
    )

    assert len(issues) == 2
    assert probed == []
