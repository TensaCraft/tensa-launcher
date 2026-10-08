from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from launcher.application.instance_operations import InstanceOperationBusy, InstanceOperationCoordinator
from launcher.application.platform_lock import OSFileLock, OSFileLockBusy
from launcher.core.game import Game
from launcher.platform import gilea_migration as windows
from launcher.storage.atomic import atomic_write_json, path_lock

from .files import fingerprint, read_object, safe_path, tree_files
from .models import (
    JsonObject,
    MigrationError,
    MigrationIssue,
    MigrationPlan,
    MigrationProgress,
    MigrationResult,
    MigrationRoots,
)
from .release import ASSET_NAME, verify_binary
from .service import STATE_FILES, check_destinations, check_plan_processes, ensure_space, validate_roots


def _roots(roots: MigrationRoots) -> dict[str, str]:
    return {name: str(value) for name, value in asdict(roots).items()}


def _encoded(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _identity(path: Path) -> list[int]:
    info = safe_path(path).stat()
    return [info.st_dev, info.st_ino]


def _save(roots: MigrationRoots, data: JsonObject) -> None:
    key = safe_path(roots.recovery / "journal.key").read_bytes()
    atomic_write_json(safe_path(roots.recovery / "journal.json"),
                      {"payload": data, "mac": hmac.new(key, _encoded(data), "sha256").hexdigest()})


def _read(roots: MigrationRoots) -> JsonObject | None:
    path = safe_path(roots.recovery / "journal.json")
    if not path.exists():
        return None
    try:
        envelope = read_object(path)
        data = envelope["payload"]
        key = safe_path(roots.recovery / "journal.key").read_bytes()
        if (len(key) != 32 or not isinstance(data, dict)
                or not hmac.compare_digest(envelope["mac"], hmac.new(key, _encoded(data), "sha256").hexdigest())
                or data.get("schema") != 1 or data.get("roots") != _roots(roots)
                or not re.fullmatch(r"[0-9a-f]{32}", data.get("id", ""))):
            raise ValueError("invalid journal")
        for entry in data["entries"]:
            _output(roots, entry)
        return data
    except (KeyError, TypeError, ValueError, OSError):
        raise MigrationError("journal_invalid") from None


def _output(roots: MigrationRoots, entry: JsonObject) -> Path:
    category = entry.get("root")
    relative = entry.get("relative")
    if not isinstance(relative, str):
        raise MigrationError("journal_invalid")
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or "." == relative:
        raise MigrationError("journal_invalid")
    if category == "state" and relative in {"config.json", "profiles.json", "profile-token.key"}:
        path = roots.target_state / part
    elif category == "install" and relative == ASSET_NAME:
        path = roots.install / part
    elif category == "game" and len(part.parts) >= 3 and part.parts[0] == "games":
        path = roots.minecraft / part
    else:
        raise MigrationError("journal_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
        raise MigrationError("journal_invalid")
    return safe_path(path)


def _temporary(path: Path, token: str) -> Path:
    return safe_path(path.with_name(f".{path.name}.gilea-{token}"))


def _result(roots: MigrationRoots, data: JsonObject) -> MigrationResult:
    exe = safe_path(roots.install / ASSET_NAME)
    if not exe.is_file():
        raise MigrationError("launch_failed")
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        raise MigrationError("journal_invalid")
    for folder in aliases.values():
        if Path(folder).name != folder or not folder:
            raise MigrationError("journal_invalid")
        safe_path(roots.minecraft / "games" / folder)
    return MigrationResult(exe, aliases, tuple(MigrationIssue(**item) for item in data.get("warnings", [])))


def load_committed(roots: MigrationRoots) -> MigrationResult | None:
    validate_roots(roots)
    data = _read(roots)
    return _result(roots, data) if data and data["phase"] == "committed" else None


def _recover(roots: MigrationRoots) -> bool:
    data = _read(roots)
    if not data or data["phase"] in {"committed", "rolled_back"}:
        return False
    changed = False
    conflicts = False
    for entry in reversed(data["entries"]):
        output = _output(roots, entry)
        for path in (output, _temporary(output, data["id"])):
            digest = fingerprint(path)
            if digest is None:
                continue
            if digest != entry["sha256"] or entry.get("identity") != _identity(path):
                conflicts = True
                continue
            path.unlink()
            changed = True
    # Only empty directories are removed. Later worlds or user files survive.
    for raw in sorted(data.get("directories", []), key=len, reverse=True):
        directory = safe_path(Path(raw))
        allowed = (directory.is_relative_to(roots.target_state) or directory.is_relative_to(roots.install)
                   or directory.is_relative_to(roots.minecraft / "games"))
        if not allowed or directory in {roots.minecraft, roots.minecraft / "games"}:
            raise MigrationError("journal_invalid")
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            if directory.exists() and any(directory.iterdir()):
                conflicts = True
    data["phase"] = "repair_required" if conflicts else "rolled_back"
    _save(roots, data)
    if conflicts:
        raise MigrationError("repair_required")
    return changed or bool(data["entries"])


def recover_migration(roots: MigrationRoots) -> bool:
    validate_roots(roots)
    if not safe_path(roots.recovery / "journal.json").exists():
        return False
    try:
        with OSFileLock.try_acquire(roots.recovery, "shared", "migration"):
            return _recover(roots)
    except OSFileLockBusy:
        raise MigrationError("running_process") from None


def _check_sources(plan: MigrationPlan) -> None:
    for path, expected in plan.snapshot.fingerprints.items():
        if fingerprint(path) != expected:
            raise MigrationError("source_changed")
    for build in plan.adapted.builds:
        if Game.is_game_dir_active(build.source):
            raise MigrationError("running_process")
        if build.copy_required:
            actual = set(tree_files(build.source))
            planned = {p for p in plan.snapshot.fingerprints if p.is_relative_to(build.source)}
            if actual != planned:
                raise MigrationError("source_changed")


def _mkdir(roots: MigrationRoots, data: JsonObject, directory: Path) -> None:
    if directory.exists():
        safe_path(directory)
        return
    missing = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for path in reversed(missing):
        safe_path(path)
        # Parent container roots are not transaction outputs.
        if (path.is_relative_to(roots.target_state) or path.is_relative_to(roots.install)
                or path.is_relative_to(roots.minecraft / "games")) and path != roots.minecraft / "games":
            data["directories"].append(str(path))
            _save(roots, data)
        path.mkdir(exist_ok=True)


def _stage(plan: MigrationPlan, binary: Path, data: JsonObject,
           progress: Callable[[MigrationProgress], None], cancel: threading.Event) -> None:
    roots = plan.roots
    run = roots.recovery / data["id"]
    backup = run / "backup"
    prepared = run / "prepared"
    backup.mkdir(parents=True)
    prepared.mkdir()
    for name in STATE_FILES:
        source = roots.source_state / name
        if source.exists():
            shutil.copyfile(source, backup / name)
            if fingerprint(backup / name) != plan.snapshot.fingerprints[source]:
                raise MigrationError("source_changed")
    progress(MigrationProgress("backup", 1, 1))

    def add(category: str, relative: str, value: bytes | Path):
        if cancel.is_set():
            raise MigrationError("cancelled")
        staged = prepared / str(len(data["entries"]))
        if isinstance(value, Path):
            shutil.copyfile(safe_path(value), staged)
            if fingerprint(staged) != fingerprint(value):
                raise MigrationError("source_changed")
        else:
            staged.write_bytes(value)
        if category == "install":
            verify_binary(staged)
        data["entries"].append({"root": category, "relative": relative, "sha256": fingerprint(staged)})
        _save(roots, data)
        progress(MigrationProgress("stage", len(data["entries"]), 0))

    add("state", "profiles.json", _encoded(plan.adapted.profiles))
    if plan.adapted.key:
        add("state", "profile-token.key", plan.adapted.key)
    for build in plan.adapted.builds:
        if build.copy_required:
            for path in tree_files(build.source):
                relative = (build.target / path.relative_to(build.source)).relative_to(roots.minecraft).as_posix()
                add("game", relative, path)
        add("game", (build.target / "version.json").relative_to(roots.minecraft).as_posix(), _encoded(build.metadata))
    add("install", ASSET_NAME, binary)
    config = dict(plan.adapted.config, setup_wizard_completed="yes", setup_wizard_version=1)
    add("state", "config.json", _encoded(config))
    data["phase"] = "staged"
    _save(roots, data)


def commit_migration(plan: MigrationPlan, binary: Path, *, allow_external_copy: bool,
                     cancel: threading.Event, progress: Callable[[MigrationProgress], None]) -> MigrationResult:
    roots = plan.roots
    validate_roots(roots)
    if cancel.is_set():
        raise MigrationError("cancelled")
    if any(b.copy_required for b in plan.adapted.builds) and not allow_external_copy:
        raise MigrationError("copy_consent")
    coordinator = InstanceOperationCoordinator()
    try:
        with ExitStack() as locks:
            locks.enter_context(OSFileLock.try_acquire(roots.recovery, "shared", "migration"))
            for path in sorted({roots.minecraft, *(b.source for b in plan.adapted.builds)}, key=str):
                locks.enter_context(coordinator.operation(path, "migration"))
            for name in STATE_FILES:
                locks.enter_context(path_lock(roots.source_state / name))
            existing = _read(roots)
            if existing and existing["phase"] == "committed":
                return _result(roots, existing)
            if existing and existing["phase"] != "rolled_back":
                _recover(roots)
            _check_sources(plan)
            locks.enter_context(windows.read_guard([p for p in plan.snapshot.fingerprints if p.is_file()] + [binary]))
            check_plan_processes(plan)
            check_destinations(roots)
            verify_binary(binary)
            ensure_space(dict(plan.required_bytes))
            windows.protect_directory(roots.recovery)
            key_path = safe_path(roots.recovery / "journal.key")
            if not key_path.exists():
                with key_path.open("xb") as stream:
                    stream.write(os.urandom(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            data: JsonObject = {
                "schema": 1, "id": uuid.uuid4().hex, "roots": _roots(roots), "phase": "preparing",
                "entries": [], "directories": [],
                "aliases": {alias: b.target.name for b in plan.adapted.builds for alias in b.aliases},
                "warnings": [asdict(w) for w in plan.adapted.warnings],
            }
            _save(roots, data)
            try:
                _stage(plan, binary, data, progress, cancel)
                _check_sources(plan)
                check_destinations(roots)
                check_plan_processes(plan)
                _mkdir(roots, data, roots.target_state)
                windows.protect_directory(roots.target_state)
                data["phase"] = "activating"
                _save(roots, data)
                for index, entry in enumerate(data["entries"]):
                    if cancel.is_set():
                        raise MigrationError("cancelled")
                    output = _output(roots, entry)
                    if output.exists():
                        raise MigrationError("destination_conflict")
                    _mkdir(roots, data, output.parent)
                    temporary = _temporary(output, data["id"])
                    staged = roots.recovery / data["id"] / "prepared" / str(index)
                    with temporary.open("xb") as target, staged.open("rb") as source:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                        target.flush()
                        os.fsync(target.fileno())
                    if fingerprint(temporary) != entry["sha256"]:
                        raise MigrationError("checksum")
                    entry["identity"] = _identity(temporary)
                    _save(roots, data)
                    # Never replace an existing file. Stage on the destination volume.
                    temporary.rename(output)
                    progress(MigrationProgress("activate", index + 1, len(data["entries"])))
                _check_sources(plan)
                for entry in data["entries"]:
                    if fingerprint(_output(roots, entry)) != entry["sha256"]:
                        raise MigrationError("destination_conflict")
                verify_binary(roots.install / ASSET_NAME)
                progress(MigrationProgress("commit", 1, 1))
                if cancel.is_set():
                    raise MigrationError("cancelled")
                data["phase"] = "committed"
                _save(roots, data)
                return _result(roots, data)
            except Exception:
                _recover(roots)
                raise
    except (OSFileLockBusy, InstanceOperationBusy):
        raise MigrationError("running_process") from None
