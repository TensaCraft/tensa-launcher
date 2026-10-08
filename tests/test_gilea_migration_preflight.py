import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from launcher.application.gilea_migration import files, service
from launcher.application.gilea_migration.models import MigrationError, MigrationRoots
from launcher.platform import gilea_migration as windows


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_windows_webview_probe_is_unavailable_elsewhere(monkeypatch, platform):
    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform=platform))
    assert windows.has_webview() is False


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_windows_read_guard_rejects_other_platforms(monkeypatch, platform):
    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform=platform))
    with pytest.raises(MigrationError, match="unsupported_platform"), windows.read_guard([]):
        pytest.fail("Windows source guard must not activate on other platforms")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    roots = MigrationRoots(tmp_path / "old", tmp_path / "mc", tmp_path / "new",
                           tmp_path / "program", tmp_path / "old/gilea-migration")
    roots.source_state.mkdir()
    (roots.minecraft / "games/a").mkdir(parents=True)
    (roots.source_state / "config.json").write_text("{}")
    (roots.source_state / "profiles.json").write_text("{}")
    (roots.source_state / "versions.json").write_text(json.dumps({"a": {"path": "games/a"}}))
    monkeypatch.setattr(windows, "check_prerequisites", lambda roots: None)
    return roots


def test_preflight_does_not_write_source(roots):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in roots.source_state.iterdir()}
    plan = service.prepare_plan(roots, effective_defaults={})
    assert plan.roots.minecraft == roots.minecraft
    assert plan.adapted.builds[0].target == roots.minecraft / "games/a"
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in roots.source_state.iterdir()}
    assert not roots.target_state.exists()
    assert not (roots.source_state / "profile-token.key").exists()


@pytest.mark.parametrize("where", ["state", "pointer", "install", "build"])
def test_preflight_uses_active_paths_and_blocks_conflicts(roots, where):
    target = {"state": roots.target_state / "profiles.json", "pointer": roots.target_state / "storage.json",
              "install": roots.install / "GileaLauncher-tensa.exe",
              "build": roots.minecraft / "games/a/version.json"}[where]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    with pytest.raises(MigrationError, match="destination_conflict"):
        service.prepare_plan(roots, effective_defaults={})
    assert target.read_text() == "{}"


@pytest.mark.parametrize("payload", ["", "null", "[]", "{bad", '{"a":1,"a":2}'])
def test_invalid_source_json_is_not_silently_empty(roots, payload):
    (roots.source_state / "profiles.json").write_text(payload)
    with pytest.raises(MigrationError, match="invalid_json"):
        service.prepare_plan(roots, effective_defaults={})


def test_missing_instance_or_overlapping_roots_block(roots):
    (roots.source_state / "versions.json").write_text('{"missing":{"path":"games/missing"}}')
    with pytest.raises(MigrationError, match="missing_build"):
        service.prepare_plan(roots, effective_defaults={})
    with pytest.raises(MigrationError, match="unsafe_path"):
        service.prepare_plan(replace(roots, target_state=roots.source_state), effective_defaults={})


def test_windows_prerequisites_and_per_volume_space(roots, monkeypatch):
    monkeypatch.setattr(service.shutil, "disk_usage", lambda path: (1, 1, 0))
    with pytest.raises(MigrationError, match="disk_space"):
        service.prepare_plan(roots, effective_defaults={})


def test_webview_or_running_gilea_blocks(tmp_path, monkeypatch):
    roots = MigrationRoots(tmp_path / "old", tmp_path / "mc", tmp_path / "new",
                           tmp_path / "program", tmp_path / "old/gilea-migration")
    monkeypatch.setattr(windows, "supported_platform", lambda: True)
    monkeypatch.setattr(windows, "has_webview", lambda: False)
    with pytest.raises(MigrationError, match="webview_missing"):
        windows.check_prerequisites(roots)
    monkeypatch.setattr(windows, "has_webview", lambda: True)
    monkeypatch.setattr(windows, "conflicting_processes", lambda roots: True)
    with pytest.raises(MigrationError, match="running_process"):
        windows.check_prerequisites(roots)
    monkeypatch.setattr(windows, "supported_platform", lambda: False)
    with pytest.raises(MigrationError, match="unsupported_platform"):
        windows.check_prerequisites(roots)


def test_reparse_source_is_rejected(roots):
    target = roots.minecraft / "games/a/link"
    try:
        target.symlink_to(roots.source_state, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(MigrationError, match="unsafe_path"):
        service.prepare_plan(roots, effective_defaults={})


@pytest.mark.parametrize("source", ["minecraft", "parent", "state", "hidden"])
def test_ambiguous_external_or_hidden_build_is_rejected(roots, source):
    path = {"minecraft": roots.minecraft, "parent": roots.minecraft.parent,
            "state": roots.source_state, "hidden": roots.minecraft / "games/.hidden"}[source]
    path.mkdir(parents=True, exist_ok=True)
    (roots.source_state / "versions.json").write_text(json.dumps({"a": {"path": str(path)}}))
    with pytest.raises(MigrationError, match="unsafe_path|ambiguous_build_path"):
        service.prepare_plan(roots, effective_defaults={})


def test_inaccessible_tree_is_not_silently_partial(roots, monkeypatch):
    def walk(path, *, followlinks, onerror=None):
        if onerror:
            onerror(PermissionError("blocked directory"))
        return iter(())

    monkeypatch.setattr(files.os, "walk", walk)
    with pytest.raises(PermissionError):
        files.tree_files(roots.minecraft)


def test_external_game_checked_and_staging_charged_to_recovery_volume(roots, monkeypatch):
    external = roots.source_state.parent / 'external'
    external.mkdir()
    (external / 'world.bin').write_bytes(b'a' * 1234)
    (roots.source_state / 'versions.json').write_text(json.dumps({'a': {'path': str(external)}}))
    checked = []
    monkeypatch.setattr(windows, 'check_prerequisites', lambda candidate: checked.append(candidate.minecraft))
    plan = service.prepare_plan(roots, effective_defaults={})
    assert external in checked
    metadata_size = sum(p.stat().st_size for p in roots.source_state.iterdir())
    assert plan.required_bytes[roots.recovery] >= service.PINNED_SIZE * 2 + metadata_size + 1234
