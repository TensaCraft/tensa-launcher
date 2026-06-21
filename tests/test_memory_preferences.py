from __future__ import annotations

from launcher.application.memory_preferences import MemoryLimits, MemoryPreferencesService


def test_memory_preferences_sanitizes_heap_arguments():
    limits = MemoryLimits(total_gb=6, available_gb=3, min_heap_gb=1, max_heap_gb=4, recommended_heap_gb=4)

    result = MemoryPreferencesService.sanitize_jvm_arguments(
        ["-Xms2G", "-XX:+UseG1GC", "-Xmx5000G"],
        limits=limits,
    )

    assert result.arguments == ["-Xmx4G", "-XX:+UseG1GC"]
    assert result.original_max_gb == 5000
    assert result.max_gb == 4
    assert result.changed is True
    assert result.removed_initial_heap is True


def test_memory_preferences_uses_recommended_fallback():
    limits = MemoryLimits(total_gb=8, available_gb=5, min_heap_gb=1, max_heap_gb=6, recommended_heap_gb=4)

    result = MemoryPreferencesService.sanitize_jvm_arguments([], fallback_max_gb=limits.recommended_heap_gb, limits=limits)

    assert result.arguments == ["-Xmx4G"]
