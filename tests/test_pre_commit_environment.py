import sys
from pathlib import Path

from tests.tools_import import load_tool_module


def test_checks_do_not_inherit_hook_repository_selection(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / ".tools"))
    tool = load_tool_module("pre_commit")
    monkeypatch.setenv("GIT_DIR", "/not-the-fixture")
    monkeypatch.setenv("GIT_INDEX_FILE", "/not-the-fixture-index")
    assert tool.run_check("isolated child", [sys.executable, "-c",
        "import os; assert 'GIT_DIR' not in os.environ; assert 'GIT_INDEX_FILE' not in os.environ"])
