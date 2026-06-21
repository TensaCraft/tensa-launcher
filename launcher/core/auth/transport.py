from __future__ import annotations

import time
from typing import Any

import requests


class MinecraftServicesUnavailable(RuntimeError):
    def __init__(self, context: str, status_code: int, payload: dict[str, Any]) -> None:
        self.context = context
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"{context} failed: HTTP {status_code} {payload}")


class AuthHttpClient:
    def __init__(
        self,
        *,
        timeout: int = 20,
        retry_attempts: int = 3,
        retry_backoff: float = 0.6,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff

    def post_form_json(self, url: str, data: dict[str, Any], context: str) -> dict[str, Any]:
        response = self.request_with_retry("post", url, context, data=data)
        return self.parse_json_response(response, context)

    def post_form_json_with_status(
        self,
        url: str,
        data: dict[str, Any],
        context: str,
    ) -> tuple[int, dict[str, Any]]:
        response = self.request_with_retry("post", url, context, data=data)
        return response.status_code, self.parse_json_payload(response, context)

    def post_json(self, url: str, data: dict[str, Any], context: str) -> dict[str, Any]:
        response = self.request_with_retry("post", url, context, json=data)
        return self.parse_json_response(response, context)

    def get_json(self, url: str, headers: dict[str, str], context: str) -> dict[str, Any]:
        response = self.request_with_retry("get", url, context, headers=headers)
        return self.parse_json_response(response, context)

    def request_with_retry(self, method: str, url: str, context: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_backoff * attempt)
                    continue
                raise RuntimeError(f"{context} request error: {exc!r}") from exc

            if response.status_code >= 500 and attempt < self.retry_attempts:
                time.sleep(self.retry_backoff * attempt)
                continue
            return response

        raise RuntimeError(f"{context} request failed: {last_error!r}")

    @staticmethod
    def parse_json_response(response: requests.Response, context: str) -> dict[str, Any]:
        payload = AuthHttpClient.parse_json_payload(response, context)
        if not response.ok:
            if context == "minecraft authenticate" and response.status_code >= 500:
                raise MinecraftServicesUnavailable(context, response.status_code, payload)
            raise RuntimeError(f"{context} failed: HTTP {response.status_code} {payload}")
        return payload

    @staticmethod
    def parse_json_payload(response: requests.Response, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        if not isinstance(payload, dict):
            raise RuntimeError(f"{context} returned unexpected payload: {payload}")
        return payload
