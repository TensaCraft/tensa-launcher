from __future__ import annotations

from launcher.application.catalog import CatalogPage, CatalogState, ModrinthCatalogService


def test_catalog_state_tracks_pagination():
    state = CatalogState(limit=20)

    token = state.begin("fabric", 20)

    assert token == 1
    assert state.query == "fabric"
    assert state.offset == 20
    assert state.loading is True

    state.apply(CatalogPage(items=[{"slug": "a"}] * 20, total_results=75))

    assert state.loading is False
    assert state.current_page == 2
    assert state.total_pages == 4
    assert state.has_previous is True
    assert state.has_next is True
    assert state.previous_offset == 0
    assert state.next_offset == 40
    assert state.show_pagination is True


def test_catalog_state_cancel_invalidates_token():
    state = CatalogState(limit=16)

    first = state.begin("query", 0)
    state.cancel()
    second = state.begin("query", 16)

    assert first == 1
    assert second == 3
    assert state.offset == 16


def test_catalog_service_normalizes_modrinth_payload(monkeypatch):
    monkeypatch.setattr(
        "launcher.core.api.modrinth.ModrinthAPI.search_modpacks",
        lambda query, offset, limit: {
            "hits": [{"slug": "pack"}],
            "total_hits": 42,
        },
    )

    service = ModrinthCatalogService()
    page = service.search_modpacks("pack", offset=20, limit=20)

    assert page.total_results == 42
    assert page.items == [{"slug": "pack"}]
