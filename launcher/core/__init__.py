from __future__ import annotations


def __getattr__(name: str):
    if name == "Launcher":
        from .launcher import Launcher

        return Launcher
    raise AttributeError(name)
