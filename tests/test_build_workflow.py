from __future__ import annotations

from pathlib import Path


def test_build_workflow_has_no_store_msix_release_path():
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "BUILD_INSTALLER" in workflow
    assert "msix-store-packaging" not in workflow
    assert "BUILD_MSIX" not in workflow
    assert "--with-windows-msix" not in workflow
    assert "TensaLauncher.msix" not in workflow
