from __future__ import annotations

from pathlib import Path

import pytest

from launcher.application.curseforge_install import (
    CurseForgeInstallLimits,
    CurseForgeInstallService,
)
from launcher.application.file_sync_journal import FileSyncJournal


def _service() -> CurseForgeInstallService:
    def unexpected_downloader(**_kwargs):
        raise AssertionError("Override-only transactions must not download files")

    return CurseForgeInstallService(
        limits=CurseForgeInstallLimits(
            override_members=100,
            override_entry_size=1024 * 1024,
            override_total_size=4 * 1024 * 1024,
        ),
        downloader_factory=unexpected_downloader,
    )


def test_curseforge_service_plans_and_applies_local_overrides(
    tmp_path: Path,
):
    source = tmp_path / "pack"
    manifest_path = source / "manifest.json"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    game_path = tmp_path / "game"
    existing = game_path / "config" / "settings.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("user value", encoding="utf-8")
    service = _service()

    plan = service.plan_overrides(
        manifest_path,
        "manifest",
        {"overrides": "overrides"},
        game_path,
    )
    result = service.install_content(
        game_path=game_path,
        remote_files=[],
        overrides=plan,
        source_path=manifest_path,
        source_kind="manifest",
        operation_name="curseforge-test",
    )

    assert result == {
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }
    assert existing.read_text(encoding="utf-8") == "pack value"
    journal = FileSyncJournal(game_path).read()
    assert journal is not None
    assert journal["status"] == "complete"


def test_curseforge_service_rolls_back_when_commit_fails(tmp_path: Path):
    source = tmp_path / "pack"
    manifest_path = source / "manifest.json"
    override = source / "overrides" / "config" / "settings.txt"
    override.parent.mkdir(parents=True)
    override.write_text("pack value", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    game_path = tmp_path / "game"
    existing = game_path / "config" / "settings.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("user value", encoding="utf-8")
    service = _service()
    plan = service.plan_overrides(
        manifest_path,
        "manifest",
        {"overrides": "overrides"},
        game_path,
    )

    def fail_commit() -> None:
        raise OSError("profile is locked")

    with pytest.raises(OSError, match="profile is locked"):
        service.install_content(
            game_path=game_path,
            remote_files=[],
            overrides=plan,
            source_path=manifest_path,
                source_kind="manifest",
                operation_name="curseforge-test",
                commit_callback=fail_commit,
                commit_key="test:curseforge-profile",
            )

    assert existing.read_text(encoding="utf-8") == "user value"
    journal = FileSyncJournal(game_path).read()
    assert journal is not None
    assert journal["status"] == "rolled_back"
