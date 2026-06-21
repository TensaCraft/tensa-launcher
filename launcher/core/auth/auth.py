from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
import urllib.parse as urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

import requests

from .device_ui import DeviceCodeUI
from .profile_builder import MinecraftProfileBuilder
from .transport import AuthHttpClient, MinecraftServicesUnavailable

OFFICIAL_MINECRAFT_CLIENT_ID = "00000000402b5328"
MICROSOFT_CLIENT_ID = "7181ef73-10e0-4354-9984-8b8343f1513d"
MICROSOFT_REDIRECT_URI = "http://localhost:8080/callback"
MICROSOFT_AUTHORIZE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MICROSOFT_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MICROSOFT_SCOPE = "XboxLive.signin offline_access"
MICROSOFT_AUTH_PROMPT = "select_account"
MICROSOFT_DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_REFRESH_LEEWAY_SEC = 300
AUTH_CODE_TIMEOUT_SEC = 180


def _is_same_or_subdomain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _base64url_no_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _base64url_no_padding(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return code_verifier, code_challenge


class AuthFlowCancelled(Exception):
    pass


class AuthFlowTimeout(Exception):
    pass


class AuthFlowDenied(Exception):
    pass


class AuthFlowUnavailable(Exception):
    pass


class OAuthLoopbackReceiver:
    def __init__(
        self,
        *,
        redirect_uri: str,
        expected_state: str,
        success_title: str,
        success_message: str,
        error_title: str,
    ) -> None:
        parsed = urlparse.urlparse(redirect_uri)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        self.expected_state = expected_state
        self.success_title = success_title
        self.success_message = success_message
        self.error_title = error_title
        self.result: dict[str, str] = {}
        self._event = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed_path = urlparse.urlparse(self.path)
                params = urlparse.parse_qs(parsed_path.query)
                if parsed_path.path != receiver.path:
                    self.send_error(404)
                    return

                receiver.result = {
                    "code": str((params.get("code") or [""])[0]),
                    "state": str((params.get("state") or [""])[0]),
                    "error": str((params.get("error") or [""])[0]),
                    "error_description": str((params.get("error_description") or [""])[0]),
                }
                state_valid = receiver.result["state"] == receiver.expected_state
                if not state_valid and not receiver.result["error"]:
                    receiver.result["error"] = "invalid_state"

                is_success = bool(receiver.result["code"]) and state_valid and not receiver.result["error"]
                title = receiver.success_title if is_success else receiver.error_title
                message = receiver.success_message if is_success else receiver.result.get("error_description") or ""
                body = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>{html.escape(title)}</title></head>"
                    "<body style='font-family:Segoe UI,Arial,sans-serif;background:#07150f;color:#e7fff6;"
                    "display:grid;place-items:center;min-height:100vh;margin:0'>"
                    "<main style='max-width:560px;padding:32px;text-align:center'>"
                    f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
                    "</main></body></html>"
                ).encode("utf-8")

                self.send_response(200 if is_success else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                receiver._event.set()

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait_for_code(self, timeout: int) -> str:
        if not self._event.wait(timeout):
            raise AuthFlowTimeout("Microsoft authorization timed out")
        error = str(self.result.get("error") or "").strip()
        if error in {"access_denied", "authorization_declined"}:
            raise AuthFlowDenied()
        if error:
            description = self.result.get("error_description") or error
            raise RuntimeError(f"Microsoft authorization failed: {description}")
        code = str(self.result.get("code") or "").strip()
        if not code:
            raise RuntimeError("Microsoft authorization callback did not include an authorization code")
        return code

    def close(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()


class Auth:
    def __init__(self, app) -> None:
        self.app = app
        self.client_id: str = MICROSOFT_CLIENT_ID
        self.redirect_uri: str = MICROSOFT_REDIRECT_URI
        self.last_auth_status: Optional[str] = None
        self.http = AuthHttpClient()
        self.profile_builder = MinecraftProfileBuilder(self.http)
        self.device_ui = DeviceCodeUI(app)
        self._auth_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def authenticate(self):
        return self._run_auth_flow(self._authenticate_with_browser_code, fallback_to_device=True)

    def authenticate_with_device_code(self):
        return self._run_auth_flow(self._authenticate_with_device_code, fallback_to_device=False)

    def _run_auth_flow(self, auth_flow, *, fallback_to_device: bool):
        if not self._auth_lock.acquire(blocking=False):
            self.app.log.warning("Microsoft auth is already in progress")
            self.last_auth_status = "busy"
            return None

        try:
            self._enforce_auth_config()
            try:
                return auth_flow()
            except AuthFlowUnavailable as exc:
                if not fallback_to_device:
                    raise
                self.app.log.warning(f"Microsoft browser auth unavailable, falling back to device code: {exc!r}")
                self._feedback_warning("microsoft_auth_browser_fallback")
                return self._authenticate_with_device_code()
        except AuthFlowTimeout:
            self.last_auth_status = "timeout"
            self._feedback_warning("microsoft_auth_timeout")
            return None
        except AuthFlowCancelled:
            self.last_auth_status = "cancelled"
            self._feedback_warning("microsoft_auth_cancelled")
            return None
        except AuthFlowDenied:
            self.last_auth_status = "denied"
            self._feedback_warning("microsoft_auth_denied")
            return None
        except MinecraftServicesUnavailable as exc:
            self.last_auth_status = "minecraft_services_unavailable"
            self.app.log.warning(f"Minecraft authentication service is unavailable: {exc}")
            self._feedback_warning("minecraft_services_auth_unavailable")
            return None
        except Exception as exc:  # pragma: no cover - network dependent
            self.last_auth_status = "error"
            self.app.log.error(f"Error completing Microsoft auth: {exc}")
            self._feedback_warning("microsoft_auth_failed", suffix=str(exc))
            return None
        finally:
            self._auth_lock.release()

    def _authenticate_with_browser_code(self):
        self.last_auth_status = "browser_code_started"
        login_url, _state, code_verifier = self._build_browser_login_request()
        receiver = OAuthLoopbackReceiver(
            redirect_uri=self.redirect_uri,
            expected_state=_state,
            success_title=self.app.trans("auth_success"),
            success_message=self.app.trans("microsoft_auth_browser_success"),
            error_title=self.app.trans("auth_error"),
        )
        try:
            try:
                receiver.start()
            except OSError as exc:
                raise AuthFlowUnavailable(f"Unable to listen on {self.redirect_uri}: {exc!r}") from exc

            self._feedback_info("microsoft_auth_browser_opening")
            if not self.device_ui.open_url(login_url):
                raise AuthFlowUnavailable("Unable to open browser for Microsoft authorization")

            auth_code = receiver.wait_for_code(AUTH_CODE_TIMEOUT_SEC)
            tokens = self._exchange_authorization_code(auth_code, code_verifier)
            login_data = self.profile_builder.build(self.client_id, tokens)
            self.app.profiles.create_profile(login_data.get("name"), login_data)
            self.last_auth_status = "ok"
            return login_data
        finally:
            receiver.close()

    def _authenticate_with_device_code(self):
        cancel_event = threading.Event()
        device_dialog: Optional[Any] = None
        try:
            self.last_auth_status = "device_code_started"

            device_data = self._request_device_code_data()
            user_code = str(device_data.get("user_code") or "").strip()
            device_code = str(device_data.get("device_code") or "").strip()
            verify_url = str(device_data.get("verification_uri") or "https://www.microsoft.com/link").strip()
            verify_url_open = self._build_device_code_open_url(
                verify_url=verify_url,
                user_code=user_code,
                device_data=device_data,
            )
            interval = int(device_data.get("interval") or 5)
            expires_in = int(device_data.get("expires_in") or 900)

            if not user_code or not device_code:
                raise RuntimeError(f"Device code response is incomplete: {device_data}")

            device_dialog = self.device_ui.open_dialog(
                user_code=user_code,
                verify_url=verify_url_open,
                cancel_event=cancel_event,
            )
            self.device_ui.open_url(verify_url_open)
            if device_dialog is None:
                self.app.feedback.warning(
                    self.app.trans("microsoft_auth_device_code_instructions", code=user_code, url=verify_url),
                )

            tokens = self._poll_device_code_tokens(
                device_code,
                interval=interval,
                expires_in=expires_in,
                cancel_event=cancel_event,
            )
            login_data = self.profile_builder.build(self.client_id, tokens)
            self.app.profiles.create_profile(login_data.get("name"), login_data)
            self.last_auth_status = "ok"
            return login_data
        finally:
            self.device_ui.close_dialog(device_dialog)

    def refresh_access_token(self, profile, *, force: bool = False):
        profile_key = profile.get("name")
        if self._is_offline_profile(profile):
            return profile

        if self.profile_requires_reauth(profile):
            return self._mark_profile_reauth(profile, self._reauth_reason(profile))

        if (
            not force
            and self._has_usable_access_token(profile)
            and self.profile_builder.is_token_fresh(profile, leeway=TOKEN_REFRESH_LEEWAY_SEC)
        ):
            return profile

        self._enforce_auth_config()
        refresh_token = profile.get("refresh_token")
        if not refresh_token:
            return self._mark_profile_reauth(profile, "refresh_token_missing")

        with self._refresh_lock:
            latest_profile = profile
            if profile_key:
                try:
                    latest_profile = self.app.profiles.get_profile(profile_key) or profile
                except Exception:
                    latest_profile = profile

            if (
                not force
                and self._has_usable_access_token(latest_profile)
                and self.profile_builder.is_token_fresh(latest_profile, leeway=TOKEN_REFRESH_LEEWAY_SEC)
            ):
                return latest_profile

            if self.profile_requires_reauth(latest_profile):
                return self._mark_profile_reauth(latest_profile, self._reauth_reason(latest_profile))

            refresh_token = latest_profile.get("refresh_token") or refresh_token
            if not refresh_token or refresh_token == "offline":
                return self._mark_profile_reauth(latest_profile, "refresh_token_missing")

            try:
                tokens = self._refresh_tokens(refresh_token)
                login_data = self.profile_builder.build(self.client_id, tokens)
                login_data["reauth_required"] = False
                login_data["reauth_reason"] = None
                if profile_key:
                    self.app.profiles.edit_profile(profile_key, login_data)
                    return self.app.profiles.get_profile(profile_key)
                return login_data
            except Exception as exc:  # pragma: no cover - network dependent
                self.app.log.error(f"Error refreshing access token: {exc!r}")
                if self._is_refresh_token_rejected(exc):
                    return self._mark_profile_reauth(latest_profile, "refresh_token_invalid")
                return latest_profile

    def get_default_profile_data(self):
        try:
            profile = self.app.profiles.get_default_profile()
            if profile:
                profile = self.ensure_profile_authorized(profile)
            return profile
        except Exception as exc:
            self.app.log.error(f"Error getting default profile data: {exc}")
            return None

    def get_profile_data(self, profile_key: str):
        try:
            profile = self.app.profiles.get_profile(profile_key)
            if profile:
                profile = self.ensure_profile_authorized(profile)
            return profile
        except Exception as exc:
            self.app.log.error(f"Error getting profile data for {profile_key}: {exc}")
            return None

    def ensure_profile_authorized(self, profile):
        if not profile or self._is_offline_profile(profile):
            return profile
        if self.profile_requires_reauth(profile):
            return self._mark_profile_reauth(profile, self._reauth_reason(profile))
        if self._has_usable_access_token(profile) and self.profile_builder.is_token_fresh(
            profile,
            leeway=TOKEN_REFRESH_LEEWAY_SEC,
        ):
            return profile
        return self.refresh_access_token(profile, force=False)

    def refresh_all_online_profiles(self) -> None:
        try:
            profiles = self.app.profiles.get_all_profiles()
        except Exception as exc:
            self.app.log.error(f"Error loading profiles for auth refresh: {exc}")
            return
        for profile in profiles.values():
            if not profile or self._is_offline_profile(profile):
                continue
            try:
                self.ensure_profile_authorized(profile)
            except Exception as exc:
                self.app.log.error(
                    f"Error refreshing profile auth for {profile.get('name')}: {exc!r}"
                )

    def verify(self, profile) -> bool:
        if self.profile_requires_reauth(profile):
            return False
        access_token = profile.get("access_token")
        if not access_token:
            return False
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(
                "https://api.minecraftservices.com/minecraft/profile",
                headers=headers,
                timeout=5,
            )
            if response.status_code == 401:
                profile = self.refresh_access_token(profile, force=True)
                refreshed_token = profile.get("access_token")
                if not refreshed_token:
                    return False
                headers = {"Authorization": f"Bearer {refreshed_token}"}
                response = requests.get(
                    "https://api.minecraftservices.com/minecraft/profile",
                    headers=headers,
                    timeout=5,
                )
            return response.status_code == 200
        except requests.RequestException as exc:  # pragma: no cover - network dependent
            self.app.log.error(f"Error checking Microsoft auth: {exc}")
            return False

    def profile_requires_reauth(self, profile) -> bool:
        if not profile or self._is_offline_profile(profile):
            return False
        if profile.get("reauth_required"):
            return True
        refresh_token = profile.get("refresh_token")
        access_token = profile.get("access_token")
        if self._is_encrypted_token(refresh_token) or self._is_encrypted_token(access_token):
            return True
        if not refresh_token:
            return True
        return False

    @staticmethod
    def _is_offline_profile(profile) -> bool:
        return (
            profile.get("type") == "offline"
            or profile.get("access_token") == "offline"
            or profile.get("refresh_token") == "offline"
        )

    @staticmethod
    def _is_encrypted_token(value: object) -> bool:
        return isinstance(value, str) and value.startswith("enc::")

    def _has_usable_access_token(self, profile) -> bool:
        access_token = profile.get("access_token")
        return bool(access_token) and not self._is_encrypted_token(access_token)

    def _reauth_reason(self, profile) -> str:
        reason = str(profile.get("reauth_reason") or "").strip()
        if reason:
            return reason
        if self._is_encrypted_token(profile.get("refresh_token")) or self._is_encrypted_token(
            profile.get("access_token")
        ):
            return "token_decryption_failed"
        if not profile.get("refresh_token"):
            return "refresh_token_missing"
        return "reauth_required"

    def _mark_profile_reauth(self, profile, reason: str):
        marked = dict(profile)
        marked["reauth_required"] = True
        marked["reauth_reason"] = reason
        profile_key = marked.get("name")
        if profile_key:
            try:
                self.app.profiles.edit_profile(
                    profile_key,
                    {
                        "reauth_required": True,
                        "reauth_reason": reason,
                    },
                )
                return self.app.profiles.get_profile(profile_key) or marked
            except Exception:
                return marked
        return marked

    @staticmethod
    def _is_refresh_token_rejected(exc: Exception) -> bool:
        message = str(exc).lower()
        return "invalid_grant" in message or "expired" in message or "revoked" in message

    def _request_device_code_data(self) -> Dict[str, Any]:
        payload = {
            "client_id": self.client_id,
            "scope": MICROSOFT_SCOPE,
        }
        return self.http.post_form_json(MICROSOFT_DEVICE_CODE_URL, payload, "microsoft device code init")

    def _poll_device_code_tokens(
        self,
        device_code: str,
        *,
        interval: int,
        expires_in: int,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        poll_interval = max(1, int(interval))
        deadline = time.time() + max(30, int(expires_in))

        while time.time() < deadline:
            if cancel_event and cancel_event.is_set():
                raise AuthFlowCancelled()

            status_code, payload = self.http.post_form_json_with_status(
                MICROSOFT_TOKEN_URL,
                {
                    "client_id": self.client_id,
                    "grant_type": MICROSOFT_DEVICE_CODE_GRANT,
                    "device_code": device_code,
                },
                "microsoft device code poll",
            )

            if status_code == 200 and payload.get("access_token"):
                if "refresh_token" not in payload:
                    payload["refresh_token"] = None
                return payload

            error = str(payload.get("error") or "").strip().lower()
            if error == "authorization_pending":
                time.sleep(poll_interval)
                continue
            if error == "slow_down":
                poll_interval += 2
                time.sleep(poll_interval)
                continue
            if error in {"authorization_declined", "access_denied"}:
                raise AuthFlowDenied()
            if error in {"expired_token", "bad_verification_code", "code_expired"}:
                raise RuntimeError("Device code expired before confirmation")

            raise RuntimeError(f"Device code polling failed: HTTP {status_code} {payload}")

        raise RuntimeError("Device code authorization timed out")

    @staticmethod
    def _build_device_code_open_url(
        *,
        verify_url: str,
        user_code: str,
        device_data: Dict[str, Any],
    ) -> str:
        complete = str(device_data.get("verification_uri_complete") or "").strip()
        if complete:
            return complete

        parsed = urlparse.urlparse(verify_url or "")
        host = parsed.netloc.lower().rstrip(".")
        path = parsed.path.lower().rstrip("/")
        if _is_same_or_subdomain(host, "microsoft.com") and path == "/link" and user_code:
            qs = dict(urlparse.parse_qsl(parsed.query, keep_blank_values=True))
            qs.setdefault("otc", user_code)
            return urlparse.urlunparse(parsed._replace(query=urlparse.urlencode(qs)))
        return verify_url

    def _build_browser_login_request(self) -> tuple[str, str, str]:
        self._enforce_auth_config()
        state = secrets.token_urlsafe(32)
        code_verifier, code_challenge = _generate_pkce_pair()
        payload = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": MICROSOFT_SCOPE,
            "prompt": MICROSOFT_AUTH_PROMPT,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        login_url = urlparse.urlparse(MICROSOFT_AUTHORIZE_URL)._replace(
            query=urlparse.urlencode(payload)
        ).geturl()
        return login_url, state, code_verifier

    def _exchange_authorization_code(self, auth_code: str, code_verifier: str) -> Dict[str, Any]:
        payload = {
            "client_id": self.client_id,
            "scope": MICROSOFT_SCOPE,
            "code": auth_code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        tokens = self.http.post_form_json(
            MICROSOFT_TOKEN_URL,
            payload,
            "microsoft authorization code exchange",
        )
        if "access_token" not in tokens:
            raise RuntimeError(f"Authorization response missing access_token: {tokens}")
        if "refresh_token" not in tokens:
            tokens["refresh_token"] = None
        return tokens

    def _refresh_tokens(self, refresh_token: str) -> Dict[str, Any]:
        payload = {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": MICROSOFT_SCOPE,
        }
        tokens = self.http.post_form_json(MICROSOFT_TOKEN_URL, payload, "microsoft token refresh")
        if "access_token" not in tokens:
            raise RuntimeError(f"Refresh response missing access_token: {tokens}")
        if "refresh_token" not in tokens:
            tokens["refresh_token"] = refresh_token
        return tokens

    def _enforce_auth_config(self) -> None:
        self.client_id = MICROSOFT_CLIENT_ID
        self.redirect_uri = MICROSOFT_REDIRECT_URI

        cfg = getattr(self.app, "config", None)
        if not cfg:
            return
        for key in ("microsoft_client_id", "microsoft_redirect_url", "microsoft_client_secret", "auth_prompt_mode"):
            try:
                if cfg.get(key) is not None:
                    cfg.delete(key)
            except Exception as exc:
                self.app.log.debug(f"Failed to delete legacy auth config key {key}: {exc!r}")

    def _enforce_official_auth_config(self) -> None:
        self._enforce_auth_config()

    def _feedback_info(self, key: str) -> None:
        feedback = getattr(self.app, "feedback", None)
        if feedback is not None and hasattr(feedback, "info"):
            feedback.info(self.app.trans(key))

    def _feedback_warning(self, key: str, *, suffix: str | None = None) -> None:
        feedback = getattr(self.app, "feedback", None)
        if feedback is None or not hasattr(feedback, "warning"):
            return
        message = self.app.trans(key)
        if suffix:
            message = f"{message}: {suffix}"
        feedback.warning(message)


__all__ = [
    "Auth",
    "MICROSOFT_AUTHORIZE_URL",
    "MICROSOFT_CLIENT_ID",
    "MICROSOFT_DEVICE_CODE_URL",
    "MICROSOFT_REDIRECT_URI",
    "MICROSOFT_SCOPE",
    "MICROSOFT_TOKEN_URL",
    "OFFICIAL_MINECRAFT_CLIENT_ID",
]
