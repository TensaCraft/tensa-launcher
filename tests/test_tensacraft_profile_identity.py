from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from launcher.application.tensacraft_profile_identity import TensaCraftProfileIdentity


class FakeVersion(SimpleNamespace):
    def save(self) -> None:
        self.saved = getattr(self, "saved", 0) + 1


def _version(path: Path, *, options: dict | None = None) -> FakeVersion:
    return FakeVersion(
        version_id="aeronautics-profile",
        id="aeronautics",
        client="neoforge",
        path=str(path),
        options=options or {},
        remote_pack_id=None,
        saved=0,
    )


def test_repairs_legacy_profile_from_tensacraft_sync_journal(tmp_path: Path):
    version = _version(tmp_path)
    (tmp_path / ".tensalauncher-sync.json").write_text(
        json.dumps({"status": "complete", "operation": "tensacraft_sync"}),
        encoding="utf-8",
    )

    assert TensaCraftProfileIdentity.repair(version, persist=True) is True
    assert version.client == "TensaCraft"
    assert version.remote_pack_id == "aeronautics"
    assert version.options["tensacraftPackId"] == "aeronautics"
    assert version.options["managedByApi"] is True
    assert version.saved == 1


def test_does_not_repair_intentional_manual_copy(tmp_path: Path):
    version = _version(
        tmp_path,
        options={"syncMode": "manual", "managedByApi": False},
    )
    (tmp_path / ".tensalauncher-sync.json").write_text(
        json.dumps({"status": "complete", "operation": "tensacraft_sync"}),
        encoding="utf-8",
    )

    assert TensaCraftProfileIdentity.repair(version, persist=True) is False
    assert version.client == "neoforge"
    assert version.saved == 0
