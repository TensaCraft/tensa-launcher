from __future__ import annotations

from contextlib import contextmanager
import ctypes
import sys
import threading
from typing import Iterator


SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
SEM_NOOPENFILEERRORBOX = 0x8000

_ERROR_MODE = SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
_PROCESS_ERROR_MODE_LOCK = threading.RLock()


@contextmanager
def suppress_windows_error_dialogs() -> Iterator[None]:
    """Suppress Windows loader popups while probing or launching child processes.

    A broken Java runtime can fail before Python receives a return code and show
    a modal "java.exe - System Error" dialog. Java probes and installer launches
    must run with Windows error dialogs suppressed so the launcher can handle
    the failure itself.
    """
    if sys.platform != "win32":
        yield
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_thread_error_mode = getattr(kernel32, "SetThreadErrorMode", None)

    with _PROCESS_ERROR_MODE_LOCK:
        previous_mode = kernel32.SetErrorMode(_ERROR_MODE)
        old_thread_mode = ctypes.c_uint(0)
        thread_mode_set = False
        if set_thread_error_mode is not None:
            thread_mode_set = bool(set_thread_error_mode(_ERROR_MODE, ctypes.byref(old_thread_mode)))

        try:
            yield
        finally:
            if thread_mode_set:
                set_thread_error_mode(old_thread_mode.value, None)
            kernel32.SetErrorMode(previous_mode)
