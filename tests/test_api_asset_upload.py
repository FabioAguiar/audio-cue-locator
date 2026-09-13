"""Tests for the bounded source-media and cue upload endpoints (M5-03):
`src/audio_cue_locator/interfaces/rest_api/asset_routes.py` and
`src/audio_cue_locator/application/asset_ingestion.py`.

Like `tests/test_api_errors.py` (M5-02), these do not use FastAPI's
`TestClient`: it requires `httpx`, which is not among this project's
declared dependencies, and adding it is out of this issue's authorized
scope (`intents/M5/M5-03/implementation-handoff.json`, out_of_scope). For
the same reason, these tests do not use the `pytest-asyncio` plugin either
-- `pyproject.toml` declares only `pytest` as a test dependency -- so each
async route coroutine is driven directly with `asyncio.run` inside an
ordinary synchronous test function instead of an `async def` test.

Route coroutines are called directly against a hand-built
`starlette.requests.Request`, constructed from a raw ASGI scope/receive
pair the same way Starlette's own test suite exercises request parsing
without a live server. This also lets oversized-upload tests feed the
request body in small chunks and assert the configured limit is enforced
*during* streaming, not only after the whole body would have been
buffered -- the actual property `asset_routes._bounded_stream` exists to
guarantee.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Coroutine

import pytest
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from audio_cue_locator.application.asset_ingestion import (
    AssetIngestionUseCase,
    AssetUploadTooLargeError,
    UnsupportedAssetMediaError,
    UploadLimits,
    UploadPolicy,
    default_upload_limits,
    sniff_media_type,
)
from audio_cue_locator.core.asset import AssetType
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.interfaces.rest_api.asset_routes import build_asset_router
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


# --- fixtures and small builders --------------------------------------------


def _fake_wav_bytes(total_size: int = 44) -> bytes:
    header = b"RIFF" + max(total_size - 8, 0).to_bytes(4, "little") + b"WAVE"
    return header + b"\x00" * max(0, total_size - len(header))


def _fake_mp4_bytes(total_size: int = 32) -> bytes:
    header = b"\x00\x00\x00\x20" + b"ftyp" + b"mp42"
    return header + b"\x00" * max(0, total_size - len(header))


def _garbage_bytes(total_size: int = 32) -> bytes:
    return b"not a recognized media container" + b"\x00" * total_size


def _multipart_body(
    *, filename: str, content: bytes, content_type: str, boundary: str = "testboundary"
) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")


def _field_only_multipart_body(boundary: str = "testboundary") -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="logical_type"\r\n\r\n'
        "source_media"
        f"\r\n--{boundary}--\r\n"
    ).encode("utf-8")


def _make_request(
    body: bytes,
    *,
    boundary: str = "testboundary",
    content_type: str = "multipart/form-data",
    declared_length: int | None = None,
    chunk_size: int | None = None,
) -> Request:
    if chunk_size is None:
        pieces = [body] if body else [b""]
    else:
        pieces = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
        if not pieces:
            pieces = [b""]

    messages = [
        {"type": "http.request", "body": piece, "more_body": index < len(pieces) - 1}
        for index, piece in enumerate(pieces)
    ]
    message_iter = iter(messages)

    async def receive():
        try:
            return next(message_iter)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    header_pairs = [
        (b"content-type", f"{content_type}; boundary={boundary}".encode("utf-8"))
    ]
    length = len(body) if declared_length is None else declared_length
    if length is not None:
        header_pairs.append((b"content-length", str(length).encode("utf-8")))

    scope = {"type": "http", "method": "POST", "headers": header_pairs}
    return Request(scope, receive)


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    return tmp_path / "assets"


@pytest.fixture()
def use_case(storage_root: Path) -> AssetIngestionUseCase:
    storage = LocalFilesystemAssetStorage(storage_root)
    return AssetIngestionUseCase(storage)


@pytest.fixture()
def router(use_case: AssetIngestionUseCase):
    return build_asset_router(use_case)


def _endpoint(router, path: str):
    return next(route for route in router.routes if route.path == path).endpoint


# --- sniff_media_type ---------------------------------------------------


def test_sniff_media_type_detects_wav_and_mp4_and_rejects_unknown():
    assert sniff_media_type(_fake_wav_bytes()) == "audio/wav"
    assert sniff_media_type(_fake_mp4_bytes()) == "video/mp4"
    assert sniff_media_type(_garbage_bytes()) is None
    assert sniff_media_type(b"too short") is None


# --- AssetIngestionUseCase (Application-level, no HTTP) ---------------------


def test_ingest_accepts_valid_wav_for_source_media_and_cue(use_case: AssetIngestionUseCase):
    for logical_type in (AssetType.SOURCE_MEDIA, AssetType.CUE):
        asset = use_case.ingest(
            content=_fake_wav_bytes(),
            logical_type=logical_type,
            informative_name="clip.wav",
        )
        assert asset.logical_type is logical_type
        assert asset.media_type == "audio/wav"
        assert asset.sanitized_name == "clip.wav"


def test_ingest_accepts_mp4_for_source_media_but_rejects_it_for_cue(
    use_case: AssetIngestionUseCase,
):
    asset = use_case.ingest(
        content=_fake_mp4_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="movie.mp4",
    )
    assert asset.media_type == "video/mp4"

    with pytest.raises(UnsupportedAssetMediaError):
        use_case.ingest(
            content=_fake_mp4_bytes(),
            logical_type=AssetType.CUE,
            informative_name="movie.mp4",
        )


def test_ingest_rejects_unsupported_media(use_case: AssetIngestionUseCase):
    with pytest.raises(UnsupportedAssetMediaError):
        use_case.ingest(
            content=_garbage_bytes(),
            logical_type=AssetType.SOURCE_MEDIA,
            informative_name="clip.bin",
        )


def test_ingest_rejects_oversized_content(storage_root: Path):
    tiny_limits = UploadLimits(
        source_media=UploadPolicy(max_size_bytes=8, supported_media_types=frozenset({"audio/wav"})),
        cue=UploadPolicy(max_size_bytes=8, supported_media_types=frozenset({"audio/wav"})),
    )
    storage = LocalFilesystemAssetStorage(storage_root)
    use_case = AssetIngestionUseCase(storage, tiny_limits)

    with pytest.raises(AssetUploadTooLargeError):
        use_case.ingest(
            content=_fake_wav_bytes(),
            logical_type=AssetType.SOURCE_MEDIA,
            informative_name="clip.wav",
        )
    assert not storage_root.exists() or not list(storage_root.iterdir())


def test_two_uploads_sharing_a_client_filename_never_collide(use_case: AssetIngestionUseCase):
    first = use_case.ingest(
        content=_fake_wav_bytes(),
        logical_type=AssetType.CUE,
        informative_name="same.wav",
    )
    second = use_case.ingest(
        content=_fake_wav_bytes(),
        logical_type=AssetType.CUE,
        informative_name="same.wav",
    )

    assert first.identifier != second.identifier
    assert first.sanitized_name == second.sanitized_name == "same.wav"


def test_default_upload_limits_source_media_allows_wav_and_mp4_cue_allows_wav_only():
    limits = default_upload_limits()
    assert limits.source_media.supported_media_types == {"audio/wav", "video/mp4"}
    assert limits.cue.supported_media_types == {"audio/wav"}


# --- HTTP-layer: bounded multipart reading and route behavior ---------------


def test_upload_source_media_valid_multipart_returns_asset_public(router, storage_root: Path):
    endpoint = _endpoint(router, "/assets/source-media")
    body = _multipart_body(filename="movie.mp4", content=_fake_mp4_bytes(), content_type="video/mp4")
    request = _make_request(body, chunk_size=16)

    result = _run(endpoint(request))

    assert result.logical_type.value == "source_media"
    assert result.media_type == "video/mp4"
    assert result.sanitized_name == "movie.mp4"
    assert (storage_root / result.identifier).exists()


def test_upload_cue_valid_multipart_returns_asset_public(router, storage_root: Path):
    endpoint = _endpoint(router, "/assets/cue")
    body = _multipart_body(filename="cue one.wav", content=_fake_wav_bytes(), content_type="audio/wav")
    request = _make_request(body)

    result = _run(endpoint(request))

    assert result.logical_type.value == "cue"
    assert result.media_type == "audio/wav"
    assert (storage_root / result.identifier).exists()


def test_upload_two_identical_filenames_do_not_collide_via_endpoint(router, storage_root: Path):
    endpoint = _endpoint(router, "/assets/cue")
    body = _multipart_body(filename="dup.wav", content=_fake_wav_bytes(), content_type="audio/wav")

    first = _run(endpoint(_make_request(body)))
    second = _run(endpoint(_make_request(body)))

    assert first.identifier != second.identifier
    assert {p.name for p in storage_root.iterdir()} == {first.identifier, second.identifier}


def test_upload_declared_content_length_exceeds_limit_is_rejected_immediately(router):
    endpoint = _endpoint(router, "/assets/cue")
    # Cue's default limit is far smaller than this declared length; the body
    # itself is never provided in full, proving the rejection happens from
    # the Content-Length pre-check alone, before any streaming read.
    request = _make_request(b"", declared_length=10**9, chunk_size=1)

    with pytest.raises(ResourceLimitExceededError):
        _run(endpoint(request))


def test_upload_oversized_body_is_rejected_during_streaming_not_after(storage_root: Path):
    tiny_limits = UploadLimits(
        source_media=UploadPolicy(max_size_bytes=64, supported_media_types=frozenset({"audio/wav"})),
        cue=UploadPolicy(max_size_bytes=64, supported_media_types=frozenset({"audio/wav"})),
    )
    storage = LocalFilesystemAssetStorage(storage_root)
    use_case = AssetIngestionUseCase(storage, tiny_limits)
    router = build_asset_router(use_case)
    endpoint = _endpoint(router, "/assets/source-media")

    body = _multipart_body(
        filename="big.wav", content=_fake_wav_bytes(total_size=4096), content_type="audio/wav"
    )
    # Chunked, undeclared-Content-Length delivery: only the bounded stream
    # read -- not the Content-Length pre-check -- can catch this.
    request = _make_request(body, declared_length=None, chunk_size=8)

    with pytest.raises(ResourceLimitExceededError):
        _run(endpoint(request))
    assert not storage_root.exists() or not list(storage_root.iterdir())


def test_upload_unsupported_media_multipart_is_rejected(router, storage_root: Path):
    endpoint = _endpoint(router, "/assets/source-media")
    body = _multipart_body(
        filename="clip.bin", content=_garbage_bytes(), content_type="application/octet-stream"
    )
    request = _make_request(body)

    with pytest.raises(UnsupportedMediaError):
        _run(endpoint(request))
    assert not storage_root.exists() or not list(storage_root.iterdir())


def test_upload_malformed_multipart_body_raises_request_validation_error(router):
    endpoint = _endpoint(router, "/assets/source-media")
    request = _make_request(b"this is not a multipart body at all")

    with pytest.raises(RequestValidationError):
        _run(endpoint(request))


def test_upload_missing_file_part_raises_request_validation_error(router):
    endpoint = _endpoint(router, "/assets/source-media")
    request = _make_request(_field_only_multipart_body())

    with pytest.raises(RequestValidationError):
        _run(endpoint(request))


def test_upload_non_multipart_content_type_raises_request_validation_error(router):
    endpoint = _endpoint(router, "/assets/source-media")
    request = _make_request(b'{"logical_type": "source_media"}', content_type="application/json")

    with pytest.raises(RequestValidationError):
        _run(endpoint(request))
