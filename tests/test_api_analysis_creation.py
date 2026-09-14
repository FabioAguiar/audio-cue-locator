"""Tests for the asynchronous Analysis creation endpoint (M5-04):
`src/audio_cue_locator/interfaces/rest_api/analysis_routes.py` and
`src/audio_cue_locator/application/create_analysis.py`.

Like `tests/test_api_asset_upload.py` (M5-03), these do not use FastAPI's
`TestClient` (`httpx` is not a declared dependency) or `pytest-asyncio`
(only `pytest` is declared). `analysis_routes.create_analysis` is a plain
synchronous function, so it is called directly -- through
`analysis_routes._create_analysis`, the same factored-out logic the
registered route delegates to -- against a hand-built
`starlette.responses.Response` used only to capture the `Location` header.

Most tests build the Analysis-creation composition root through
`interfaces.rest_api.app._build_asset_storage` /
`_build_create_analysis_use_case` (with `AUDIO_CUE_LOCATOR_*` environment
variables pointed at `tmp_path`) rather than constructing
`CreateAnalysisUseCase` by hand, so they also exercise the real
`_ThreadLocalAnalysisRepository` composition-root wiring: `LocalAnalysisExecutor`
runs `AnalysisRepositoryPort.transition` from its own bounded worker-pool
thread, never the thread that built the use case, and a single shared
`SQLiteAnalysisRepository` instance is not safe across that boundary
(confirmed by direct execution during implementation: sharing one instance
raises `sqlite3.ProgrammingError` from inside the worker thread, silently,
leaving every created Analysis permanently `queued`).
"""

from __future__ import annotations

import io
import struct
import time
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.responses import Response

from audio_cue_locator.application.asset_ingestion import sniff_media_type
from audio_cue_locator.application.create_analysis import (
    AssetCanonicalizationError,
    AssetContentIncompatibleError,
    CueRequest,
    TooManyCuesError,
    _wav_bytes_to_canonical_array,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.asset import AssetNotFoundError, AssetType
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)
from audio_cue_locator.infrastructure.media_processing.errors import (
    FFmpegTimeoutError,
    InvalidMediaError,
)
from audio_cue_locator.infrastructure.media_processing.models import (
    ExtractionResult,
    ProbeResult,
)
from audio_cue_locator.interfaces.rest_api.analysis_routes import (
    STATUS_LOCATION_HEADER,
    _create_analysis,
)
from audio_cue_locator.interfaces.rest_api.app import (
    _build_asset_storage,
    _build_create_analysis_use_case,
)
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisCueReference,
    AnalysisStatus,
)


# --- fixtures and small builders --------------------------------------------


def _make_wav_bytes(
    *, sample_rate: int = 8000, channels: int = 1, samples: list[int] | None = None
) -> bytes:
    if samples is None:
        samples = [1000, -1000, 2000, -2000, 500, -500, 250, -250] * 20
    frame_count = len(samples) // channels
    samples = samples[: frame_count * channels]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buffer.getvalue()


def _make_mp4_bytes(total_size: int = 64) -> bytes:
    header = b"\x00\x00\x00\x20" + b"ftyp" + b"mp42"
    return header + b"\x00" * max(0, total_size - len(header))


def _malformed_wav_bytes() -> bytes:
    # A real RIFF/WAVE header (so sniff_media_type detects "audio/wav")
    # followed by bytes that are not a valid fmt/data chunk structure.
    return b"RIFF" + (16).to_bytes(4, "little") + b"WAVEnotachunk!!!"


def _fake_avi_bytes(total_size: int = 64) -> bytes:
    header = b"RIFF" + max(total_size - 8, 0).to_bytes(4, "little") + b"AVI "
    return header + b"\x00" * max(0, total_size - len(header))


def _fake_mov_bytes(total_size: int = 64) -> bytes:
    header = (16).to_bytes(4, "big") + b"ftyp" + b"qt  " + b"\x00\x00\x00\x00"
    return header + b"\x00" * max(0, total_size - len(header))


def _build_ebml_bytes(doctype: str, total_size: int = 64) -> bytes:
    doctype_bytes = doctype.encode("ascii")
    doctype_element = bytes([0x42, 0x82, 0x80 | len(doctype_bytes)]) + doctype_bytes
    header = b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(doctype_element)]) + doctype_element
    return header + b"\x00" * max(0, total_size - len(header))


def _fake_webm_bytes(total_size: int = 64) -> bytes:
    return _build_ebml_bytes("webm", total_size)


def _fake_mkv_bytes(total_size: int = 64) -> bytes:
    return _build_ebml_bytes("matroska", total_size)


_ALL_S0002_VIDEO_CONTENT_BUILDERS = [
    pytest.param(_make_mp4_bytes, ".mp4", id="mp4"),
    pytest.param(_fake_mov_bytes, ".mov", id="mov"),
    pytest.param(_fake_webm_bytes, ".webm", id="webm"),
    pytest.param(_fake_mkv_bytes, ".mkv", id="mkv"),
    pytest.param(_fake_avi_bytes, ".avi", id="avi"),
]


class _StubMediaAdapter:
    """A deterministic, in-process stand-in for `FFmpegMediaAdapter` (no
    real ffmpeg/ffprobe subprocess involved), used only to prove
    `CreateAnalysisUseCase`'s own dispatch/translation logic for each S0002
    video media type. Real FFmpeg extraction evidence for these container
    families lives in `tests/test_media_processing_ffmpeg_adapter.py` and
    `tests/e2e/test_m7_baseline.py`'s real WebM scenario -- this stub is
    deliberately not the only evidence for WebM support."""

    def __init__(
        self,
        *,
        wav_bytes: bytes = b"",
        duration_seconds: float | None = 0.5,
        has_audio_stream: bool = True,
        raise_on_probe: Exception | None = None,
        raise_on_extract: Exception | None = None,
    ) -> None:
        self._wav_bytes = wav_bytes
        self._duration_seconds = duration_seconds
        self._has_audio_stream = has_audio_stream
        self._raise_on_probe = raise_on_probe
        self._raise_on_extract = raise_on_extract
        self.probed_paths: list[str] = []
        self.extracted_paths: list[str] = []

    def probe(self, media_path: str) -> ProbeResult:
        self.probed_paths.append(media_path)
        if self._raise_on_probe is not None:
            raise self._raise_on_probe
        return ProbeResult(
            container_format="stub",
            duration_seconds=self._duration_seconds,
            has_audio_stream=self._has_audio_stream,
            audio_stream_index=0 if self._has_audio_stream else None,
            audio_codec_name="pcm_s16le" if self._has_audio_stream else None,
            sample_rate=8000 if self._has_audio_stream else None,
            channels=1 if self._has_audio_stream else None,
        )

    def extract_audio(self, media_path: str, output_path: str) -> ExtractionResult:
        self.extracted_paths.append(media_path)
        if self._raise_on_extract is not None:
            raise self._raise_on_extract
        Path(output_path).write_bytes(self._wav_bytes)
        return ExtractionResult(output_path=output_path, sample_rate=8000, channels=1)


def _wait_for_terminal_state(use_case, analysis_id: str) -> AnalysisLifecycleState:
    reader = SQLiteAnalysisRepository(use_case._repository._database_path)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        record = reader.get(analysis_id)
        if record.state in (AnalysisLifecycleState.SUCCEEDED, AnalysisLifecycleState.FAILED):
            return record.state
        time.sleep(0.02)
    raise AssertionError(f"Analysis {analysis_id!r} did not reach a terminal state")


@pytest.fixture()
def use_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(tmp_path / "analysis.sqlite3")
    )
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(tmp_path / "assets"))
    storage = _build_asset_storage()
    return _build_create_analysis_use_case(storage)


@pytest.fixture()
def source_asset_id(use_case) -> str:
    asset = use_case._asset_storage.ingest(
        _make_wav_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="source.wav",
        detected_media_type="audio/wav",
    )
    return asset.identifier


@pytest.fixture()
def cue_asset_id(use_case) -> str:
    asset = use_case._asset_storage.ingest(
        _make_wav_bytes(samples=[1000, -1000, 2000, -2000] * 10),
        logical_type=AssetType.CUE,
        informative_name="cue.wav",
        detected_media_type="audio/wav",
    )
    return asset.identifier


def _payload(source_asset_id: str, cue_asset_id: str) -> AnalysisCreateRequest:
    return AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[AnalysisCueReference(cue_id="cue-1", asset_id=cue_asset_id)],
    )


def _persisted_count(use_case) -> int:
    db_path = use_case._repository._database_path
    reader = SQLiteAnalysisRepository(db_path)
    total = 0
    for state in AnalysisLifecycleState:
        total += len(reader.list_by_state(state))
    return total


# --- valid creation: immediate 202, later terminal state --------------------


def test_create_analysis_returns_immediate_queued_response_with_location(
    use_case, source_asset_id, cue_asset_id
):
    response = Response()
    public = _create_analysis(_payload(source_asset_id, cue_asset_id), response, use_case)

    assert public.status == AnalysisStatus.QUEUED
    assert public.source_asset_id == source_asset_id
    assert public.cues[0].asset_id == cue_asset_id
    assert public.lifecycle_timestamps.queued_at is not None
    assert response.headers[STATUS_LOCATION_HEADER] == f"/api/v1/analyses/{public.analysis_id}"


def test_queued_analysis_reaches_a_terminal_state_independently(
    use_case, source_asset_id, cue_asset_id
):
    response = Response()
    public = _create_analysis(_payload(source_asset_id, cue_asset_id), response, use_case)
    assert public.status == AnalysisStatus.QUEUED

    reader = SQLiteAnalysisRepository(use_case._repository._database_path)
    deadline = time.time() + 5.0
    final_state = None
    while time.time() < deadline:
        record = reader.get(public.analysis_id)
        if record.state in (AnalysisLifecycleState.SUCCEEDED, AnalysisLifecycleState.FAILED):
            final_state = record.state
            break
        time.sleep(0.02)

    assert final_state is AnalysisLifecycleState.SUCCEEDED


def test_two_identical_requests_produce_two_distinct_analyses(
    use_case, source_asset_id, cue_asset_id
):
    payload = _payload(source_asset_id, cue_asset_id)
    first = _create_analysis(payload, Response(), use_case)
    second = _create_analysis(payload, Response(), use_case)

    assert first.analysis_id != second.analysis_id
    assert _persisted_count(use_case) == 2


# --- rejections before any persistence or scheduling ------------------------


def test_missing_source_asset_is_rejected_before_persistence(use_case, cue_asset_id):
    payload = _payload("00000000-0000-4000-8000-000000000000", cue_asset_id)
    with pytest.raises(AssetNotFoundError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_wrong_type_cue_is_rejected_as_unsupported_media(use_case, source_asset_id):
    mp4_cue = use_case._asset_storage.ingest(
        _make_mp4_bytes(),
        logical_type=AssetType.CUE,
        informative_name="cue.mp4",
        detected_media_type="video/mp4",
    )
    payload = _payload(source_asset_id, mp4_cue.identifier)
    with pytest.raises(UnsupportedMediaError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_excessive_cue_count_is_rejected_as_resource_limit_exceeded(
    use_case, source_asset_id, cue_asset_id
):
    too_many_cues = [
        AnalysisCueReference(cue_id=f"cue-{i}", asset_id=cue_asset_id)
        for i in range(use_case.max_cue_count + 1)
    ]
    payload = AnalysisCreateRequest(source_asset_id=source_asset_id, cues=too_many_cues)
    with pytest.raises(ResourceLimitExceededError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_zero_cues_is_rejected_by_request_schema_validation(source_asset_id):
    with pytest.raises(ValidationError):
        AnalysisCreateRequest(source_asset_id=source_asset_id, cues=[])


# --- canonicalization (gap G1) ----------------------------------------------


def test_wav_canonicalization_produces_conformant_array():
    array = _wav_bytes_to_canonical_array(
        _make_wav_bytes(sample_rate=8000, channels=2, samples=list(range(-4000, 4000, 40)))
    )

    assert array.ndim == 1
    assert array.dtype.name == "float32"
    assert bool((array >= -1.0001).all() and (array <= 1.0001).all())


def test_wav_canonicalization_rejects_malformed_content():
    with pytest.raises(AssetCanonicalizationError):
        _wav_bytes_to_canonical_array(_malformed_wav_bytes())


def test_canonical_spec_matches_infrastructure_target():
    array = _wav_bytes_to_canonical_array(_make_wav_bytes(sample_rate=48000))
    assert array.dtype.name == CANONICAL_AUDIO_SPEC.sample_format


# --- S0002: every supported video type dispatches through the shared path --


@pytest.mark.parametrize("content_builder,extension", _ALL_S0002_VIDEO_CONTENT_BUILDERS)
def test_every_supported_video_source_type_dispatches_through_shared_ffmpeg_path(
    use_case, cue_asset_id, content_builder, extension
):
    media_type = sniff_media_type(content_builder())
    source_asset = use_case._asset_storage.ingest(
        content_builder(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name=f"source{extension}",
        detected_media_type=media_type,
    )
    stub_adapter = _StubMediaAdapter(wav_bytes=_make_wav_bytes())
    use_case._media_adapter = stub_adapter

    payload = _payload(source_asset.identifier, cue_asset_id)
    response = _create_analysis(payload, Response(), use_case)
    assert response.status == AnalysisStatus.QUEUED

    final_state = _wait_for_terminal_state(use_case, response.analysis_id)
    assert final_state is AnalysisLifecycleState.SUCCEEDED
    assert stub_adapter.probed_paths and stub_adapter.probed_paths[0].endswith(extension)
    assert stub_adapter.extracted_paths and stub_adapter.extracted_paths[0].endswith(extension)


@pytest.mark.parametrize(
    "content_builder",
    [_fake_avi_bytes, _fake_mov_bytes, _fake_webm_bytes, _fake_mkv_bytes, _make_mp4_bytes],
)
def test_video_asset_supplied_as_cue_is_rejected_as_unsupported_media_for_every_s0002_type(
    use_case, source_asset_id, content_builder
):
    media_type = sniff_media_type(content_builder())
    video_cue = use_case._asset_storage.ingest(
        content_builder(),
        logical_type=AssetType.CUE,
        informative_name="cue.bin",
        detected_media_type=media_type,
    )
    payload = _payload(source_asset_id, video_cue.identifier)
    with pytest.raises(UnsupportedMediaError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_uncanonicalizable_video_source_is_rejected_before_persistence(use_case, cue_asset_id):
    source_asset = use_case._asset_storage.ingest(
        _fake_webm_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="broken.webm",
        detected_media_type="video/webm",
    )
    use_case._media_adapter = _StubMediaAdapter(
        raise_on_extract=InvalidMediaError("bad video")
    )

    payload = _payload(source_asset.identifier, cue_asset_id)
    with pytest.raises(UnsupportedMediaError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_video_source_with_no_audio_stream_is_rejected_before_persistence(use_case, cue_asset_id):
    source_asset = use_case._asset_storage.ingest(
        _fake_mkv_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="video-only.mkv",
        detected_media_type="video/x-matroska",
    )
    use_case._media_adapter = _StubMediaAdapter(
        has_audio_stream=False,
        raise_on_extract=InvalidMediaError("no audio stream"),
    )

    payload = _payload(source_asset.identifier, cue_asset_id)
    with pytest.raises(UnsupportedMediaError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_video_source_ffmpeg_timeout_is_translated_and_rejected_before_persistence(
    use_case, cue_asset_id
):
    source_asset = use_case._asset_storage.ingest(
        _fake_webm_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="slow.webm",
        detected_media_type="video/webm",
    )
    use_case._media_adapter = _StubMediaAdapter(
        raise_on_probe=FFmpegTimeoutError("timed out probing")
    )

    payload = _payload(source_asset.identifier, cue_asset_id)
    with pytest.raises(UnsupportedMediaError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0
