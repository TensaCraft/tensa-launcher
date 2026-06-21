from __future__ import annotations

from pathlib import Path

import launcher.core.async_downloader as downloader_module


def test_async_downloader_verifies_generic_hash_for_existing_files(tmp_path: Path):
    file_path = tmp_path / "mod.jar"
    file_path.write_bytes(b"hello")

    task = downloader_module.DownloadTask(
        url="https://example.com/mod.jar",
        destination=file_path,
        expected_hash="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        expected_hash_algorithm="sha256",
    )

    downloader = downloader_module.AsyncDownloader()

    assert downloader._should_skip(task, verify_sha1=True) is True


def test_async_downloader_prefers_requests_for_https(monkeypatch):
    task = downloader_module.DownloadTask(url="https://example.com/mod.jar", destination=Path("mod.jar"))
    downloader = downloader_module.AsyncDownloader()
    called: list[str] = []

    monkeypatch.setattr(downloader_module, "HAS_REQUESTS", True)
    monkeypatch.setattr(downloader_module.AsyncDownloader, "_download_file_requests", lambda self, _task: called.append("requests") or True)
    monkeypatch.setattr(downloader_module.AsyncDownloader, "_download_file_urllib", lambda self, _task: called.append("urllib") or True)

    assert downloader._download_file(task) is True
    assert called == ["requests"]


def test_async_downloader_reuses_thread_local_requests_session(monkeypatch):
    downloader = downloader_module.AsyncDownloader()
    created = []

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.mounts = []

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter))

    monkeypatch.setattr(downloader_module.requests, "Session", lambda: created.append(FakeSession()) or created[-1])

    session_one = downloader._requests_session()
    session_two = downloader._requests_session()

    assert session_one is session_two
    assert session_one.headers["User-Agent"] == downloader.user_agent
    assert len(created) == 1


def test_async_downloader_retries_incomplete_requests_stream(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=2, retry_delay=0)
    attempts = []

    class FakeResponse:
        def __init__(self, *, fail: bool):
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            if self.fail:
                yield b"he"
                raise downloader_module.requests.exceptions.ChunkedEncodingError("incomplete read")
            yield b"hello"

    class FakeSession:
        def get(self, *_args, **_kwargs):
            attempts.append("get")
            return FakeResponse(fail=len(attempts) == 1)

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file_requests(task) is True
    assert attempts == ["get", "get"]
    assert task.destination.read_bytes() == b"hello"
    assert not task.destination.with_suffix(".jar.tmp").exists()


def test_async_downloader_resumes_incomplete_requests_stream(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=2, retry_delay=0)
    request_headers = []

    class FakeResponse:
        def __init__(self, *, chunks, status_code=200):
            self._chunks = chunks
            self.status_code = status_code

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for chunk in self._chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk

    class FakeSession:
        def get(self, *_args, **kwargs):
            headers = kwargs.get("headers") or {}
            request_headers.append(dict(headers))
            if len(request_headers) == 1:
                return FakeResponse(
                    chunks=[
                        b"he",
                        downloader_module.requests.exceptions.ChunkedEncodingError(
                            "incomplete read"
                        ),
                    ]
                )
            return FakeResponse(chunks=[b"llo"], status_code=206)

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file_requests(task) is True
    assert request_headers[0].get("Range") is None
    assert request_headers[1]["Range"] == "bytes=2-"
    assert task.destination.read_bytes() == b"hello"
    assert not (tmp_path / "client.jar.part.tmp").exists()


def test_async_downloader_ignores_locked_stale_temp_file(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    stale_temp = task.destination.with_suffix(".jar.tmp")
    stale_temp.write_bytes(b"stale")
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1, retry_delay=0)
    original_unlink = Path.unlink

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"hello"

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    def fake_unlink(self, *args, **kwargs):
        if self == stale_temp:
            raise PermissionError("[WinError 32] The process cannot access the file")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())
    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert downloader._download_file_requests(task) is True
    assert task.destination.read_bytes() == b"hello"
