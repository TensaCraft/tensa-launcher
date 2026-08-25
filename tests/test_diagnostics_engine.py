from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from launcher.application.diagnostics import (
    Confidence,
    DiagnosticArtifact,
    DiagnosticCase,
    DiagnosticEngine,
    Finding,
    default_engine,
)


def _case(text: str, *, fresh: bool = True, managed_pack: bool = False) -> DiagnosticCase:
    return DiagnosticCase(
        artifacts=(DiagnosticArtifact(text=text, fresh=fresh),),
        managed_pack=managed_pack,
    )


def test_engine_ranks_exact_dependency_above_generic_mod_error() -> None:
    result = default_engine().analyze(
        _case(
            "Incompatible mods found!\n"
            "Mod 'Glowtone' (glowtone) 1.0.2 requires any version of fabric-api, which is missing!"
        )
    )

    assert result.primary.id == "mods.missing_dependency.fabric_api"
    assert result.primary.params == {"mod": "Glowtone", "dependency": "fabric-api"}
    assert [finding.id for finding in result.suppressed] == ["mods.incompatible.generic"]


def test_engine_parses_quilt_missing_dependency_without_mod_prefix() -> None:
    result = default_engine().analyze(
        _case("Glowtone requires any version of fabric-api, which is missing!")
    )

    assert result.primary.id == "mods.missing_dependency.fabric_api"
    assert result.primary.params == {"mod": "Glowtone", "dependency": "fabric-api"}


def test_engine_keeps_multiple_independent_findings() -> None:
    result = default_engine().analyze(
        _case(
            "Mod createbetterfps requires sodium 0.6.9 or above\n"
            "Currently, sodium is not installed\n"
            "OpenGL initialization failed: graphics driver unavailable"
        )
    )

    assert result.primary.kind == "missing_mod_dependency"
    assert [finding.kind for finding in result.findings] == [
        "missing_mod_dependency",
        "graphics_compatibility",
    ]


def test_managed_pack_dependency_finding_offers_safe_navigation_and_confirmed_repair() -> None:
    result = default_engine().analyze(
        _case(
            "Mod createbetterfps requires sodium 0.6.9 or above\nCurrently, sodium is not installed",
            managed_pack=True,
        )
    )

    assert [(action.kind, action.safety) for action in result.primary.actions] == [
        ("open_mod_manager", "safe"),
        ("repair_sync", "confirm"),
    ]


def test_engine_ignores_stale_artifacts() -> None:
    result = default_engine().analyze(_case("OpenGL initialization failed: graphics driver unavailable", fresh=False))

    assert result.primary.kind == "unknown"


def test_engine_does_not_classify_unrelated_channel_text() -> None:
    result = default_engine().analyze(_case("Client selected release channel beta before launch"))

    assert result.primary.kind == "unknown"


def test_engine_rejects_duplicate_detector_ids() -> None:
    @dataclass(frozen=True)
    class FakeDetector:
        id: str

        def detect(self, case: DiagnosticCase) -> Iterable[Finding]:
            del case
            return (
                Finding(
                    id=self.id,
                    kind="fake",
                    severity="warning",
                    confidence=Confidence.MEDIUM,
                    priority=1,
                    title_key="fake",
                    message_key="fake",
                ),
            )

    with pytest.raises(ValueError, match="Duplicate diagnostic detector id"):
        DiagnosticEngine((FakeDetector("same"), FakeDetector("same")))


def test_engine_isolates_detector_failure_and_keeps_other_findings(caplog: pytest.LogCaptureFixture) -> None:
    @dataclass(frozen=True)
    class BrokenDetector:
        id: str = "broken.detector"

        def detect(self, case: DiagnosticCase) -> Iterable[Finding]:
            del case
            raise RuntimeError("private artifact content")

    @dataclass(frozen=True)
    class WorkingDetector:
        id: str = "working.detector"

        def detect(self, case: DiagnosticCase) -> Iterable[Finding]:
            del case
            return (
                Finding(
                    id="working.finding",
                    kind="working",
                    severity="warning",
                    confidence=Confidence.HIGH,
                    priority=10,
                    title_key="working",
                    message_key="working",
                ),
            )

    with caplog.at_level(logging.WARNING, logger="launcher.application.diagnostics.engine"):
        result = DiagnosticEngine((BrokenDetector(), WorkingDetector())).analyze(_case("sensitive log"))

    assert result.primary.id == "working.finding"
    assert "broken.detector" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "private artifact content" not in caplog.text
    assert "sensitive log" not in caplog.text


def test_engine_uses_unknown_fallback_when_every_detector_fails() -> None:
    @dataclass(frozen=True)
    class BrokenDetector:
        id: str = "broken.detector"

        def detect(self, case: DiagnosticCase) -> Iterable[Finding]:
            del case
            raise ValueError

    result = DiagnosticEngine((BrokenDetector(),)).analyze(_case("Unexpected Java crash"))

    assert result.primary.id == "launch.unknown"


def test_engine_parses_multiple_fabric_missing_dependencies() -> None:
    result = default_engine().analyze(
        _case(
            "Incompatible mods found!\n"
            "Mod 'Better Clouds' (better-clouds) 1.3.0 requires version 0.100.8 or later "
            "of mod 'Fabric API' (fabric-api), which is missing!\n"
            "Mod 'Mod Menu' (modmenu) 11.0.3 requires version 15.0.0 or later "
            "of cloth-config, which is missing!"
        )
    )

    assert [finding.id for finding in result.findings] == [
        "mods.missing_dependency.fabric_api",
        "mods.missing_dependency.cloth-config",
    ]
    assert [finding.params for finding in result.findings] == [
        {"mod": "Better Clouds", "dependency": "fabric-api"},
        {"mod": "Mod Menu", "dependency": "cloth-config"},
    ]
    assert [finding.id for finding in result.suppressed] == ["mods.incompatible.generic"]


def test_engine_matches_currently_not_installed_to_the_named_dependency() -> None:
    result = default_engine().analyze(
        _case(
            "Mod performance-pack requires sodium 0.6.9 or above\n"
            "Mod performance-pack requires lithium 0.14.0 or above\n"
            "Currently, sodium is not installed"
        )
    )

    assert [finding.id for finding in result.findings] == ["mods.missing_dependency.sodium"]
    assert result.primary.params == {"mod": "performance-pack", "dependency": "sodium"}


def test_engine_parses_neoforge_missing_dependency_manifest() -> None:
    result = default_engine().analyze(
        _case(
            "Missing or unsupported mandatory dependencies:\n"
            "Mod ID: 'sodium', Requested by: 'sodium_extra', "
            "Expected range: '[0.8.12,)', Actual version: '[MISSING]'"
        )
    )

    assert result.primary.id == "mods.missing_dependency.sodium"
    assert result.primary.params == {"mod": "sodium_extra", "dependency": "sodium"}


def test_engine_ranks_duplicate_java_module_export_above_missing_dependency() -> None:
    result = default_engine().analyze(
        _case(
            "Missing or unsupported mandatory dependencies:\n"
            "Mod ID: 'sodium', Requested by: 'sodium_extra', Expected range: '[0.8.12,)', "
            "Actual version: '[MISSING]'\n"
            "java.lang.module.ResolutionException: Modules sodium and sodium_service "
            "export package net.caffeinemc.mods.sodium.service to module runtime._1._21._1"
        )
    )

    assert result.primary.id == "mods.module_conflict"
    assert result.primary.kind == "mod_incompatibility"
    assert [finding.id for finding in result.findings] == ["mods.module_conflict"]
    assert [finding.id for finding in result.suppressed] == ["mods.missing_dependency.sodium"]


def test_engine_classifies_broken_mixin_configuration() -> None:
    result = default_engine().analyze(
        _case(
            "java.lang.IllegalClassLoadError: Illegal classload request for accessor mixin "
            "com.kapiteon.freecam.mixin.ClientCommonPacketListenerImplAccessor. "
            "The mixin is missing from freecam_by_kapiteon.mixins.json"
        )
    )

    assert result.primary.id == "mods.broken_mixin"
    assert result.primary.kind == "mod_incompatibility"
    assert result.primary.confidence == Confidence.EXACT


def test_engine_classifies_vulkanmod_device_failure() -> None:
    result = default_engine().analyze(
        _case(
            "Description: Initializing game\n"
            "java.lang.IllegalStateException: Failed to find a suitable GPU\n"
            "at net.vulkanmod.vulkan.device.DeviceManager.autoPickDevice(DeviceManager.java:147)"
        )
    )

    assert result.primary.id == "graphics.vulkan_device"
    assert result.primary.confidence == Confidence.EXACT


def test_engine_classifies_ftb_chunks_local_map_failure_without_graphics_advice() -> None:
    result = default_engine().analyze(
        _case(
            "Description: Unexpected error\n"
            "java.util.ConcurrentModificationException: null\n"
            "at dev.ftb.mods.ftbchunks.client.map.MapManager.saveAllRegions(MapManager.java:174)\n"
            "OpenGL renderer failed while writing unrelated diagnostics"
        )
    )

    assert result.primary.id == "mods.ftb_chunks_local_data"
    assert [finding.id for finding in result.findings] == ["mods.ftb_chunks_local_data"]
    assert [finding.id for finding in result.suppressed] == ["graphics.initialization"]


def test_engine_classifies_create_block_entity_model_failure_without_graphics_advice() -> None:
    result = default_engine().analyze(
        _case(
            "Description: Rendering Block Entity\n"
            'java.lang.NullPointerException: Cannot invoke "BakedModel.getModelData(...)" because "model" is null\n'
            "at net.createmod.catnip.impl.client.render.model.BakedModelBuffererImpl.bufferModel("
            "BakedModelBuffererImpl.java:50)\n"
            "OpenGL renderer failed while writing unrelated diagnostics"
        )
    )

    assert result.primary.id == "mods.create_block_entity_rendering"
    assert [finding.id for finding in result.findings] == ["mods.create_block_entity_rendering"]
    assert [finding.id for finding in result.suppressed] == ["graphics.initialization"]


def test_engine_reports_the_create_addon_block_that_failed_to_render() -> None:
    result = default_engine().analyze(
        _case(
            "Description: Rendering Block Entity\n"
            'java.lang.NullPointerException: Cannot invoke "BakedModel.getModelData(...)" because "model" is null\n'
            "at net.createmod.catnip.impl.client.render.model.BakedModelBuffererImpl.bufferModel\n"
            "Block: Block{create_vibrant_vaults:white_packager}[facing=east]"
        )
    )

    assert result.primary.id == "mods.create_block_entity_rendering"
    assert result.primary.message_key == "launch_diagnostic_create_rendering_block"
    assert result.primary.params == {"block": "create_vibrant_vaults:white_packager"}


def test_engine_classifies_create_configuration_payload_failure_without_generic_advice() -> None:
    result = default_engine().analyze(
        _case(
            "java.util.concurrent.CompletionException: java.lang.UnsupportedOperationException: "
            "Cannot retrieve the client player during the configuration phase.\n"
            "at ponder@1.0.82/net.createmod.ponder.foundation.networking.NeoForgeNetworkHelper\n"
            "Failed to process a synchronized task of the payload: create:server_speed\n"
            "java.lang.NoClassDefFoundError: net/minecraft/client/player/LocalPlayer\n"
            "A client channel requires state which is missing during configuration"
        )
    )

    assert result.primary.id == "mods.create_configuration_payload"
    assert [finding.id for finding in result.findings] == ["mods.create_configuration_payload"]


def test_engine_does_not_treat_minecraft_class_failure_as_missing_runtime() -> None:
    result = default_engine().analyze(
        _case("java.lang.NoClassDefFoundError: net/minecraft/client/gui/screens/LoadingOverlay")
    )

    assert result.primary.id == "launch.unknown"


def test_engine_does_not_infer_missing_dependency_without_a_parsed_requirement() -> None:
    result = default_engine().analyze(
        _case(
            "The client requires configuration before joining the server.\n"
            "Optional player state is missing during the configuration phase."
        )
    )

    assert result.primary.id == "launch.unknown"


def test_engine_does_not_infer_channel_mismatch_from_unrelated_lines() -> None:
    result = default_engine().analyze(
        _case(
            "Failed to process a synchronized task of the payload: create:server_speed\n"
            "Client player is missing during the configuration phase\n"
            "Registered network channel create:main"
        )
    )

    assert result.primary.id == "launch.unknown"


def test_engine_ignores_graphics_words_inside_long_mixin_stack_line() -> None:
    mixins = ",".join(f"renderer_{index}_failed_mixin" for index in range(80))
    result = default_engine().analyze(
        _case(
            "at net.minecraft.client.Minecraft.run(Minecraft.java:825) "
            f"{{pl:mixin:APP:{mixins}}}"
        )
    )

    assert result.primary.id == "launch.unknown"


def test_engine_bounds_evidence_for_reports() -> None:
    long_line = "OpenGL initialization failed: " + ("x" * 500)
    result = default_engine().analyze(_case(long_line))

    assert len(result.primary.evidence) == 1
    assert len(result.primary.evidence[0]) == 300
