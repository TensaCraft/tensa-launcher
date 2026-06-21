from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from launcher.core.auth.auth import (
    MICROSOFT_AUTHORIZE_URL,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_DEVICE_CODE_URL,
    MICROSOFT_REDIRECT_URI,
    MICROSOFT_SCOPE,
    MICROSOFT_TOKEN_URL,
    OFFICIAL_MINECRAFT_CLIENT_ID,
    Auth,
)
from launcher.core.auth.transport import MinecraftServicesUnavailable


class _Logger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, _message):
        pass


class _Config:
    def __init__(self):
        self.deleted = []

    def get(self, _key, default=None):
        return default

    def delete(self, key):
        self.deleted.append(key)


class _ProfilesRepo:
    def __init__(self, profiles):
        self.profiles = profiles

    def get_profile(self, name):
        profile = self.profiles.get(name)
        return dict(profile) if profile else None

    def edit_profile(self, name, new_data):
        self.profiles[name].update(new_data)
        if new_data.get("reauth_required") is False:
            self.profiles[name].pop("reauth_required", None)
            self.profiles[name].pop("reauth_reason", None)
        return {"status": True}


class _Http:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"access_token": "ms-access", "refresh_token": "ms-refresh"}

    def post_form_json(self, url, payload, context):
        self.calls.append((url, payload, context))
        return dict(self.response)

    def post_form_json_with_status(self, url, payload, context):
        self.calls.append((url, payload, context))
        return 200, dict(self.response)


class _Feedback:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


def _auth(profiles=None):
    feedback = _Feedback()
    app = SimpleNamespace(
        profiles=_ProfilesRepo(profiles or {}),
        config=_Config(),
        log=_Logger(),
        trans=lambda key, **_: key,
        feedback=feedback,
        page=None,
    )
    return Auth(app), app


def test_browser_authorization_url_uses_tensa_azure_app_with_pkce():
    auth, _app = _auth()

    login_url, state, code_verifier = auth._build_browser_login_request()
    parsed = urlparse(login_url)
    query = parse_qs(parsed.query)

    assert login_url.startswith(MICROSOFT_AUTHORIZE_URL)
    assert "/consumers/oauth2/v2.0/authorize" in login_url
    assert query["client_id"] == [MICROSOFT_CLIENT_ID]
    assert query["redirect_uri"] == [MICROSOFT_REDIRECT_URI]
    assert query["scope"] == [MICROSOFT_SCOPE]
    assert query["response_type"] == ["code"]
    assert query["response_mode"] == ["query"]
    assert query["prompt"] == ["select_account"]
    assert query["state"] == [state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert code_verifier
    assert OFFICIAL_MINECRAFT_CLIENT_ID not in login_url


def test_authorization_code_exchange_uses_public_client_without_secret():
    auth, _app = _auth()
    auth.http = _Http()

    tokens = auth._exchange_authorization_code("auth-code", "pkce-verifier")

    assert tokens["access_token"] == "ms-access"
    url, payload, context = auth.http.calls[-1]
    assert url == MICROSOFT_TOKEN_URL
    assert context == "microsoft authorization code exchange"
    assert payload == {
        "client_id": MICROSOFT_CLIENT_ID,
        "scope": MICROSOFT_SCOPE,
        "code": "auth-code",
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": "pkce-verifier",
    }
    assert "client_secret" not in payload


def test_token_refresh_uses_tensa_azure_app_and_consumers_endpoint():
    auth, _app = _auth()
    auth.http = _Http(response={"access_token": "new-ms-access"})

    tokens = auth._refresh_tokens("old-refresh")

    assert tokens == {"access_token": "new-ms-access", "refresh_token": "old-refresh"}
    url, payload, context = auth.http.calls[-1]
    assert url == MICROSOFT_TOKEN_URL
    assert "/consumers/oauth2/v2.0/token" in url
    assert context == "microsoft token refresh"
    assert payload == {
        "client_id": MICROSOFT_CLIENT_ID,
        "refresh_token": "old-refresh",
        "grant_type": "refresh_token",
        "scope": MICROSOFT_SCOPE,
    }
    assert "client_secret" not in payload


def test_device_code_fallback_uses_tensa_azure_app_and_consumers_endpoint():
    auth, _app = _auth()
    auth.http = _Http(response={"device_code": "device", "user_code": "ABCD"})

    auth._request_device_code_data()

    url, payload, context = auth.http.calls[-1]
    assert url == MICROSOFT_DEVICE_CODE_URL
    assert "/consumers/oauth2/v2.0/devicecode" in url
    assert context == "microsoft device code init"
    assert payload == {
        "client_id": MICROSOFT_CLIENT_ID,
        "scope": MICROSOFT_SCOPE,
    }


def test_minecraft_services_503_shows_service_unavailable_without_device_fallback():
    auth, app = _auth()
    device_fallback_calls = []
    auth._authenticate_with_device_code = lambda: device_fallback_calls.append(True)

    result = auth._run_auth_flow(
        lambda: (_ for _ in ()).throw(
            MinecraftServicesUnavailable(
                "minecraft authenticate",
                503,
                {"path": "/authentication/login_with_xbox"},
            )
        ),
        fallback_to_device=True,
    )

    assert result is None
    assert auth.last_auth_status == "minecraft_services_unavailable"
    assert device_fallback_calls == []
    assert app.feedback.warnings == ["minecraft_services_auth_unavailable"]


def test_device_code_poll_uses_standard_device_code_grant():
    auth, _app = _auth()
    auth.http = _Http(response={"access_token": "ms-access", "refresh_token": "ms-refresh"})

    tokens = auth._poll_device_code_tokens("device-code", interval=1, expires_in=30)

    assert tokens["access_token"] == "ms-access"
    url, payload, context = auth.http.calls[-1]
    assert url == MICROSOFT_TOKEN_URL
    assert context == "microsoft device code poll"
    assert payload == {
        "client_id": MICROSOFT_CLIENT_ID,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "device-code",
    }


def test_refresh_access_token_rebuilds_minecraft_profile_with_tensa_client_id():
    auth, app = _auth(
        {
            "Player": {
                "name": "Player",
                "type": "microsoft",
                "access_token": "old-minecraft-access",
                "refresh_token": "old-refresh",
                "expires_at": 1,
            }
        }
    )
    auth._refresh_tokens = lambda _refresh_token: {
        "access_token": "new-ms-access",
        "refresh_token": "new-refresh",
    }
    seen_client_ids = []
    auth.profile_builder = SimpleNamespace(
        is_token_fresh=lambda _profile, leeway=300: False,
        build=lambda client_id, _tokens: seen_client_ids.append(client_id)
        or {
            "name": "Player",
            "id": "uuid",
            "type": "microsoft",
            "access_token": "new-minecraft-access",
            "refresh_token": "new-refresh",
            "expires_at": 12345,
            "auth_client_id": client_id,
        },
    )

    refreshed = auth.refresh_access_token(app.profiles.get_profile("Player"), force=True)

    assert seen_client_ids == [MICROSOFT_CLIENT_ID]
    assert refreshed["auth_client_id"] == MICROSOFT_CLIENT_ID
    assert app.profiles.profiles["Player"]["access_token"] == "new-minecraft-access"
