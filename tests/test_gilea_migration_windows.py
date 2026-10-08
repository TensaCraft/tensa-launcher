import json
import subprocess
import sys
from pathlib import Path

import pytest

from launcher.platform import gilea_migration as windows
from launcher.platform.instance_shortcuts import _powershell

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows filesystem integration')


def test_backup_acl_is_protected_for_current_user(tmp_path, monkeypatch):
    def run(script, payload=None):
        try:
            return _powershell(script, payload)
        except subprocess.CalledProcessError as error:
            pytest.fail(error.stderr, pytrace=False)

    monkeypatch.setattr(windows, '_powershell', run)
    path = tmp_path / 'protected'
    windows.protect_directory(path)
    acl = json.loads(_powershell(
        "$p = ($env:TENSALAUNCHER_SHORTCUT_DATA | ConvertFrom-Json).path; "
        "$acl = [IO.Directory]::GetAccessControl($p); $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "@{ protected = $acl.AreAccessRulesProtected; owner = $acl.Owner; "
        "expected = $sid.Value; principals = @($acl.Access | ForEach-Object { "
        "$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }) } | ConvertTo-Json",
        {'path': str(path)},
    ))
    assert acl['protected'] is True
    assert acl['principals'] == [acl['expected']]


def test_source_guard_denies_write_until_released(tmp_path):
    path = tmp_path / 'fixture.json'
    path.write_text('{}')
    with windows.read_guard([path]):
        assert path.read_text() == '{}'
        with pytest.raises(PermissionError):
            path.write_text('changed')
    path.write_text('changed')
    assert path.read_text() == 'changed'


def test_process_probe_matches_current_executable_and_rejects_absent_path(tmp_path):
    assert windows._gilea_running(Path(sys.executable))
    assert not windows._gilea_running(tmp_path / 'not-running.exe')
