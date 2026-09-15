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
import logging
import struct
import time
import wave
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from starlette.responses import Response

from audio_cue_locator.application.asset_ingestion import sniff_media_type
from audio_cue_locator.application.create_analysis import (
    MAX_CUE_LABEL_CODEPOINTS,
    AssetCanonicalizationError,
    AssetContentIncompatibleError,
    CueRequest,
    InvalidCueRequestError,
    InvalidSimilarityScoreError,
    TooManyCuesError,
    _wav_bytes_to_canonical_array,
    default_effective_configuration,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.asset import AssetNotFoundError, AssetType
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
    EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION,
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
from audio_cue_locator.observability.events import LOGGER_NAME


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


def _acl_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every structured payload `emit_diagnostic_event` attached to a log
    record, mirroring `tests/operational/test_observability.py::_acl_events`
    and `tests/e2e/test_m7_baseline.py::_acl_events` exactly (same
    attribute name, same shape; this project's tests do not cross-import
    each other's helpers)."""

    return [
        record.audio_cue_locator_event
        for record in caplog.records
        if hasattr(record, "audio_cue_locator_event")
    ]


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


@pytest.mark.parametrize("minimum_similarity_score", [0.0, 0.9, 1.0])
def test_explicit_minimum_similarity_score_is_persisted_with_override_provenance(
    use_case, source_asset_id, cue_asset_id, minimum_similarity_score
):
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[AnalysisCueReference(cue_id="cue-1", asset_id=cue_asset_id)],
        minimum_similarity_score=minimum_similarity_score,
    )

    public = _create_analysis(payload, Response(), use_case)
    record = SQLiteAnalysisRepository(use_case._repository._database_path).get(
        public.analysis_id
    )

    assert record.effective_configuration.matching.method == (
        "normalized_cross_correlation_multi_v1"
    )
    assert record.effective_configuration.matching.acceptance_threshold == (
        minimum_similarity_score
    )
    assert record.effective_configuration.configuration_source_name == (
        "acoustic_matching.acceptance."
        "EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION"
        "+request.minimum_similarity_score"
    )


def test_omitted_minimum_similarity_score_persists_exact_server_default(
    use_case, source_asset_id, cue_asset_id
):
    public = _create_analysis(
        _payload(source_asset_id, cue_asset_id), Response(), use_case
    )
    record = SQLiteAnalysisRepository(use_case._repository._database_path).get(
        public.analysis_id
    )

    assert record.effective_configuration.matching.method == (
        EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.method
    )
    assert record.effective_configuration.matching.acceptance_threshold == (
        EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.acceptance_threshold
    )
    assert record.effective_configuration.configuration_source_name == (
        "acoustic_matching.acceptance."
        "EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION"
    )


def test_explicit_server_default_value_still_records_request_provenance():
    configuration = default_effective_configuration(
        EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.acceptance_threshold
    )

    assert configuration.matching.acceptance_threshold == (
        EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.acceptance_threshold
    )
    assert configuration.configuration_source_name.endswith(
        "+request.minimum_similarity_score"
    )


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


@pytest.mark.parametrize(
    "minimum_similarity_score",
    [True, False, "0.9", float("nan"), float("inf"), float("-inf"), -0.01, 1.01],
)
def test_invalid_direct_application_similarity_score_is_rejected_before_side_effects(
    use_case, minimum_similarity_score
):
    submitted: list[str] = []

    def _record_submit(analysis_id, source, cues):
        submitted.append(analysis_id)
        return Future()

    use_case._executor.submit = _record_submit

    with pytest.raises(InvalidSimilarityScoreError):
        use_case.create(
            source_asset_id="not-read-for-invalid-score",
            cues=(),
            minimum_similarity_score=minimum_similarity_score,
        )

    assert _persisted_count(use_case) == 0
    assert submitted == []


@pytest.mark.parametrize("minimum_similarity_score", [True, "0.9", -0.01, 1.01])
def test_invalid_similarity_score_is_rejected_by_request_schema(
    source_asset_id, minimum_similarity_score
):
    with pytest.raises(ValidationError):
        AnalysisCreateRequest(
            source_asset_id=source_asset_id,
            cues=[AnalysisCueReference(cue_id="cue-1", asset_id="cue-asset")],
            minimum_similarity_score=minimum_similarity_score,
        )


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


# --- Cue labels and optional per-Cue source-search windows -------------------

CUE_SEGMENT_SAMPLE_COUNT = 4800
"""0.1s at `CANONICAL_AUDIO_SPEC.sample_rate_hz` (48000 Hz) exactly, so the
S0003 tests below need no resampling-rounding tolerance: a WAV built at
this sample rate passes through `_resample` as a no-op."""


def _make_three_segment_cue_wav_bytes() -> bytes:
    """0.3s of 48 kHz mono PCM16 built from three 0.1s blocks: loud
    (amplitude 32000), quiet (amplitude 8000), loud (32000) again. The
    quiet middle block is the interior segment a start+end trim test below
    selects; its own raw peak is far below the two outer blocks', which is
    exactly what lets a test distinguish "normalized using the effective
    trimmed segment's own peak" (S0003 ordering) from "normalized using
    the whole Cue's peak" (pre-S0003 behavior applied to a sub-interval,
    which S0003 forbids)."""

    samples = (
        [32000] * CUE_SEGMENT_SAMPLE_COUNT
        + [8000] * CUE_SEGMENT_SAMPLE_COUNT
        + [32000] * CUE_SEGMENT_SAMPLE_COUNT
    )
    return _make_wav_bytes(
        sample_rate=CANONICAL_AUDIO_SPEC.sample_rate_hz, channels=1, samples=samples
    )


def _ingest_cue_bytes(use_case, content: bytes, *, name: str = "cue.wav") -> str:
    asset = use_case._asset_storage.ingest(
        content,
        logical_type=AssetType.CUE,
        informative_name=name,
        detected_media_type="audio/wav",
    )
    return asset.identifier


def _ingest_source_bytes(use_case, content: bytes, *, name: str = "source.wav") -> str:
    asset = use_case._asset_storage.ingest(
        content,
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name=name,
        detected_media_type="audio/wav",
    )
    return asset.identifier


def _capture_submitted_arrays(use_case) -> list[tuple[str, np.ndarray, dict[str, np.ndarray]]]:
    """Replace `use_case._executor.submit` with a stub that records the
    exact `(analysis_id, canonical_source, canonical_cues)` arguments
    `CreateAnalysisUseCase.create` passes it, without running any real
    matching -- the S0003 canonicalization/trim pipeline runs entirely
    before this call, so no asynchronous wait is needed to observe its
    result. Returns the list `submit` appends each call's arguments to."""

    calls: list[tuple[str, np.ndarray, dict[str, np.ndarray]]] = []

    def _fake_submit(analysis_id, canonical_source, canonical_cues):
        calls.append((analysis_id, canonical_source, canonical_cues))
        future: Future = Future()
        future.set_result(None)
        return future

    use_case._executor.submit = _fake_submit
    return calls


def test_no_optional_cue_fields_preserves_pre_s0003_full_cue_behavior(
    use_case, source_asset_id
):
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[AnalysisCueReference(cue_id="cue-1", asset_id=cue_id)],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].label is None
    assert public.cues[0].trim_start_seconds is None
    assert public.cues[0].trim_end_seconds is None
    canonical_cues = calls[0][2]
    assert np.array_equal(
        canonical_cues["cue-1"], _wav_bytes_to_canonical_array(cue_bytes)
    )


def test_label_is_normalized_and_persisted_without_changing_canonical_samples(
    use_case, source_asset_id
):
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(cue_id="cue-1", asset_id=cue_id, label="  My Cue  ")
        ],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].label == "My Cue"
    canonical_cues = calls[0][2]
    assert np.array_equal(
        canonical_cues["cue-1"], _wav_bytes_to_canonical_array(cue_bytes)
    )


def test_blank_after_trim_label_normalizes_to_none(
    use_case, source_asset_id, cue_asset_id
):
    _capture_submitted_arrays(use_case)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(cue_id="cue-1", asset_id=cue_asset_id, label="   ")
        ],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].label is None


def test_start_only_source_window_preserves_full_cue_submitted_to_executor(use_case):
    source_asset_id = _ingest_source_bytes(use_case, _make_three_segment_cue_wav_bytes())
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_start_seconds=0.1
            )
        ],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].trim_start_seconds == 0.1
    assert public.cues[0].trim_end_seconds is None
    canonical_cues = calls[0][2]
    assert canonical_cues["cue-1"].shape[0] == 3 * CUE_SEGMENT_SAMPLE_COUNT
    assert np.array_equal(canonical_cues["cue-1"], _wav_bytes_to_canonical_array(cue_bytes))


def test_end_only_source_window_preserves_full_cue_submitted_to_executor(use_case):
    source_asset_id = _ingest_source_bytes(use_case, _make_three_segment_cue_wav_bytes())
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_end_seconds=0.2
            )
        ],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].trim_start_seconds is None
    assert public.cues[0].trim_end_seconds == 0.2
    canonical_cues = calls[0][2]
    assert canonical_cues["cue-1"].shape[0] == 3 * CUE_SEGMENT_SAMPLE_COUNT
    assert np.array_equal(canonical_cues["cue-1"], _wav_bytes_to_canonical_array(cue_bytes))


def test_both_source_window_bounds_preserve_full_cue_submitted_to_executor(use_case):
    source_asset_id = _ingest_source_bytes(use_case, _make_three_segment_cue_wav_bytes())
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1",
                asset_id=cue_id,
                trim_start_seconds=0.1,
                trim_end_seconds=0.2,
            )
        ],
    )
    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].trim_start_seconds == 0.1
    assert public.cues[0].trim_end_seconds == 0.2
    submitted_cue = calls[0][2]["cue-1"]
    assert submitted_cue.shape[0] == 3 * CUE_SEGMENT_SAMPLE_COUNT
    assert np.array_equal(submitted_cue, _wav_bytes_to_canonical_array(cue_bytes))


def test_independent_source_windows_keep_complete_cues_under_their_own_ids(use_case):
    source_asset_id = _ingest_source_bytes(use_case, _make_three_segment_cue_wav_bytes())
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id_a = _ingest_cue_bytes(use_case, cue_bytes, name="cue-a.wav")
    cue_id_b = _ingest_cue_bytes(use_case, cue_bytes, name="cue-b.wav")
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="alpha",
                asset_id=cue_id_a,
                trim_start_seconds=0.1,
                trim_end_seconds=0.2,
            ),
            AnalysisCueReference(cue_id="beta", asset_id=cue_id_b),
        ],
    )
    _create_analysis(payload, Response(), use_case)

    canonical_cues = calls[0][2]
    assert set(canonical_cues) == {"alpha", "beta"}
    assert canonical_cues["alpha"].shape[0] == 3 * CUE_SEGMENT_SAMPLE_COUNT
    assert canonical_cues["beta"].shape[0] == 3 * CUE_SEGMENT_SAMPLE_COUNT


def test_creation_submits_full_source_and_defers_per_cue_windows_to_executor(use_case):
    source_bytes = _make_three_segment_cue_wav_bytes()
    source_asset_id = _ingest_source_bytes(use_case, source_bytes)
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1",
                asset_id=cue_id,
                trim_start_seconds=0.1,
                trim_end_seconds=0.2,
            )
        ],
    )
    _create_analysis(payload, Response(), use_case)

    canonical_source = calls[0][1]
    assert np.array_equal(
        canonical_source, _wav_bytes_to_canonical_array(source_bytes)
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"trim_start_seconds": -0.5}, id="negative-start"),
        pytest.param({"trim_end_seconds": -0.1}, id="negative-end"),
        pytest.param({"trim_start_seconds": float("nan")}, id="nan-start"),
        pytest.param({"trim_end_seconds": float("nan")}, id="nan-end"),
        pytest.param({"trim_start_seconds": float("inf")}, id="inf-start"),
        pytest.param(
            {"trim_start_seconds": 2.0, "trim_end_seconds": 1.0}, id="reversed"
        ),
        pytest.param(
            {"trim_start_seconds": 1.0, "trim_end_seconds": 1.0}, id="equal"
        ),
    ],
)
def test_cue_request_rejects_negative_non_finite_and_reversed_trim_bounds(kwargs):
    with pytest.raises(InvalidCueRequestError):
        CueRequest(cue_id="cue-1", asset_id="asset-1", **kwargs)


def test_cue_request_rejects_label_exceeding_max_length():
    with pytest.raises(InvalidCueRequestError):
        CueRequest(
            cue_id="cue-1",
            asset_id="asset-1",
            label="x" * (MAX_CUE_LABEL_CODEPOINTS + 1),
        )


def test_cue_request_accepts_label_at_max_length():
    label = "x" * MAX_CUE_LABEL_CODEPOINTS
    request = CueRequest(cue_id="cue-1", asset_id="asset-1", label=label)
    assert request.label == label


def test_source_window_start_at_or_after_source_duration_is_rejected_before_persistence(
    use_case, source_asset_id
):
    cue_bytes = _make_three_segment_cue_wav_bytes()  # 0.3s total
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_start_seconds=0.3
            )
        ],
    )
    with pytest.raises(InvalidCueRequestError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_source_window_end_exceeding_source_duration_is_rejected_before_persistence(
    use_case, source_asset_id
):
    cue_bytes = _make_three_segment_cue_wav_bytes()  # 0.3s total
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_end_seconds=0.31
            )
        ],
    )
    with pytest.raises(InvalidCueRequestError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_empty_effective_interval_is_rejected_before_persistence(
    use_case
):
    source_asset_id = _ingest_source_bytes(use_case, _make_three_segment_cue_wav_bytes())
    cue_bytes = _make_three_segment_cue_wav_bytes()
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    # Both bounds round to the same sample index, leaving zero effective
    # samples in the requested interval.
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1",
                asset_id=cue_id,
                trim_start_seconds=0.1,
                trim_end_seconds=0.1 + 1e-7,
            )
        ],
    )
    with pytest.raises(InvalidCueRequestError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_end_only_zero_is_rejected_as_an_empty_source_window(
    use_case, source_asset_id, cue_asset_id
):
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_asset_id, trim_end_seconds=0.0
            )
        ],
    )
    with pytest.raises(InvalidCueRequestError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


def test_end_equal_to_source_duration_is_valid(use_case, cue_asset_id):
    source_bytes = _make_three_segment_cue_wav_bytes()
    source_asset_id = _ingest_source_bytes(use_case, source_bytes)
    calls = _capture_submitted_arrays(use_case)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_asset_id, trim_end_seconds=0.3
            )
        ],
    )
    public = _create_analysis(payload, Response(), use_case)
    assert public.cues[0].trim_end_seconds == 0.3
    assert calls


def test_end_ten_seconds_is_valid_for_short_cue_when_source_is_longer(use_case):
    source_bytes = _make_wav_bytes(
        sample_rate=CANONICAL_AUDIO_SPEC.sample_rate_hz,
        samples=[1000, -1000] * (CANONICAL_AUDIO_SPEC.sample_rate_hz * 11 // 2),
    )
    source_asset_id = _ingest_source_bytes(use_case, source_bytes)
    cue_bytes = _make_wav_bytes(
        sample_rate=CANONICAL_AUDIO_SPEC.sample_rate_hz,
        samples=[1000, -1000] * 2400,
    )
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    calls = _capture_submitted_arrays(use_case)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_end_seconds=10.0
            )
        ],
    )

    public = _create_analysis(payload, Response(), use_case)

    assert public.cues[0].trim_end_seconds == 10.0
    assert np.array_equal(
        calls[0][2]["cue-1"], _wav_bytes_to_canonical_array(cue_bytes)
    )


def test_cue_duration_guardrail_runs_on_full_media_before_trim_is_applied(
    use_case, source_asset_id
):
    cue_bytes = _make_three_segment_cue_wav_bytes()  # 0.3s total
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    use_case._max_cue_media_duration_seconds = 0.05  # smaller than the full 0.3s

    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1",
                asset_id=cue_id,
                trim_start_seconds=0.0,
                # A tiny requested subsegment that would fit comfortably
                # if only the trimmed portion -- not the full uploaded
                # Cue -- were checked against the guardrail.
                trim_end_seconds=0.01,
            )
        ],
    )
    with pytest.raises(ResourceLimitExceededError):
        _create_analysis(payload, Response(), use_case)
    assert _persisted_count(use_case) == 0


# --- S0005: diagnostic split between Cue validation and media canonicalization


def test_duration_relative_invalid_cue_request_emits_cue_validation_failed_diagnostic(
    use_case, source_asset_id, caplog: pytest.LogCaptureFixture
):
    """A duration-relative `InvalidCueRequestError` raised after the source is
    canonicalized and its duration is known is
    still raised, still prevents persistence and executor submission, and
    is reported as an Application validation event -- distinct from a
    media/canonicalization failure -- per
    `specs/S0005-cue-trim-validation-clarity-and-analysis-creation-
    regression/spec.md` section 4.1/4.7."""

    cue_bytes = _make_three_segment_cue_wav_bytes()  # 0.3s total
    cue_id = _ingest_cue_bytes(use_case, cue_bytes)
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset_id,
        cues=[
            AnalysisCueReference(
                cue_id="cue-1", asset_id=cue_id, trim_end_seconds=0.31
            )
        ],
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(InvalidCueRequestError):
            _create_analysis(payload, Response(), use_case)

    assert _persisted_count(use_case) == 0

    events = _acl_events(caplog)
    cue_validation_events = [
        event for event in events if event["event"] == "cue_validation_failed"
    ]
    assert len(cue_validation_events) == 1
    assert cue_validation_events[0]["boundary"] == "application"
    assert cue_validation_events[0]["outcome"] == "failed"
    assert cue_validation_events[0]["category"] == "InvalidCueRequestError"
    assert not any(event["event"] == "media_canonicalization_failed" for event in events)


def test_real_media_canonicalization_failure_still_emits_media_canonicalization_failed_diagnostic(
    use_case, cue_asset_id, caplog: pytest.LogCaptureFixture
):
    """An actual media canonicalization/probe/decode failure (here, a
    malformed WAV source that passes container sniffing but fails `wave`'s
    own chunk parse) continues to emit `media_canonicalization_failed` in
    `boundary=media_processing`, and is never relabeled
    `cue_validation_failed`, per the same spec section."""

    source_asset = use_case._asset_storage.ingest(
        _malformed_wav_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="broken.wav",
        detected_media_type="audio/wav",
    )
    payload = _payload(source_asset.identifier, cue_asset_id)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(UnsupportedMediaError):
            _create_analysis(payload, Response(), use_case)

    assert _persisted_count(use_case) == 0

    events = _acl_events(caplog)
    media_events = [
        event for event in events if event["event"] == "media_canonicalization_failed"
    ]
    assert len(media_events) == 1
    assert media_events[0]["boundary"] == "media_processing"
    assert media_events[0]["outcome"] == "failed"
    assert media_events[0]["category"] == "AssetCanonicalizationError"
    assert not any(event["event"] == "cue_validation_failed" for event in events)
