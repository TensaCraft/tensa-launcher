from __future__ import annotations

import json
import shutil
import stat
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Sequence

from launcher.application.mod_identity import (
    inspect_mod_jar,
    normalize_file_hash,
    verify_file_hash,
)
from launcher.storage.atomic import (
    atomic_copy_file,
    atomic_write_json,
    atomic_write_text,
)


class VersionContentService:
    MODRINTH_METADATA_FILE = "modrinth-content.json"
    MODRINTH_METADATA_SCHEMA_VERSION = 2
    IRIS_PROPERTIES_FILE = "iris.properties"
    MOD_METADATA_CACHE_SIZE = 512
    MOD_METADATA_MAX_CHARS = 4096

    def __init__(self, minecraft_dir: str | Path, logger) -> None:
        self.minecraft_dir = Path(minecraft_dir)
        self.log = logger
        self._mod_metadata: OrderedDict[Path, tuple[tuple[int, ...], dict[str, Any]]] = OrderedDict()
        self._metadata_lock = Lock()

    def _log(self, level: str, message: str) -> None:
        logger_method = getattr(self.log, level, None)
        if callable(logger_method):
            logger_method(message)

    @staticmethod
    def mods_supported(version) -> bool:
        client = (version.client or "").lower()
        if client == "minecraft":
            return False
        return any(loader in client for loader in ("fabric", "forge", "neoforge", "quilt", "tensacraft"))

    def get_mods_directory(self, version) -> Path | None:
        version_dir = self._resolve_version_dir(version)
        if version_dir is None:
            return None
        mods_dir = version_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        return mods_dir

    def get_backup_directory(self, mods_dir: Path | None) -> Path | None:
        if mods_dir is None:
            return None
        backup_dir = mods_dir / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def get_resourcepacks_directory(self, version) -> Path | None:
        version_dir = self._resolve_version_dir(version)
        if version_dir is None:
            return None
        resourcepacks_dir = version_dir / "resourcepacks"
        resourcepacks_dir.mkdir(parents=True, exist_ok=True)
        return resourcepacks_dir

    def get_shaderpacks_directory(self, version) -> Path | None:
        version_dir = self._resolve_version_dir(version)
        if version_dir is None:
            return None
        shaderpacks_dir = version_dir / "shaderpacks"
        shaderpacks_dir.mkdir(parents=True, exist_ok=True)
        return shaderpacks_dir

    def scan_installed_mods(self, mods_dir: Path | None) -> list[dict[str, Any]]:
        if mods_dir is None or not mods_dir.exists():
            return []

        mods: list[dict[str, Any]] = []
        for mod_file in mods_dir.iterdir():
            name = mod_file.name.lower()
            if not name.endswith((".jar", ".jar.disabled")):
                continue
            try:
                file_stat = mod_file.stat()
            except OSError as exc:
                self._log("debug", f"Unable to inspect mod {mod_file.name}: {exc}")
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            enabled = not name.endswith(".disabled")
            mod_info = {
                "filename": mod_file.name if enabled else mod_file.name[:-9],
                "path": str(mod_file),
                "size": file_stat.st_size,
                "enabled": enabled,
            }
            mod_info.update(self.read_mod_metadata(mod_file))
            mods.append(mod_info)

        return sorted(mods, key=lambda item: item.get("name", item["filename"]).lower())

    def scan_installed_resourcepacks(self, resourcepacks_dir: Path | None) -> list[dict[str, Any]]:
        enabled_entries = self._read_options_list(
            self._options_path_for_content_dir(resourcepacks_dir),
            "resourcePacks",
        )
        return self._scan_installed_packs(
            resourcepacks_dir,
            file_type="resourcepack",
            folder_type="resourcepack_folder",
            content_type="resourcepack",
            enabled_entries=enabled_entries,
        )

    def scan_installed_shaderpacks(
        self,
        shaderpacks_dir: Path | None,
        *,
        mods_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        if mods_dir is None and shaderpacks_dir is not None:
            mods_dir = shaderpacks_dir.parent / "mods"
        iris_available = self.has_iris_mod(mods_dir)
        iris_properties = self._read_properties(self._iris_properties_path(shaderpacks_dir)) if iris_available else {}
        return self._scan_installed_packs(
            shaderpacks_dir,
            file_type="shaderpack",
            folder_type="shaderpack_folder",
            content_type="shaderpack",
            enabled_entries=[],
            enabled_by_name=(
                lambda filename: self._shaderpack_enabled(filename, iris_properties)
                if iris_available
                else False
            ),
            toggle_supported=iris_available,
        )

    def _scan_installed_packs(
        self,
        directory: Path | None,
        *,
        file_type: str,
        folder_type: str,
        content_type: str,
        enabled_entries: list[str] | None = None,
        enabled_by_name=None,
        toggle_supported: bool = True,
    ) -> list[dict[str, Any]]:
        if directory is None or not directory.exists():
            return []

        packs: list[dict[str, Any]] = []
        for path in directory.iterdir():
            name = path.name
            try:
                info = path.stat()
                folder = stat.S_ISDIR(info.st_mode)
                if folder:
                    if name.startswith("."):
                        continue
                    size = 0
                    for child in path.rglob("*"):
                        try:
                            child_info = child.stat()
                            if stat.S_ISREG(child_info.st_mode):
                                size += child_info.st_size
                        except OSError as exc:
                            self._log("debug", f"Unable to inspect pack entry {child}: {exc}")
                    disabled = False
                    default = not (directory / f"{name}.disabled").exists()
                else:
                    if not stat.S_ISREG(info.st_mode) or not name.lower().endswith((".zip", ".zip.disabled")):
                        continue
                    size = info.st_size
                    disabled = name.lower().endswith(".disabled")
                    if disabled:
                        name = name[:-9]
                    default = True
            except OSError as exc:
                self._log("debug", f"Unable to inspect pack {path}: {exc}")
                continue
            packs.append(
                {
                    "filename": name,
                    "path": str(path),
                    "size": size,
                    "type": folder_type if folder else file_type,
                    "content_type": content_type,
                    "enabled": not disabled and self._pack_enabled(
                        name,
                        enabled_entries,
                        default=default,
                        enabled_by_name=enabled_by_name,
                    ),
                    "toggle_supported": toggle_supported,
                }
            )

        return sorted(packs, key=lambda item: item["filename"].lower())

    @staticmethod
    def _pack_enabled(
        filename: str,
        enabled_entries: list[str] | None,
        *,
        default: bool,
        enabled_by_name=None,
    ) -> bool:
        if enabled_by_name is not None:
            return bool(enabled_by_name(filename))
        if enabled_entries is None:
            return default
        return VersionContentService._resourcepack_entry(filename) in enabled_entries

    def has_iris_mod(self, mods_dir: Path | None) -> bool:
        if mods_dir is None or not mods_dir.exists():
            return False
        for mod_path in mods_dir.glob("*.jar"):
            lowered = mod_path.name.lower()
            if lowered.startswith("iris") or "iris-shaders" in lowered:
                return True
            metadata = self.read_mod_metadata(mod_path)
            mod_id = str(metadata.get("id", "")).lower()
            mod_name = str(metadata.get("name", "")).lower()
            if mod_id == "iris" or mod_name.startswith("iris shaders"):
                return True
        return False

    def record_modrinth_content(
        self,
        version,
        content_key: str,
        file_path: Path,
        project: dict[str, Any],
        version_data: dict[str, Any],
        install_file,
    ) -> None:
        self.write_modrinth_content_batch(
            version,
            [
                (
                    content_key,
                    Path(file_path),
                    project,
                    version_data,
                    install_file,
                )
            ],
        )

    def write_modrinth_content_batch(
        self,
        version,
        records: Sequence[tuple[str, Path, dict[str, Any], dict[str, Any], Any]],
        *,
        removed_paths: Sequence[Path] = (),
        output_path: Path | None = None,
    ) -> Path | None:
        index_path = self._modrinth_metadata_path(version)
        if index_path is None:
            return None

        version_root = index_path.parent.parent
        index = self._load_modrinth_metadata(index_path)
        files = index.get("files")
        if not isinstance(files, dict):
            files = {}
            index["files"] = files
        index["schema_version"] = self.MODRINTH_METADATA_SCHEMA_VERSION

        for removed_path in removed_paths:
            relative_path = self._strict_relative_content_path(version_root, Path(removed_path))
            for candidate in self._metadata_path_candidates_from_relative(relative_path):
                files.pop(candidate, None)

        for content_key, file_path, project, version_data, install_file in records:
            relative_path = self._strict_relative_content_path(version_root, Path(file_path))
            metadata = {
                "content_key": content_key,
                "filename": install_file.filename,
                "project_id": project.get("project_id"),
                "project_slug": project.get("slug"),
                "project_title": project.get("title"),
                "version_id": version_data.get("id"),
                "version_number": version_data.get("version_number"),
            }
            file_hash = self._install_file_hash(install_file)
            if file_hash is not None:
                hash_algorithm, expected_digest = file_hash
                source_path = self._metadata_hash_source(
                    version_root,
                    Path(file_path),
                    relative_path,
                    output_path,
                )
                if not verify_file_hash(source_path, hash_algorithm, expected_digest):
                    raise ValueError(
                        f"Installed Modrinth content hash does not match: {relative_path}"
                    )
                metadata["hash_algorithm"] = hash_algorithm
                metadata["file_hash"] = expected_digest
            files[relative_path] = metadata

        self._write_modrinth_metadata(output_path or index_path, index)
        return index_path

    def apply_modrinth_metadata(self, version, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        index_path = self._modrinth_metadata_path(version)
        if index_path is None:
            return items

        version_root = index_path.parent.parent
        files = self._load_modrinth_metadata(index_path).get("files", {})
        if not isinstance(files, dict):
            return items
        for item in items:
            candidates = self._metadata_path_candidates(version_root, Path(item["path"]))
            metadata = next((files[path] for path in candidates if path in files), None)
            if not isinstance(metadata, dict):
                continue
            item["modrinth_project_id"] = metadata.get("project_id")
            item["modrinth_project_slug"] = metadata.get("project_slug")
            item["modrinth_project_title"] = metadata.get("project_title")
            item["modrinth_version_id"] = metadata.get("version_id")
            item["modrinth_version_number"] = metadata.get("version_number")
            file_hash = normalize_file_hash(
                metadata.get("hash_algorithm"),
                metadata.get("file_hash"),
            )
            item["modrinth_provenance_authoritative"] = False
            if file_hash is None:
                continue
            hash_algorithm, expected_digest = file_hash
            item["modrinth_hash_algorithm"] = hash_algorithm
            item["modrinth_file_hash"] = expected_digest
            item["modrinth_provenance_authoritative"] = verify_file_hash(
                Path(item["path"]),
                hash_algorithm,
                expected_digest,
            )
        return items

    def _modrinth_metadata_path(self, version, *, create: bool = False) -> Path | None:
        version_root = self._resolve_version_dir(version)
        if version_root is None:
            return None
        metadata_dir = version_root / ".tensalauncher"
        if create:
            metadata_dir.mkdir(parents=True, exist_ok=True)
        return metadata_dir / self.MODRINTH_METADATA_FILE

    def get_version_directory(self, version) -> Path | None:
        return self._resolve_version_dir(version)

    def get_modrinth_metadata_path(self, version) -> Path | None:
        return self._modrinth_metadata_path(version)

    @staticmethod
    def _load_modrinth_metadata(index_path: Path) -> dict[str, Any]:
        if not index_path.exists():
            return {"files": {}}
        try:
            with index_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {"files": {}}
        return data if isinstance(data, dict) else {"files": {}}

    @staticmethod
    def _write_modrinth_metadata(index_path: Path, data: dict[str, Any]) -> None:
        atomic_write_json(index_path, data, ensure_ascii=False, indent=2)

    @staticmethod
    def _relative_content_path(version_root: Path, file_path: Path) -> str:
        try:
            return file_path.relative_to(version_root).as_posix()
        except ValueError:
            return file_path.name

    @staticmethod
    def _strict_relative_content_path(version_root: Path, file_path: Path) -> str:
        resolved_root = version_root.resolve()
        resolved_path = file_path.resolve()
        try:
            return resolved_path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Modrinth content path is outside the version directory: {file_path}") from exc

    def _metadata_path_candidates(self, version_root: Path, file_path: Path) -> list[str]:
        relative_path = self._relative_content_path(version_root, file_path)
        return self._metadata_path_candidates_from_relative(relative_path)

    @staticmethod
    def _metadata_path_candidates_from_relative(relative_path: str) -> list[str]:
        candidates = [relative_path]
        if relative_path.endswith(".disabled"):
            candidates.append(relative_path[:-9])
        return candidates

    @staticmethod
    def _install_file_hash(install_file: Any) -> tuple[str, str] | None:
        algorithm = getattr(install_file, "hash_algorithm", "")
        digest = getattr(install_file, "file_hash", "")
        if not algorithm and not digest:
            return None
        normalized = normalize_file_hash(algorithm, digest)
        if normalized is None:
            raise ValueError("Modrinth install file has an invalid content hash")
        return normalized

    @staticmethod
    def _metadata_hash_source(
        version_root: Path,
        file_path: Path,
        relative_path: str,
        output_path: Path | None,
    ) -> Path:
        if output_path is not None:
            work_root = (version_root / ".tensalauncher-sync").resolve()
            resolved_output = output_path.resolve()
            for ancestor in resolved_output.parents:
                if ancestor.name != "stage" or ancestor.parent.parent.resolve() != work_root:
                    continue
                staged_path = ancestor.joinpath(*PurePosixPath(relative_path).parts)
                if staged_path.is_file():
                    return staged_path
                break
        return file_path

    def read_mod_metadata(self, jar_path: Path) -> dict[str, Any]:
        # Display metadata only; ownership and installation still verify the file hash.
        key = jar_path.absolute()
        fingerprint = self._metadata_fingerprint(jar_path)
        with self._metadata_lock:
            cached = self._mod_metadata.get(key)
            if fingerprint is not None and cached is not None and cached[0] == fingerprint:
                self._mod_metadata.move_to_end(key)
                return dict(cached[1])
            self._mod_metadata.pop(key, None)

        inspection = inspect_mod_jar(jar_path)
        descriptor = inspection.primary_descriptor
        if descriptor is None:
            reason = inspection.error_kind.value if inspection.error_kind is not None else "missing descriptor"
            self._log("debug", f"Failed to read mod metadata from {jar_path.name}: {reason}")
            return {}
        metadata = {
            "name": descriptor.name or "",
            "version": descriptor.version or "",
            "description": descriptor.description or "",
            "id": descriptor.mod_id,
        }
        if (
            fingerprint is not None
            and sum(len(value) for value in metadata.values()) <= self.MOD_METADATA_MAX_CHARS
            and fingerprint == self._metadata_fingerprint(jar_path)
        ):
            with self._metadata_lock:
                self._mod_metadata[key] = (fingerprint, dict(metadata))
                self._mod_metadata.move_to_end(key)
                while len(self._mod_metadata) > self.MOD_METADATA_CACHE_SIZE:
                    self._mod_metadata.popitem(last=False)
        return metadata

    @staticmethod
    def _metadata_fingerprint(path: Path) -> tuple[int, ...] | None:
        try:
            info = path.stat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def toggle_mod(self, mod: dict[str, Any]) -> bool:
        mod_path = Path(mod["path"])
        if mod["enabled"]:
            mod_path.rename(Path(f"{mod_path}.disabled"))
            return False
        if str(mod_path).endswith(".disabled"):
            mod_path.rename(Path(str(mod_path)[:-9]))
        return True

    def has_backup(self, mods_dir: Path | None, filename: str) -> bool:
        backup_dir = self.get_backup_directory(mods_dir)
        if backup_dir is None:
            return False
        return (backup_dir / f"{filename}.backup").exists()

    def create_backup(self, mods_dir: Path | None, mod: dict[str, Any]) -> bool:
        backup_dir = self.get_backup_directory(mods_dir)
        if backup_dir is None:
            return False

        mod_path = Path(mod["path"])
        backup_path = backup_dir / f"{mod['filename']}.backup"
        atomic_copy_file(mod_path, backup_path)
        self._log("info", f"Backup created for {mod['filename']}")
        return True

    def restore_backup(self, mods_dir: Path | None, mod: dict[str, Any]) -> None:
        backup_dir = self.get_backup_directory(mods_dir)
        if backup_dir is None:
            raise FileNotFoundError("Backup directory is not available")
        backup_path = backup_dir / f"{mod['filename']}.backup"
        if not backup_path.exists():
            raise FileNotFoundError("Backup file was not found")
        atomic_copy_file(backup_path, Path(mod["path"]))

    @staticmethod
    def delete_mod(mod: dict[str, Any]) -> None:
        Path(mod["path"]).unlink()

    def toggle_resourcepack(self, resourcepacks_dir: Path | None, resourcepack: dict[str, Any]) -> bool:
        if resourcepacks_dir is None:
            raise FileNotFoundError("Content directory is not available")
        self._restore_disabled_pack_file(resourcepack)
        options_path = self._options_path_for_content_dir(resourcepacks_dir)
        entry = self._resourcepack_entry(resourcepack["filename"])
        entries = self._read_options_list(options_path, "resourcePacks")
        enabled = entry in entries if options_path is not None else bool(resourcepack.get("enabled"))
        if enabled:
            self._remove_options_entries(options_path, "resourcePacks", {entry})
            self._remove_options_entries(options_path, "incompatibleResourcePacks", {entry})
            return False
        if entry not in entries:
            entries.append(entry)
        self._write_options_list(options_path, "resourcePacks", entries)
        return True

    def toggle_shaderpack(self, shaderpacks_dir: Path | None, shaderpack: dict[str, Any]) -> bool:
        if shaderpacks_dir is None:
            raise FileNotFoundError("Content directory is not available")
        config_path = self._iris_properties_path(shaderpacks_dir)
        if shaderpack.get("enabled", False):
            self._write_properties(config_path, {"enableShaders": "false"})
            return False
        self._write_properties(
            config_path,
            {
                "enableShaders": "true",
                "shaderPack": shaderpack["filename"],
            },
        )
        return True

    def _toggle_pack(self, directory: Path | None, pack: dict[str, Any], *, folder_type: str) -> bool:
        pack_path = Path(pack["path"])
        if pack["type"] != folder_type:
            if pack.get("enabled", True):
                pack_path.rename(Path(f"{pack_path}.disabled"))
                return False
            if str(pack_path).endswith(".disabled"):
                pack_path.rename(Path(str(pack_path)[:-9]))
            return True

        if directory is None:
            raise FileNotFoundError("Content directory is not available")
        disabled_marker = directory / f"{pack['filename']}.disabled"
        if pack.get("enabled", True):
            disabled_marker.touch()
            return False
        if disabled_marker.exists():
            disabled_marker.unlink()
        return True

    def delete_resourcepack(self, resourcepacks_dir: Path | None, resourcepack: dict[str, Any]) -> None:
        if resourcepacks_dir is not None:
            entry = self._resourcepack_entry(resourcepack["filename"])
            options_path = self._options_path_for_content_dir(resourcepacks_dir)
            self._remove_options_entries(options_path, "resourcePacks", {entry})
            self._remove_options_entries(options_path, "incompatibleResourcePacks", {entry})
        self._delete_pack(resourcepacks_dir, resourcepack, folder_type="resourcepack_folder")

    def delete_shaderpack(self, shaderpacks_dir: Path | None, shaderpack: dict[str, Any]) -> None:
        if shaderpacks_dir is not None and shaderpack.get("enabled", False):
            self._write_properties(self._iris_properties_path(shaderpacks_dir), {"enableShaders": "false"})
        self._delete_pack(shaderpacks_dir, shaderpack, folder_type="shaderpack_folder")

    def _delete_pack(self, directory: Path | None, pack: dict[str, Any], *, folder_type: str) -> None:
        pack_path = Path(pack["path"])
        if pack["type"] == folder_type:
            shutil.rmtree(pack_path)
            if directory is not None:
                disabled_marker = directory / f"{pack['filename']}.disabled"
                if disabled_marker.exists():
                    disabled_marker.unlink()
            return
        pack_path.unlink()

    @staticmethod
    def _options_path_for_content_dir(directory: Path | None) -> Path | None:
        if directory is None:
            return None
        return directory.parent / "options.txt"

    @staticmethod
    def _resourcepack_entry(filename: str) -> str:
        return f"file/{filename}"

    def _read_options_list(self, options_path: Path | None, key: str) -> list[str]:
        if options_path is None or not options_path.exists():
            return []
        prefix = f"{key}:"
        for line in options_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith(prefix):
                continue
            raw_value = line[len(prefix):].strip()
            try:
                data = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                self._log("debug", f"Failed to parse {key} from options.txt: {exc}")
                return []
            if not isinstance(data, list):
                return []
            return [str(item) for item in data]
        return []

    def _write_options_list(self, options_path: Path | None, key: str, values: list[str]) -> None:
        if options_path is None:
            raise FileNotFoundError("options.txt path is not available")

        options_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = f"{key}:"
        replacement = f"{prefix}{json.dumps(list(values), ensure_ascii=False, separators=(',', ':'))}"
        lines = options_path.read_text(encoding="utf-8", errors="replace").splitlines() if options_path.exists() else []
        replaced = False
        output: list[str] = []
        for line in lines:
            if line.startswith(prefix):
                if not replaced:
                    output.append(replacement)
                    replaced = True
                continue
            output.append(line)
        if not replaced:
            output.append(replacement)
        atomic_write_text(options_path, "\n".join(output) + "\n")

    def _remove_options_entries(self, options_path: Path | None, key: str, entries_to_remove: set[str]) -> None:
        entries = [entry for entry in self._read_options_list(options_path, key) if entry not in entries_to_remove]
        self._write_options_list(options_path, key, entries)

    @staticmethod
    def _restore_disabled_pack_file(pack: dict[str, Any]) -> None:
        pack_path = Path(pack["path"])
        if str(pack_path).endswith(".disabled"):
            restored_path = pack_path.with_name(pack["filename"])
            pack_path.rename(restored_path)
            pack["path"] = str(restored_path)

    @staticmethod
    def _iris_properties_path(shaderpacks_dir: Path | None) -> Path | None:
        if shaderpacks_dir is None:
            return None
        return shaderpacks_dir.parent / "config" / VersionContentService.IRIS_PROPERTIES_FILE

    @staticmethod
    def _shaderpack_enabled(filename: str, properties: dict[str, str]) -> bool:
        return (
            properties.get("enableShaders", "").strip().lower() == "true"
            and properties.get("shaderPack", "").strip() == filename
        )

    def _read_properties(self, properties_path: Path | None) -> dict[str, str]:
        if properties_path is None or not properties_path.exists():
            return {}
        properties: dict[str, str] = {}
        for line in properties_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
        return properties

    def _write_properties(self, properties_path: Path | None, updates: dict[str, str]) -> None:
        if properties_path is None:
            raise FileNotFoundError("Iris properties path is not available")
        properties_path.parent.mkdir(parents=True, exist_ok=True)
        lines = (
            properties_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if properties_path.exists()
            else []
        )
        pending = dict(updates)
        output: list[str] = []
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                output.append(line)
                continue
            key, _value = line.split("=", 1)
            stripped_key = key.strip()
            if stripped_key in pending:
                output.append(f"{stripped_key}={pending.pop(stripped_key)}")
            else:
                output.append(line)
        for key, value in pending.items():
            output.append(f"{key}={value}")
        atomic_write_text(properties_path, "\n".join(output) + "\n")

    def _resolve_version_dir(self, version) -> Path | None:
        if not version.path:
            return None
        path = Path(version.path)
        if path.is_absolute():
            return path
        return self.minecraft_dir / path
