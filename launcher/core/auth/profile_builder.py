from __future__ import annotations

import base64
import json
import time
from typing import Any


def decode_jwt_payload(jwt_token: str) -> dict[str, Any]:
    try:
        payload = jwt_token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


class MinecraftProfileBuilder:
    def __init__(self, http_client) -> None:
        self.http = http_client

    def build(self, client_id: str, tokens: dict[str, Any]) -> dict[str, Any]:
        ms_access_token = str(tokens["access_token"])

        xbl = self.http.post_json(
            "https://user.auth.xboxlive.com/user/authenticate",
            {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={ms_access_token}",
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
            "xbox authenticate",
        )
        xbl_token = xbl["Token"]
        user_hash = xbl["DisplayClaims"]["xui"][0]["uhs"]

        xsts = self.http.post_json(
            "https://xsts.auth.xboxlive.com/xsts/authorize",
            {
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
            "xsts authorize",
        )
        xsts_token = xsts["Token"]

        mc_auth = self.http.post_json(
            "https://api.minecraftservices.com/authentication/login_with_xbox",
            {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
            "minecraft authenticate",
        )
        mc_access_token = mc_auth.get("access_token")
        if not mc_access_token:
            raise RuntimeError(f"Minecraft auth response missing access_token: {mc_auth}")

        profile = self.http.get_json(
            "https://api.minecraftservices.com/minecraft/profile",
            headers={"Authorization": f"Bearer {mc_access_token}"},
            context="minecraft profile",
        )
        if "error" in profile and profile.get("error") == "NOT_FOUND":
            raise RuntimeError("Account does not own Minecraft")

        claims = decode_jwt_payload(str(mc_access_token))
        expires_at = claims.get("exp")
        expires_in = mc_auth.get("expires_in")
        if expires_in is not None:
            try:
                expires_at = int(time.time()) + int(expires_in)
            except (TypeError, ValueError):
                expires_at = claims.get("exp")
        return {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "access_token": mc_access_token,
            "refresh_token": tokens.get("refresh_token"),
            "xuid": claims.get("xuid"),
            "expires_at": expires_at,
            "auth_client_id": client_id,
        }

    @staticmethod
    def is_token_fresh(profile: dict[str, Any], *, leeway: int = 300) -> bool:
        exp = profile.get("expires_at")
        try:
            exp_int = int(exp)
        except (TypeError, ValueError):
            return False
        return exp_int > int(time.time()) + leeway
