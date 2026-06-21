from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Callable

from launcher.application.memory_preferences import MemoryPreferencesService


RECOMMENDED_PRESETS = [
    ("keep", "jvm_preset_keep", None),
    ("none", "jvm_preset_none", []),
    (
        "performance",
        "jvm_preset_performance",
        [
            "-XX:+UseG1GC",
            "-XX:+UseStringDeduplication",
            "-XX:MaxGCPauseMillis=80",
            "-XX:+ParallelRefProcEnabled",
        ],
    ),
]


@dataclass(slots=True)
class VersionOptionsPayload:
    name: str = ""
    java_path: str = ""
    loader_id: str = ""
    min_ram: str = ""
    max_ram: str = ""
    custom_args_text: str = ""
    server_host: str = ""
    server_port: str = ""
    gpu_mode: str = "dgpu"
    image_path: str | None = None


class VersionOptionsService:
    _MEMORY_PATTERN = re.compile(r"^-Xm([xs])(\d+)([mMgG]?)$")

    def parse_jvm_arguments(self, jvm_arguments: list[str]) -> tuple[int | None, int | None]:
        xmx_value = xms_value = None
        for argument in jvm_arguments:
            match = self._MEMORY_PATTERN.match(argument.strip())
            if not match:
                continue
            group_type, value, unit = match.groups()
            amount = int(value)
            if unit.lower() == "m":
                amount = max(1, (amount + 1023) // 1024)
            if group_type == "x":
                xmx_value = amount
            else:
                xms_value = amount
        return xmx_value, xms_value

    @staticmethod
    def extract_custom_arguments(jvm_arguments: list[str]) -> list[str]:
        custom_arguments: list[str] = []
        for argument in jvm_arguments:
            cleaned = argument.strip()
            if not cleaned or cleaned.startswith("-Xmx") or cleaned.startswith("-Xms"):
                continue
            custom_arguments.append(cleaned)
        return custom_arguments

    @staticmethod
    def collect_custom_arguments(value: str) -> list[str]:
        seen: set[str] = set()
        collected: list[str] = []
        for line in (value or "").splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            collected.append(cleaned)
        return collected

    @staticmethod
    def compose_jvm_arguments(
        xmx: str | None,
        xms: str | None,
        extra: list[str],
    ) -> list[str]:
        arguments: list[str] = []
        if xmx:
            arguments.append(xmx)
        for argument in extra:
            if argument not in arguments:
                arguments.append(argument)
        return arguments

    @staticmethod
    def format_memory_argument(prefix: str, value: str | None) -> str | None:
        if prefix != "-Xmx":
            return None
        if not value:
            return None
        return MemoryPreferencesService.format_xmx_argument(value)

    def build_preset_options(
        self,
        translate: Callable[[str], str],
    ) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
        options: list[dict[str, str]] = []
        preset_map: dict[str, list[str]] = {}
        for key, label_key, args in RECOMMENDED_PRESETS:
            options.append({"text": translate(label_key), "key": key})
            if args is not None:
                preset_map[key] = list(args)
        return options, preset_map

    def apply(self, version, payload: VersionOptionsPayload) -> None:
        xmx_value = self.format_memory_argument("-Xmx", payload.max_ram)
        custom_arguments = self.collect_custom_arguments(payload.custom_args_text)
        jvm_arguments = self.compose_jvm_arguments(xmx_value, None, custom_arguments)
        sanitized = MemoryPreferencesService.sanitize_jvm_arguments(jvm_arguments)

        if sanitized.arguments:
            version.options["jvmArguments"] = sanitized.arguments
        else:
            version.options.pop("jvmArguments", None)

        if payload.java_path:
            version.options["executablePath"] = payload.java_path
        else:
            version.options.pop("executablePath", None)

        version.loader = payload.loader_id or None
        version.options["gpuMode"] = payload.gpu_mode or "dgpu"
        version.options.pop("graphicsPreset", None)

        host = (payload.server_host or "").strip()
        port_raw = (payload.server_port or "").strip()
        if host:
            server_config = {"host": host}
            if port_raw:
                try:
                    server_config["port"] = int(port_raw)
                except ValueError as exc:
                    raise ValueError("invalid_port") from exc
            version.options["server"] = server_config
        else:
            version.options.pop("server", None)

        if payload.name:
            version.name = payload.name

        image_data = self._read_image_data(payload.image_path)
        if image_data:
            version.image = image_data

    @staticmethod
    def _read_image_data(image_path: str | None) -> str | None:
        if not image_path or not os.path.exists(image_path):
            return None
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
