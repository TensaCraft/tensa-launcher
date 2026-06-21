import json
from types import SimpleNamespace

from cryptography.fernet import Fernet

from launcher.storage.profile_store import Profiles


class _Logger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)


def _app(tmp_path, key, legacy_key=None):
    util = SimpleNamespace(
        app_state_dir=tmp_path,
        get_user_secret=lambda: key,
        get_legacy_user_secret=lambda: legacy_key,
    )
    return SimpleNamespace(util=util, log=_Logger(), trans=lambda key, **_: key)


def _raw_profiles(tmp_path):
    return json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))


def test_profiles_mark_undecryptable_tokens_and_preserve_raw_values(tmp_path):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    store = Profiles(_app(tmp_path, old_key))
    store.create_profile(
        "Player",
        {
            "name": "Player",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "default": True,
        },
    )
    raw_before = _raw_profiles(tmp_path)

    reloaded = Profiles(_app(tmp_path, new_key))
    profile = reloaded.get_profile("Player")

    assert profile["access_token"] is None
    assert profile["refresh_token"] is None
    assert profile["reauth_required"] is True
    assert profile["reauth_reason"] == "token_decryption_failed"

    reloaded.set_default_profile("Player")
    raw_after = _raw_profiles(tmp_path)

    assert raw_after["Player"]["access_token"] == raw_before["Player"]["access_token"]
    assert raw_after["Player"]["refresh_token"] == raw_before["Player"]["refresh_token"]


def test_profiles_migrate_legacy_encryption_to_stable_key(tmp_path):
    legacy_key = Fernet.generate_key().decode()
    stable_key = Fernet.generate_key().decode()

    store = Profiles(_app(tmp_path, legacy_key))
    store.create_profile(
        "Player",
        {
            "name": "Player",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "default": True,
        },
    )
    raw_before = _raw_profiles(tmp_path)

    migrated = Profiles(_app(tmp_path, stable_key, legacy_key=legacy_key))
    profile = migrated.get_profile("Player")
    raw_after = _raw_profiles(tmp_path)

    assert profile["access_token"] == "access-token"
    assert profile["refresh_token"] == "refresh-token"
    assert raw_after["Player"]["access_token"] != raw_before["Player"]["access_token"]
    assert raw_after["Player"]["refresh_token"] != raw_before["Player"]["refresh_token"]

    reloaded_with_stable_key = Profiles(_app(tmp_path, stable_key))
    assert reloaded_with_stable_key.get_profile("Player")["refresh_token"] == "refresh-token"


def test_created_profile_becomes_default(tmp_path):
    key = Fernet.generate_key().decode()
    store = Profiles(_app(tmp_path, key))

    store.create_profile(
        "First",
        {
            "name": "First",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    )
    store.create_profile(
        "Second",
        {
            "name": "Second",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    )

    profiles = store.get_all_profiles()
    assert profiles["First"]["default"] is False
    assert profiles["Second"]["default"] is True
    assert store.get_default_profile()["name"] == "Second"


def test_single_existing_profile_is_promoted_to_default(tmp_path):
    key = Fernet.generate_key().decode()
    (tmp_path / "profiles.json").write_text(
        json.dumps(
            {
                "Player": {
                    "name": "Player",
                    "id": "uuid",
                    "access_token": "offline",
                    "refresh_token": "offline",
                    "default": False,
                }
            }
        ),
        encoding="utf-8",
    )

    store = Profiles(_app(tmp_path, key))

    assert store.get_default_profile()["name"] == "Player"


def test_deleting_default_profile_promotes_only_remaining_profile(tmp_path):
    key = Fernet.generate_key().decode()
    store = Profiles(_app(tmp_path, key))
    store.create_profile(
        "First",
        {
            "name": "First",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    )
    store.create_profile(
        "Second",
        {
            "name": "Second",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    )

    store.delete_profile("Second")

    profiles = store.get_all_profiles()
    assert profiles["First"]["default"] is True
    assert store.get_default_profile()["name"] == "First"
