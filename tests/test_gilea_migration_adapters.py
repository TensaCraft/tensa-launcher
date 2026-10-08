import base64
from copy import deepcopy

import pytest
from cryptography.fernet import Fernet

from launcher.application.gilea_migration.adapters import adapt_snapshot
from launcher.application.gilea_migration.models import MigrationError, MigrationRoots, SourceSnapshot


@pytest.fixture
def roots(tmp_path):
    return MigrationRoots(tmp_path / "old", tmp_path / "Minecraft Space", tmp_path / "new",
                          tmp_path / "program", tmp_path / "old" / "gilea-migration")


def adapt(roots, *, config=None, profiles=None, versions=None, key=None, legacy_key=None):
    return adapt_snapshot(
        SourceSnapshot(config or {}, profiles or {}, versions or {}, key, {}),
        roots, effective_defaults={"default_max_ram_gb": 6, "lang": "uk_UA"}, legacy_key=legacy_key,
    )


def test_config_preserves_effective_defaults(roots):
    result = adapt(roots, config={"auto_update": False, "compact_sidebar": "yes"})
    assert result.config["minecraft_game_dir"] == str(roots.minecraft)
    assert result.config["gpu_mode_default"] == "dgpu"
    assert result.config["default_max_ram_gb"] == 6
    assert result.config["auto_update"] == "no"
    assert result.config["lang"] == "uk_UA"
    assert "setup_wizard_completed" not in result.config


@pytest.mark.parametrize(("old", "new", "want"), [("yes", None, "close"), (False, None, "nothing"),
                                                ("yes", "tray", "tray")])
@pytest.mark.parametrize("args", [["-Xmx4G", "-Dlabel=two words"], '-Xmx4G "-Dlabel=two words"'])
def test_close_setting_and_quoted_arguments(roots, old, new, want, args):
    result = adapt(roots, config={"close_launcher_on_game": old, "on_game_start": new},
                   versions={"a": {"path": "games/Aero", "options": {"jvmArguments": args}}})
    assert result.config["on_game_start"] == want
    assert result.builds[0].metadata["options"]["jvmArguments"] == args


def test_supported_preferences_and_relative_paths(roots):
    result = adapt(roots, config={
        "lang": "en_US", "include_beta_updates": "yes", "report_contact": "test",
        "ui_click_sound": "gate_latch_click", "world_backups_dir": "backups",
        "custom_java_versions": [{"Java21": "java/bin/java.exe"}],
        "show_tensacraft_versions": "no", "unknown_setting": "do-not-report-this-value",
    })
    assert result.config["world_backups_dir"] == str(roots.source_state / "backups")
    assert result.config["custom_java_versions"] == [{"Java21": str(roots.source_state / "java/bin/java.exe")}]
    assert result.config["include_beta_updates"] == "yes"
    assert result.config["report_contact"] == "test"
    assert "unknown_setting" not in result.config
    assert "do-not-report-this-value" not in repr(result)
    assert any(w.item == "unknown_setting" for w in result.warnings)


def test_account_identity_and_key_survive(roots):
    key = Fernet.generate_key()
    encrypted = "enc::" + Fernet(key).encrypt(b"fixture-token").decode()
    profiles = {"Player": {"id": "0123456789abcdef0123456789abcdef", "type": "offline",
                           "access_token": "offline", "default": False},
                "Online": {"id": "abcdef0123456789abcdef0123456789", "default": True,
                           "access_token": encrypted, "refresh_token": encrypted, "expires_at": 123,
                           "xuid": "42", "auth_client_id": "fixture-client"}}
    result = adapt(roots, profiles=profiles, key=key)
    assert result.profiles == profiles
    assert result.key == key
    assert Fernet(result.key).decrypt(result.profiles["Online"]["access_token"][5:].encode()) == b"fixture-token"
    assert "fixture-token" not in repr(result)


@pytest.mark.parametrize("key", [None, b"broken", Fernet.generate_key()])
def test_bad_key_requires_sign_in_without_losing_identity(roots, key):
    encrypted = "enc::" + Fernet(Fernet.generate_key()).encrypt(b"fixture-token").decode()
    result = adapt(roots, profiles={"Online": {"id": "identity", "access_token": encrypted}}, key=key)
    assert result.profiles["Online"]["id"] == "identity"
    assert result.profiles["Online"]["reauth_required"] is True
    assert result.profiles["Online"]["reauth_reason"] == "token_decryption_failed"
    assert result.profiles["Online"]["access_token"] is None


def test_legacy_and_plaintext_credentials_are_sealed_only_in_destination(roots):
    legacy = Fernet.generate_key()
    encrypted = "enc::" + Fernet(legacy).encrypt(b"old-token").decode()
    profiles = {"Online": {"id": "identity", "access_token": encrypted, "refresh_token": "plain-token"}}
    before = deepcopy(profiles)
    result = adapt(roots, profiles=profiles, legacy_key=legacy)
    assert profiles == before
    assert Fernet(result.key).decrypt(result.profiles["Online"]["access_token"][5:].encode()) == b"old-token"
    assert Fernet(result.key).decrypt(result.profiles["Online"]["refresh_token"][5:].encode()) == b"plain-token"


def test_no_default_keeps_prompting(roots):
    result = adapt(roots, profiles={"Player": {"id": "uuid", "access_token": "offline"}})
    assert result.config["ask_profile_on_launch"] == "yes"
    assert not result.profiles["Player"].get("default")


def test_build_records_keep_real_folders_and_options(roots):
    record = {"id": "old-id", "path": "games/Aero", "name": "Aero", "version": "1.21.1",
              "loader": "neoforge-21.1", "client": "tensa", "remote_pack_id": 42,
              "image": "pictures/a.png", "options": {"profileKey": "Player", "homePinned": True,
                                                    "server": {"host": "localhost", "port": 25565},
                                                    "executablePath": "runtime/java.exe"}}
    versions = {"old_key": record}
    before = deepcopy(versions)
    result = adapt(roots, profiles={"Player": {"access_token": "offline"}}, versions=versions)
    build = result.builds[0]
    assert build.source == roots.minecraft / "games/Aero"
    assert build.target == build.source
    assert build.metadata["options"]["profileKey"] == "Player"
    assert build.metadata["remote_pack_id"] == 42
    assert build.metadata["options"]["gpuMode"] == "dgpu"
    assert build.metadata["options"]["executablePath"] == str(roots.source_state / "runtime/java.exe")
    assert build.metadata["image"] == str(roots.source_state / "pictures/a.png")
    assert {"old_key", "old-id", "Aero"} <= set(build.aliases)
    assert versions == before


def test_external_build_requires_copy_and_aliases_are_unique(roots):
    external = roots.source_state / "\u041c\u0456\u0439 \u0441\u0432\u0456\u0442"
    build = adapt(roots, versions={"world": {"path": str(external)}}).builds[0]
    assert build.copy_required is True
    assert build.target.parent == roots.minecraft / "games"
    with pytest.raises(MigrationError, match="build_collision"):
        adapt(roots, versions={"one": {"path": "games/Aero"}, "two": {"path": "games/aero"}})
    with pytest.raises(MigrationError, match="build_collision"):
        adapt(roots, versions={"one": {"id": "same", "path": "games/a"},
                              "two": {"id": "same", "path": "games/b"}})


@pytest.mark.parametrize("path", ["../escape", "games/../escape", "C:relative", "games/a:stream"])
def test_build_path_rejects_ambiguous_or_unsafe_names(roots, path):
    with pytest.raises(MigrationError, match="unsafe_path"):
        adapt(roots, versions={"a": {"path": path}})


def test_missing_assigned_profile_blocks_instead_of_using_another_account(roots):
    with pytest.raises(MigrationError, match="missing_profile"):
        adapt(roots, versions={"a": {"path": "games/a", "options": {"profileKey": "Absent"}}})


def test_legacy_normalized_shortcut_alias_is_preserved_and_collision_checked(roots):
    result = adapt(roots, versions={"My Pack": {"path": "games/Actual Folder"}})
    assert "my_pack" in result.builds[0].aliases
    with pytest.raises(MigrationError, match="build_collision"):
        adapt(roots, versions={"My Pack": {"path": "games/One"}, "my_pack": {"path": "games/Two"}})


def test_raw_base64_build_image_is_preserved(roots):
    encoded = base64.b64encode(b'\x89PNG\r\n\x1a\nfixture-image').decode()
    result = adapt(roots, versions={"a": {"path": "games/a", "image": encoded}})
    assert result.builds[0].metadata["image"] == encoded
