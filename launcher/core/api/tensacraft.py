from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


class TensaCraftAPI:
    REQUEST_TIMEOUT = 20
    REQUEST_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 0.5
    def __init__(self, app: Any) -> None:
        self.base_url = "https://gigabait.uk/api/mods"
        self.app = app

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
        try:
            data = self._request_json(self.base_url)
            if not isinstance(data, list):
                data = []
            return [pack for pack in data if isinstance(pack, dict)]
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

    def get_version_files(
        self,
        client: str,
        *,
        endpoint: str | None = None,
    ) -> list[dict[str, Any]] | None:
        pack_id = client.strip()
        if not pack_id:
            return None
        files_url = str(endpoint or "").strip() or f"{self.base_url}/{pack_id}"

        try:
            data = self._request_json(files_url)
            if isinstance(data, dict):
                files = data.get("files") if isinstance(data.get("files"), list) else []
            elif isinstance(data, list):
                files = [item for item in data if isinstance(item, dict)]
            else:
                files = []
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
        endpoint: str | None = None,
    ) -> dict[str, Any] | None:
        pack_id = client.strip()
        if not pack_id:
            return None
        force_url = str(endpoint or "").strip() or f"{self.base_url}/{pack_id}/force-update"
        if include_directory_files:
            force_url = self._with_query(force_url, {"include_directory_files": 1})

        try:
            data = self._request_json(force_url)
            if isinstance(data, dict):
                raw_files = data.get("files")
                raw_directories = data.get("directories")
                files: list[Any] = raw_files if isinstance(raw_files, list) else []
                directories: list[Any] = raw_directories if isinstance(raw_directories, list) else []
                manifest = {
                    **data,
                    "files": [item for item in files if isinstance(item, dict)],
                    "directories": [item for item in directories if isinstance(item, dict)],
                }
            elif isinstance(data, list):
                manifest = {"files": [item for item in data if isinstance(item, dict)], "directories": []}
            else:
                manifest = {"files": [], "directories": []}
            self._validate_force_update_manifest(manifest)
            return manifest
        except (requests.RequestException, ValueError) as exc:
            self.app.log.warning(f"Error fetching Tensa force-update manifest for pack {pack_id}: {exc}")
            return None

    @classmethod
    def _validate_force_update_manifest(cls, manifest: dict[str, Any]) -> None:
        files = manifest.get("files")
        directories = manifest.get("directories")
        if not isinstance(files, list) or not isinstance(directories, list):
            raise ValueError("Tensa force-update manifest has an invalid structure")

        summary = manifest.get("summary")
        if isinstance(summary, dict):
            expected_files = cls._positive_count(summary.get("returned_files_count"))
            expected_directories = cls._positive_count(summary.get("directories_count"))
            if expected_files is not None and expected_files != len(files):
                raise ValueError("Tensa force-update manifest file count does not match its summary")
            if expected_directories is not None and expected_directories != len(directories):
                raise ValueError("Tensa force-update manifest directory count does not match its summary")

        for file_data in files:
            relative_path = cls.relative_path(file_data)
            download_url = str(file_data.get("download_url") or "").strip()
            expected_hash, _algorithm = cls.expected_hash(file_data)
            if not relative_path or not download_url or not expected_hash:
                raise ValueError(f"Incomplete Tensa force-update file entry: {relative_path or '<missing path>'}")

    @staticmethod
    def _positive_count(value: Any) -> int | None:
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        return count if count >= 0 else None

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
