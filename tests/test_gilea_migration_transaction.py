import hashlib
import json
import sys
import threading
from contextlib import nullcontext
from dataclasses import replace

import pytest

from launcher.application.gilea_migration import release, service, transaction
from launcher.application.gilea_migration.models import MigrationError, MigrationRoots
from launcher.application.instance_operations import InstanceOperationCoordinator
from launcher.platform import gilea_migration as windows


class Interruption(BaseException):
    pass


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    roots = MigrationRoots(tmp_path / "old", tmp_path / "mc", tmp_path / "new",
                           tmp_path / "program", tmp_path / "old/gilea-migration")
    roots.source_state.mkdir()
    (roots.minecraft / "games/a/saves/world").mkdir(parents=True)
    (roots.minecraft / "games/a/saves/world/level.dat").write_bytes(b"world-original")
    for name, data in {"config": {"lang": "uk_UA"}, "profiles": {"Player": {"id": "uuid", "access_token": "offline"}},
                       "versions": {"a": {"path": "games/a", "options": {"profileKey": "Player"}}}}.items():
        (roots.source_state / f"{name}.json").write_text(json.dumps(data))
    binary = tmp_path / "download.exe"
    binary.write_bytes(b"MZ-fixture")
    monkeypatch.setattr(release, "PINNED_SHA256", hashlib.sha256(binary.read_bytes()).hexdigest())
    monkeypatch.setattr(release, "PINNED_SIZE", binary.stat().st_size)
    monkeypatch.setattr(windows, "check_prerequisites", lambda roots: None)
    monkeypatch.setattr(windows, "protect_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    if sys.platform != "win32":
        monkeypatch.setattr(windows, "read_guard", lambda paths: nullcontext())
    plan = service.prepare_plan(roots, effective_defaults={"default_max_ram_gb": 4})
    return roots, plan, binary


def commit(plan, binary, *, progress=lambda event: None, allow_external_copy=False, cancel=None):
    return transaction.commit_migration(plan, binary, allow_external_copy=allow_external_copy,
                                        cancel=cancel or threading.Event(), progress=progress)


def test_commit_preserves_sources_and_shared_content(fixture):
    roots, plan, binary = fixture
    originals = {p: p.read_bytes() for p in roots.source_state.glob("*.json")}
    result = commit(plan, binary)
    assert result.executable.read_bytes() == b"MZ-fixture"
    assert {p: p.read_bytes() for p in originals} == originals
    assert (roots.minecraft / "games/a/saves/world/level.dat").read_bytes() == b"world-original"
    assert json.loads((roots.minecraft / "games/a/version.json").read_text())["options"]["profileKey"] == "Player"
    config = json.loads((roots.target_state / "config.json").read_text())
    assert config["setup_wizard_completed"] == "yes"
    assert config["setup_wizard_version"] == 1
    assert not (roots.target_state / "versions.json").exists()
    assert result.build_aliases["a"] == "a"


def test_binary_pin_survives_replacement_between_preflight_and_stage(fixture):
    roots, plan, binary = fixture

    def replace_binary(event):
        if event.phase == "backup":
            binary.write_bytes(b"MZ-replaced-after-verification")

    with pytest.raises((PermissionError, MigrationError)):
        commit(plan, binary, progress=replace_binary)
    assert not (roots.install / release.ASSET_NAME).exists()


def test_external_copy_requires_consent_and_verification(fixture):
    roots, _, binary = fixture
    external = roots.source_state.parent / "external"
    external.mkdir()
    (external / "level.dat").write_bytes(b"external-world")
    (roots.source_state / "versions.json").write_text(json.dumps({"external": {"path": str(external)}}))
    plan = service.prepare_plan(roots, effective_defaults={})
    with pytest.raises(MigrationError, match="copy_consent"):
        commit(plan, binary)
    assert not roots.target_state.exists()
    commit(plan, binary, allow_external_copy=True)
    assert (roots.minecraft / "games/external/level.dat").read_bytes() == b"external-world"
    assert (external / "level.dat").read_bytes() == b"external-world"


@pytest.mark.parametrize("change", ["source", "destination", "binary", "lease"])
def test_source_or_destination_change_aborts_commit(fixture, change):
    roots, plan, binary = fixture
    lease = None
    if change == "source":
        (roots.source_state / "config.json").write_text('{"changed":true}')
    if change == "destination":
        roots.target_state.mkdir()
        (roots.target_state / "profiles.json").write_text("new user's profiles")
    if change == "binary":
        binary.write_bytes(b"not-the-reviewed-binary")
    if change == "lease":
        lease = InstanceOperationCoordinator().try_acquire(roots.minecraft / "games/a", "install")
    try:
        with pytest.raises(MigrationError):
            commit(plan, binary)
        assert not (roots.install / release.ASSET_NAME).exists()
    finally:
        if lease:
            lease.release()


@pytest.mark.parametrize("phase", ["backup", "stage", "activate", "commit"])
@pytest.mark.parametrize("error", [OSError, Interruption])
def test_failure_at_each_phase_is_recoverable(fixture, phase, error):
    roots, plan, binary = fixture

    def fail(event):
        if event.phase == phase:
            raise error("fixture interruption")

    with pytest.raises((OSError, Interruption)):
        commit(plan, binary, progress=fail)
    transaction.recover_migration(roots)
    assert not (roots.minecraft / "games/a/version.json").exists()
    assert (roots.minecraft / "games/a/saves/world/level.dat").read_bytes() == b"world-original"
    assert not (roots.target_state / "config.json").exists()
    result = commit(plan, binary)
    assert result.executable.exists()


def test_recovery_preserves_later_user_edits(fixture):
    roots, plan, binary = fixture

    def stop(event):
        if event.phase == "activate" and event.completed == 1:
            raise Interruption()

    with pytest.raises(Interruption):
        commit(plan, binary, progress=stop)
    (roots.target_state / "profiles.json").write_text("changed-after-interruption")
    (roots.minecraft / "games/a/new-world.dat").write_bytes(b"new")
    with pytest.raises(MigrationError, match="repair_required"):
        transaction.recover_migration(roots)
    assert (roots.target_state / "profiles.json").read_text() == "changed-after-interruption"
    assert (roots.minecraft / "games/a/new-world.dat").read_bytes() == b"new"


def test_tampered_journal_and_cross_volume_recovery(fixture):
    roots, plan, binary = fixture

    def stop(event):
        if event.phase == "activate":
            raise Interruption()

    with pytest.raises(Interruption):
        commit(plan, binary, progress=stop)
    journal = roots.recovery / "journal.json"
    original = journal.read_text()
    journal.write_text(original.replace('"activating"', '"committed"'))
    with pytest.raises(MigrationError, match="journal_invalid"):
        transaction.recover_migration(roots)
    journal.write_text(original)
    wrong = replace(roots, minecraft=roots.minecraft.parent / "other-volume")
    with pytest.raises(MigrationError, match="journal_invalid"):
        transaction.recover_migration(wrong)
    assert transaction.recover_migration(roots)


def test_retry_is_idempotent_and_backup_is_private(fixture, monkeypatch):
    roots, plan, binary = fixture
    protected = []

    def protect(path):
        protected.append(path)
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(windows, "protect_directory", protect)
    first = commit(plan, binary)
    before = (roots.target_state / "config.json").stat().st_mtime_ns
    second = commit(plan, binary)
    assert first == second
    assert (roots.target_state / "config.json").stat().st_mtime_ns == before
    assert roots.recovery in protected and roots.target_state in protected
    assert transaction.recover_migration(roots) is False


def test_backup_protection_failure_copies_no_credentials(fixture, monkeypatch):
    roots, plan, binary = fixture

    def denied(path):
        raise MigrationError("backup_protection")

    monkeypatch.setattr(windows, "protect_directory", denied)
    with pytest.raises(MigrationError, match="backup_protection"):
        commit(plan, binary)
    assert not roots.target_state.exists()
    assert not roots.recovery.exists()


def test_cancel_leaves_old_launcher_data_usable(fixture):
    roots, plan, binary = fixture
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MigrationError, match="cancelled"):
        commit(plan, binary, cancel=cancel)
    assert json.loads((roots.source_state / "profiles.json").read_text())["Player"]["id"] == "uuid"
    assert not roots.target_state.exists()


def test_recovery_does_not_delete_unactivated_matching_file(fixture):
    roots, plan, binary = fixture

    def insert(event):
        if event.phase == "stage" and event.completed == 1:
            roots.target_state.mkdir()
            (roots.target_state / "profiles.json").write_bytes(transaction._encoded(plan.adapted.profiles))
            raise Interruption()

    with pytest.raises(Interruption):
        commit(plan, binary, progress=insert)
    with pytest.raises(MigrationError, match="repair_required"):
        transaction.recover_migration(roots)
    assert (roots.target_state / "profiles.json").exists()


def test_recovery_keeps_replaced_file_even_with_identical_bytes(fixture):
    roots, plan, binary = fixture

    def stop(event):
        if event.phase == "activate" and event.completed == 1:
            raise Interruption()

    with pytest.raises(Interruption):
        commit(plan, binary, progress=stop)
    path = roots.target_state / "profiles.json"
    other = path.with_name("replacement")
    other.write_bytes(path.read_bytes())
    other.replace(path)
    with pytest.raises(MigrationError, match="repair_required"):
        transaction.recover_migration(roots)
    assert path.exists()
