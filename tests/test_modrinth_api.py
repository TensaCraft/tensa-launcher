from __future__ import annotations

from pathlib import Path

from launcher.core.api.modrinth import ModrinthAPI


class FakeResponse:
    headers = {"content-length": "10"}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield b"abc"
        yield b"defghij"


def test_modrinth_download_reports_byte_progress(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "launcher.core.api.modrinth.requests.get",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    events = []
    target = tmp_path / "pack.zip"

    ModrinthAPI.download_mod_file(
        "https://example.com/pack.zip",
        target,
        progress_callback=lambda completed, total, filename: events.append((completed, total, filename)),
    )

    assert target.read_bytes() == b"abcdefghij"
    assert events[0] == (0, 10, "pack.zip")
    assert events[-1] == (10, 10, "pack.zip")
