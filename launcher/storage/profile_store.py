from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


class Profiles:
    ENCRYPT_KEYS = ["access_token", "refresh_token"]
    ENCRYPTED_PREFIX = "enc::"

    def __init__(self, app, storage_dir: Path | None = None) -> None:
        self.app = app
        self.storage_path = Path(storage_dir or self.app.util.app_state_dir) / "profiles.json"
        secret = self.app.util.get_user_secret()
        self.cipher_suite = self._make_cipher(secret)
        self.fallback_cipher_suites = self._build_fallback_ciphers(secret)
        self.profiles = self.load()
        self._migrate_legacy_encryption()
        if self._ensure_single_profile_default():
            self.save_profiles()

    def _make_cipher(self, secret: str | bytes) -> Fernet:
        secret_bytes = secret if isinstance(secret, bytes) else secret.encode("utf-8")
        return Fernet(secret_bytes)

    def _build_fallback_ciphers(self, primary_secret: str | bytes) -> list[Fernet]:
        legacy_getter = getattr(self.app.util, "get_legacy_user_secret", None)
        if not callable(legacy_getter):
            return []
        try:
            legacy_secret = legacy_getter()
        except Exception:
            return []
        if not legacy_secret or legacy_secret == primary_secret:
            return []
        try:
            return [self._make_cipher(legacy_secret)]
        except Exception as exc:
            self.app.log.error(f"Error initialising legacy profile encryption key: {exc.__class__.__name__}")
            return []

    def _encrypt_value(self, value: Any) -> Any:
        if value is None or value == "offline" or self._is_encrypted(value):
            return value
        try:
            encrypted = self.cipher_suite.encrypt(str(value).encode()).decode()
            return f"{self.ENCRYPTED_PREFIX}{encrypted}"
        except Exception as exc:
            self.app.log.error(f"Error encrypting value: {exc}")
            return value

    def _decrypt_value(self, value: Any) -> Any:
        if value == "offline" or not self._is_encrypted(value):
            return value
        decrypted, _cipher_index = self._decrypt_encrypted_value(value)
        return decrypted

    def _decrypt_encrypted_value(self, value: str) -> tuple[str, int]:
        encrypted_value = value[len(self.ENCRYPTED_PREFIX) :].encode()
        last_exc: Exception | None = None
        for cipher_index, cipher in enumerate([self.cipher_suite, *self.fallback_cipher_suites]):
            try:
                return cipher.decrypt(encrypted_value).decode(), cipher_index
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise ValueError("No profile encryption ciphers available")

    def _mark_decryption_failed(self, data: dict[str, Any]) -> None:
        data["reauth_required"] = True
        data.setdefault("reauth_reason", "token_decryption_failed")

    def _migrate_legacy_encryption(self) -> None:
        if not self.fallback_cipher_suites:
            return
        changed = False
        for profile in self.profiles.values():
            for key in self.ENCRYPT_KEYS:
                value = profile.get(key)
                if not self._is_encrypted(value):
                    continue
                try:
                    decrypted, cipher_index = self._decrypt_encrypted_value(value)
                except Exception:
                    continue
                if cipher_index > 0:
                    profile[key] = decrypted
                    changed = True
        if changed:
            self.save_profiles()

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

    def _ensure_single_profile_default(self) -> bool:
        if len(self.profiles) != 1:
            return False
        _profile_key, profile = next(iter(self.profiles.items()))
        if profile.get("default") is True:
            return False
        profile["default"] = True
        return True

    @classmethod
    def _is_encrypted(cls, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(cls.ENCRYPTED_PREFIX)

    def _encrypt_data(self, data: dict[str, Any]) -> dict[str, Any]:
        encrypted_data = self._sanitize_profile_data(data)
        self._clear_reauth_markers_if_resolved(encrypted_data)
        for key in self.ENCRYPT_KEYS:
            if key in encrypted_data and not self._is_encrypted(encrypted_data[key]):
                encrypted_data[key] = self._encrypt_value(encrypted_data[key])
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

    def __getitem__(self, profile_key: str) -> dict[str, Any]:
        return self._decrypt_data(self.profiles.get(profile_key, {}))

    def __setitem__(self, profile_key: str, profile_data: dict[str, Any]) -> None:
        self.profiles[profile_key] = self._encrypt_data(profile_data)
        self.save_profiles()

    def encrypt_all_profiles(self) -> None:
        for profile_key, profile_data in self.profiles.items():
            self.profiles[profile_key] = self._encrypt_data(profile_data)
        self.save_profiles()

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        try:
            profiles = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.app.log.error("Profiles file is corrupted; starting with empty profiles")
            return {}
        if not isinstance(profiles, dict):
            return {}
        return {
            str(key): self._sanitize_profile_data(profile)
            for key, profile in profiles.items()
            if isinstance(profile, dict)
        }

    def save_profiles(self) -> None:
        encrypted_profiles = {key: self._encrypt_data(profile) for key, profile in self.profiles.items()}
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps(encrypted_profiles, indent=4), encoding="utf-8")
        except OSError as exc:
            self.app.log.error(f"Unable to save profiles file '{self.storage_path}': {exc}")

    def create_profile(self, profile_key: str, auth_data: dict[str, Any]) -> dict[str, Any]:
        if not profile_key.strip():
            self.app.log.error("Profile name is empty")
            return {"status": False, "text": self.app.trans("profile_name_empty")}
        auth_data = auth_data.copy()
        if "id" not in auth_data:
            auth_data["id"] = self.generate_offline_player_uuid(auth_data["name"])
        for profile in self.profiles.values():
            profile["default"] = False
        auth_data["default"] = True
        self[profile_key] = auth_data
        return {"status": True, "text": self.app.trans("profile_created")}

    def edit_profile(self, profile_key: str, new_data: dict[str, Any]) -> dict[str, Any]:
        if profile_key not in self.profiles:
            return {"status": False, "text": self.app.trans("profile_not_found", profile_key=profile_key)}
        if not new_data:
            return {"status": False, "text": self.app.trans("no_update_data")}
        profile = self[profile_key]
        self._preserve_failed_token_values(profile_key, profile, new_data)
        profile.update(new_data)
        self[profile_key] = profile
        return {"status": True, "text": self.app.trans("profile_updated", profile_key=profile_key)}

    def get_profile(self, profile_key: str) -> dict[str, Any]:
        return self[profile_key]

    def delete_profile(self, profile_key: str) -> None:
        if profile_key in self.profiles:
            was_default = self.profiles[profile_key].get("default") is True
            del self.profiles[profile_key]
            if was_default:
                self._ensure_single_profile_default()
            self.save_profiles()

    def set_default_profile(self, default_profile_id: str) -> None:
        for profile_key, profile in self.profiles.items():
            profile["default"] = profile_key == default_profile_id
        self.save_profiles()

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
