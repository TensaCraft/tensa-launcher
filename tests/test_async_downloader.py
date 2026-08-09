from __future__ import annotations

import json
from pathlib import Path

import pytest

import launcher.core.async_downloader as downloader_module
from launcher.application.storage_preflight import StoragePreflightError


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


def test_async_downloader_accepts_generic_existing_hash_flag(tmp_path: Path):
    file_path = tmp_path / "mod.jar"
    file_path.write_bytes(b"hello")
    task = downloader_module.DownloadTask(
        url="https://example.com/mod.jar",
        destination=file_path,
        expected_hash="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        expected_hash_algorithm="sha256",
    )
    downloader = downloader_module.AsyncDownloader()

    result = downloader.download_files(
        [task],
        skip_existing=True,
        verify_existing_hash=True,
    )

    assert result == {"success": 0, "failed": 0, "skipped": 1, "errors": []}


def test_async_downloader_stops_before_workers_when_storage_preflight_fails(
    monkeypatch,
    tmp_path: Path,
):
    tasks = [
        downloader_module.DownloadTask(
            url="https://example.com/one.jar",
            destination=tmp_path / "one.jar",
            expected_size=100,
        ),
        downloader_module.DownloadTask(
            url="https://example.com/two.jar",
            destination=tmp_path / "two.jar",
            expected_size=200,
        ),
    ]
    downloader = downloader_module.AsyncDownloader(max_workers=1)
    started: list[str] = []

    monkeypatch.setattr(
        downloader_module,
        "ensure_storage_available",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StoragePreflightError("disk full")
        ),
    )
    monkeypatch.setattr(
        downloader,
        "_download_file",
        lambda task: started.append(task.task_id) or True,
    )

    result = downloader.download_files(tasks)

    assert started == []
    assert result["failed"] == 2
    assert result["success"] == 0
    assert all("disk full" in error for error in result["errors"])


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


def test_async_downloader_uses_post_and_isolates_request_bodies(monkeypatch, tmp_path: Path):
    destination = tmp_path / "client.jar"
    first = downloader_module.DownloadTask(
        url="https://example.com/client",
        destination=destination,
        expected_size=5,
        post_data={"build": "first"},
    )
    second = downloader_module.DownloadTask(
        url=first.url,
        destination=destination,
        expected_size=5,
        post_data={"build": "second"},
    )
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {}

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
            raise AssertionError("POST task must not use GET")

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    downloader = downloader_module.AsyncDownloader(max_retries=1)
    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(first) is True
    assert calls[0][1]["data"] == {"build": "first"}
    assert downloader._partial_file_for(first) != downloader._partial_file_for(second)


def test_async_downloader_retries_incomplete_requests_stream(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=2, retry_delay=0)
    request_headers = []

    class FakeResponse:
        def __init__(self, *, fail: bool):
            self.fail = fail
            self.status_code = 200
            self.headers = {}

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
        def get(self, *_args, **kwargs):
            request_headers.append(dict(kwargs.get("headers") or {}))
            return FakeResponse(fail=len(request_headers) == 1)

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert len(request_headers) == 2
    assert all("Range" not in headers for headers in request_headers)
    assert all("If-Range" not in headers for headers in request_headers)
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
        def __init__(self, *, chunks, status_code=200, headers=None):
            self._chunks = chunks
            self.status_code = status_code
            self.headers = headers or {}

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
                    ],
                    headers={"ETag": '"client-v1"'},
                )
            return FakeResponse(
                chunks=[b"llo"],
                status_code=206,
                headers={
                    "ETag": '"client-v1"',
                    "Content-Range": "bytes 2-4/5",
                    "Content-Length": "3",
                },
            )

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert request_headers[0].get("Range") is None
    assert request_headers[1]["Range"] == "bytes=2-"
    assert request_headers[1]["If-Range"] == '"client-v1"'
    assert task.destination.read_bytes() == b"hello"
    assert not downloader._partial_file_for(task).exists()
    assert not downloader._partial_metadata_file_for(task).exists()


@pytest.mark.parametrize(
    "content_range",
    [
        None,
        "bytes 1-3/5",
        "bytes 2-3/5",
        "bytes 2-5/5",
        "bytes 2-4/*",
    ],
)
def test_async_downloader_restarts_when_resume_range_is_unsafe(
    monkeypatch,
    tmp_path: Path,
    content_range: str | None,
):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(
        max_workers=1,
        max_retries=1,
        retry_delay=0,
    )
    partial = downloader._prepare_partial_file(task)
    partial.write_bytes(b"he")
    metadata_path = downloader._partial_metadata_file_for(task)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["etag"] = '"client-v1"'
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    request_headers = []

    class FakeResponse:
        def __init__(self, *, chunks, status_code, headers):
            self._chunks = chunks
            self.status_code = status_code
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield from self._chunks

    class FakeSession:
        def get(self, *_args, **kwargs):
            request_headers.append(dict(kwargs.get("headers") or {}))
            if len(request_headers) == 1:
                headers = {"ETag": '"client-v1"'}
                if content_range is not None:
                    headers["Content-Range"] = content_range
                return FakeResponse(
                    chunks=[b"unsafe"],
                    status_code=206,
                    headers=headers,
                )
            return FakeResponse(
                chunks=[b"hello"],
                status_code=200,
                headers={"ETag": '"client-v1"'},
            )

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert request_headers[0]["Range"] == "bytes=2-"
    assert request_headers[0]["If-Range"] == '"client-v1"'
    assert "Range" not in request_headers[1]
    assert "If-Range" not in request_headers[1]
    assert task.destination.read_bytes() == b"hello"


def test_async_downloader_restarts_when_resume_etag_changes(
    monkeypatch,
    tmp_path: Path,
):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(
        max_workers=1,
        max_retries=1,
        retry_delay=0,
    )
    partial = downloader._prepare_partial_file(task)
    partial.write_bytes(b"he")
    metadata_path = downloader._partial_metadata_file_for(task)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["etag"] = '"client-v1"'
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    request_headers = []

    class FakeResponse:
        def __init__(self, *, chunks, status_code, headers):
            self._chunks = chunks
            self.status_code = status_code
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield from self._chunks

    class FakeSession:
        def get(self, *_args, **kwargs):
            request_headers.append(dict(kwargs.get("headers") or {}))
            if len(request_headers) == 1:
                return FakeResponse(
                    chunks=[b"rld"],
                    status_code=206,
                    headers={
                        "ETag": '"client-v2"',
                        "Content-Range": "bytes 2-4/5",
                    },
                )
            return FakeResponse(
                chunks=[b"world"],
                status_code=200,
                headers={"ETag": '"client-v2"'},
            )

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert request_headers[0]["If-Range"] == '"client-v1"'
    assert "Range" not in request_headers[1]
    assert task.destination.read_bytes() == b"world"


def test_async_downloader_partial_identity_changes_with_remote_artifact(tmp_path: Path):
    destination = tmp_path / "client.jar"
    base = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=destination,
        expected_size=5,
        expected_hash="a" * 40,
        expected_hash_algorithm="sha1",
    )
    same = downloader_module.DownloadTask(
        url=base.url,
        destination=destination,
        expected_size=base.expected_size,
        expected_hash=base.expected_hash,
        expected_hash_algorithm=base.expected_hash_algorithm,
    )
    different_url = downloader_module.DownloadTask(
        url="https://mirror.example.com/client.jar",
        destination=destination,
        expected_size=5,
        expected_hash="a" * 40,
        expected_hash_algorithm="sha1",
    )
    different_size = downloader_module.DownloadTask(
        url=base.url,
        destination=destination,
        expected_size=6,
        expected_hash="a" * 40,
        expected_hash_algorithm="sha1",
    )
    different_hash = downloader_module.DownloadTask(
        url=base.url,
        destination=destination,
        expected_size=5,
        expected_hash="b" * 40,
        expected_hash_algorithm="sha1",
    )

    downloader = downloader_module.AsyncDownloader()

    assert downloader._partial_file_for(base) == downloader._partial_file_for(same)
    assert downloader._partial_file_for(base) != downloader._partial_file_for(different_url)
    assert downloader._partial_file_for(base) != downloader._partial_file_for(different_size)
    assert downloader._partial_file_for(base) != downloader._partial_file_for(different_hash)


def test_async_downloader_discards_incompatible_partial_state(monkeypatch, tmp_path: Path):
    destination = tmp_path / "client.jar"
    old_task = downloader_module.DownloadTask(
        url="https://old.example.com/client.jar",
        destination=destination,
        expected_size=5,
    )
    new_task = downloader_module.DownloadTask(
        url="https://new.example.com/client.jar",
        destination=destination,
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1, retry_delay=0)
    old_partial = downloader._partial_file_for(old_task)
    old_partial.write_bytes(b"he")
    metadata_path = downloader._partial_metadata_file_for(old_task)
    metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "identity": downloader._partial_identity(old_task),
                "partial_file": old_partial.name,
            }
        ),
        encoding="utf-8",
    )
    request_headers = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"hello"

    class FakeSession:
        def get(self, *_args, **kwargs):
            request_headers.append(dict(kwargs.get("headers") or {}))
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(new_task) is True
    assert request_headers == [
        {"User-Agent": downloader.user_agent, "Accept-Encoding": "identity"}
    ]
    assert not old_partial.exists()
    assert destination.read_bytes() == b"hello"
    assert not metadata_path.exists()


def test_async_downloader_removes_legacy_unidentified_partial(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    legacy_partial = task.destination.with_name(f"{task.destination.name}.part.tmp")
    legacy_partial.write_bytes(b"unsafe")
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1, retry_delay=0)
    request_headers = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"hello"

    class FakeSession:
        def get(self, *_args, **kwargs):
            request_headers.append(dict(kwargs.get("headers") or {}))
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert request_headers[0].get("Range") is None
    assert not legacy_partial.exists()


def test_async_downloader_aborts_when_expected_size_is_exceeded(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    task.destination.write_bytes(b"old")
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=2, retry_delay=0)
    chunks_read = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for chunk in (b"123456", b"should-not-be-read"):
                chunks_read.append(chunk)
                yield chunk

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is False
    assert chunks_read == [b"123456"]
    assert task.destination.read_bytes() == b"old"
    assert "Download size limit exceeded" in (task.error or "")
    assert not downloader._partial_file_for(task).exists()
    assert not downloader._partial_metadata_file_for(task).exists()


def test_async_downloader_enforces_configurable_fallback_size_limit(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/unbounded.bin",
        destination=tmp_path / "unbounded.bin",
    )
    downloader = downloader_module.AsyncDownloader(
        max_workers=1,
        max_retries=1,
        retry_delay=0,
        max_download_size=5,
    )

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"123456"

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is False
    assert "Download size limit exceeded: limit 5 bytes" in (task.error or "")
    assert not task.destination.exists()


def test_async_downloader_can_disable_fallback_size_limit(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/trusted.bin",
        destination=tmp_path / "trusted.bin",
    )
    downloader = downloader_module.AsyncDownloader(
        max_workers=1,
        max_retries=1,
        retry_delay=0,
        max_download_size=None,
    )

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"123456"

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert task.destination.read_bytes() == b"123456"


def test_async_downloader_enforces_size_limit_for_http_downloads(
    monkeypatch,
    tmp_path: Path,
):
    task = downloader_module.DownloadTask(
        url="http://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1)
    chunks_read = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for chunk in (b"123456", b"should-not-be-read"):
                chunks_read.append(chunk)
                yield chunk

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is False
    assert chunks_read == [b"123456"]
    assert "Download size limit exceeded" in (task.error or "")
    assert not task.destination.exists()


def test_async_downloader_does_not_unlink_path_from_hostile_metadata(
    monkeypatch,
    tmp_path: Path,
):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "downloads" / "client.jar",
        expected_size=5,
    )
    task.destination.parent.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep")
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1)
    metadata_path = downloader._partial_metadata_file_for(task)
    metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "identity": "stale",
                "partial_file": "../victim.txt",
            }
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200
        headers = {}

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

    monkeypatch.setattr(downloader, "_requests_session", lambda: FakeSession())

    assert downloader._download_file(task) is True
    assert victim.read_bytes() == b"keep"
    assert task.destination.read_bytes() == b"hello"


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
        headers = {}

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

    assert downloader._download_file(task) is True
    assert task.destination.read_bytes() == b"hello"


def test_async_downloader_preserves_existing_file_when_replace_fails(monkeypatch, tmp_path: Path):
    task = downloader_module.DownloadTask(
        url="https://example.com/client.jar",
        destination=tmp_path / "client.jar",
        expected_size=5,
    )
    task.destination.write_bytes(b"valid-old")
    temp_file = tmp_path / "client.jar.part.tmp"
    temp_file.write_bytes(b"hello")
    downloader = downloader_module.AsyncDownloader(max_workers=1, max_retries=1, retry_delay=0)

    def fail_replace(*_args, **_kwargs):
        raise PermissionError("[WinError 32] The process cannot access the file")

    monkeypatch.setattr(downloader_module.os, "replace", fail_replace)

    assert downloader._validate_and_move(task, temp_file) is False
    assert task.destination.read_bytes() == b"valid-old"
    assert "WinError 32" in (task.error or "")
