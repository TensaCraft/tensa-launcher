from __future__ import annotations

import ctypes
import os
import re
import sys
from dataclasses import dataclass
from math import ceil
from typing import Iterable

GIB = 1024**3
MIN_HEAP_GB = 1
SYSTEM_RESERVE_GB = 2
ABSOLUTE_MAX_HEAP_GB = 32
FALLBACK_TOTAL_GB = 8

_MEMORY_ARGUMENT_RE = re.compile(r"^-Xm(?P<kind>[sx])(?P<amount>\d+)(?P<unit>[kKmMgG]?)$")


@dataclass(frozen=True, slots=True)
class MemoryLimits:
    total_gb: int
    available_gb: int | None
    min_heap_gb: int
    max_heap_gb: int
    recommended_heap_gb: int


@dataclass(frozen=True, slots=True)
class JvmMemorySanitizationResult:
    arguments: list[str]
    original_max_gb: int | None
    max_gb: int | None
    changed: bool
    removed_initial_heap: bool


class MemoryPreferencesService:
    """Normalise Minecraft JVM memory settings against the user's machine."""

    @classmethod
    def detect_limits(cls) -> MemoryLimits:
        total_bytes, available_bytes = cls._detect_memory_bytes()
        total_gb = max(MIN_HEAP_GB, int(total_bytes // GIB) if total_bytes else FALLBACK_TOTAL_GB)
        available_gb = int(available_bytes // GIB) if available_bytes else None
        max_heap_gb = cls._max_heap_for_total(total_gb)
        recommended_heap_gb = min(max_heap_gb, cls._recommended_heap_for_total(total_gb))
        return MemoryLimits(
            total_gb=total_gb,
            available_gb=available_gb,
            min_heap_gb=MIN_HEAP_GB,
            max_heap_gb=max_heap_gb,
            recommended_heap_gb=max(MIN_HEAP_GB, recommended_heap_gb),
        )

    @classmethod
    def normalize_max_ram_gb(cls, value: object, *, limits: MemoryLimits | None = None, default: int | None = None) -> int:
        resolved_limits = limits or cls.detect_limits()
        parsed = cls.parse_memory_gb(value)
        if parsed is None:
            parsed = default if default is not None else resolved_limits.recommended_heap_gb
        return min(max(int(parsed), resolved_limits.min_heap_gb), resolved_limits.max_heap_gb)

    @classmethod
    def format_xmx_argument(cls, value: object, *, limits: MemoryLimits | None = None) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return f"-Xmx{cls.normalize_max_ram_gb(value, limits=limits)}G"

    @classmethod
    def sanitize_jvm_arguments(
        cls,
        arguments: Iterable[object] | None,
        *,
        fallback_max_gb: object | None = None,
        limits: MemoryLimits | None = None,
    ) -> JvmMemorySanitizationResult:
        resolved_limits = limits or cls.detect_limits()
        original_arguments = [str(argument).strip() for argument in arguments or () if str(argument).strip()]
        extra_arguments: list[str] = []
        original_max_gb: int | None = None
        selected_max_gb: int | None = None
        removed_initial_heap = False

        for argument in original_arguments:
            lowered = argument.lower()
            if lowered.startswith("-xmx"):
                parsed = cls.parse_memory_gb(argument)
                if parsed is not None:
                    original_max_gb = parsed
                    selected_max_gb = parsed
                continue
            if lowered.startswith("-xms"):
                removed_initial_heap = True
                continue
            if argument not in extra_arguments:
                extra_arguments.append(argument)

        if selected_max_gb is None:
            selected_max_gb = cls.parse_memory_gb(fallback_max_gb)

        normalized_max_gb: int | None = None
        sanitized_arguments = list(extra_arguments)
        if selected_max_gb is not None:
            normalized_max_gb = cls.normalize_max_ram_gb(selected_max_gb, limits=resolved_limits)
            sanitized_arguments = [f"-Xmx{normalized_max_gb}G", *extra_arguments]

        return JvmMemorySanitizationResult(
            arguments=sanitized_arguments,
            original_max_gb=original_max_gb,
            max_gb=normalized_max_gb,
            changed=sanitized_arguments != original_arguments,
            removed_initial_heap=removed_initial_heap,
        )

    @staticmethod
    def parse_memory_gb(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            amount = int(value)
            return amount if amount > 0 else None

        raw = str(value).strip()
        if not raw:
            return None
        if raw.isdigit():
            amount = int(raw)
            return amount if amount > 0 else None

        match = _MEMORY_ARGUMENT_RE.match(raw)
        if not match:
            return None

        amount = int(match.group("amount"))
        unit = match.group("unit").lower()
        if unit == "m":
            amount = ceil(amount / 1024)
        elif unit == "k":
            amount = ceil(amount / (1024 * 1024))
        return max(1, amount)

    @staticmethod
    def _max_heap_for_total(total_gb: int) -> int:
        if total_gb <= 2:
            safe_max = 1
        elif total_gb <= 4:
            safe_max = total_gb - 1
        else:
            safe_max = total_gb - SYSTEM_RESERVE_GB
        return min(ABSOLUTE_MAX_HEAP_GB, max(MIN_HEAP_GB, safe_max))

    @staticmethod
    def _recommended_heap_for_total(total_gb: int) -> int:
        if total_gb <= 4:
            return 2
        if total_gb <= 8:
            return 4
        if total_gb <= 16:
            return 6
        if total_gb <= 32:
            return 8
        return 12

    @classmethod
    def _detect_memory_bytes(cls) -> tuple[int | None, int | None]:
        if sys.platform.startswith("win"):
            return cls._detect_windows_memory_bytes()
        if sys.platform.startswith("linux"):
            linux = cls._detect_linux_memory_bytes()
            if linux[0] is not None:
                return linux
        return cls._detect_sysconf_memory_bytes()

    @staticmethod
    def _detect_windows_memory_bytes() -> tuple[int | None, int | None]:
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)

    @staticmethod
    def _detect_linux_memory_bytes() -> tuple[int | None, int | None]:
        try:
            values: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, _, raw_value = line.partition(":")
                    if key in {"MemTotal", "MemAvailable"}:
                        values[key] = int(raw_value.strip().split()[0]) * 1024
            return values.get("MemTotal"), values.get("MemAvailable")
        except (OSError, ValueError):
            return None, None

    @staticmethod
    def _detect_sysconf_memory_bytes() -> tuple[int | None, int | None]:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            page_count = os.sysconf("SC_PHYS_PAGES")
            return int(page_size * page_count), None
        except (AttributeError, OSError, ValueError):
            return FALLBACK_TOTAL_GB * GIB, None


__all__ = ["JvmMemorySanitizationResult", "MemoryLimits", "MemoryPreferencesService"]
