from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from launcher.storage.profile_store import Profiles


@pytest.fixture
def profile_app(tmp_path: Path):
    key = Fernet.generate_key().decode()
    return SimpleNamespace(
        util=SimpleNamespace(app_state_dir=tmp_path, get_user_secret=lambda: key),
        log=SimpleNamespace(error=lambda *_args: None),
        trans=lambda key, **_kwargs: key,
    )


@pytest.mark.parametrize(
    ("first_operation", "second_operation"),
    [("create", "refresh"), ("edit", "refresh"), ("delete", "refresh"), ("default", "refresh"), ("refresh", "delete")],
)
def test_profile_mutations_serialize_candidate_creation_and_commit(
    profile_app, monkeypatch: pytest.MonkeyPatch, first_operation: str, second_operation: str,
) -> None:
    store = Profiles(profile_app)
    assert store.create_profile("Player", {"name": "Player", "access_token": "old-access"})["status"]
    assert store.create_profile("Second", {"name": "Second", "access_token": "offline"})["status"]
    candidate_ready = Event()
    second_candidate_ready = Event()
    second_started = Event()
    resume_first = Event()
    original_replace = store._replace_profiles

    def paused_replace(candidate):
        if current_thread().name.startswith("first-profile"):
            candidate_ready.set()
            assert resume_first.wait(5)
        else:
            second_candidate_ready.set()
        return original_replace(candidate)

    def mutate(operation, *, second=False):
        if second:
            second_started.set()
        if operation == "create":
            return store.create_profile("Third", {"name": "Third", "access_token": "offline"})
        if operation == "edit":
            return store.edit_profile("Second", {"name": "Renamed"})
        if operation == "delete":
            return store.delete_profile("Player" if second else "Second")
        if operation == "default":
            return store.set_default_profile("Player")
        return store.edit_profile("Player", {"access_token": "new-access", "refresh_token": "new-refresh"})

    monkeypatch.setattr(store, "_replace_profiles", paused_replace)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-profile") as first_pool:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="second-profile") as second_pool:
            first_future = first_pool.submit(mutate, first_operation)
            try:
                assert candidate_ready.wait(5)
                second_future = second_pool.submit(mutate, second_operation, second=True)
                assert second_started.wait(5)
                if second_candidate_ready.wait(0.2):
                    second_future.result(timeout=5)
            finally:
                resume_first.set()
            for future in (first_future, second_future):
                result = future.result(timeout=5)
                assert result["status"] if isinstance(result, dict) else result

    reloaded = Profiles(profile_app)
    assert reloaded.get_all_profiles() == store.get_all_profiles()
    if second_operation == "delete":
        assert set(store.get_all_profiles()) == {"Second"}
    else:
        assert store.get_profile("Player")["access_token"] == "new-access"
        assert store.get_profile("Player")["refresh_token"] == "new-refresh"
        if first_operation == "create":
            assert set(store.get_all_profiles()) == {"Player", "Second", "Third"}
        elif first_operation == "edit":
            assert store.get_profile("Second")["name"] == "Renamed"
        elif first_operation == "delete":
            assert set(store.get_all_profiles()) == {"Player"}
        else:
            assert store.get_default_profile(return_key=True)[0] == "Player"
    raw = json.loads(store.storage_path.read_text(encoding="utf-8"))
    if "Player" in raw:
        assert raw["Player"]["access_token"].startswith(Profiles.ENCRYPTED_PREFIX)


def test_invalid_utf8_profile_file_does_not_crash_or_rewrite_on_load(profile_app) -> None:
    path = profile_app.util.app_state_dir / "profiles.json"
    path.write_bytes(b"\xff")

    assert Profiles(profile_app).get_all_profiles() == {}
    assert path.read_bytes() == b"\xff"
