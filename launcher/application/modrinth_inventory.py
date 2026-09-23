from __future__ import annotations

from pathlib import Path
from typing import Any

from launcher.application.mod_identity import compute_file_hash
from launcher.core.api.modrinth import ModrinthAPI


def _fingerprint(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def identify_installed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identified = [dict(item) for item in items]
    files: list[tuple[dict, Path, str, tuple[int, int, int]]] = []
    for item in identified:
        if not item.get("path"):
            continue
        path = Path(item["path"])
        if not path.is_file() or path.is_symlink():
            item["modrinth_provenance_authoritative"] = False
            item.pop("modrinth_version_data", None)
            continue
        before = _fingerprint(path)
        digest = compute_file_hash(path, "sha512")
        if _fingerprint(path) != before:
            raise OSError(f"Content changed during identification: {path.name}")
        files.append((item, path, digest, before))

    versions = ModrinthAPI.get_versions_by_hashes([digest for _, _, digest, _ in files]) if files else {}
    for item, path, digest, before in files:
        if _fingerprint(path) != before:
            raise OSError(f"Content changed during identification: {path.name}")
        data = versions.get(digest)
        if data is None:
            item["modrinth_provenance_authoritative"] = bool(
                item.get("modrinth_provenance_authoritative")
                and item.get("modrinth_hash_algorithm") == "sha512"
                and item.get("modrinth_file_hash") == digest
            )
            continue
        matching_file = any(
            isinstance(file, dict)
            and isinstance(file.get("hashes"), dict)
            and file["hashes"].get("sha512") == digest
            and file.get("size") == before[0]
            for file in data.get("files", [])
        )
        if not matching_file or not data.get("project_id") or not data.get("id"):
            raise ValueError(f"Modrinth returned mismatched file metadata: {path.name}")
        item.update(
            modrinth_project_id=data["project_id"],
            modrinth_version_id=data["id"],
            modrinth_version_number=data.get("version_number", ""),
            modrinth_hash_algorithm="sha512",
            modrinth_file_hash=digest,
            modrinth_provenance_authoritative=True,
            modrinth_version_data=data,
        )
    return identified
