from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher.application.modrinth_content_install as install_module
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.mod_identity import ModMatch, ModMatchKind
from launcher.application.modrinth_content_install import (
    MODRINTH_CONTENT_JOURNAL,
    ModrinthContentGameRunning,
    ModrinthContentInstaller,
)
from launcher.application.modrinth_mods import (
    ModInstallFile,
    ModrinthInstallCandidate,
)
from launcher.core.game import Game


class _Content:
    MODRINTH_METADATA_FILE = "modrinth-content.json"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.backup_calls = []

    def get_version_directory(self, _version):
        return self.root

    def create_backup(self, _target_dir, _installed_item):
        self.backup_calls.append((_target_dir, _installed_item))
        return True

    def write_modrinth_content_batch(
        self,
        _version,
        records,
        *,
        removed_paths,
        output_path,
    ):
        assert records
        assert removed_paths == []
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("metadata", encoding="utf-8")


class _ModrinthMods:
    def match_installed(self, *_args):
        raise AssertionError("install candidate should not need an installed match")


def _install_candidate(
    *,
    project_id: str = "example",
    filename: str = "example.jar",
    action: str = "install",
    installed_item: dict | None = None,
) -> ModrinthInstallCandidate:
    return ModrinthInstallCandidate(
        project={"project_id": project_id, "title": "Example"},
        version_data={"id": "version-one"},
        install_file=ModInstallFile(
            url=f"https://example.com/{filename}",
            filename=filename,
            size=3,
        ),
        action=action,
        installed_item=installed_item,
    )


def _download_files(_self, tasks, **_kwargs):
    for task in tasks:
        task.destination.parent.mkdir(parents=True, exist_ok=True)
        task.destination.write_bytes(b"new")
    return {"success": len(tasks), "failed": 0, "skipped": 0, "errors": []}


@pytest.mark.parametrize(
    ("content_key", "directory", "filename"),
    [
        ("resourcepacks", "resourcepacks", "faithful.zip"),
        ("shaders", "shaderpacks", "complementary.zip"),
        ("mods", "mods", "new-mod.jar"),
    ],
)
def test_modrinth_content_installs_safe_additions_while_game_is_running(
    monkeypatch,
    tmp_path: Path,
    content_key: str,
    directory: str,
    filename: str,
) -> None:
    target_dir = tmp_path / directory
    target_dir.mkdir()
    monkeypatch.setattr(Game, "is_game_dir_active", lambda _path: True)
    monkeypatch.setattr(install_module.AsyncDownloader, "download_files", _download_files)

    installed = ModrinthContentInstaller(_Content(tmp_path), _ModrinthMods()).install(
        SimpleNamespace(name="Running"),
        [_install_candidate(filename=filename)],
        content_key=content_key,
        target_dir=target_dir,
    )

    assert installed == {"example": target_dir / filename}
    assert (target_dir / filename).read_bytes() == b"new"


def test_modrinth_content_blocks_mod_replacement_while_game_is_running(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "mods"
    target_dir.mkdir()
    installed_path = target_dir / "old.jar"
    installed_path.write_bytes(b"old")
    installed_item = {"filename": installed_path.name, "path": str(installed_path)}
    monkeypatch.setattr(Game, "is_game_dir_active", lambda _path: True)

    with pytest.raises(ModrinthContentGameRunning):
        ModrinthContentInstaller(_Content(tmp_path), _ModrinthMods()).install(
            SimpleNamespace(name="Running"),
            [
                _install_candidate(
                    filename="new.jar",
                    action="replace",
                    installed_item=installed_item,
                )
            ],
            content_key="mods",
            target_dir=target_dir,
        )

    assert installed_path.read_bytes() == b"old"


def test_modrinth_content_installer_stages_and_activates_batch(
    monkeypatch,
    tmp_path: Path,
):
    target_dir = tmp_path / "mods"
    target_dir.mkdir()
    candidate = ModrinthInstallCandidate(
        project={"project_id": "fabric-api", "title": "Fabric API"},
        version_data={"id": "version-one"},
        install_file=ModInstallFile(
            url="https://example.com/fabric-api.jar",
            filename="fabric-api.jar",
            size=3,
        ),
        action="install",
    )

    def download_files(_self, tasks, **_kwargs):
        for task in tasks:
            task.destination.parent.mkdir(parents=True, exist_ok=True)
            task.destination.write_bytes(b"jar")
        return {"success": len(tasks), "failed": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(Game, "is_game_dir_active", lambda _path: False)
    monkeypatch.setattr(
        install_module.AsyncDownloader,
        "download_files",
        download_files,
    )

    installed = ModrinthContentInstaller(
        _Content(tmp_path),
        _ModrinthMods(),
    ).install(
        SimpleNamespace(name="Test"),
        [candidate],
        content_key="mods",
        target_dir=target_dir,
    )

    assert installed == {"fabric-api": target_dir / "fabric-api.jar"}
    assert (target_dir / "fabric-api.jar").read_bytes() == b"jar"
    assert (
        tmp_path / ".tensalauncher" / "modrinth-content.json"
    ).read_text(encoding="utf-8") == "metadata"
    assert (
        FileSyncJournal(tmp_path, filename=MODRINTH_CONTENT_JOURNAL)
        .read()["status"]
        == "complete"
    )


def test_modrinth_replacement_preflights_before_creating_backup(
    monkeypatch,
    tmp_path: Path,
):
    target_dir = tmp_path / "mods"
    target_dir.mkdir()
    installed_path = target_dir / "old.jar"
    installed_path.write_bytes(b"old")
    installed_item = {
        "filename": installed_path.name,
        "path": str(installed_path),
    }
    candidate = ModrinthInstallCandidate(
        project={"project_id": "example", "title": "Example"},
        version_data={"id": "new-version"},
        install_file=ModInstallFile(
            url="https://example.com/new.jar",
            filename="new.jar",
            size=3,
        ),
        action="replace",
        installed_item=installed_item,
        installed_match=ModMatch(
            installed_item,
            ModMatchKind.OWNED,
            "verified_modrinth_provenance",
        ),
    )
    content = _Content(tmp_path)
    monkeypatch.setattr(Game, "is_game_dir_active", lambda _path: False)
    monkeypatch.setattr(
        install_module,
        "ensure_storage_available",
        lambda _requests: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        ModrinthContentInstaller(content, _ModrinthMods()).install(
            SimpleNamespace(name="Test"),
            [candidate],
            content_key="mods",
            target_dir=target_dir,
        )

    assert content.backup_calls == []
    assert installed_path.read_bytes() == b"old"
