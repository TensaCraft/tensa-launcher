from __future__ import annotations

import json
from pathlib import Path

import pytest

from launcher.storage.config_store import Config


@pytest.mark.parametrize("payload", [None, [], ["lang"], "value", 42, True])
def test_non_object_config_loads_as_empty(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    config = Config(filename=path)

    assert config.get("lang", "en_US") == "en_US"
    assert tuple(config.keys()) == ()
    config.set("lang", "uk_UA")
    assert json.loads(path.read_text(encoding="utf-8")) == {"lang": "uk_UA"}


def test_invalid_utf8_config_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"\xff")

    assert Config(filename=path).get("lang", "en_US") == "en_US"


def test_stale_config_saves_only_local_changes(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"lang": "en_US", "obsolete": True}), encoding="utf-8")
    first = Config(filename=path)
    second = Config(filename=path)

    first.set("lang", "uk_UA")
    first.delete("obsolete")
    first.set("external", {"future": True})
    second.set("theme", "dark")

    expected = {"lang": "uk_UA", "external": {"future": True}, "theme": "dark"}
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert dict(second.items()) == expected
    second.save()
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_pending_config_updates_and_deletions_preserve_external_data(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = Config(filename=path)
    config.update({"lang": "en_US", "delete_me": True})
    config.update({"lang": "uk_UA", "theme": "dark"}, persist=False)
    config.delete("delete_me", persist=False)
    config.delete("external_deleted", persist=False)
    path.write_text(
        json.dumps({"lang": "en_US", "delete_me": True, "external": 1, "external_deleted": 2}),
        encoding="utf-8",
    )

    config.save()

    assert json.loads(path.read_text(encoding="utf-8")) == {"lang": "uk_UA", "theme": "dark", "external": 1}


def test_explicit_set_overrides_external_change_even_when_local_value_is_unchanged(tmp_path: Path) -> None:
    config = Config(storage_dir=tmp_path)
    config.set("lang", "en_US")
    Config(storage_dir=tmp_path).set("lang", "uk_UA")

    config.set("lang", "en_US")

    assert Config(storage_dir=tmp_path).get("lang") == "en_US"


def test_save_retains_mutable_config_value_edits(tmp_path: Path) -> None:
    config = Config(storage_dir=tmp_path)
    config.set("nested", {"values": [1]})
    config.get("nested")["values"].append(2)
    Config(storage_dir=tmp_path).set("external", True)

    config.save()

    assert dict(Config(storage_dir=tmp_path).items()) == {"nested": {"values": [1, 2]}, "external": True}


@pytest.mark.parametrize("corrupted", [b"{", b"[]", b"\xff"])
def test_failed_reload_preserves_last_good_config(tmp_path: Path, corrupted: bytes) -> None:
    path = tmp_path / "config.json"
    config = Config(filename=path)
    config.set("lang", "uk_UA")
    path.write_bytes(corrupted)

    assert config.reload() == {"lang": "uk_UA"}
    config.set("theme", "dark")
    assert json.loads(path.read_text(encoding="utf-8")) == {"lang": "uk_UA", "theme": "dark"}


def test_read_failure_preserves_config_and_does_not_overwrite_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.json"
    config = Config(filename=path)
    config.set("lang", "uk_UA")
    original = path.read_bytes()

    with monkeypatch.context() as failure:
        def fail_read(*_args, **_kwargs):
            raise PermissionError("read denied")

        failure.setattr(type(path), "read_text", fail_read)
        assert config.reload() == {"lang": "uk_UA"}
        with pytest.raises(PermissionError, match="read denied"):
            config.set("theme", "dark")

    assert path.read_bytes() == original
    config.save()
    assert Config(filename=path).get("theme") == "dark"


def test_failed_config_save_retains_pending_edits_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(storage_dir=tmp_path)
    config.update({"lang": "en_US", "obsolete": True})
    path = tmp_path / "config.json"
    original = path.read_bytes()

    with monkeypatch.context() as failure:
        def fail_replace(*_args):
            raise OSError("disk full")

        failure.setattr("launcher.storage.atomic.os.replace", fail_replace)
        config.set("lang", "uk_UA", persist=False)
        with pytest.raises(OSError, match="disk full"):
            config.delete("obsolete")

    assert path.read_bytes() == original
    Config(storage_dir=tmp_path).set("external", True)
    config.save()

    assert dict(Config(storage_dir=tmp_path).items()) == {"lang": "uk_UA", "external": True}


@pytest.mark.parametrize("operation", ["set", "update", "delete", "save"])
def test_config_persistence_errors_reach_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    config = Config(storage_dir=tmp_path)
    config.set("existing", True)
    path = tmp_path / "config.json"
    original = path.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("launcher.storage.config_store.atomic_write_json", fail_write)

    with pytest.raises(OSError, match="disk full"):
        if operation == "set":
            config.set("new", True)
        elif operation == "update":
            config.update({"new": True})
        elif operation == "delete":
            config.delete("existing")
        else:
            config.save()

    assert path.read_bytes() == original
