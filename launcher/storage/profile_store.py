from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from cryptography.fernet import Fernet

from launcher.platform.security import (
    CredentialDecryptionUnavailableError,
    CredentialEncryptionUnavailableError,
)
from launcher.storage.atomic import atomic_write_json


class Profiles:
    ENCRYPT_KEYS = ["access_token", "refresh_token"]
    ENCRYPTED_PREFIX = "enc::"
    ENCRYPTION_UNAVAILABLE_REASON = "credential_encryption_unavailable"
    DECRYPTION_FAILED_REASON = "token_decryption_failed"

    def __init__(self, app, storage_dir: Path | None = None) -> None:
        self.app = app
        self.storage_path = Path(storage_dir or self.app.util.app_state_dir) / "profiles.json"
        self._lock = RLock()
        secret: str | bytes | None = None
        self.cipher_suite: Fernet | None = None
        self.encryption_error: CredentialEncryptionUnavailableError | None = None
        try:
            loaded_secret = self.app.util.get_user_secret()
            if not isinstance(loaded_secret, (str, bytes)):
                raise CredentialEncryptionUnavailableError(
                    "Profile encryption key has an unsupported type"
                )
            secret = loaded_secret
            self.cipher_suite = self._make_cipher(secret)
        except Exception as exc:
            self.encryption_error = CredentialEncryptionUnavailableError(
                "Profile credential encryption is unavailable"
            )
            self.app.log.error(
                f"Unable to initialise profile encryption: {exc.__class__.__name__}"
            )
        self.fallback_cipher_suites = self._build_fallback_ciphers(secret)
        self.profiles = self.load()
        self._migrate_profiles()

    def _make_cipher(self, secret: str | bytes) -> Fernet:
        secret_bytes = secret if isinstance(secret, bytes) else secret.encode("utf-8")
        return Fernet(secret_bytes)

    def _build_fallback_ciphers(self, primary_secret: str | bytes | None) -> list[Fernet]:
        legacy_getter = getattr(self.app.util, "get_legacy_user_secret", None)
        if not callable(legacy_getter):
            return []
        try:
            legacy_secret = legacy_getter()
        except Exception:
            return []
        if not isinstance(legacy_secret, (str, bytes)) or not legacy_secret or legacy_secret == primary_secret:
            return []
        try:
            return [self._make_cipher(legacy_secret)]
        except Exception as exc:
            self.app.log.error(f"Error initialising legacy profile encryption key: {exc.__class__.__name__}")
            return []

    def _encrypt_value(self, value: Any) -> Any:
        if value is None or value == "offline" or self._is_encrypted(value):
            return value
        if self.cipher_suite is None:
            raise self.encryption_error or CredentialEncryptionUnavailableError(
                "Profile credential encryption is unavailable"
            )
        try:
            encrypted = self.cipher_suite.encrypt(str(value).encode()).decode()
            return f"{self.ENCRYPTED_PREFIX}{encrypted}"
        except Exception as exc:
            self.app.log.error(f"Error encrypting profile credential: {exc.__class__.__name__}")
            raise CredentialEncryptionUnavailableError(
                "Profile credential encryption failed"
            ) from exc

    def _decrypt_value(self, value: Any) -> Any:
        if value is None or value == "offline":
            return value
        if not self._is_encrypted(value):
            raise CredentialDecryptionUnavailableError(
                "Plaintext profile credentials are unavailable outside migration"
            )
        decrypted, _cipher_index = self._decrypt_encrypted_value(value)
        return decrypted

    def _decrypt_encrypted_value(self, value: str) -> tuple[str, bool]:
        encrypted_value = value[len(self.ENCRYPTED_PREFIX) :].encode()
        last_exc: Exception | None = None
        ciphers = [(True, self.cipher_suite)] if self.cipher_suite is not None else []
        ciphers.extend((False, cipher) for cipher in self.fallback_cipher_suites)
        for is_primary, cipher in ciphers:
            try:
                return cipher.decrypt(encrypted_value).decode(), is_primary
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise ValueError("No profile encryption ciphers available")

    def _mark_decryption_failed(self, data: dict[str, Any]) -> None:
        data["reauth_required"] = True
        data["reauth_reason"] = self.DECRYPTION_FAILED_REASON

    def _mark_encryption_unavailable(self, data: dict[str, Any]) -> None:
        for key in self.ENCRYPT_KEYS:
            if key in data and data[key] != "offline":
                data[key] = None
        data["reauth_required"] = True
        data["reauth_reason"] = self.ENCRYPTION_UNAVAILABLE_REASON

    @classmethod
    def _is_plaintext_secret(cls, value: Any) -> bool:
        return value is not None and value != "offline" and not cls._is_encrypted(value)

    def _migrate_profiles(self) -> None:
        candidate = {key: profile.copy() for key, profile in self.profiles.items()}
        changed = False
        for profile_key, profile in candidate.items():
            if any(
                key in profile and self._is_plaintext_secret(profile[key])
                for key in self.ENCRYPT_KEYS
            ):
                profile = candidate[profile_key] = self._encrypt_data(profile)
                changed = True

            if self.fallback_cipher_suites:
                for key in self.ENCRYPT_KEYS:
                    value = profile.get(key)
                    if not self._is_encrypted(value):
                        continue
                    assert isinstance(value, str)
                    try:
                        decrypted, is_primary = self._decrypt_encrypted_value(value)
                        if not is_primary:
                            profile[key] = self._encrypt_value(decrypted)
                            changed = True
                    except Exception:
                        continue

        if len(candidate) == 1:
            profile = next(iter(candidate.values()))
            if profile.get("default") is not True:
                profile["default"] = True
                changed = True

        if changed:
            self._replace_profiles(candidate)

    def _sanitize_profile_data(self, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = data.copy()
        sanitized.pop("auth_api", None)
        return sanitized

    def _preserve_failed_token_values(
        self,
        profile_key: str,
        profile: dict[str, Any],
        new_data: dict[str, Any],
    ) -> None:
        raw_profile = self.profiles.get(profile_key, {})
        for key in self.ENCRYPT_KEYS:
            if key in new_data:
                continue
            raw_value = raw_profile.get(key)
            if profile.get(key) is None and self._is_encrypted(raw_value):
                profile[key] = raw_value

    def _clear_reauth_markers_if_resolved(self, profile: dict[str, Any]) -> None:
        if profile.get("reauth_required") is False:
            profile.pop("reauth_required", None)
            profile.pop("reauth_reason", None)
        if profile.get("reauth_reason") is None:
            profile.pop("reauth_reason", None)

    @classmethod
    def _is_encrypted(cls, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(cls.ENCRYPTED_PREFIX)

    def _encrypt_data(self, data: dict[str, Any]) -> dict[str, Any]:
        encrypted_data = self._sanitize_profile_data(data)
        self._clear_reauth_markers_if_resolved(encrypted_data)
        try:
            for key in self.ENCRYPT_KEYS:
                if key in encrypted_data and not self._is_encrypted(encrypted_data[key]):
                    encrypted_data[key] = self._encrypt_value(encrypted_data[key])
        except CredentialEncryptionUnavailableError:
            self._mark_encryption_unavailable(encrypted_data)
        return encrypted_data

    def _decrypt_data(self, data: dict[str, Any]) -> dict[str, Any]:
        decrypted_data = self._sanitize_profile_data(data)
        decrypt_failed = False
        for key in self.ENCRYPT_KEYS:
            if key in decrypted_data:
                try:
                    decrypted_data[key] = self._decrypt_value(decrypted_data[key])
                except Exception as exc:
                    self.app.log.error(
                        f"Error decrypting profile token '{key}': {exc.__class__.__name__}"
                    )
                    decrypted_data[key] = None
                    decrypt_failed = True
        if decrypt_failed:
            self._mark_decryption_failed(decrypted_data)
        return decrypted_data

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        try:
            profiles = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.app.log.error("Profiles file is corrupted; starting with empty profiles")
            return {}
        if not isinstance(profiles, dict):
            return {}
        return {
            str(key): self._sanitize_profile_data(profile)
            for key, profile in profiles.items()
            if isinstance(profile, dict)
        }

    def _replace_profiles(self, profiles: dict[str, dict[str, Any]]) -> bool:
        try:
            atomic_write_json(self.storage_path, profiles, ensure_ascii=True, indent=4)
        except OSError as exc:
            self.app.log.error(f"Unable to save profiles file '{self.storage_path}': {exc}")
            return False
        self.profiles = profiles
        return True

    def create_profile(self, profile_key: str, auth_data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not profile_key.strip():
                self.app.log.error("Profile name is empty")
                return {"status": False, "text": self.app.trans("profile_name_empty")}
            auth_data = auth_data.copy()
            if "id" not in auth_data:
                auth_data["id"] = self.generate_offline_player_uuid(auth_data["name"])
            candidate = {key: profile.copy() for key, profile in self.profiles.items()}
            for profile in candidate.values():
                profile["default"] = False
            auth_data["default"] = True
            secured_profile = self._encrypt_data(auth_data)
            candidate[profile_key] = secured_profile
            if not self._replace_profiles(candidate):
                return {"status": False, "text": self.app.trans("profile_save_failed")}
            if secured_profile.get("reauth_reason") == self.ENCRYPTION_UNAVAILABLE_REASON:
                return {"status": False, "text": self.app.trans("profile_reauth_required")}
            return {"status": True, "text": self.app.trans("profile_created")}

    def edit_profile(self, profile_key: str, new_data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if profile_key not in self.profiles:
                return {"status": False, "text": self.app.trans("profile_not_found", profile_key=profile_key)}
            if not new_data:
                return {"status": False, "text": self.app.trans("no_update_data")}
            profile = self._decrypt_data(self.profiles[profile_key])
            self._preserve_failed_token_values(profile_key, profile, new_data)
            profile.update(new_data)
            candidate = {key: value.copy() for key, value in self.profiles.items()}
            candidate[profile_key] = self._encrypt_data(profile)
            if not self._replace_profiles(candidate):
                return {"status": False, "text": self.app.trans("profile_save_failed")}
            return {"status": True, "text": self.app.trans("profile_updated", profile_key=profile_key)}

    def get_profile(self, profile_key: str) -> dict[str, Any]:
        return self._decrypt_data(self.profiles.get(profile_key, {}))

    def delete_profile(self, profile_key: str) -> bool:
        with self._lock:
            if profile_key not in self.profiles:
                return False
            candidate = {
                key: profile.copy()
                for key, profile in self.profiles.items()
                if key != profile_key
            }
            if len(candidate) == 1:
                next(iter(candidate.values()))["default"] = True
            return self._replace_profiles(candidate)

    def set_default_profile(self, default_profile_id: str) -> bool:
        with self._lock:
            if default_profile_id not in self.profiles:
                return False
            candidate = {key: profile.copy() for key, profile in self.profiles.items()}
            for profile_key, profile in candidate.items():
                profile["default"] = profile_key == default_profile_id
            return self._replace_profiles(candidate)

    def get_default_profile(self, return_key: bool = False):
        for key, profile in self.profiles.items():
            if profile.get("default"):
                decrypted_profile = self._decrypt_data(profile)
                return (key, decrypted_profile) if return_key else decrypted_profile
        return None

    def get_all_profiles(self) -> dict[str, dict[str, Any]]:
        return {key: self._decrypt_data(profile) for key, profile in self.profiles.items()}

    @staticmethod
    def generate_offline_player_uuid(player_name: str) -> str:
        offline_player_uuid = uuid.UUID(hashlib.md5(f"OfflinePlayer:{player_name}".encode("utf-8")).hexdigest())
        return str(offline_player_uuid)
