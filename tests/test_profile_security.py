from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from launcher.platform.security import (
    PROFILE_SECRET_FILE,
    CredentialEncryptionUnavailableError,
    SecurityService,
)
from launcher.storage.profile_store import Profiles


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)


class _EncryptFailingCipher:
    def encrypt(self, _value: bytes) -> bytes:
        raise RuntimeError("injected encryption failure")

    def decrypt(self, _value: bytes) -> bytes:
        raise RuntimeError("unexpected decrypt call")


def _app(tmp_path, key: str, legacy_key: str | None = None):
    util = SimpleNamespace(
        app_state_dir=tmp_path,
        get_user_secret=lambda: key,
        get_legacy_user_secret=lambda: legacy_key,
    )
    return SimpleNamespace(util=util, log=_Logger(), trans=lambda key, **_: key)


def _read_profiles(tmp_path) -> dict[str, dict[str, object]]:
    return json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))


def _online_profile() -> dict[str, object]:
    return {
        "name": "Player",
        "id": "uuid",
        "type": "microsoft",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "default": True,
    }


def test_encrypt_failure_never_writes_tokens_and_marks_profile_for_reauth(
    tmp_path,
    monkeypatch,
):
    store = Profiles(_app(tmp_path, Fernet.generate_key().decode()))
    monkeypatch.setattr(store, "cipher_suite", _EncryptFailingCipher())

    result = store.create_profile("Player", _online_profile())

    raw_profile = _read_profiles(tmp_path)["Player"]
    assert raw_profile["name"] == "Player"
    assert raw_profile["id"] == "uuid"
    assert raw_profile["type"] == "microsoft"
    assert raw_profile.get("access_token") is None
    assert raw_profile.get("refresh_token") is None
    assert raw_profile["reauth_required"] is True
    assert raw_profile["reauth_reason"] == "credential_encryption_unavailable"
    assert result["status"] is False


def test_decrypt_failure_never_returns_ciphertext_and_requires_reauth(tmp_path):
    original = Profiles(_app(tmp_path, Fernet.generate_key().decode()))
    original.create_profile("Player", _online_profile())
    raw_profile = _read_profiles(tmp_path)["Player"]

    reloaded = Profiles(_app(tmp_path, Fernet.generate_key().decode()))
    profile = reloaded.get_profile("Player")

    assert profile["access_token"] is None
    assert profile["refresh_token"] is None
    assert profile["access_token"] != raw_profile["access_token"]
    assert profile["refresh_token"] != raw_profile["refresh_token"]
    assert profile["reauth_required"] is True
    assert profile["reauth_reason"] == "token_decryption_failed"


def test_plaintext_migration_encrypts_tokens_and_removes_plaintext(tmp_path):
    (tmp_path / "profiles.json").write_text(
        json.dumps({"Player": _online_profile()}),
        encoding="utf-8",
    )

    store = Profiles(_app(tmp_path, Fernet.generate_key().decode()))

    raw_profile = _read_profiles(tmp_path)["Player"]
    assert raw_profile["access_token"] != "access-token"
    assert raw_profile["refresh_token"] != "refresh-token"
    assert str(raw_profile["access_token"]).startswith(Profiles.ENCRYPTED_PREFIX)
    assert str(raw_profile["refresh_token"]).startswith(Profiles.ENCRYPTED_PREFIX)
    assert store.get_profile("Player")["access_token"] == "access-token"
    assert store.get_profile("Player")["refresh_token"] == "refresh-token"


def test_plaintext_migration_failure_redacts_tokens_without_losing_profile(tmp_path, monkeypatch):
    (tmp_path / "profiles.json").write_text(
        json.dumps({"Player": _online_profile()}),
        encoding="utf-8",
    )

    def fail_encrypt(_self, _value):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(Fernet, "encrypt", fail_encrypt)

    store = Profiles(_app(tmp_path, Fernet.generate_key().decode()))

    raw_profile = _read_profiles(tmp_path)["Player"]
    profile = store.get_profile("Player")
    assert raw_profile["name"] == "Player"
    assert raw_profile["id"] == "uuid"
    assert raw_profile["type"] == "microsoft"
    assert raw_profile.get("access_token") is None
    assert raw_profile.get("refresh_token") is None
    assert raw_profile["reauth_required"] is True
    assert raw_profile["reauth_reason"] == "credential_encryption_unavailable"
    assert profile["access_token"] is None
    assert profile["refresh_token"] is None


def test_mixed_migration_uses_one_candidate_and_rolls_back_atomic_failure(
    tmp_path,
    monkeypatch,
):
    stable_key = Fernet.generate_key().decode()
    legacy_key = Fernet.generate_key().decode()
    legacy_cipher = Fernet(legacy_key.encode())
    legacy_profile = {
        **_online_profile(),
        "name": "Legacy",
        "access_token": (
            f"{Profiles.ENCRYPTED_PREFIX}"
            f"{legacy_cipher.encrypt(b'legacy-access').decode()}"
        ),
        "refresh_token": (
            f"{Profiles.ENCRYPTED_PREFIX}"
            f"{legacy_cipher.encrypt(b'legacy-refresh').decode()}"
        ),
    }
    original = {"Plaintext": _online_profile(), "Legacy": legacy_profile}
    (tmp_path / "profiles.json").write_text(json.dumps(original), encoding="utf-8")
    candidates = []

    def fail_write(_path, payload, **_kwargs):
        candidates.append(payload)
        raise OSError("injected migration save failure")

    monkeypatch.setattr(
        "launcher.storage.profile_store.atomic_write_json",
        fail_write,
    )

    store = Profiles(_app(tmp_path, stable_key, legacy_key))

    assert len(candidates) == 1
    stable_cipher = Fernet(stable_key.encode())
    for profile_key, expected_tokens in {
        "Plaintext": ("access-token", "refresh-token"),
        "Legacy": ("legacy-access", "legacy-refresh"),
    }.items():
        profile = candidates[0][profile_key]
        for token_key, expected in zip(Profiles.ENCRYPT_KEYS, expected_tokens):
            encrypted = str(profile[token_key])[len(Profiles.ENCRYPTED_PREFIX) :]
            assert stable_cipher.decrypt(encrypted.encode()).decode() == expected
    assert _read_profiles(tmp_path) == original
    assert store.profiles == original


def test_profile_key_write_failure_is_explicit_and_has_no_legacy_fallback(tmp_path, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise OSError("injected key write failure")

    monkeypatch.setattr("launcher.storage.atomic.atomic_write_text", fail_write)

    with pytest.raises(CredentialEncryptionUnavailableError):
        SecurityService().get_user_secret(tmp_path)

    assert not (tmp_path / PROFILE_SECRET_FILE).exists()


def test_profile_mutations_do_not_change_memory_when_atomic_save_fails(
    tmp_path,
    monkeypatch,
):
    store = Profiles(_app(tmp_path, Fernet.generate_key().decode()))
    assert store.create_profile("Player", _online_profile())["status"] is True
    original = store.profiles.copy()

    def fail_write(*_args, **_kwargs):
        raise OSError("injected profile save failure")

    monkeypatch.setattr(
        "launcher.storage.profile_store.atomic_write_json",
        fail_write,
    )

    assert store.create_profile(
        "Second",
        {
            "name": "Second",
            "access_token": "offline",
            "refresh_token": "offline",
        },
    ) == {"status": False, "text": "profile_save_failed"}
    assert store.edit_profile("Player", {"name": "Changed"}) == {
        "status": False,
        "text": "profile_save_failed",
    }
    assert store.delete_profile("Player") is False
    assert store.set_default_profile("Player") is False
    assert store.profiles == original
