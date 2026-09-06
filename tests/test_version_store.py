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


@pytest.mark.parametrize("payload", [None, [], [1], "invalid", 42, True])
def test_load_skips_invalid_version_records_and_retains_valid_records(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "versions.json"
    path.write_text(json.dumps({"invalid": payload, "valid": {"name": "Valid"}}), encoding="utf-8")

    store = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")

    assert [version.version_id for version in store.all()] == ["valid"]
    assert json.loads(path.read_text(encoding="utf-8"))["invalid"] == payload


@pytest.mark.parametrize("options", [[1], "invalid", 42, True])
def test_load_recovers_invalid_options_without_discarding_version(tmp_path: Path, options: object) -> None:
    path = tmp_path / "versions.json"
    path.write_text(json.dumps({"demo": {"name": "Demo", "options": options}}), encoding="utf-8")

    version = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft").get("demo")

    assert version is not None
    assert version.name == "Demo"
    assert version.options == {"gpuMode": "dgpu"}


def test_invalid_utf8_versions_loads_as_empty(tmp_path: Path) -> None:
    (tmp_path / "versions.json").write_bytes(b"\xff")

    assert Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft").all() == []


def test_save_preserves_external_version_fields_and_option_edits(tmp_path: Path) -> None:
    path = tmp_path / "versions.json"
    path.write_text(
        json.dumps({"demo": {"name": "Demo", "future_field": True, "options": {"local": 1, "other": 1}}}),
        encoding="utf-8",
    )
    store = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    version = store.get("demo")
    assert version is not None
    external = json.loads(path.read_text(encoding="utf-8"))
    external["demo"].update({"name": "External name", "future_field": {"changed": True}})
    external["demo"]["options"].update({"other": 2, "external": True})
    path.write_text(json.dumps(external), encoding="utf-8")
    version.options["local"] = 2

    version.save()
    version.save()

    saved = json.loads(path.read_text(encoding="utf-8"))["demo"]
    assert saved["name"] == "External name"
    assert saved["future_field"] == {"changed": True}
    assert saved["options"]["other"] == 2
    assert saved["options"]["external"] is True
    assert saved["options"]["local"] == 2


@pytest.mark.parametrize("corrupted", [b"{", b"[]", b"\xff"])
def test_save_recovers_cached_versions_when_file_is_corrupt(tmp_path: Path, corrupted: bytes) -> None:
    store = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    first = Version("first", {"name": "First"})
    store.add(first)
    store.add(Version("second", {"name": "Second"}))
    store.filepath.write_bytes(corrupted)
    first.name = "Renamed"

    first.save()

    saved = json.loads(store.filepath.read_text(encoding="utf-8"))
    assert set(saved) == {"first", "second"}
    assert saved["first"]["name"] == "Renamed"


def test_save_does_not_resurrect_version_removed_by_other_store(tmp_path: Path) -> None:
    first = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    version = Version("demo", {"name": "Demo"})
    first.add(version)
    second = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    second.remove("demo", delete_files=False)

    with pytest.raises(RuntimeError, match="removed"):
        version.save()

    assert json.loads(first.filepath.read_text(encoding="utf-8")) == {}


def test_removed_version_cannot_be_saved_by_captured_callback(tmp_path: Path) -> None:
    store = Versions(storage_dir=tmp_path, minecraft_dir=tmp_path / "minecraft")
    version = Version("demo", {"name": "Demo"})
    store.add(version)
    pending_save = version._persist
    assert pending_save is not None
    store.remove("demo", delete_files=False)

    with pytest.raises(RuntimeError, match="removed|not bound"):
        pending_save(version)

    assert json.loads(store.filepath.read_text(encoding="utf-8")) == {}
