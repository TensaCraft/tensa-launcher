from __future__ import annotations

from typing import Any


class TensaCraftPayloadService:
    _FORCE_UPDATE_ENDPOINT_KEYS = (
        "force_update_endpoint",
        "forceUpdateEndpoint",
        "force_update_url",
        "forceUpdateUrl",
        "force-update_endpoint",
    )
    _TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}
    _PROFILE_FIELD_ALIASES = {
        "minecraft": "minecraft_version",
        "minecraftversion": "minecraft_version",
        "minecraft_version": "minecraft_version",
        "version": "minecraft_version",
        "loader": "loader_id",
        "loaderid": "loader_id",
        "loader_id": "loader_id",
        "loaderversion": "loader_version",
        "loader_version": "loader_version",
        "server": "server",
        "serverhost": "server_host",
        "server_host": "server_host",
        "serverport": "server_port",
        "server_port": "server_port",
        "gpu": "gpu_preference",
        "gpumode": "gpu_preference",
        "gpu_mode": "gpu_preference",
        "gpupreference": "gpu_preference",
        "gpu_preference": "gpu_preference",
        "image": "image",
        "icon": "image",
        "jvm": "jvm_arguments",
        "jvmarguments": "jvm_arguments",
        "jvm_arguments": "jvm_arguments",
    }
    _GPU_MODE_MAP = {
        "discrete": "dgpu",
        "integrated": "igpu",
        "auto": "auto",
        "dgpu": "dgpu",
        "igpu": "igpu",
    }

    @staticmethod
    def _current_loader_key(version) -> str | None:
        loader = str(getattr(version, "loader", "") or "").lower()
        if loader.startswith("fabric-loader-"):
            return "fabric"
        if loader.startswith("quilt-loader-"):
            return "quilt"
        if loader.startswith("neoforge-"):
            return "neoforge"
        if "-forge-" in loader or loader.startswith("forge-"):
            return "forge"

        client = str(getattr(version, "client", "") or "").lower()
        if client in {"minecraft", "fabric", "forge", "neoforge", "quilt"}:
            return client

        return loader or None

    @staticmethod
    def _loader_name(client_data: dict[str, Any]) -> str | None:
        return str(client_data.get("loader_id") or client_data.get("loader") or "").strip().lower() or None

    @staticmethod
    def _minecraft_version(client_data: dict[str, Any]) -> str | None:
        return str(client_data.get("minecraft_version") or client_data.get("version") or "").strip() or None

    @classmethod
    def _profile_fields(cls, client_data: dict[str, Any]) -> set[str]:
        raw_fields = client_data.get("force_update_profile_fields")
        if not isinstance(raw_fields, list):
            return set()

        fields: set[str] = set()
        for raw_field in raw_fields:
            key = str(raw_field or "").strip().lower().replace("-", "_").replace(".", "_")
            if not key:
                continue
            normalized = cls._PROFILE_FIELD_ALIASES.get(key)
            if normalized:
                fields.add(normalized)
        return fields

    @staticmethod
    def _server_port(value: Any, fallback: int = 25565) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError):
            return fallback
        return port if 1 <= port <= 65535 else fallback

    @classmethod
    def _gpu_mode(cls, value: Any) -> str | None:
        text = str(value or "").strip().lower()
        if not text:
            return None
        return cls._GPU_MODE_MAP.get(text, text)

    @classmethod
    def _sync_enabled(cls, client_data: dict[str, Any]) -> bool:
        if "force_update" in client_data:
            value = client_data.get("force_update")
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in cls._TRUTHY_VALUES
            return bool(value)
        return any(bool(str(client_data.get(key) or "").strip()) for key in cls._FORCE_UPDATE_ENDPOINT_KEYS)

    @staticmethod
    def get_client_data(payload: dict[str, Any] | None, version_key: str) -> dict[str, Any]:
        if not payload:
            raise ValueError(f"No version data found for the specified version: {version_key}")

        client_data = payload.get("client")
        if not client_data:
            raise ValueError(f"'client' field missing in API response for version {version_key}")
        return client_data

    def apply_install_payload(self, version, *, version_key: str, client_data: dict[str, Any]) -> str:
        loader_name = self._loader_name(client_data)
        mc_version = self._minecraft_version(client_data)
        if mc_version is None:
            raise ValueError(f"'minecraft_version' field missing in client data for {version_key}")
        if not loader_name:
            raise ValueError(f"'loader_id' field missing in client data for {version_key}")

        version.id = version_key
        version.version = mc_version
        version.loader_version = client_data.get("loader_version")
        version.force_update = self._sync_enabled(client_data)
        version.image = client_data.get("image")
        version.options = version.options or {}
        self.merge_sync_payload(version, client_data, apply_api_defaults=True)
        return loader_name

    def loader_changed(self, version, client_data: dict[str, Any]) -> bool:
        expected_loader = self._loader_name(client_data)
        expected_mc_version = self._minecraft_version(client_data)
        return (
            version.version != expected_mc_version
            or version.loader_version != client_data.get("loader_version")
            or self._current_loader_key(version) != expected_loader
        )

    def merge_sync_payload(
        self,
        version,
        client_data: dict[str, Any],
        java_path: str | None = None,
        *,
        apply_api_defaults: bool = False,
    ) -> None:
        if apply_api_defaults or not getattr(version, "image", None):
            version.image = client_data.get("image") or getattr(version, "image", None)
        version.force_update = self._sync_enabled(client_data)
        version.options = version.options or {}
        force_fields = self._profile_fields(client_data)

        if not apply_api_defaults:
            self._apply_forced_profile_fields(version, client_data, force_fields)
            return

        server_host = client_data.get("server_host")
        server_port = client_data.get("server_port")
        if server_host and "server" not in version.options:
            version.options["server"] = {"host": server_host, "port": self._server_port(server_port)}

        gpu_preference = client_data.get("gpu_preference")
        if gpu_preference:
            gpu_mode = self._gpu_mode(gpu_preference)
            if gpu_mode:
                version.options["gpuMode"] = gpu_mode

        api_options = client_data.get("options")
        if isinstance(api_options, dict):
            api_opts_no_server = dict(api_options)
            server_cfg = api_opts_no_server.pop("server", None)
            if server_cfg and "server" not in version.options:
                version.options["server"] = server_cfg
            version.options.update(api_opts_no_server)

        self.apply_jvm_arguments(version, client_data)
        if java_path:
            version.options["executablePath"] = java_path

    def _apply_forced_profile_fields(
        self,
        version,
        client_data: dict[str, Any],
        fields: set[str],
    ) -> None:
        if not fields:
            return

        if "minecraft_version" in fields:
            version.version = self._minecraft_version(client_data) or getattr(version, "version", None)

        if "loader_version" in fields:
            version.loader_version = client_data.get("loader_version") or getattr(version, "loader_version", None)

        if "image" in fields:
            version.image = client_data.get("image") or None

        if "gpu_preference" in fields:
            gpu_mode = self._gpu_mode(client_data.get("gpu_preference"))
            if gpu_mode:
                version.options["gpuMode"] = gpu_mode

        if "jvm_arguments" in fields:
            self.apply_jvm_arguments(version, client_data)

        if fields.intersection({"server", "server_host", "server_port"}):
            self._apply_forced_server(version, client_data, fields)

    def _apply_forced_server(self, version, client_data: dict[str, Any], fields: set[str]) -> None:
        options = version.options or {}
        existing = options.get("server") if isinstance(options.get("server"), dict) else {}
        force_all = "server" in fields
        force_host = force_all or "server_host" in fields
        force_port = force_all or "server_port" in fields

        host_value = client_data.get("server_host") if force_host else existing.get("host")
        host = str(host_value or "").strip()
        if not host:
            options.pop("server", None)
            version.options = options
            return

        fallback_port = self._server_port(existing.get("port"))
        port_value = client_data.get("server_port") if force_port else existing.get("port")
        options["server"] = {
            "host": host,
            "port": self._server_port(port_value, fallback=fallback_port),
        }
        version.options = options

    @staticmethod
    def apply_jvm_arguments(version, client_data: dict[str, Any]) -> None:
        options = version.options or {}
        candidates = []

        api_arguments = client_data.get("jvm_arguments")
        if api_arguments is not None:
            candidates.append(api_arguments)

        api_options = client_data.get("options")
        if isinstance(api_options, dict):
            if "jvm_arguments" in api_options:
                candidates.append(api_options["jvm_arguments"])
            if "jvmArguments" in api_options:
                candidates.append(api_options["jvmArguments"])

        for candidate in candidates:
            if isinstance(candidate, list):
                normalized = [str(arg).strip() for arg in candidate if str(arg).strip()]
                if normalized:
                    options["jvmArguments"] = normalized
                else:
                    options.pop("jvmArguments", None)
                version.options = options
                return

            if isinstance(candidate, str):
                stripped = candidate.strip()
                if not stripped:
                    options.pop("jvmArguments", None)
                    version.options = options
                    return
                normalized = [
                    line.strip()
                    for line in candidate.replace("\r", "").split("\n")
                    if line.strip()
                ]
                if normalized:
                    options["jvmArguments"] = normalized
                else:
                    options.pop("jvmArguments", None)
                version.options = options
                return
