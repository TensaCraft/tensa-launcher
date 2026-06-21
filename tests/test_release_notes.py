from __future__ import annotations

from tools_import import load_tool_module

release_notes = load_tool_module("release_notes")


def test_release_notes_groups_conventional_commits():
    notes = release_notes.build_notes(
        [
            release_notes.CommitEntry("1", "release: bump version to 4.0.10"),
            release_notes.CommitEntry("2", "fix(updater): select stable release when newer than beta"),
            release_notes.CommitEntry("3", "ui(home): balance version card title and icon spacing"),
            release_notes.CommitEntry("4", "feat(reports): show confirmation after sending crash report"),
            release_notes.CommitEntry("5", "build(release): generate grouped release notes from commits"),
            release_notes.CommitEntry("6", "ci(build): improve installer artifact upload workflow"),
            release_notes.CommitEntry("7", "docs(release): document release note commit rules"),
            release_notes.CommitEntry("5", "up"),
            release_notes.CommitEntry("6", "Bump version to 4.0.10"),
        ],
        tag="v4.0.10",
        previous="v4.0.9",
    )

    assert "# v4.0.10" in notes
    assert "Changes since `v4.0.9`." in notes
    assert "## New Features" in notes
    assert "- **Reports:** Show confirmation after sending crash report" in notes
    assert "## Fixes" in notes
    assert "- **Updater:** Select stable release when newer than beta" in notes
    assert "## Interface" in notes
    assert "- **Home:** Balance version card title and icon spacing" in notes
    assert "bump version" not in notes
    assert "Bump version" not in notes
    assert "\n- Up" not in notes
    assert "Technical Changes" not in notes
    assert "Documentation" not in notes
    assert "Downloads" not in notes
    assert "Generate grouped release notes" not in notes
    assert "Improve installer artifact upload workflow" not in notes
    assert "Document release note commit rules" not in notes


def test_release_notes_falls_back_to_maintenance_release_when_no_public_changes():
    notes = release_notes.build_notes(
        [
            release_notes.CommitEntry("1", "release: bump version to 4.0.10"),
            release_notes.CommitEntry("2", "chore: update internal metadata only"),
        ],
        tag="v4.0.10",
        previous=None,
    )

    assert "## Changes" in notes
    assert "- Maintenance release." in notes
    assert "update internal metadata" not in notes
    assert "Downloads" not in notes


def test_release_notes_omit_microsoft_store_packaging_changes():
    notes = release_notes.build_notes(
        [
            release_notes.CommitEntry("1", "fix(updater): separate Store package updates from self updater"),
            release_notes.CommitEntry("2", "fix(store): keep launcher data outside package directory"),
            release_notes.CommitEntry("3", "ui(msix): make splash screen transparent"),
            release_notes.CommitEntry("4", "fix(storage): standardize paths and backup restore"),
        ],
        tag="v4.2.0",
        previous="v4.1.5",
    )

    assert "Store package" not in notes
    assert "Store:" not in notes
    assert "Msix:" not in notes
    assert "splash screen" not in notes
    assert "- **Storage:** Standardize paths and backup restore" in notes


def test_previous_stable_release_tag_skips_prereleases(monkeypatch):
    monkeypatch.setattr(
        release_notes,
        "github_releases",
        lambda _repo, _token: [
            {"tag_name": "v4.0.31", "draft": False, "prerelease": True},
            {"tag_name": "v4.0.30", "draft": False, "prerelease": False},
            {"tag_name": "v4.0.29", "draft": False, "prerelease": False},
        ],
    )
    monkeypatch.setattr(
        release_notes,
        "resolve_ref",
        lambda ref: {
            "v4.0.32": "target",
            "v4.0.31": "prerelease",
            "v4.0.30": "stable",
            "v4.0.29": "older-stable",
        }[ref],
    )
    monkeypatch.setattr(
        release_notes,
        "is_ancestor",
        lambda older_ref, newer_ref: older_ref == "stable" and newer_ref == "target",
    )

    assert release_notes.previous_stable_release_tag(
        "v4.0.32",
        repo="TensaCraft/TensaLauncher",
        token="token",
    ) == "v4.0.30"
