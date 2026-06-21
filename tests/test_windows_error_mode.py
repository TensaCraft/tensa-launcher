from __future__ import annotations

from launcher.platform import windows_error_mode


def test_windows_error_dialog_suppression_sets_process_error_mode(monkeypatch):
    events = []

    class FakeKernel32:
        def SetThreadErrorMode(self, mode, old_mode):
            events.append(("thread", mode))
            if old_mode is not None:
                old_mode._obj.value = 0x20
            return 1

        def SetErrorMode(self, mode):
            events.append(("process", mode))
            return 0x40

    monkeypatch.setattr(windows_error_mode.sys, "platform", "win32")
    monkeypatch.setattr(windows_error_mode.ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32(), raising=False)

    with windows_error_mode.suppress_windows_error_dialogs():
        events.append(("inside", None))

    assert ("process", windows_error_mode._ERROR_MODE) in events
    assert ("thread", windows_error_mode._ERROR_MODE) in events
    assert events[-1] == ("process", 0x40)
