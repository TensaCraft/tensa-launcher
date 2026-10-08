from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

from launcher.core.game import Game
from launcher.platform import gilea_migration as windows

from .adapters import adapt_snapshot
from .files import fingerprint, read_object, safe_path, tree_files
from .models import JsonObject, MigrationError, MigrationPlan, MigrationResult, MigrationRoots, SourceSnapshot
from .release import PINNED_SIZE

STATE_FILES = ("config.json", "profiles.json", "versions.json", "profile-token.key")


def validate_roots(roots: MigrationRoots) -> None:
    for path in (roots.source_state, roots.minecraft, roots.target_state, roots.install, roots.recovery):
        safe_path(path)
    if roots.recovery != roots.source_state / "gilea-migration":
        raise MigrationError("unsafe_path")
    for target in (roots.target_state, roots.install):
        for source in (roots.source_state, roots.minecraft):
            if target.is_relative_to(source) or source.is_relative_to(target):
                raise MigrationError("unsafe_path")
    if roots.install.is_relative_to(roots.target_state) or roots.target_state.is_relative_to(roots.install):
        raise MigrationError("unsafe_path")


def check_destinations(roots: MigrationRoots) -> None:
    for path in (roots.target_state, roots.install):
        safe_path(path)
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise MigrationError("destination_conflict")


def ensure_space(required: dict[Path, int]) -> None:
    volumes: dict[str, tuple[Path, int]] = {}
    for path, count in required.items():
        parent = path
        while not parent.exists():
            parent = parent.parent
        key = str(parent.stat().st_dev)
        previous = volumes.get(key, (parent, 0))[1]
        volumes[key] = (parent, previous + count)
    for path, count in volumes.values():
        if shutil.disk_usage(path)[2] < count + 16 * 1024 * 1024:
            raise MigrationError("disk_space")


def prepare_plan(roots: MigrationRoots, *, effective_defaults: JsonObject,
                 legacy_key: bytes | None = None) -> MigrationPlan:
    validate_roots(roots)
    windows.check_prerequisites(roots)
    if os.environ.get("LAUNCHER_APP_BASE"):
        raise MigrationError("destination_conflict")
    check_destinations(roots)
    fingerprints = {roots.source_state / name: fingerprint(roots.source_state / name) for name in STATE_FILES}
    config = read_object(roots.source_state / "config.json")
    profiles = read_object(roots.source_state / "profiles.json", missing=True)
    versions = read_object(roots.source_state / "versions.json", missing=True)
    key_path = roots.source_state / "profile-token.key"
    key = key_path.read_bytes() if key_path.exists() else None
    snapshot = SourceSnapshot(config, profiles, versions, key, fingerprints)
    adapted = adapt_snapshot(snapshot, roots, effective_defaults=effective_defaults, legacy_key=legacy_key)
    required = {roots.recovery: PINNED_SIZE * 2 + sum(p.stat().st_size for p in fingerprints if p.exists()),
                roots.target_state: 1024 * 1024, roots.install: PINNED_SIZE * 2, roots.minecraft: 1024 * 1024}
    for build in adapted.builds:
        safe_path(build.source)
        safe_path(build.target)
        if build.target.name.startswith('.'):
            raise MigrationError("ambiguous_build_path")
        if build.copy_required:
            for root in (roots.minecraft, roots.source_state, roots.target_state, roots.install):
                if root.is_relative_to(build.source):
                    raise MigrationError("unsafe_path")
            if any(build.source.is_relative_to(root) for root in (roots.recovery, roots.target_state, roots.install)):
                raise MigrationError("unsafe_path")
        if not build.source.is_dir():
            raise MigrationError("missing_build", build.source.name)
        if Game.is_game_dir_active(build.source):
            raise MigrationError("running_process")
        if (build.target / "version.json").exists() or (build.copy_required and build.target.exists()):
            raise MigrationError("destination_conflict", build.target.name)
        files = tree_files(build.source)
        if build.copy_required:
            windows.check_prerequisites(replace(roots, minecraft=build.source))
            if any(path.name == "version.json" and path.parent == build.source for path in files):
                raise MigrationError("destination_conflict")
            copy_bytes = sum(path.stat().st_size for path in files)
            required[roots.minecraft] += copy_bytes
            required[roots.recovery] += copy_bytes
            for path in files:
                fingerprints[path] = fingerprint(path)
    for path, expected in fingerprints.items():
        if fingerprint(path) != expected:
            raise MigrationError("source_changed")
    ensure_space(required)
    return MigrationPlan(roots, snapshot, adapted, required)


def check_plan_processes(plan: MigrationPlan) -> None:
    windows.check_prerequisites(plan.roots)
    for build in plan.adapted.builds:
        if build.copy_required:
            windows.check_prerequisites(replace(plan.roots, minecraft=build.source))


def load_committed_migration(roots: MigrationRoots) -> MigrationResult | None:
    from .transaction import load_committed

    return load_committed(roots)


def startup_forward(*, version_id: str | None = None, stay_on_tensa: bool = False) -> bool:
    from launcher.platform.paths import LauncherPaths, is_frozen

    from .adapters import absolute_path
    from .transaction import recover_migration

    if stay_on_tensa or not is_frozen() or not windows.supported_platform():
        return False
    layout = LauncherPaths.detect()
    if not (layout.app_state_dir / "gilea-migration/journal.json").exists():
        return False
    try:
        config = read_object(layout.app_state_dir / "config.json")
        minecraft = absolute_path(str(config["minecraft_game_dir"]), layout.app_state_dir) if config.get(
            "minecraft_game_dir") else layout.minecraft_dir
        roots = windows.default_roots(layout.app_state_dir, minecraft)
        recover_migration(roots)
        result = load_committed_migration(roots)
        if result is None:
            return False
        windows.launch_gilea(result, version_id=version_id)
        return True
    except (MigrationError, OSError):
        # The original application is the recovery path; do not strand its startup.
        return False
