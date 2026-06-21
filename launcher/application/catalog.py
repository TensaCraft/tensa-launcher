from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from launcher.core.api.modrinth import ModrinthAPI


@dataclass(slots=True)
class CatalogPage:
    items: list[dict[str, Any]] = field(default_factory=list)
    total_results: int = 0


@dataclass(slots=True)
class CatalogState:
    limit: int
    query: str = ""
    offset: int = 0
    total_results: int = 0
    current_page_size: int = 0
    loading: bool = False
    token: int = 0

    def begin(self, query: str, offset: int) -> int:
        self.query = (query or "").strip()
        self.offset = max(offset, 0)
        self.current_page_size = 0
        if self.offset == 0:
            self.total_results = 0
        self.loading = True
        self.token += 1
        return self.token

    def cancel(self) -> None:
        self.loading = False
        self.token += 1

    def fail(self, *, clear_results: bool = False) -> None:
        self.loading = False
        if clear_results:
            self.total_results = 0
            self.current_page_size = 0

    def apply(self, page: CatalogPage) -> None:
        self.total_results = max(int(page.total_results or 0), len(page.items))
        self.current_page_size = len(page.items)
        self.loading = False

    @property
    def has_previous(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + self.current_page_size < self.total_results

    @property
    def previous_offset(self) -> int:
        return max(self.offset - self.limit, 0)

    @property
    def next_offset(self) -> int:
        return self.offset + self.limit

    @property
    def total_pages(self) -> int:
        if not self.total_results:
            return 1
        return max(math.ceil(self.total_results / self.limit), 1)

    @property
    def current_page(self) -> int:
        return min((self.offset // self.limit) + 1, self.total_pages)

    @property
    def show_pagination(self) -> bool:
        return self.total_results > self.limit


class ModrinthCatalogService:
    def search_modpacks(self, query: str = "", *, offset: int = 0, limit: int = 20) -> CatalogPage:
        payload = ModrinthAPI.search_modpacks(query, offset, limit)
        return self._page_from_payload(payload)

    def search_mods(
        self,
        query: str = "",
        *,
        facets: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> CatalogPage:
        payload = ModrinthAPI.search_mods(
            query=query,
            facets=facets,
            offset=offset,
            limit=limit,
        )
        return self._page_from_payload(payload)

    @staticmethod
    def _page_from_payload(payload: dict[str, Any] | None) -> CatalogPage:
        data = payload or {}
        items = data.get("hits", [])
        total_results = int(data.get("total_hits", len(items)) or len(items))
        return CatalogPage(items=list(items), total_results=total_results)
