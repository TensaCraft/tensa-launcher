from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
import requests

import launcher.core.loaders.tensacraft as tensacraft_module
from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.instance_operations import InstanceOperationCoordinator
from launcher.core.api.tensacraft import TensaCraftAPI
from launcher.core.async_downloader import DownloadTask
from launcher.pages.home import Home


def test_tensacraft_sync_snapshot_excludes_runtime_locks(fake_app, monkeypatch, tmp_path: Path):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    runtime = SimpleNamespace(lock=threading.Lock())
    version = SimpleNamespace(
        id="aeronautics",
        name="Aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.230",
        loader_version="21.1.230",
        client="TensaCraft",
        path=str(tmp_path / "instance"),
        options={"gpuMode": "dgpu"},
        image=None,
        force_update=True,
        _runtime=runtime,
    )

    def fail_after_snapshot(current):
        current.name = "Changed"
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(loader, "_find_version_payload", fail_after_snapshot)
    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        loader._sync_update_locked(version)

    assert version.name == "Aeronautics"
    assert version._runtime is runtime


def test_tensacraft_force_sync_reports_unavailable_api(fake_app, monkeypatch, tmp_path: Path):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version = SimpleNamespace(
        id="aeronautics",
        name="Aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.230",
        loader_version="21.1.230",
        client="TensaCraft",
        path=str(tmp_path / "instance"),
        options={},
        image=None,
        force_update=True,
    )
    monkeypatch.setattr(loader, "_find_version_payload", lambda _version: None)
    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None

    loader._sync_update_locked(version, force=False)
    with pytest.raises(RuntimeError, match="tensacraft_sync_api_unavailable"):
        loader._sync_update_locked(version, force=True)


def test_tensacraft_sync_accepts_launch_borrowed_lease(fake_app, monkeypatch, tmp_path: Path):
    fake_app.instance_operations = InstanceOperationCoordinator()
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_path = tmp_path / "instance"
    version = SimpleNamespace(path=str(version_path), version_id="demo", name="Demo")
    calls = []
    monkeypatch.setattr(
        loader,
        "_sync_update_locked",
        lambda current, force=False: calls.append((current, force)),
    )

    with fake_app.instance_operations.operation(version_path, "launch") as lease:
        loader.sync_update(version, force=True, lease=lease)
        assert fake_app.instance_operations.active_kind(version_path) == "launch"

    assert calls == [(version, True)]
    assert fake_app.instance_operations.active_kind(version_path) is None


def test_tensacraft_sync_rejects_concurrent_instance_operation(fake_app, tmp_path: Path):
    fake_app.instance_operations = InstanceOperationCoordinator()
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_path = tmp_path / "instance"
    version = SimpleNamespace(path=str(version_path), version_id="demo", name="Demo")

    with fake_app.instance_operations.operation(version_path, "launch"):
        with pytest.raises(RuntimeError, match="instance_operation_busy"):
            loader.sync_update(version)


def test_tensacraft_sync_update_shows_progress_for_noop_check(fake_app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "existing.jar").write_bytes(b"ok")

    version = SimpleNamespace(
        id="tensa",
        version="1.20.1",
        loader="fabric-loader-0.16-1.20.1",
        loader_version="0.16",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: None,
    )

    starts = []
    closes = []
    progress_updates = []
    operation = SimpleNamespace()

    loader.begin_feedback_operation = lambda **kwargs: starts.append(kwargs) or operation
    loader.finish_feedback_operation = (
        lambda op, message=None, show_success=True: closes.append((op, message, show_success))
    )
    fake_app.feedback.update_current_operation = (
        lambda *args, **kwargs: progress_updates.append((args, kwargs))
    )

    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.20.1",
            "loader": "fabric",
            "loader_version": "0.16",
            "force_update": True,
            "options": {},
        }
    }
    loader.api.get_version_files = lambda _key: [
        {
            "name": "existing.jar",
            "path": "/mods/",
            "download_url": "https://example.com/existing.jar",
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )
    loader.sync_update(version)

    assert starts == [
        {
            "status": "syncing_files_check",
            "progress": 0,
            "max_progress": 100,
            "title": "syncing_files_check",
            "kind": "sync",
            "visible": False,
            "auto_open": False,
        }
    ]
    assert closes == [(operation, None, False)]
    assert progress_updates == []


def test_tensacraft_sync_update_allows_running_game_dir_when_no_update_needed(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    version_root.mkdir(parents=True, exist_ok=True)
    saved = []
    sync_calls = []

    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.228",
        loader_version="21.1.228",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: saved.append(True),
    )

    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.21.1",
            "loader": "neoforge",
            "loader_version": "21.1.228",
            "force_update": True,
            "options": {},
        }
    }
    loader._prepare_file_sync = lambda *_args, **_kwargs: tensacraft_module.TensaCraftContentPlan(
        root=version_root,
        api_available=True,
    )
    loader.payload.loader_changed = lambda *_args, **_kwargs: False
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.payload.merge_sync_payload = lambda *_args, **_kwargs: None
    loader._sync_files = lambda *_args, **_kwargs: sync_calls.append(True)
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True), raising=False)

    loader.sync_update(version)

    assert saved == [True]
    assert sync_calls == []


def test_tensacraft_sync_update_blocks_running_game_dir_when_files_need_update(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    version_root.mkdir(parents=True, exist_ok=True)
    saved = []
    sync_calls = []

    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.228",
        loader_version="21.1.228",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: saved.append(True),
    )

    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.21.1",
            "loader": "neoforge",
            "loader_version": "21.1.228",
            "force_update": True,
            "options": {},
        }
    }
    loader._prepare_file_sync = lambda *_args, **_kwargs: tensacraft_module.TensaCraftContentPlan(
        root=version_root,
        api_available=True,
        force_changes=True,
    )
    loader.payload.loader_changed = lambda *_args, **_kwargs: False
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.payload.merge_sync_payload = lambda *_args, **_kwargs: None
    loader._sync_files = lambda *_args, **_kwargs: sync_calls.append(True)
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True), raising=False)

    with pytest.raises(RuntimeError, match=r"tensacraft_game_directory_running \(version=Aeronautics\)"):
        loader.sync_update(version)

    assert saved == []
    assert sync_calls == []


def test_tensacraft_sync_blocks_running_game_before_recovering_interrupted_files(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    current = version_root / "mods" / "managed.jar"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old")
    journal = FileSyncJournal(version_root)
    journal.begin_transaction(
        operation="sync",
        replacements=["mods/managed.jar"],
        stale=[],
    )
    staged = journal.stage_path("mods/managed.jar")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"new")
    journal.mark_prepared()
    payload = journal.read()
    transaction_root = version_root / ".tensalauncher-sync" / payload["transaction_id"]
    backup = transaction_root / "backup" / "mods" / "managed.jar"
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(current, backup)
    os.replace(staged, current)
    payload["status"] = "applying"
    payload["entries"][0].update({"had_original": True, "state": "applied"})
    journal.path.write_text(json.dumps(payload), encoding="utf-8")
    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="fabric-loader-0.16-1.21.1",
        loader_version="0.16",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        save=lambda: None,
    )
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.21.1",
            "loader": "fabric",
            "loader_version": "0.16",
            "options": {},
        }
    }
    monkeypatch.setattr(
        "launcher.core.game.Game.is_game_dir_active",
        lambda _game_path: True,
    )

    with pytest.raises(RuntimeError, match="tensacraft_game_directory_running"):
        loader.sync_update(version)

    assert current.read_bytes() == b"new"
    assert backup.read_bytes() == b"old"
    assert journal.read()["status"] == "applying"


def test_tensacraft_sync_update_installs_api_pinned_neoforge_build(fake_app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    version_root.mkdir(parents=True, exist_ok=True)
    saved = []
    install_calls = []

    version = SimpleNamespace(
        id="tensa",
        version="1.21.1",
        loader="neoforge-21.1.227",
        loader_version="21.1.227",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: saved.append(True),
    )

    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.21.1",
            "loader": "neoforge",
            "loader_version": "21.1.228",
            "force_update": True,
            "options": {},
        }
    }
    loader.api.get_version_files = lambda _key: []
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    loader.get_version_java_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"

    def fake_install_mod_loader(**kwargs):
        install_calls.append(kwargs)
        return "neoforge-21.1.228", "21.1.228"

    loader._install_mod_loader = fake_install_mod_loader

    loader.sync_update(version)

    assert install_calls == [
        {
            "mc_version": "1.21.1",
            "loader_name": "neoforge",
            "requested_loader_version": "21.1.228",
        }
    ]
    assert version.loader == "neoforge-21.1.228"
    assert version.loader_version == "21.1.228"
    assert saved == [True]


def test_tensacraft_sync_update_preserves_user_custom_options(fake_app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "existing.jar").write_bytes(b"ok")
    saved = []

    version = SimpleNamespace(
        id="tensa",
        version="1.20.1",
        loader="fabric-loader-0.16-1.20.1",
        loader_version="0.16",
        client="TensaCraft",
        path=str(version_root),
        options={
            "gpuMode": "igpu",
            "jvmArguments": ["-Xmx2G"],
            "executablePath": "D:/CustomJava/bin/javaw.exe",
        },
        image="custom-icon",
        force_update=True,
        save=lambda: saved.append(True),
    )

    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.20.1",
            "loader": "fabric",
            "loader_version": "0.16",
            "force_update": True,
            "image": "api-icon",
            "server_host": "play.tensa.example",
            "server_port": 25565,
            "gpu_preference": "discrete",
            "jvm_arguments": ["-Xmx8G"],
            "options": {
                "server": {"host": "api-server"},
                "jvmArguments": ["-Xmx10G"],
            },
        }
    }
    loader.api.get_version_files = lambda _key: [
        {
            "name": "existing.jar",
            "path": "/mods/",
            "download_url": "https://example.com/existing.jar",
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/LauncherJava/bin/javaw.exe"
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    loader.sync_update(version)

    assert saved == [True]
    assert "server" not in version.options
    assert version.options["gpuMode"] == "igpu"
    assert version.options["jvmArguments"] == ["-Xmx2G"]
    assert version.options["executablePath"] == "D:/CustomJava/bin/javaw.exe"
    assert version.image == "custom-icon"


def test_tensacraft_existing_java_path_requires_usable_launcher_runtime(fake_app, monkeypatch):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime-delta"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/LauncherJava/bin/java.exe"
    loader.runtime.runtime_is_usable = lambda *_args, **_kwargs: False

    assert loader._existing_java_path("1.21.1") is None


def test_tensacraft_sync_update_applies_api_forced_profile_fields(fake_app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "existing.jar").write_bytes(b"ok")
    saved = []

    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.229",
        loader_version="21.1.229",
        client="TensaCraft",
        path=str(version_root),
        options={
            "server": {"host": "custom.example", "port": 24454},
            "gpuMode": "igpu",
            "jvmArguments": ["-Xmx2G"],
            "executablePath": "D:/CustomJava/bin/javaw.exe",
        },
        image="custom-icon",
        force_update=True,
        save=lambda: saved.append(version.options.copy()),
    )

    loader.api.get_versions = lambda _key: {
        "client": {
            "minecraft_version": "1.21.1",
            "loader_id": "neoforge",
            "loader_version": "21.1.229",
            "force_update": True,
            "server_host": "auro.tensa.co.ua",
            "server_port": 25565,
            "gpu_preference": "discrete",
            "jvm_arguments": ["-Xmx8G"],
            "image": "api-icon",
            "force_update_profile_fields": [
                "server_host",
                "server_port",
                "gpu_preference",
                "jvm_arguments",
                "image",
            ],
        }
    }
    loader.api.get_version_files = lambda _key: [
        {
            "name": "existing.jar",
            "path": "/mods/",
            "download_url": "https://example.com/existing.jar",
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/LauncherJava/bin/javaw.exe"
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    loader.sync_update(version)

    assert saved
    assert version.options["server"] == {"host": "auro.tensa.co.ua", "port": 25565}
    assert version.options["gpuMode"] == "dgpu"
    assert version.options["jvmArguments"] == ["-Xmx8G"]
    assert version.options["executablePath"] == "D:/CustomJava/bin/javaw.exe"
    assert version.image == "api-icon"
    assert "force_update_profile_fields" not in version.options


def test_tensacraft_sync_update_opens_progress_for_file_changes(fake_app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("launcher.core.util.minecraft_dir", str(fake_app.util.minecraft_dir))

    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "existing.jar").write_bytes(b"old")
    (mods_dir / "stale.jar").write_bytes(b"old")

    saved = []
    starts = []
    closes = []
    operation = SimpleNamespace()

    version = SimpleNamespace(
        id="tensa",
        version="1.20.1",
        loader="fabric-loader-0.16-1.20.1",
        loader_version="0.16",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: saved.append(True),
    )

    loader.begin_feedback_operation = lambda **kwargs: starts.append(kwargs) or operation
    loader.finish_feedback_operation = (
        lambda op, message=None, show_success=True: closes.append((op, message, show_success))
    )
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.20.1",
            "loader": "fabric",
            "loader_version": "0.16",
            "force_update": True,
            "options": {},
        }
    }
    loader.api.get_version_files = lambda _key: [
        {
            "name": "existing.jar",
            "path": "/mods/",
            "download_url": "https://example.com/existing.jar",
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: {
        "directories": [{"path": "mods/", "sync_scope": "directory"}],
        "files": [
            {
                "name": "existing.jar",
                "path": "mods/",
                "relative_path": "mods/existing.jar",
                "download_url": "https://example.com/existing.jar",
            }
        ],
    }
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    loader.get_version_java_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    loader.sync_update(version)

    assert starts == [
        {
            "status": "syncing_files_check",
            "progress": 0,
            "max_progress": 100,
            "title": "syncing_files_check",
            "kind": "sync",
            "visible": False,
            "auto_open": False,
        }
    ]
    assert closes == [(operation, "syncing_files_complete", True)]
    assert saved == [True]
    assert not (mods_dir / "stale.jar").exists()


def test_tensacraft_sync_files_reports_download_failures_without_success_alert(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    version_root.mkdir(parents=True)
    alerts = []
    fake_app.feedback.info = lambda message, **_kwargs: alerts.append(message)
    fake_app.feedback.warning = lambda message, **_kwargs: alerts.append(message)

    sync_plan = tensacraft_module.TensaCraftContentPlan(
        root=version_root,
        api_available=True,
        download_tasks=(
            DownloadTask(
                url="https://example.com/broken.jar",
                destination=version_root / "mods" / "broken.jar",
                task_id="mods/broken.jar",
            ),
        ),
        eligible_files=1,
    )

    class FailingDownloader:
        def __init__(self, *args, **kwargs):
            pass

        def download_files(self, *args, **kwargs):
            return {
                "success": 0,
                "failed": 1,
                "skipped": 0,
                "errors": ["broken.jar: Permission denied"],
            }

    monkeypatch.setattr("launcher.core.loaders.tensacraft.AsyncDownloader", FailingDownloader)

    with pytest.raises(RuntimeError, match="broken.jar: Permission denied"):
        loader._sync_files(SimpleNamespace(path=str(version_root)), "aeronautics", sync_plan)

    assert "syncing_files_complete" not in alerts


def test_tensacraft_sync_files_reports_stale_removal_failures_without_success_alert(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    stale_file = version_root / "mods" / "stale.jar"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_bytes(b"old")
    alerts = []
    fake_app.feedback.info = lambda message, **_kwargs: alerts.append(message)
    fake_app.feedback.warning = lambda message, **_kwargs: alerts.append(message)

    sync_plan = tensacraft_module.TensaCraftContentPlan(
        root=version_root,
        api_available=True,
        managed_directories=frozenset({PurePosixPath("mods")}),
        stale_paths=(PurePosixPath("mods/stale.jar"),),
        eligible_files=1,
    )

    original_replace = os.replace

    def fail_replace(source, destination):
        if Path(source) == stale_file:
            raise PermissionError("file is locked")
        return original_replace(source, destination)

    monkeypatch.setattr("launcher.application.file_sync_journal.os.replace", fail_replace)

    with pytest.raises(RuntimeError, match="stale.jar: file is locked"):
        loader._sync_files(SimpleNamespace(path=str(version_root)), "aeronautics", sync_plan)

    assert "syncing_files_complete" not in alerts


def test_tensacraft_sync_rolls_back_files_when_profile_commit_fails(fake_app, tmp_path: Path):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    stale_file = version_root / "mods" / "stale.jar"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_bytes(b"old")
    sync_plan = tensacraft_module.TensaCraftContentPlan(
        root=version_root,
        api_available=True,
        managed_directories=frozenset({PurePosixPath("mods")}),
        stale_paths=(PurePosixPath("mods/stale.jar"),),
        eligible_files=1,
    )

    with pytest.raises(OSError, match="profile is locked"):
        loader._sync_files(
            SimpleNamespace(path=str(version_root)),
            "aeronautics",
            sync_plan,
            commit_callback=lambda: (_ for _ in ()).throw(OSError("profile is locked")),
        )

    assert stale_file.read_bytes() == b"old"
    assert FileSyncJournal(version_root).read()["status"] == "rolled_back"


def test_tensacraft_sync_restores_in_memory_profile_when_commit_fails(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="fabric-loader-0.16-1.21.1",
        loader_version="0.16",
        client="TensaCraft",
        path=str(tmp_path / "instance"),
        options={"gpuMode": "integrated"},
        image=None,
        force_update=False,
        save=lambda: (_ for _ in ()).throw(OSError("profile is locked")),
    )
    original = dict(vars(version))
    original["options"] = dict(version.options)
    loader.api.get_versions = lambda _key: {
        "client": {
            "version": "1.21.1",
            "loader": "fabric",
            "loader_version": "0.16",
            "force_update": True,
            "force_update_profile_fields": ["gpu_preference"],
            "gpu_preference": "dgpu",
        }
    }
    loader.payload.loader_changed = lambda *_args, **_kwargs: False
    loader._prepare_file_sync = lambda *_args, **_kwargs: tensacraft_module.TensaCraftContentPlan(
        root=Path(version.path),
        api_available=True,
        eligible_files=1,
        force_changes=True,
    )
    loader._existing_java_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    loader._get_version_java_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    loader._ensure_game_directory_idle = lambda *_args, **_kwargs: None

    def fail_commit(_version, _key, _plan, **kwargs):
        kwargs["commit_callback"]()

    monkeypatch.setattr(loader, "_sync_files", fail_commit)

    with pytest.raises(OSError, match="profile is locked"):
        loader.sync_update(version)

    assert version.force_update is original["force_update"]
    assert version.options == original["options"]
    assert version.id == original["id"]


def test_tensacraft_force_sync_removes_extra_files_from_locked_directory(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    managed_dir = version_root / "kubejs" / "server_scripts"
    managed_dir.mkdir(parents=True)
    (managed_dir / "keep.js").write_text("old", encoding="utf-8")
    (managed_dir / "stale.js").write_text("old", encoding="utf-8")
    (managed_dir / "nested").mkdir()
    (managed_dir / "nested" / "stale.js").write_text("old", encoding="utf-8")
    user_dir = version_root / "config"
    user_dir.mkdir()
    (user_dir / "user.toml").write_text("local", encoding="utf-8")

    version = SimpleNamespace(path=str(version_root))
    loader.api.get_version_files = lambda _key: [
        {
            "name": "keep.js",
            "path": "kubejs/server_scripts/",
            "relative_path": "kubejs/server_scripts/keep.js",
            "download_url": "https://example.com/keep.js",
            "force_update": True,
            "force_update_locked": True,
            "sha256": "0" * 64,
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    plan = loader._prepare_file_sync(version, "aeronautics")
    loader._sync_files(version, "aeronautics", plan)

    assert (managed_dir / "keep.js").exists()
    assert not (managed_dir / "stale.js").exists()
    assert not (managed_dir / "nested").exists()
    assert (user_dir / "user.toml").exists()


def test_tensacraft_force_sync_uses_force_update_manifest_directories(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    managed_dir = version_root / "kubejs"
    managed_dir.mkdir(parents=True)
    (managed_dir / "keep.js").write_text("old", encoding="utf-8")
    (managed_dir / "stale.js").write_text("old", encoding="utf-8")

    version = SimpleNamespace(path=str(version_root))
    loader.api.get_force_update_manifest = lambda _key, include_directory_files=True: {
        "directories": [{"path": "kubejs/", "sync_scope": "directory", "mode": "recursive"}],
        "files": [
            {
                "name": "keep.js",
                "path": "kubejs/",
                "relative_path": "kubejs/keep.js",
                "download_url": "https://example.com/keep.js",
                "sync_scope": "directory",
                "force_update_directory": "kubejs/",
                "sha256": "0" * 64,
            }
        ],
    }
    loader.api.get_version_files = lambda _key: (_ for _ in ()).throw(
        AssertionError("full file manifest should not be used when force-update manifest is available")
    )
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    plan = loader._prepare_file_sync(version, "aeronautics")
    loader._sync_files(version, "aeronautics", plan)

    assert (managed_dir / "keep.js").exists()
    assert not (managed_dir / "stale.js").exists()


def test_tensacraft_force_sync_preserves_api_allowed_local_files(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    shaderpacks_dir = version_root / "shaderpacks"
    mods_dir.mkdir(parents=True)
    shaderpacks_dir.mkdir()
    (mods_dir / "managed.jar").write_text("old", encoding="utf-8")
    (mods_dir / "replaymod-1.21.1.jar").write_text("local", encoding="utf-8")
    (mods_dir / "some-client-only-mod.jar").write_text("local", encoding="utf-8")
    (mods_dir / "stale.jar").write_text("old", encoding="utf-8")
    (shaderpacks_dir / "local-shader.zip").write_text("local", encoding="utf-8")

    version = SimpleNamespace(
        id="aeronautics",
        version="1.21.1",
        loader="neoforge-21.1.230",
        loader_version="21.1.230",
        client="TensaCraft",
        path=str(version_root),
        options={},
        image=None,
        force_update=True,
        save=lambda: None,
    )

    loader.begin_feedback_operation = lambda **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.api.get_versions = lambda _key: {
        "client": {
            "minecraft_version": "1.21.1",
            "loader_id": "neoforge",
            "loader_version": "21.1.230",
            "force_update": True,
            "preserve_rules": [
                {"type": "glob", "path": "mods/replaymod-*.jar", "enabled": True},
                {"type": "file", "path": "mods/some-client-only-mod.jar", "enabled": True},
                {"type": "directory", "path": "shaderpacks/", "enabled": True},
                {"type": "glob", "path": "mods/disabled-*.jar", "enabled": False},
            ],
        }
    }
    loader.api.get_force_update_manifest = lambda _key, include_directory_files=True: {
        "directories": [
            {"path": "mods/", "sync_scope": "directory"},
            {"path": "shaderpacks/", "sync_scope": "directory"},
        ],
        "files": [
            {
                "name": "managed.jar",
                "path": "mods/",
                "relative_path": "mods/managed.jar",
                "download_url": "https://example.com/managed.jar",
                "sync_scope": "directory",
                "sha256": "0" * 64,
            }
        ],
    }
    loader.api.get_version_files = lambda _key: (_ for _ in ()).throw(
        AssertionError("force-update manifest should be used")
    )
    loader.runtime.has_runtime = lambda *_args, **_kwargs: True
    loader.runtime.get_runtime_name = lambda *_args, **_kwargs: "java-runtime"
    loader.runtime.get_executable_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    loader.get_version_java_path = lambda *_args, **_kwargs: "C:/Java/bin/java.exe"
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    loader.sync_update(version)

    assert (mods_dir / "managed.jar").exists()
    assert (mods_dir / "replaymod-1.21.1.jar").exists()
    assert (mods_dir / "some-client-only-mod.jar").exists()
    assert not (mods_dir / "stale.jar").exists()
    assert (shaderpacks_dir / "local-shader.zip").exists()
    assert "preserve_rules" not in version.options


def test_tensacraft_force_sync_keeps_siblings_for_single_managed_file(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    config_dir = version_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "managed.toml").write_text("old", encoding="utf-8")
    (config_dir / "custom.toml").write_text("local", encoding="utf-8")

    version = SimpleNamespace(path=str(version_root))
    loader.api.get_version_files = lambda _key: [
        {
            "name": "managed.toml",
            "path": "config/",
            "relative_path": "config/managed.toml",
            "download_url": "https://example.com/managed.toml",
            "force_update": True,
            "force_update_locked": False,
            "sha256": "1" * 64,
        }
    ]
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    plan = loader._prepare_file_sync(version, "aeronautics")
    loader._sync_files(version, "aeronautics", plan)

    assert (config_dir / "managed.toml").exists()
    assert (config_dir / "custom.toml").exists()
    assert plan.has_changes is False


def test_tensacraft_sync_without_management_metadata_preserves_unknown_local_mods(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True)
    local_mod = mods_dir / "local-user-mod.jar"
    local_mod.write_bytes(b"local")
    version = SimpleNamespace(path=str(version_root))
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.api.get_version_files = lambda _key: [
        {
            "name": "managed.jar",
            "path": "mods/",
            "relative_path": "mods/managed.jar",
            "download_url": "https://example.com/managed.jar",
            "sha256": "1" * 64,
        }
    ]
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )

    plan = loader._prepare_file_sync(version, "aeronautics")
    loader._sync_files(version, "aeronautics", plan)

    assert local_mod.read_bytes() == b"local"
    assert plan.stale_paths == ()


def test_tensacraft_repair_journal_forces_existing_managed_file_download(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    mods_dir = version_root / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "sodium.jar").write_bytes(b"broken")

    version = SimpleNamespace(path=str(version_root))
    loader.api.get_force_update_manifest = lambda _key, include_directory_files=True: {
        "directories": [{"path": "mods/", "sync_scope": "directory"}],
        "files": [
            {
                "name": "sodium.jar",
                "path": "mods/",
                "relative_path": "mods/sodium.jar",
                "download_url": "https://example.com/sodium.jar",
                "size": 6,
            }
        ],
    }
    loader.api.get_version_files = lambda _key: (_ for _ in ()).throw(
        AssertionError("force-update manifest should be used")
    )
    monkeypatch.setattr(
        "launcher.core.async_downloader.AsyncDownloader._should_skip",
        lambda self, task, verify_sha1=False: True,
    )
    FileSyncJournal(version_root).mark_repair_required(
        reason="missing_mod_dependency",
        details={"diagnostic_path": str(version_root / "logs" / "latest.log")},
    )

    plan = loader._prepare_file_sync(version, "aeronautics")

    assert plan.has_changes is True
    assert [task.task_id for task in plan.download_tasks] == ["mods/sodium.jar"]


def test_tensacraft_install_prefers_saved_pack_id_over_mc_version(fake_app):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    requested = []

    version = SimpleNamespace(
        id="tensa",
        version_id="tensacraft-1.21.11",
        version="1.21.11",
        loader_version="0.16",
        client="TensaCraft",
        path="",
        save=lambda: None,
    )

    loader.begin_feedback_operation = lambda *_args, **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.get_game_path = lambda _version_id: Path("instance")
    loader.api.get_versions = lambda version_key: requested.append(version_key) or {}

    try:
        loader.install(version)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected install to fail on sentinel payload")

    assert requested[0] == "tensa"
    assert requested.index("tensa") < requested.index("1.21.11")


def test_tensacraft_install_recovers_pack_id_from_name_when_local_id_is_not_pack_id(fake_app, monkeypatch, tmp_path):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    requested = []
    saved = []
    version_root = tmp_path / "instance"

    version = SimpleNamespace(
        id="aeronautics-copy",
        version_id="aeronautics-copy",
        name="Aeronautics",
        version="1.21.1",
        loader_version="21.1.228",
        client="TensaCraft",
        path="",
        options={},
        image=None,
        force_update=True,
        save=lambda: saved.append(version.id),
    )

    def fake_get_versions(version_key):
        requested.append(version_key)
        if version_key == "Aeronautics":
            return {
                "client": {
                    "id": "aeronautics",
                    "minecraft_version": "1.21.1",
                    "loader_id": "neoforge",
                    "loader_version": "21.1.228",
                    "force_update": True,
                    "options": {},
                }
            }
        return {}

    class FakeBaseLoader:
        def install(self, version, loader_version=None):
            version.loader = "neoforge-21.1.228"
            version.loader_version = loader_version

    loader.begin_feedback_operation = lambda *_args, **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.get_game_path = lambda _version_id: version_root
    loader.api.get_versions = fake_get_versions
    loader.api.get_version_files = lambda version_key: [] if version_key == "aeronautics" else None
    monkeypatch.setattr(fake_app.launcher, "get_loader", lambda _loader_name: FakeBaseLoader())

    loader.install(version)

    assert requested == ["aeronautics-copy", "Aeronautics"]
    assert version.id == "aeronautics"
    assert saved == ["aeronautics"]


def test_tensacraft_install_blocks_when_game_directory_is_running(fake_app, monkeypatch, tmp_path: Path):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    requested = []

    version = SimpleNamespace(
        id="aeronautics",
        version_id="tensacraft-aeronautics",
        version="aeronautics",
        loader_version=None,
        client="TensaCraft",
        path="",
        options={},
        save=lambda: None,
    )

    loader.begin_feedback_operation = lambda *_args, **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.get_game_path = lambda _version_id: version_root
    loader.api.get_versions = lambda version_key: requested.append(version_key) or {}
    monkeypatch.setattr("launcher.core.game.Game.is_game_dir_active", classmethod(lambda cls, _path: True), raising=False)

    try:
        loader.install(version)
    except RuntimeError as exc:
        assert str(exc) == "tensacraft_game_directory_running (version=Aeronautics)"
    else:
        raise AssertionError("Expected install to stop while the game directory is active")

    assert requested == []


def test_tensacraft_pack_content_is_activated_from_transaction_staging(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    version_root = tmp_path / "instance"
    destination = version_root / "mods" / "managed.jar"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")
    staged_destinations = []

    class SuccessfulDownloader:
        def __init__(self, *args, **kwargs):
            pass

        def download_files(self, tasks, *args, **kwargs):
            for task in tasks:
                staged_destinations.append(task.destination)
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                task.destination.write_bytes(b"new")
            return {
                "success": len(tasks),
                "failed": 0,
                "skipped": 0,
                "errors": [],
            }

    monkeypatch.setattr(tensacraft_module, "AsyncDownloader", SuccessfulDownloader)

    loader._download_pack_files(
        version_root,
        [
            {
                "name": "managed.jar",
                "relative_path": "mods/managed.jar",
                "download_url": "https://example.com/managed.jar",
                "size": 3,
            }
        ],
        "syncing_files",
    )

    assert destination.read_bytes() == b"new"
    assert staged_destinations
    assert all(path != destination for path in staged_destinations)
    assert FileSyncJournal(version_root).read()["status"] == "complete"


def test_tensacraft_pack_content_rejects_duplicate_destinations(
    fake_app,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    duplicate = {
        "name": "managed.jar",
        "relative_path": "mods/managed.jar",
        "download_url": "https://example.com/managed.jar",
    }

    with pytest.raises(RuntimeError, match="duplicate destinations"):
        loader._download_pack_files(
            tmp_path / "instance",
            [duplicate, dict(duplicate)],
            "syncing_files",
        )


def test_tensacraft_install_error_includes_first_download_failure(
    fake_app,
    monkeypatch,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)

    version = SimpleNamespace(
        id="tensa-lite",
        version_id="tensacraft-1.21.11",
        version="1.21.11",
        loader_version="0.18.4",
        client="TensaCraft",
        path="",
        options={},
        save=lambda: None,
    )

    loader.begin_feedback_operation = lambda *_args, **_kwargs: None
    loader.finish_feedback_operation = lambda *_args, **_kwargs: None
    loader.get_game_path = lambda _version_id: tmp_path / "instance"
    loader.api.get_versions = lambda _version_key: {
        "client": {
            "id": "tensa-lite",
            "name": "Tensa",
            "minecraft_version": "1.21.11",
            "loader_id": "fabric",
            "loader_version": "0.18.4",
        }
    }
    loader.api.get_version_files = lambda _version_key: [
        {
            "name": "client.json",
            "relative_path": "client.json",
            "download_url": "https://example.com/client.json",
        }
    ]

    class FailingDownloader:
        def __init__(self, *args, **kwargs):
            pass

        def download_files(self, *args, **kwargs):
            return {"success": 0, "failed": 1, "skipped": 0, "errors": ["client.json: Permission denied"]}

    original_downloader = tensacraft_module.AsyncDownloader
    tensacraft_module.AsyncDownloader = FailingDownloader
    monkeypatch.setattr(
        fake_app.launcher,
        "get_loader",
        lambda _name: SimpleNamespace(install=lambda **kwargs: None),
    )
    try:
        try:
            loader.install(version)
        except RuntimeError as exc:
            assert str(exc) == "Failed to download 1 files for Tensa pack: client.json: Permission denied"
        else:
            raise AssertionError("Expected install to fail when pack downloads fail")
    finally:
        tensacraft_module.AsyncDownloader = original_downloader


def test_tensacraft_api_retries_transient_timeout(fake_app, monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"client": {"id": "aeronautics"}}]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise requests.Timeout("read timed out")
        return FakeResponse()

    monkeypatch.setattr("launcher.core.api.tensacraft.requests.get", fake_get)
    monkeypatch.setattr("launcher.core.api.tensacraft.time.sleep", lambda _seconds: None)

    assert TensaCraftAPI(fake_app).list_versions() == [{"client": {"id": "aeronautics"}}]
    assert len(calls) == 2


def test_tensacraft_api_fetches_force_update_manifest_with_directory_files(fake_app, monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://gigabait.uk/api/mods/aeronautics/force-update?include_directory_files=1":
            return FakeResponse(
                    {
                        "directories": [{"path": "mods/", "sync_scope": "directory"}],
                        "files": [
                            {
                                "relative_path": "mods/example.jar",
                                "download_url": "https://example.com/example.jar",
                                "sha256": "abc",
                            }
                        ],
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("launcher.core.api.tensacraft.requests.get", fake_get)

    manifest = TensaCraftAPI(fake_app).get_force_update_manifest("aeronautics", include_directory_files=True)

    assert manifest == {
        "directories": [{"path": "mods/", "sync_scope": "directory"}],
        "files": [
            {
                "relative_path": "mods/example.jar",
                "download_url": "https://example.com/example.jar",
                "sha256": "abc",
            }
        ],
    }
    assert calls == [
        (
            "https://gigabait.uk/api/mods/aeronautics/force-update?include_directory_files=1",
            {"timeout": TensaCraftAPI.REQUEST_TIMEOUT},
        ),
    ]


def test_tensacraft_api_does_not_cache_pack_metadata(fake_app, monkeypatch):
    responses = iter(
        [
            [{"client": {"id": "aeronautics", "loader_version": "21.1.1"}}],
            [{"client": {"id": "aeronautics", "loader_version": "21.1.2"}}],
        ]
    )

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        "launcher.core.api.tensacraft.requests.get",
        lambda _url, **_kwargs: FakeResponse(next(responses)),
    )

    api = TensaCraftAPI(fake_app)
    assert api.get_versions("aeronautics")["client"]["loader_version"] == "21.1.1"
    assert api.get_versions("aeronautics")["client"]["loader_version"] == "21.1.2"


def test_tensacraft_api_rejects_incomplete_force_update_manifest(fake_app, monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "summary": {"returned_files_count": 2, "directories_count": 0},
                "directories": [],
                "files": [
                    {
                        "relative_path": "mods/one.jar",
                        "download_url": "https://example.com/one.jar",
                        "sha256": "abc",
                    }
                ],
            }

    monkeypatch.setattr(
        "launcher.core.api.tensacraft.requests.get",
        lambda _url, **_kwargs: FakeResponse(),
    )

    assert TensaCraftAPI(fake_app).get_force_update_manifest("aeronautics") is None


def test_tensacraft_api_does_not_cache_sync_manifests(fake_app, monkeypatch):
    calls = 0

    class FakeResponse:
        def __init__(self, index: int):
            self.index = index

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "summary": {"returned_files_count": 1, "directories_count": 0},
                "directories": [],
                "files": [
                    {
                        "relative_path": f"mods/version-{self.index}.jar",
                        "download_url": f"https://example.com/version-{self.index}.jar",
                        "sha256": f"hash-{self.index}",
                    }
                ],
            }

    def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse(calls)

    monkeypatch.setattr("launcher.core.api.tensacraft.requests.get", fake_get)

    api = TensaCraftAPI(fake_app)
    first = api.get_force_update_manifest("aeronautics")
    second = api.get_force_update_manifest("aeronautics")

    assert first is not None
    assert second is not None
    assert first["files"][0]["relative_path"] == "mods/version-1.jar"
    assert second["files"][0]["relative_path"] == "mods/version-2.jar"
    assert calls == 2


def test_tensacraft_required_manifest_failure_does_not_use_legacy_file_list(
    fake_app,
    tmp_path: Path,
):
    loader = tensacraft_module.TensaCraftLoader(app=fake_app)
    loader.api.get_force_update_manifest = lambda *_args, **_kwargs: None
    loader.api.get_version_files = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("legacy file list must not replace a required manifest")
    )

    plan = loader.content.prepare(
        tmp_path / "instance",
        "aeronautics",
        force_update_endpoint="https://example.com/force-update",
    )

    assert plan.api_available is False
    assert plan.has_changes is False


def test_home_tensacraft_install_error_report_includes_context(fake_app, monkeypatch):
    page = Home(fake_app)
    alerts = []
    finished = []
    operation = SimpleNamespace(
        finish=lambda message=None, show_success=True: finished.append((message, show_success)),
        fail=lambda message, **_kwargs: finished.append((message, False)),
    )

    async def fake_run_blocking(*_args, **_kwargs):
        raise RuntimeError("Could not retrieve version files from API for aeronautics")

    monkeypatch.setattr("launcher.pages.home.run_blocking", fake_run_blocking)
    fake_app.feedback.warning = lambda message, **kwargs: alerts.append((message, kwargs))

    asyncio.run(page._install_and_launch_tensacraft_async("Aeronautics", "aeronautics", operation))

    assert alerts
    _, kwargs = alerts[0]
    assert kwargs["report_title"] == "TensaCraft install failed: Aeronautics"
    assert kwargs["report_metadata"]["action"] == "tensacraft_install"
    assert kwargs["report_metadata"]["pack_id"] == "aeronautics"
    assert kwargs["report_metadata"]["version_name"] == "Aeronautics"
    assert "Could not retrieve version files" in kwargs["report_metadata"]["exception"]
    assert finished


def test_home_start_version_schedules_local_launch_without_blocking_ui(fake_app, monkeypatch):
    page = Home(fake_app)
    started = []
    scheduled = []
    version = SimpleNamespace(
        is_remote=False,
        start=lambda: started.append(True) or {"status": True, "text": "ok"},
    )

    monkeypatch.setattr(
        "launcher.pages.home.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )

    page.start_version(version)

    assert started == []
    assert len(scheduled) == 1
    assert scheduled[0][1] == (version,)


def test_home_install_and_launch_runs_launch_in_background(fake_app, monkeypatch):
    page = Home(fake_app)
    installed = SimpleNamespace(start=lambda: {"status": True, "text": "started"})
    blocking_calls = []
    alerts = []
    operation = SimpleNamespace(finish=lambda *_args, **_kwargs: None)

    async def fake_run_blocking(fn, *args, **kwargs):
        blocking_calls.append((fn, args, kwargs))
        if getattr(fn, "__self__", None) is page and getattr(fn, "__name__", "") == "_install_tensacraft_version":
            return installed
        return fn(*args, **kwargs)

    monkeypatch.setattr("launcher.pages.home.run_blocking", fake_run_blocking)
    fake_app.feedback.info = lambda message, **_kwargs: alerts.append((message, False))
    fake_app.feedback.warning = lambda message, **_kwargs: alerts.append((message, True))

    asyncio.run(page._install_and_launch_tensacraft_async("Aeronautics", "aeronautics", operation))

    assert getattr(blocking_calls[0][0], "__self__", None) is page
    assert getattr(blocking_calls[0][0], "__name__", "") == "_install_tensacraft_version"
    assert blocking_calls[1][0] is installed.start
    assert alerts == [("started", False)]


def test_home_remote_tensacraft_requires_description_confirmation(fake_app, monkeypatch):
    page = Home(fake_app)
    scheduled = []
    fake_app.versions = SimpleNamespace(all=lambda: [], get_by_name=lambda _name: None)
    monkeypatch.setattr(
        "launcher.pages.home.run_task",
        lambda _page, task, *args, **_kwargs: scheduled.append((task, args)),
    )
    version = SimpleNamespace(
        is_remote=True,
        name="Aeronautics",
        id="aeronautics",
        version="aeronautics",
        remote_pack_id="aeronautics",
        description="Aeronautics Create mods server TensaCraft",
    )

    page._install_and_launch_tensacraft(version)

    assert scheduled == []
    assert page._tensacraft_install_dialog.open is True
    assert page._tensacraft_description_text.value == "Aeronautics Create mods server TensaCraft"

    page._confirm_tensacraft_install(None)

    assert len(scheduled) == 1
    assert scheduled[0][0] == page._install_and_launch_tensacraft_async
    assert scheduled[0][1][:2] == ("Aeronautics", "aeronautics")


def test_home_skips_pending_remote_tensacraft_pack(fake_app, monkeypatch):
    page = Home(fake_app)
    page.grid = SimpleNamespace(controls=[])
    fake_app.pending_tensacraft_pack_ids = {"aeronautics"}
    fake_app.versions = SimpleNamespace(all=lambda: [])

    fake_app.tensa_api = SimpleNamespace(
        list_versions=lambda: [
            {
                "name": "aeronautics",
                "client": {
                    "id": "aeronautics",
                    "name": "Aeronautics",
                    "description": "Aeronautics Create mods server TensaCraft",
                },
            }
        ]
    )

    asyncio.run(page._load_tensacraft_versions())

    assert page.grid.controls == []


def test_home_removes_visible_remote_card_when_pack_becomes_pending(fake_app):
    page = Home(fake_app)
    visible_remote = SimpleNamespace(key="tensacraft:aeronautics")
    other_card = SimpleNamespace(key="tensacraft:tensa-lite")
    page.grid = SimpleNamespace(controls=[visible_remote, other_card])

    page.hide_pending_tensacraft_pack("aeronautics")

    assert fake_app.pending_tensacraft_pack_ids == {"aeronautics"}
    assert page.grid.controls == [other_card]
