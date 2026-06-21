from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from launcher.shared import AppContext


class TensaCraftAPI:
    REQUEST_TIMEOUT = 20
    REQUEST_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 0.5
    _packs_cache: list[dict[str, Any]] | None = None
    _packs_cache_ts = 0.0
    _packs_cache_ttl = 300.0
    _files_cache: dict[str, tuple[float, list[dict[str, Any]] | None]] = {}
    _files_cache_ttl = 60.0
    _force_update_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
    _force_update_cache_ttl = 60.0

    def __init__(self) -> None:
        self.base_url = "https://gigabait.uk/api/mods"
        self.app = AppContext.get()

    def _request_json(self, url: str) -> Any:
        last_error: requests.RequestException | None = None
        for attempt in range(1, self.REQUEST_RETRIES + 1):
            try:
                response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.REQUEST_RETRIES:
                    break
                self.app.log.warning(
                    f"Tensa API request failed, retrying "
                    f"({attempt}/{self.REQUEST_RETRIES}) {url}: {exc}"
                )
                time.sleep(self.RETRY_BACKOFF_SECONDS * attempt)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Tensa API request failed without an exception: {url}")

    @staticmethod
    def pack_id(pack: dict[str, Any]) -> str:
        client = pack.get("client") if isinstance(pack, dict) else None
        candidates = (
            (client or {}).get("id") if isinstance(client, dict) else None,
            pack.get("slug") if isinstance(pack, dict) else None,
            pack.get("name") if isinstance(pack, dict) else None,
            (client or {}).get("name") if isinstance(client, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def list_versions(self) -> list[dict[str, Any]]:
        now = time.time()
        cache = type(self)
        if cache._packs_cache is not None and (now - cache._packs_cache_ts) < cache._packs_cache_ttl:
            return cache._packs_cache
        try:
            data = self._request_json(self.base_url)
            if not isinstance(data, list):
                data = []
            cache._packs_cache = [pack for pack in data if isinstance(pack, dict)]
            cache._packs_cache_ts = now
            return cache._packs_cache
        except (requests.RequestException, ValueError) as exc:
            self.app.log.error(f"Error fetching Tensa packs: {exc}")
            return []

    def get_versions(self, client: str | None = None) -> list[str] | dict[str, Any]:
        packs = self.list_versions()
        if client is None:
            return [self.pack_id(pack) for pack in packs if self.pack_id(pack)]

        needle = client.strip().lower()
        for pack in packs:
            candidates = {
                self.pack_id(pack).lower(),
                str(pack.get("slug") or "").strip().lower(),
                str(pack.get("name") or "").strip().lower(),
                str((pack.get("client") or {}).get("id") or "").strip().lower(),
                str((pack.get("client") or {}).get("name") or "").strip().lower(),
            }
            candidates.discard("")
            if needle in candidates:
                return pack
        return {}

    def get_version_files(self, client: str) -> list[dict[str, Any]] | None:
        pack = self.get_versions(client)
        pack_id = self.pack_id(pack) if isinstance(pack, dict) else client
        if not pack_id:
            return None

        now = time.time()
        cache = type(self)
        cached = cache._files_cache.get(pack_id)
        if cached is not None:
            ts, data = cached
            if (now - ts) < cache._files_cache_ttl:
                return data

        client_data = pack.get("client") if isinstance(pack, dict) else None
        files_url = None
        if isinstance(client_data, dict):
            files_url = client_data.get("files_endpoint") or client_data.get("endpoint")
        files_url = files_url or f"{self.base_url}/{pack_id}"

        try:
            data = self._request_json(files_url)
            if isinstance(data, dict):
                files = data.get("files") if isinstance(data.get("files"), list) else []
            elif isinstance(data, list):
                files = [item for item in data if isinstance(item, dict)]
            else:
                files = []
            cache._files_cache[pack_id] = (now, files)
            return files
        except (requests.RequestException, ValueError) as exc:
            self.app.log.warning(f"Error fetching Tensa files for pack {pack_id}: {exc}")
            return None

    @staticmethod
    def _with_query(url: str, params: dict[str, Any]) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({key: str(value) for key, value in params.items()})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def get_force_update_manifest(
        self,
        client: str,
        *,
        include_directory_files: bool = True,
    ) -> dict[str, Any] | None:
        pack = self.get_versions(client)
        pack_id = self.pack_id(pack) if isinstance(pack, dict) else client
        if not pack_id:
            return None

        cache_key = f"{pack_id}:files={int(include_directory_files)}"
        now = time.time()
        cache = type(self)
        cached = cache._force_update_cache.get(cache_key)
        if cached is not None:
            ts, data = cached
            if (now - ts) < cache._force_update_cache_ttl:
                return data

        client_data = pack.get("client") if isinstance(pack, dict) else None
        force_url = None
        endpoint_sources = []
        if isinstance(client_data, dict):
            endpoint_sources.append(client_data)
        if isinstance(pack, dict):
            endpoint_sources.append(pack)
        for source in endpoint_sources:
            for key in (
                "force_update_endpoint",
                "forceUpdateEndpoint",
                "force_update_url",
                "forceUpdateUrl",
                "force-update_endpoint",
            ):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    force_url = value.strip()
                    break
            if force_url:
                break
        force_url = force_url or f"{self.base_url}/{pack_id}/force-update"
        if include_directory_files:
            force_url = self._with_query(force_url, {"include_directory_files": 1})

        try:
            data = self._request_json(force_url)
            if isinstance(data, dict):
                files = data.get("files") if isinstance(data.get("files"), list) else []
                directories = data.get("directories") if isinstance(data.get("directories"), list) else []
                manifest = {
                    **data,
                    "files": [item for item in files if isinstance(item, dict)],
                    "directories": [item for item in directories if isinstance(item, dict)],
                }
            elif isinstance(data, list):
                manifest = {"files": [item for item in data if isinstance(item, dict)], "directories": []}
            else:
                manifest = {"files": [], "directories": []}
            cache._force_update_cache[cache_key] = (now, manifest)
            return manifest
        except (requests.RequestException, ValueError) as exc:
            self.app.log.warning(f"Error fetching Tensa force-update manifest for pack {pack_id}: {exc}")
            cache._force_update_cache[cache_key] = (now, None)
            return None

    @staticmethod
    def relative_path(file_data: dict[str, Any]) -> str:
        relative = str(file_data.get("relative_path") or "").strip().replace("\\", "/")
        if relative:
            return str(PurePosixPath(relative.lstrip("/")))
        path = str(file_data.get("path") or "").strip().replace("\\", "/").strip("/")
        name = str(file_data.get("name") or "").strip()
        if path and name:
            return str(PurePosixPath(path) / name)
        return name

    @classmethod
    def is_mod_file(cls, file_data: dict[str, Any]) -> bool:
        relative = cls.relative_path(file_data).lower()
        return relative.startswith("mods/")

    @staticmethod
    def expected_hash(file_data: dict[str, Any]) -> tuple[str | None, str | None]:
        for algorithm in ("sha256", "sha1"):
            value = file_data.get(algorithm)
            if isinstance(value, str) and value.strip():
                return value.strip().lower(), algorithm
        return None, None
