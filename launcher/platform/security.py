from __future__ import annotations

import base64
import hashlib
import os
import random
import re
import string
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Optional

import requests
from requests import Response
from transliterate import translit
from transliterate.exceptions import LanguageDetectionError

SECRET_ENDPOINT_TEMPLATE = "https://gigabait.uk/api/mods/launcher/secret/{secret}"
PROFILE_SECRET_FILE = "profile-token.key"


class SecurityService:
    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None

    def get_user_secret(self, storage_dir: str | Path | None = None) -> str:
        if storage_dir is None:
            return self.get_legacy_user_secret()

        secret_path = Path(storage_dir) / PROFILE_SECRET_FILE
        try:
            if secret_path.exists():
                key = secret_path.read_text(encoding="utf-8").strip()
                if self._is_fernet_key(key):
                    return key

            key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            secret_path.write_text(key, encoding="utf-8")
            with suppress(OSError):
                secret_path.chmod(0o600)
            return key
        except OSError:
            return self.get_legacy_user_secret()

    def get_legacy_user_secret(self) -> str:
        mac_address = ":".join(("%012X" % uuid.getnode())[i : i + 2] for i in range(0, 12, 2))
        key = hashlib.sha256(mac_address.replace(":", "").encode()).digest()
        return base64.urlsafe_b64encode(key).decode("ascii")

    def get_client_secret(self) -> Optional[dict[str, str]]:
        if self._client_id and self._client_secret:
            return {"id": self._client_id, "secret": self._client_secret}

        try:
            response = requests.get(
                SECRET_ENDPOINT_TEMPLATE.format(secret=self.get_legacy_user_secret()),
                timeout=5,
            )
        except requests.RequestException:
            return None

        data = self._parse_json(response)
        if data:
            self._client_id = data.get("id")
            self._client_secret = data.get("secret")
        return data

    @staticmethod
    def normalize_string(text: object) -> str:
        raw_text = str(text)
        transliterated = None
        for lang in ("uk", "ru", "bg", "sr", "mk"):
            try:
                transliterated = translit(raw_text, lang, reversed=True)
                break
            except LanguageDetectionError:
                continue
        if transliterated is None:
            return "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        normalized = re.sub(r"[^a-zA-Z0-9]", "_", transliterated).lower()
        return normalized or "".join(random.choices(string.ascii_lowercase + string.digits, k=10))

    @staticmethod
    def _parse_json(response: Response) -> Optional[dict[str, str]]:
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _is_fernet_key(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32
        except Exception:
            return False
