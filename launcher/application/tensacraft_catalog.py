from __future__ import annotations

from launcher.core.versions import Version


class TensaCraftCatalogService:
    @staticmethod
    def filter_local_versions(versions: list[Version], *, show_tensacraft: bool) -> list[Version]:
        filtered: list[Version] = []
        for version in versions:
            if not version.is_tensacraft():
                filtered.append(version)
                continue
            if show_tensacraft or version.is_home_pinned():
                filtered.append(version)
        return filtered

    @staticmethod
    def pack_id(pack: dict) -> str:
        client = pack.get("client") if isinstance(pack, dict) else None
        candidates = (
            client.get("id") if isinstance(client, dict) else None,
            pack.get("slug") if isinstance(pack, dict) else None,
            pack.get("name") if isinstance(pack, dict) else None,
            client.get("ver_id") if isinstance(client, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @staticmethod
    def pack_description(pack: dict) -> str:
        client = pack.get("client") if isinstance(pack, dict) else None
        candidates = (
            pack.get("description") if isinstance(pack, dict) else None,
            pack.get("summary") if isinstance(pack, dict) else None,
            pack.get("short_description") if isinstance(pack, dict) else None,
            client.get("description") if isinstance(client, dict) else None,
            client.get("summary") if isinstance(client, dict) else None,
            client.get("short_description") if isinstance(client, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @staticmethod
    def build_stub(pack: dict, pack_id: str) -> Version:
        client = pack.get("client") if isinstance(pack.get("client"), dict) else {}
        name = str(pack.get("title") or client.get("name") or pack.get("name") or pack_id).strip()
        image = pack.get("image")
        if not image and isinstance(pack.get("client"), dict):
            image = pack["client"].get("image")
        version = Version(
            name,
            {
                "name": name,
                "version": pack_id,
                "client": "TensaCraft",
                "id": pack_id,
                "image": image,
            },
        )
        version.is_remote = True
        version.remote_pack_id = pack_id
        version.description = TensaCraftCatalogService.pack_description(pack)
        return version

    @staticmethod
    def local_pack_ids(versions: list[Version]) -> set[str]:
        return {
            version.id
            for version in versions
            if version.is_tensacraft() and version.id
        }

    @staticmethod
    def find_local(versions: list[Version], pack_id: str) -> Version | None:
        for version in versions:
            if version.is_tensacraft() and version.id == pack_id:
                return version
        return None
