import asyncio

import pytest

from launcher.application.modrinth_mods import (
    ModInstallFile,
    ModrinthDependencyIssue,
    ModrinthDependencyPlan,
    ModrinthInstallCandidate,
)
from launcher.pages.mods_manager import ModsManagerPage


def candidate(project_id):
    return ModrinthInstallCandidate(
        project={"project_id": project_id, "title": project_id},
        version_data={"id": f"{project_id}-version"},
        install_file=ModInstallFile(f"https://example.com/{project_id}.jar", f"{project_id}.jar"),
        action="install",
    )


@pytest.mark.parametrize("blocked", [False, True])
def test_optional_changes_require_confirmation_before_any_download(fake_app, monkeypatch, blocked):
    page = ModsManagerPage(fake_app, fake_app.versions.all()[0])
    main, optional, dependency = (candidate(name) for name in ("main", "optional", "dependency"))
    original = ModrinthDependencyPlan(main, [], [], [], [optional], [], [])
    resolved = ModrinthDependencyPlan(main, [dependency, optional], [], [], [], [], [])
    if blocked:
        resolved.blocking_issues.append(ModrinthDependencyIssue("dependency_version_conflict", "conflict"))
    confirmations, installed, calls = [], [], []

    def resolve(plan, selected, context):
        calls.append((plan, selected, context["version"]))
        return resolved

    async def install(items, _context):
        installed.extend(items)

    async def refresh():
        pass

    monkeypatch.setattr(page, "_resolve_optional_install", resolve)
    monkeypatch.setattr("launcher.pages.mods_manager_search.invoke_on_ui", lambda _page, callback, *args: callback(*args))
    monkeypatch.setattr(page, "_show_modrinth_dependency_plan_dialog", lambda plan, ctx: confirmations.append(plan))
    monkeypatch.setattr(page, "_install_modrinth_candidates_transaction", install)
    monkeypatch.setattr(page, "_refresh_installed_mods_after_mutation", refresh)

    asyncio.run(page._install_modrinth_plan_async(original, page._content_context(), [optional]))

    assert calls == [(original, [optional], page.version)]
    assert confirmations == [resolved]
    assert installed == []
    if not blocked:
        asyncio.run(page._install_modrinth_plan_async(resolved, page._content_context()))
        assert installed == [dependency, optional, main]


def test_optional_resolution_rescans_disk_instead_of_using_old_ui_list(fake_app, monkeypatch):
    page = ModsManagerPage(fake_app, fake_app.versions.all()[0])
    fresh = [{"filename": "installed-after-dialog.jar"}]
    plan = ModrinthDependencyPlan(candidate("main"), [], [], [], [], [], [])
    calls = []
    monkeypatch.setattr(page, "_scan_modrinth_inventory", lambda context: fresh)
    fake_app.modrinth_mods.resolve_optional_dependencies = (
        lambda *args, **kwargs: calls.append((args, kwargs)) or plan
    )
    context = {**page._content_context(), "version": page.version}

    assert page._resolve_optional_install(plan, [], context) is plan
    assert calls[0][0] == (plan, [], page.version)
    assert calls[0][1]["installed_items"] is fresh
