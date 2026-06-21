from __future__ import annotations

from launcher.application.launch_diagnostics import classify_launch_failure


def test_launch_diagnostics_classifies_missing_minecraft_dependency() -> None:
    diagnosis = classify_launch_failure(
        "Mod ID: 'minecraft', Requested by: 'create', Expected range: '[1.21.1]', Actual version: '[MISSING]'\n"
        "java.lang.NoClassDefFoundError: net/minecraft/client/gui/screens/LoadingOverlay"
    )

    assert diagnosis.kind == "missing_minecraft"
    assert diagnosis.severity == "error"
    assert "Actual version: '[MISSING]'" in diagnosis.evidence[0]


def test_launch_diagnostics_classifies_graphics_compatibility() -> None:
    diagnosis = classify_launch_failure(
        "Sodium Renderer 0.6.13+mc1.21.1\n"
        "OpenGL 4.5 Compatibility Profile Context AMD Radeon HD 7600M Series\n"
        "transparent textures and buffer storage issues"
    )

    assert diagnosis.kind == "graphics_compatibility"
    assert diagnosis.severity == "warning"


def test_launch_diagnostics_classifies_channel_mismatch() -> None:
    diagnosis = classify_launch_failure(
        "Не вдалося з'єднатися з каналом моду \"Create Connected\". "
        "Цей канал відсутній на стороні клієнта, але необхідний на сервері."
    )

    assert diagnosis.kind == "network_channel_mismatch"


def test_launch_diagnostics_falls_back_to_unknown() -> None:
    diagnosis = classify_launch_failure("Unexpected Java crash")

    assert diagnosis.kind == "unknown"
    assert diagnosis.evidence == []
