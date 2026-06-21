from urllib.parse import urlparse

from launcher.core.auth.profile_builder import MinecraftProfileBuilder


class _Http:
    @staticmethod
    def _host_and_path(url):
        parsed = urlparse(url)
        return (parsed.hostname or "").lower(), parsed.path

    def post_json(self, url, *_args, **_kwargs):
        host, path = self._host_and_path(url)
        if host == "user.auth.xboxlive.com" and path == "/user/authenticate":
            return {"Token": "xbl-token", "DisplayClaims": {"xui": [{"uhs": "user-hash"}]}}
        if host == "xsts.auth.xboxlive.com" and path == "/xsts/authorize":
            return {"Token": "xsts-token"}
        if host == "api.minecraftservices.com" and path == "/authentication/login_with_xbox":
            return {"access_token": "minecraft-token-without-jwt-exp", "expires_in": 60}
        raise AssertionError(f"Unexpected POST URL: {url}")

    def get_json(self, url, *_args, **_kwargs):
        host, path = self._host_and_path(url)
        if host == "api.minecraftservices.com" and path == "/minecraft/profile":
            return {"id": "uuid", "name": "Player"}
        raise AssertionError(f"Unexpected GET URL: {url}")


def test_profile_builder_uses_minecraft_expires_in(monkeypatch):
    monkeypatch.setattr("launcher.core.auth.profile_builder.time.time", lambda: 1000)

    profile = MinecraftProfileBuilder(_Http()).build(
        "client-id",
        {"access_token": "microsoft-access", "refresh_token": "microsoft-refresh"},
    )

    assert profile["access_token"] == "minecraft-token-without-jwt-exp"
    assert profile["refresh_token"] == "microsoft-refresh"
    assert profile["expires_at"] == 1060
