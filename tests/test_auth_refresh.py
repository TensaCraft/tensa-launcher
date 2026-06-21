from types import SimpleNamespace

import requests

from launcher.core.auth.auth import Auth


class _Logger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass


class _Config:
    def get(self, _key, default=None):
        return default

    def delete(self, _key):
        pass


class _ProfilesRepo:
    def __init__(self, profiles):
        self.profiles = profiles

    def get_profile(self, name):
        profile = self.profiles.get(name)
        return dict(profile) if profile else None

    def get_default_profile(self):
        for profile in self.profiles.values():
            if profile.get("default"):
                return dict(profile)
        return None

    def get_all_profiles(self):
        return {key: dict(value) for key, value in self.profiles.items()}

    def edit_profile(self, name, new_data):
        for key, value in new_data.items():
            if key == "reauth_required" and value is False:
                self.profiles[name].pop("reauth_required", None)
                self.profiles[name].pop("reauth_reason", None)
                continue
            if key == "reauth_reason" and value is None:
                self.profiles[name].pop("reauth_reason", None)
                continue
            self.profiles[name][key] = value
        return {"status": True}


def _auth(profiles):
    app = SimpleNamespace(
        profiles=_ProfilesRepo(profiles),
        config=_Config(),
        log=_Logger(),
        trans=lambda key, **_: key,
    )
    return Auth(app), app


def test_refresh_access_token_never_uses_encrypted_refresh_token():
    auth, app = _auth(
        {
            "Player": {
                "name": "Player",
                "type": "microsoft",
                "access_token": None,
                "refresh_token": "enc::still-encrypted",
                "default": True,
            }
        }
    )
    calls = []
    auth._refresh_tokens = lambda token: calls.append(token) or {}

    refreshed = auth.refresh_access_token(app.profiles.get_profile("Player"), force=True)

    assert calls == []
    assert refreshed["reauth_required"] is True
    assert refreshed["reauth_reason"] == "token_decryption_failed"
    assert app.profiles.profiles["Player"]["reauth_required"] is True


def test_get_default_profile_data_refreshes_expired_online_profile():
    auth, app = _auth(
        {
            "Player": {
                "name": "Player",
                "type": "microsoft",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 1,
                "default": True,
            }
        }
    )
    auth._refresh_tokens = lambda refresh_token: {
        "access_token": "new-ms-token",
        "refresh_token": "new-refresh",
    }
    auth.profile_builder = SimpleNamespace(
        is_token_fresh=lambda profile, leeway=300: False,
        build=lambda _client_id, _tokens: {
            "name": "Player",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "new-minecraft-token",
            "refresh_token": "new-refresh",
            "expires_at": 12345,
            "auth_client_id": "client",
        },
    )

    profile = auth.get_default_profile_data()

    assert profile["access_token"] == "new-minecraft-token"
    assert profile["refresh_token"] == "new-refresh"
    assert "reauth_required" not in app.profiles.profiles["Player"]


def test_get_default_profile_data_refreshes_when_access_token_is_missing():
    auth, app = _auth(
        {
            "Player": {
                "name": "Player",
                "type": "microsoft",
                "access_token": None,
                "refresh_token": "old-refresh",
                "expires_at": 9999999999,
                "default": True,
            }
        }
    )
    auth._refresh_tokens = lambda refresh_token: {
        "access_token": "new-ms-token",
        "refresh_token": "new-refresh",
    }
    auth.profile_builder = SimpleNamespace(
        is_token_fresh=lambda profile, leeway=300: True,
        build=lambda _client_id, _tokens: {
            "name": "Player",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "new-minecraft-token",
            "refresh_token": "new-refresh",
            "expires_at": 12345,
            "auth_client_id": "client",
        },
    )

    profile = auth.get_default_profile_data()

    assert profile["access_token"] == "new-minecraft-token"
    assert app.profiles.profiles["Player"]["access_token"] == "new-minecraft-token"


def test_verify_returns_false_for_reauth_required_profile(monkeypatch):
    auth, _app = _auth({})

    def fail_get(*_args, **_kwargs):
        raise AssertionError("verify must not call the network for reauth-required profiles")

    monkeypatch.setattr(requests, "get", fail_get)

    assert auth.verify({"name": "Player", "type": "microsoft", "reauth_required": True}) is False


def test_refresh_all_online_profiles_refreshes_non_default_profile():
    auth, app = _auth(
        {
            "Offline": {
                "name": "Offline",
                "type": "offline",
                "access_token": "offline",
                "refresh_token": "offline",
                "default": True,
            },
            "Online": {
                "name": "Online",
                "type": "microsoft",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 1,
                "default": False,
            },
        }
    )
    auth._refresh_tokens = lambda _refresh_token: {
        "access_token": "new-ms-token",
        "refresh_token": "new-refresh",
    }
    auth.profile_builder = SimpleNamespace(
        is_token_fresh=lambda profile, leeway=300: False,
        build=lambda _client_id, _tokens: {
            "name": "Online",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "new-minecraft-token",
            "refresh_token": "new-refresh",
            "expires_at": 12345,
            "auth_client_id": "client",
        },
    )

    auth.refresh_all_online_profiles()

    assert app.profiles.profiles["Online"]["access_token"] == "new-minecraft-token"
    assert app.profiles.profiles["Offline"]["access_token"] == "offline"
