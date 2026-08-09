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
        "OpenGL initialization failed: buffer storage is not supported by the graphics driver"
    )

    assert diagnosis.kind == "graphics_compatibility"
    assert diagnosis.severity == "warning"


def test_launch_diagnostics_classifies_mod_version_incompatibility_before_graphics() -> None:
    diagnosis = classify_launch_failure(
        "Incompatible mods found!\n"
        "HARD_DEP iris 1.10.7+mc1.21.11 {depends sodium @ [0.8.x]}\n"
        "NEG_HARD_DEP sodium 0.8.13+mc1.21.11 {breaks iris @ [<=1.10.7]}\n"
        "Replace mod 'Sodium' with a version compatible with Iris."
    )

    assert diagnosis.kind == "mod_incompatibility"
    assert diagnosis.severity == "warning"
    assert diagnosis.evidence == [
        "Incompatible mods found!",
        "NEG_HARD_DEP sodium 0.8.13+mc1.21.11 {breaks iris @ [<=1.10.7]}",
        "Replace mod 'Sodium' with a version compatible with Iris.",
    ]


def test_launch_diagnostics_does_not_treat_sodium_name_as_graphics_failure() -> None:
    diagnosis = classify_launch_failure(
        "Loaded Sodium 0.8.13+mc1.21.11\n"
        "java.lang.IllegalStateException: unrelated launch failure"
    )

    assert diagnosis.kind == "unknown"


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
