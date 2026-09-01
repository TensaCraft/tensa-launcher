from __future__ import annotations

from pathlib import Path
from typing import Any

from launcher.application.file_sync_journal import FileSyncJournal
from launcher.application.version_snapshot import COPY_SYNC_MODE


class TensaCraftProfileIdentity:
    """Keep the catalog identity separate from the selected game component."""

    PACK_OPTION_KEYS = ("tensacraftPackId", "tensacraft_pack_id")

    @classmethod
    def pack_id(cls, version: Any) -> str:
        options = getattr(version, "options", None)
        option_pack_id = None
        if isinstance(options, dict):
            option_pack_id = next(
                (options.get(key) for key in cls.PACK_OPTION_KEYS if options.get(key)),
                None,
            )
        for candidate in (
            getattr(version, "remote_pack_id", None),
            option_pack_id,
            getattr(version, "id", None),
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return ""

    @classmethod
    def is_managed(
        cls,
        version: Any,
        *,
        minecraft_dir: str | Path | None = None,
    ) -> bool:
        options = getattr(version, "options", None)
        options = options if isinstance(options, dict) else {}
        if options.get("managedByApi") is False:
            return False
        if str(options.get("syncMode") or "").strip().lower() == COPY_SYNC_MODE:
            return False

        client = str(getattr(version, "client", "") or "").strip().lower()
        if "tensa" in client:
            return True
        if getattr(version, "remote_pack_id", None):
            return True
        if any(options.get(key) for key in cls.PACK_OPTION_KEYS):
            return True

        root = cls._profile_root(version, minecraft_dir=minecraft_dir)
        if root is None:
            return False
        journal = FileSyncJournal(root).read() or {}
        operation = str(journal.get("operation") or "").strip().lower()
        return operation.startswith("tensacraft_")

    @classmethod
    def mark(cls, version: Any, pack_id: str) -> bool:
        normalized_pack_id = str(pack_id or "").strip()
        changed = False

        if str(getattr(version, "client", "") or "").strip().lower() != "tensacraft":
            version.client = "TensaCraft"
            changed = True
        if normalized_pack_id and getattr(version, "remote_pack_id", None) != normalized_pack_id:
            version.remote_pack_id = normalized_pack_id
            changed = True

        options = dict(getattr(version, "options", None) or {})
        if options.get("managedByApi") is not True:
            options["managedByApi"] = True
            changed = True
        if normalized_pack_id and options.get("tensacraftPackId") != normalized_pack_id:
            options["tensacraftPackId"] = normalized_pack_id
            changed = True
        if changed:
            version.options = options
        return changed

    @classmethod
    def repair(
        cls,
        version: Any,
        *,
        minecraft_dir: str | Path | None = None,
        persist: bool = False,
    ) -> bool:
        if not cls.is_managed(version, minecraft_dir=minecraft_dir):
            return False
        changed = cls.mark(version, cls.pack_id(version))
        if changed and persist:
            version.save()
        return changed

    @staticmethod
    def _profile_root(
        version: Any,
        *,
        minecraft_dir: str | Path | None,
    ) -> Path | None:
        raw_path = str(getattr(version, "path", "") or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute() and minecraft_dir is not None:
            path = Path(minecraft_dir) / path
        return path


__all__ = ["TensaCraftProfileIdentity"]
