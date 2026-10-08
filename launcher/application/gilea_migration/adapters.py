from __future__ import annotations

import base64
import binascii
import re
from copy import deepcopy
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from launcher.application.java_preferences import JavaPreferencesService
from launcher.application.memory_preferences import MemoryPreferencesService
from launcher.platform.security import SecurityService

from .models import AdaptedData, BuildImport, JsonObject, MigrationError, MigrationIssue, MigrationRoots, SourceSnapshot

BOOL_DEFAULTS = {
    "auto_update": True, "include_beta_updates": False, "ask_profile_on_launch": False,
    "compact_sidebar": True, "ui_click_sound_enabled": True, "show_tensacraft_versions": True,
    "world_backups_enabled": False,
}
CONFIG_FIELDS = {
    *BOOL_DEFAULTS, "lang", "on_game_start", "ui_click_sound", "default_max_ram_gb", "gpu_mode_default",
    "world_backups_dir", "world_backups_keep_count", "custom_java_versions", "launcher_java_versions",
    "launcher_java_versions_last_scan", "report_contact", "window_size", "home_recent_builds",
    "home_recent_cleared_ms", "home_card_play",
}
BUILD_FIELDS = {
    "id", "name", "version", "loader", "client", "path", "loader_version", "force_update", "options",
    "image", "is_remote", "remote_pack_id", "description",
}
ACCOUNT_FIELDS = {
    "id", "name", "type", "access_token", "refresh_token", "xuid", "expires_at",
    "auth_client_id", "default", "reauth_required", "reauth_reason",
}


def boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str) and value.strip().lower() in {"yes", "true", "1", "on", "no", "false", "0", "off"}:
        return value.strip().lower() in {"yes", "true", "1", "on"}
    raise MigrationError("invalid_settings")


def absolute_path(raw: str, base: Path) -> Path:
    path = Path(raw)
    if not raw or "\x00" in raw or ".." in path.parts or (path.drive and not path.is_absolute()):
        raise MigrationError("unsafe_path")
    for part in path.parts[1:] if path.anchor else path.parts:
        if part.rstrip(" .") != part or any(c in part for c in '<>:"|?*') or any(ord(c) < 32 for c in part):
            raise MigrationError("unsafe_path")
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                         *(f"LPT{i}" for i in range(1, 10))}:
            raise MigrationError("unsafe_path")
    return path if path.is_absolute() else base / path


def _config(raw: JsonObject, roots: MigrationRoots, defaults: JsonObject, warnings: list[MigrationIssue]) -> JsonObject:
    config = {k: deepcopy(v) for k, v in raw.items() if k in CONFIG_FIELDS}
    for key in raw.keys() - CONFIG_FIELDS - {"close_launcher_on_game", "minecraft_game_dir",
                                            "setup_wizard_completed", "setup_wizard_version"}:
        warnings.append(MigrationIssue("unsupported_setting", key))
    for key, default in BOOL_DEFAULTS.items():
        config[key] = "yes" if boolean(raw.get(key, defaults.get(key, default))) else "no"
    config["minecraft_game_dir"] = str(roots.minecraft)
    config["on_game_start"] = raw.get("on_game_start")
    if config["on_game_start"] not in {"close", "tray", "nothing"}:
        config["on_game_start"] = "close" if boolean(raw.get("close_launcher_on_game", False)) else "nothing"
    config["lang"] = raw.get("lang", defaults.get("lang", "en_US"))
    config["gpu_mode_default"] = raw.get("gpu_mode_default", "dgpu")
    if config["lang"] not in {"en_US", "uk_UA"} or config["gpu_mode_default"] not in {"auto", "igpu", "dgpu"}:
        raise MigrationError("invalid_settings")
    memory = MemoryPreferencesService.parse_memory_gb(raw.get("default_max_ram_gb", defaults.get("default_max_ram_gb")))
    if memory is not None:
        config["default_max_ram_gb"] = memory
    elif raw.get("default_max_ram_gb") is not None:
        raise MigrationError("invalid_settings")
    for key in ("world_backups_keep_count", "home_recent_builds", "home_recent_cleared_ms"):
        if key in config:
            try:
                value = int(config[key])
            except (TypeError, ValueError, OverflowError):
                raise MigrationError("invalid_settings") from None
            if value < (1 if key == "world_backups_keep_count" else 0):
                raise MigrationError("invalid_settings")
            config[key] = value
    if config.get("world_backups_dir"):
        config["world_backups_dir"] = str(absolute_path(str(config["world_backups_dir"]), roots.source_state))
    for key in ("custom_java_versions", "launcher_java_versions"):
        if key in config:
            entries = JavaPreferencesService.normalize_entries(config[key])
            if not isinstance(config[key], list) or len(entries) != len(config[key]):
                raise MigrationError("invalid_settings")
            config[key] = [{label: str(absolute_path(path, roots.source_state))}
                           for entry in entries for label, path in entry.items()]
    return config


def _cipher(key: bytes | None) -> Fernet | None:
    try:
        return Fernet(key.strip()) if key else None
    except (ValueError, TypeError):
        return None


def _accounts(snapshot: SourceSnapshot, legacy_key: bytes | None, warnings: list[MigrationIssue]) -> tuple[JsonObject, bytes]:
    key = snapshot.key.strip() if _cipher(snapshot.key) and snapshot.key else Fernet.generate_key()
    destination = Fernet(key)
    sources = [cipher for candidate in (snapshot.key, legacy_key) if (cipher := _cipher(candidate)) is not None]
    profiles: JsonObject = {}
    for name, raw in snapshot.profiles.items():
        if not name or not isinstance(raw, dict):
            raise MigrationError("invalid_profiles")
        profile = {k: deepcopy(v) for k, v in raw.items() if k in ACCOUNT_FIELDS}
        offline = raw.get("type") == "offline" or raw.get("access_token") == "offline"
        failed = False
        for field in ("default", "reauth_required"):
            if field in profile:
                profile[field] = boolean(profile[field])
        for field in ("access_token", "refresh_token"):
            token = raw.get(field)
            if token is None:
                continue
            if not isinstance(token, str):
                raise MigrationError("invalid_profiles")
            if token == "offline" and offline:
                continue
            plain: bytes | None = None
            if token.startswith("enc::"):
                for cipher in sources:
                    try:
                        plain = cipher.decrypt(token[5:].encode())
                        break
                    except (InvalidToken, ValueError):
                        continue
                if plain is None:
                    failed = True
                    profile[field] = None
                    continue
            else:
                plain = token.encode()
            if snapshot.key and key == snapshot.key.strip() and token.startswith("enc::"):
                try:
                    destination.decrypt(token[5:].encode())
                    continue
                except InvalidToken:
                    pass
            profile[field] = "enc::" + destination.encrypt(plain).decode()
        if failed and not offline:
            profile.update(access_token=None, refresh_token=None, reauth_required=True,
                           reauth_reason="token_decryption_failed")
            warnings.append(MigrationIssue("account_sign_in", name))
        profiles[name] = profile
    return profiles, key


def _builds(records: JsonObject, profiles: JsonObject, roots: MigrationRoots) -> tuple[BuildImport, ...]:
    builds = []
    targets: set[str] = set()
    aliases: dict[str, Path] = {}
    for key, raw in records.items():
        if not isinstance(raw, dict) or not key:
            raise MigrationError("invalid_builds")
        # Missing paths are accepted only for already-normalized legacy folder IDs.
        path = raw.get("path")
        if not path and not re.fullmatch(r"[a-z0-9_]+", key):
            raise MigrationError("ambiguous_build_path", key)
        source = absolute_path(str(path or key), roots.minecraft)
        direct = source.parent == roots.minecraft / "games"
        target = source if direct else roots.minecraft / "games" / source.name
        absolute_path(str(target), roots.minecraft)
        if str(target).casefold() in targets:
            raise MigrationError("build_collision")
        targets.add(str(target).casefold())
        data = {k: deepcopy(v) for k, v in raw.items() if k in BUILD_FIELDS}
        data["path"] = f"games/{target.name}"
        options = data.get("options") or {}
        if not isinstance(options, dict):
            raise MigrationError("invalid_builds")
        profile_key = options.get("profileKey")
        if profile_key and profile_key not in profiles:
            raise MigrationError("missing_profile", key)
        options.setdefault("gpuMode", "dgpu")
        if options.get("executablePath"):
            options["executablePath"] = str(absolute_path(str(options["executablePath"]), roots.source_state))
        data["options"] = options
        if data.get("image") and not str(data["image"]).startswith(("https://", "http://", "data:")):
            image = str(data["image"])
            try:
                encoded_image = bool(base64.b64decode(image, validate=True))
            except (binascii.Error, ValueError):
                encoded_image = False
            if not encoded_image:
                data["image"] = str(absolute_path(image, roots.source_state))
        names = tuple(dict.fromkeys(str(n) for n in
                      (key, SecurityService.normalize_string(key), raw.get("id"), target.name) if n))
        for alias in names:
            folded = alias.casefold()
            if folded in aliases and aliases[folded] != target:
                raise MigrationError("build_collision")
            aliases[folded] = target
        builds.append(BuildImport(source, target, data, names, not direct))
    return tuple(builds)


def adapt_snapshot(snapshot: SourceSnapshot, roots: MigrationRoots, *, effective_defaults: JsonObject,
                   legacy_key: bytes | None = None) -> AdaptedData:
    warnings: list[MigrationIssue] = []
    config = _config(snapshot.config, roots, effective_defaults, warnings)
    profiles, key = _accounts(snapshot, legacy_key, warnings)
    if profiles and not any(profile.get("default") for profile in profiles.values()):
        config["ask_profile_on_launch"] = "yes"
    builds = _builds(snapshot.versions, profiles, roots)
    return AdaptedData(config, profiles, key, builds, tuple(warnings))
