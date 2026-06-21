from __future__ import annotations

from pathlib import Path


def test_build_workflow_has_no_store_msix_release_path():
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "BUILD_INSTALLER" in workflow
    assert "msix-store-packaging" not in workflow
    assert "BUILD_MSIX" not in workflow
    assert "--with-windows-msix" not in workflow
    assert "TensaLauncher.msix" not in workflow


def test_build_workflow_signs_windows_executables_with_signpath():
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "SIGNPATH_READY" in workflow
    assert "actions: read" in workflow
    assert "signpath/github-action-submit-signing-request@v2" in workflow
    assert "github-artifact-id: ${{ steps.upload-signpath-windows-input.outputs.artifact-id }}" in workflow
    assert "output-artifact-directory: ./.build/windows-signpath-signed" in workflow
    assert "TensaLauncher-Windows-Unsigned-SignPath" in workflow

    build_step = workflow.index("name: Build Windows artifact")
    sign_step = workflow.index("name: Sign Windows executables with SignPath")
    replace_step = workflow.index("name: Replace Windows executables with signed artifacts")
    smoke_step = workflow.index("name: Smoke Windows packaged runtime")
    upload_step = workflow.index("name: Upload platform artifact")

    assert build_step < sign_step < replace_step < smoke_step < upload_step
