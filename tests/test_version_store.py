from __future__ import annotations

import json
from pathlib import Path

import pytest

from launcher.domain.version import Version
from launcher.storage.version_store import VersionDirectoryCleanupError, Versions


@pytest.fixture
def version_store(tmp_path: Path) -> tuple[Versions, Path]:
    minecraft_dir = tmp_path / "configured-minecraft"
    return Versions(storage_dir=tmp_path / "state", minecraft_dir=minecraft_dir), minecraft_dir


def test_remove_deletes_absolute_version_path_under_configured_root(
    version_store: tuple[Versions, Path],
) -> None:
    store, minecraft_dir = version_store
    version_dir = minecraft_dir / "custom-games" / "demo"
    version_dir.mkdir(parents=True)
    store.add(Version("demo", {"path": str(version_dir)}))

    store.remove("demo")

    assert not version_dir.exists()
    assert store.get("demo") is None


def test_remove_write_failure_preserves_state_binding_json_and_directory(
    version_store: tuple[Versions, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, minecraft_dir = version_store
    version_dir = minecraft_dir / "custom-games" / "demo"
    version_dir.mkdir(parents=True)
    version = Version("demo", {"name": "Before", "path": str(version_dir)})
    store.add(version)
    saved_json = store.filepath.read_bytes()

    with monkeypatch.context() as atomic_failure:

        def fail_replace(_source: Path, _destination: Path) -> None:
            raise OSError("disk full")

        atomic_failure.setattr("launcher.storage.atomic.os.replace", fail_replace)
        with pytest.raises(OSError, match="disk full"):
            store.remove("demo")

    assert store.get("demo") is version
    assert store.filepath.read_bytes() == saved_json
    assert version_dir.is_dir()

    version.name = "After"
    version.save()
    saved = Versions(
        storage_dir=store.storage_dir,
        minecraft_dir=minecraft_dir,
    ).get("demo")
    assert saved is not None
    assert saved.name == "After"


def test_remove_read_failure_preserves_state_and_directory(
    version_store: tuple[Versions, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, minecraft_dir = version_store
    version_dir = minecraft_dir / "custom-games" / "demo"
    version_dir.mkdir(parents=True)
    version = Version("demo", {"path": str(version_dir)})
    store.add(version)

    path_type = type(store.filepath)
    original_read_text = path_type.read_text

    def fail_store_read(path: Path, *args, **kwargs):
        if path == store.filepath:
            raise OSError("read failed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", fail_store_read)

    with pytest.raises(OSError, match="read failed"):
        store.remove("demo")

    assert store.get("demo") is version
    assert version_dir.is_dir()


def test_remove_cleanup_failure_keeps_committed_removal_and_reports_orphan(
    version_store: tuple[Versions, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, minecraft_dir = version_store
    version_dir = minecraft_dir / "custom-games" / "demo"
    version_dir.mkdir(parents=True)
    version = Version("demo", {"path": str(version_dir)})
    store.add(version)

    def fail_cleanup(_path: Path) -> None:
        raise OSError("directory locked")

    monkeypatch.setattr("launcher.storage.version_store.shutil.rmtree", fail_cleanup)

    with pytest.raises(
        VersionDirectoryCleanupError,
        match="metadata was removed, but its directory remains",
    ) as error:
        store.remove("demo")

    assert str(version_dir) in str(error.value)
    assert version_dir.is_dir()
    assert store.get("demo") is None
    assert (
        Versions(
            storage_dir=store.storage_dir,
            minecraft_dir=minecraft_dir,
        ).get("demo")
        is None
    )
    with pytest.raises(RuntimeError, match="not bound to a Versions store"):
        version.save()


def test_remove_preserves_unrelated_persisted_versions_and_fields(
    version_store: tuple[Versions, Path],
) -> None:
    store, minecraft_dir = version_store
    store.add(Version("demo", {"name": "Demo"}))
    persisted = {
        "demo": {"name": "Demo"},
        "external": {"name": "External", "future_field": {"enabled": True}},
    }
    store.filepath.write_text(json.dumps(persisted), encoding="utf-8")

    store.remove("demo", delete_files=False)

    saved = json.loads(store.filepath.read_text(encoding="utf-8"))
    assert saved == {"external": persisted["external"]}
    assert Versions(storage_dir=store.storage_dir, minecraft_dir=minecraft_dir).get("external") is not None


@pytest.mark.parametrize("stored_path", ["../outside/demo", "../../outside/demo"])
def test_remove_preserves_relative_version_path_outside_configured_root(
    version_store: tuple[Versions, Path],
    stored_path: str,
) -> None:
    store, minecraft_dir = version_store
    outside_dir = (minecraft_dir / stored_path).resolve()
    outside_dir.mkdir(parents=True)
    sentinel = outside_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    store.add(Version("demo", {"path": stored_path}))

    store.remove("demo")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert store.get("demo") is None


def test_remove_preserves_absolute_version_path_outside_configured_root(
    version_store: tuple[Versions, Path],
    tmp_path: Path,
) -> None:
    store, _minecraft_dir = version_store
    outside_dir = tmp_path / "external-version"
    outside_dir.mkdir()
    sentinel = outside_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    store.add(Version("demo", {"path": str(outside_dir)}))

    store.remove("demo")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert store.get("demo") is None


def test_remove_preserves_version_path_that_resolves_through_symlink_outside_root(
    version_store: tuple[Versions, Path],
    tmp_path: Path,
) -> None:
    store, minecraft_dir = version_store
    outside_root = tmp_path / "external-games"
    outside_version = outside_root / "demo"
    outside_version.mkdir(parents=True)
    sentinel = outside_version / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = minecraft_dir / "games" / "external"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")
    store.add(Version("demo", {"path": str(link / "demo")}))

    store.remove("demo")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert store.get("demo") is None


def test_added_version_saves_through_its_owning_store(tmp_path: Path) -> None:
    storage_dir = tmp_path / "state"
    store = Versions(storage_dir=storage_dir, minecraft_dir=tmp_path / "minecraft")
    version = Version("demo", {"name": "Before"})
    store.add(version)

    version.name = "After"
    version.save()

    reloaded = Versions(storage_dir=storage_dir, minecraft_dir=tmp_path / "minecraft")
    saved = reloaded.get("demo")
    assert saved is not None
    assert saved.name == "After"


def test_loaded_version_remains_bound_to_store(tmp_path: Path) -> None:
    storage_dir = tmp_path / "state"
    minecraft_dir = tmp_path / "minecraft"
    Versions(storage_dir=storage_dir, minecraft_dir=minecraft_dir).add(Version("demo", {"name": "Before"}))

    reloaded = Versions(storage_dir=storage_dir, minecraft_dir=minecraft_dir)
    version = reloaded.get("demo")
    assert version is not None
    version.name = "After"
    version.save()

    saved = Versions(storage_dir=storage_dir, minecraft_dir=minecraft_dir).get("demo")
    assert saved is not None
    assert saved.name == "After"


def test_unbound_version_cannot_save_through_global_state() -> None:
    version = Version("demo", {"name": "Demo"})

    with pytest.raises(RuntimeError, match="not bound to a Versions store"):
        version.save()


def test_prepared_version_is_persisted_only_after_successful_save(tmp_path: Path) -> None:
    storage_dir = tmp_path / "state"
    store = Versions(storage_dir=storage_dir, minecraft_dir=tmp_path / "minecraft")
    version = store.prepare(Version("demo", {"name": "Demo"}))

    assert store.get("demo") is None
    assert not (storage_dir / "versions.json").exists()

    version.save()

    assert store.get("demo") is version
    assert (
        Versions(
            storage_dir=storage_dir,
            minecraft_dir=tmp_path / "minecraft",
        ).get("demo")
        is not None
    )
