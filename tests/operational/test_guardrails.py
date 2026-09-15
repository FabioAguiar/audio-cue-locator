"""Cross-boundary tests for M7-02's safe-by-default guardrails: media
duration, executor concurrency, FFmpeg timeout, and the resulting
structured-error behavior, plus a lock against silent default drift.

Filename/path safety (formal issue acceptance criterion 4) is already
exhaustively covered by `tests/test_asset_storage.py` (traversal
rejection, symlink rejection, opaque storage identifiers); this suite does
not duplicate it. Upload-size and cue-count guardrails already have
dedicated coverage in `tests/test_api_asset_upload.py` and
`tests/test_api_analysis_creation.py` respectively. This suite focuses on
the two guardrails M7-02 adds (source/cue media duration) and the two
guardrails M7-02 makes environment-configurable for the first time
(executor concurrency, FFmpeg timeout), plus the structured-error
translation this issue changes.

`FFmpegMediaAdapter.probe`/`extract_audio` are patched directly (mirroring
`tests/test_media_processing_ffmpeg_adapter.py`'s own subprocess-mocking
approach, one boundary lower) so these tests run deterministically without
requiring FFmpeg to be installed and without re-testing
`FFmpegMediaAdapter`'s own subprocess-to-exception mapping, already
covered by that file.
"""

from __future__ import annotations

import io
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.responses import Response

from audio_cue_locator.application.asset_ingestion import (
    DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES,
    DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES,
)
from audio_cue_locator.application.create_analysis import (
    DEFAULT_MAX_CUE_COUNT,
    DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS,
    DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
    AssetProcessingTimeoutError,
    MediaDurationExceededError,
    _mp4_bytes_to_canonical_array,
    _wav_bytes_to_canonical_array,
)
from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisNotFoundError,
    AnalysisRecord,
    CueAssetReference,
    LifecycleTimestamps,
)
from audio_cue_locator.application.query_analysis import (
    DEFAULT_MAX_AUDIO_AUDITION_DURATION_SECONDS,
    DEFAULT_MAX_AUDIO_AUDITION_RESPONSE_BYTES,
    AudioAuditionResourceLimitError,
    QueryAnalysisUseCase,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueNoMatch,
    CueOccurrences,
    CueResult,
    EffectiveConfigurationSnapshot,
    MatchingSnapshot,
    NormalizationSnapshot,
    Occurrence,
    serialize_analysis_result,
)
from audio_cue_locator.core.asset import AssetType
from audio_cue_locator.infrastructure.execution.local_analysis_executor import (
    DEFAULT_MAX_CONCURRENCY,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)
from audio_cue_locator.infrastructure.media_processing.errors import (
    FFmpegTimeoutError,
)
from audio_cue_locator.infrastructure.media_processing.ffmpeg_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    FFmpegMediaAdapter,
)
from audio_cue_locator.infrastructure.media_processing.models import ProbeResult
from audio_cue_locator.interfaces.rest_api.analysis_routes import (
    _create_analysis,
    _get_cue_audio,
    _get_occurrence_audio,
)
from audio_cue_locator.interfaces.rest_api.app import (
    _build_asset_storage,
    _build_create_analysis_use_case,
    _ffmpeg_timeout_seconds,
    _max_concurrency,
    _max_cue_media_duration_seconds,
    _max_source_media_duration_seconds,
)
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisCueReference,
)

_CANONICAL_RATE = CANONICAL_AUDIO_SPEC.sample_rate_hz
"""Generating fixtures at this rate makes `_resample` a no-op (`source_rate
== target_rate`), keeping these tests fast regardless of duration."""


# --- fixtures and small builders --------------------------------------------


def _wav_bytes_of_duration(duration_seconds: float, *, sample_rate: int = _CANONICAL_RATE) -> bytes:
    """Build a real, well-formed mono 16-bit PCM WAV whose `wave`-parsed
    duration is exactly `duration_seconds`."""

    frame_count = round(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(struct.pack(f"<{frame_count}h", *([0] * frame_count)))
    return buffer.getvalue()


def _mp4_stub_bytes() -> bytes:
    header = b"\x00\x00\x00\x20" + b"ftyp" + b"mp42"
    return header + b"\x00" * 32


def _probe_result(duration_seconds: float | None) -> ProbeResult:
    return ProbeResult(
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=duration_seconds,
        has_audio_stream=True,
        audio_stream_index=0,
        audio_codec_name="aac",
        sample_rate=48000,
        channels=2,
    )


# --- default-value lock (M7-02 acceptance criterion 6) ----------------------


def test_documented_defaults_have_not_silently_drifted():
    """Locks the numeric defaults `docs/supported-media-and-limits.md`
    documents; a change to any of these constants must also update that
    document (see its own "Revision path")."""

    assert DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES == 500 * 1024 * 1024
    assert DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES == 50 * 1024 * 1024
    assert DEFAULT_MAX_CUE_COUNT == 20
    assert DEFAULT_MAX_CONCURRENCY == 4
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS == 3600.0
    assert DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS == 600.0
    assert DEFAULT_MAX_AUDIO_AUDITION_DURATION_SECONDS == 600.0
    assert DEFAULT_MAX_AUDIO_AUDITION_RESPONSE_BYTES == 64 * 1024 * 1024


# --- media duration: WAV -----------------------------------------------------


def test_wav_at_exactly_the_duration_limit_is_accepted():
    array = _wav_bytes_of_duration(2.0)
    result = _wav_bytes_to_canonical_array(array, max_duration_seconds=2.0)
    assert result.dtype.name == "float32"


def test_wav_over_the_duration_limit_is_rejected():
    array = _wav_bytes_of_duration(2.5)
    with pytest.raises(MediaDurationExceededError):
        _wav_bytes_to_canonical_array(array, max_duration_seconds=2.0)


# --- media duration: MP4 (probed before decode) ------------------------------


def test_mp4_over_the_duration_limit_is_rejected_before_decode_runs():
    adapter = FFmpegMediaAdapter()
    with (
        patch.object(adapter, "probe", return_value=_probe_result(10.0)) as probe,
        patch.object(adapter, "extract_audio") as extract_audio,
    ):
        with pytest.raises(MediaDurationExceededError):
            _mp4_bytes_to_canonical_array(
                _mp4_stub_bytes(), adapter, max_duration_seconds=5.0
            )
    probe.assert_called_once()
    extract_audio.assert_not_called()


def test_mp4_at_exactly_the_duration_limit_proceeds_to_decode():
    adapter = FFmpegMediaAdapter()
    wav_bytes = _wav_bytes_of_duration(1.0)

    def _fake_extract_audio(input_path: str, output_path: str):
        Path(output_path).write_bytes(wav_bytes)

    with (
        patch.object(adapter, "probe", return_value=_probe_result(5.0)),
        patch.object(adapter, "extract_audio", side_effect=_fake_extract_audio) as extract_audio,
    ):
        result = _mp4_bytes_to_canonical_array(
            _mp4_stub_bytes(), adapter, max_duration_seconds=5.0
        )
    extract_audio.assert_called_once()
    assert result.dtype.name == "float32"


def test_mp4_with_unknown_probed_duration_is_not_rejected_by_duration_limit():
    """`ProbeResult.duration_seconds` can be `None`; this guardrail bounds
    a *known* excessive duration and does not reject media whose duration
    could not be determined (see `_enforce_duration_limit`)."""

    adapter = FFmpegMediaAdapter()
    wav_bytes = _wav_bytes_of_duration(1.0)

    def _fake_extract_audio(input_path: str, output_path: str):
        Path(output_path).write_bytes(wav_bytes)

    with (
        patch.object(adapter, "probe", return_value=_probe_result(None)),
        patch.object(adapter, "extract_audio", side_effect=_fake_extract_audio),
    ):
        # max_duration_seconds (2.0) is larger than the 1.0s extracted WAV
        # above, so a correct None-tolerant probe check must not be what
        # decides this outcome either way; it only proves a `None` probed
        # duration does not itself raise or short-circuit decoding.
        result = _mp4_bytes_to_canonical_array(
            _mp4_stub_bytes(), adapter, max_duration_seconds=2.0
        )
    assert result.dtype.name == "float32"


# --- FFmpeg timeout: distinguished internally, conflated at the REST boundary


def test_mp4_probe_timeout_raises_asset_processing_timeout_error():
    adapter = FFmpegMediaAdapter()
    with patch.object(adapter, "probe", side_effect=FFmpegTimeoutError("probe timed out")):
        with pytest.raises(AssetProcessingTimeoutError):
            _mp4_bytes_to_canonical_array(_mp4_stub_bytes(), adapter, max_duration_seconds=60.0)


def test_mp4_decode_timeout_raises_asset_processing_timeout_error():
    adapter = FFmpegMediaAdapter()
    with (
        patch.object(adapter, "probe", return_value=_probe_result(1.0)),
        patch.object(adapter, "extract_audio", side_effect=FFmpegTimeoutError("decode timed out")),
    ):
        with pytest.raises(AssetProcessingTimeoutError):
            _mp4_bytes_to_canonical_array(_mp4_stub_bytes(), adapter, max_duration_seconds=60.0)


# --- REST-boundary structured-error translation ------------------------------


@pytest.fixture()
def use_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(tmp_path / "analysis.sqlite3")
    )
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(tmp_path / "assets"))
    storage = _build_asset_storage()
    return _build_create_analysis_use_case(storage)


def test_over_duration_source_is_rejected_as_resource_limit_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(tmp_path / "analysis.sqlite3")
    )
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS", "1.0")
    storage = _build_asset_storage()
    use_case = _build_create_analysis_use_case(storage)

    source_asset = use_case._asset_storage.ingest(
        _wav_bytes_of_duration(2.0),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="source.wav",
        detected_media_type="audio/wav",
    )
    cue_asset = use_case._asset_storage.ingest(
        _wav_bytes_of_duration(0.5),
        logical_type=AssetType.CUE,
        informative_name="cue.wav",
        detected_media_type="audio/wav",
    )
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset.identifier,
        cues=[AnalysisCueReference(cue_id="cue-1", asset_id=cue_asset.identifier)],
    )

    with pytest.raises(ResourceLimitExceededError):
        _create_analysis(payload, Response(), use_case)


def test_ffmpeg_timeout_is_reported_as_unsupported_media_at_the_rest_boundary(use_case):
    source_asset = use_case._asset_storage.ingest(
        _mp4_stub_bytes(),
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="source.mp4",
        detected_media_type="video/mp4",
    )
    cue_asset = use_case._asset_storage.ingest(
        _wav_bytes_of_duration(0.5),
        logical_type=AssetType.CUE,
        informative_name="cue.wav",
        detected_media_type="audio/wav",
    )
    payload = AnalysisCreateRequest(
        source_asset_id=source_asset.identifier,
        cues=[AnalysisCueReference(cue_id="cue-1", asset_id=cue_asset.identifier)],
    )

    with patch.object(
        use_case._media_adapter, "probe", side_effect=FFmpegTimeoutError("probe timed out")
    ):
        with pytest.raises(UnsupportedMediaError):
            _create_analysis(payload, Response(), use_case)


# --- composition-root environment-variable wiring ----------------------------


def test_ffmpeg_timeout_and_concurrency_env_vars_are_read(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CONCURRENCY", raising=False)
    assert _ffmpeg_timeout_seconds() == DEFAULT_TIMEOUT_SECONDS
    assert _max_concurrency() == DEFAULT_MAX_CONCURRENCY

    monkeypatch.setenv("AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_CONCURRENCY", "7")
    assert _ffmpeg_timeout_seconds() == 12.5
    assert _max_concurrency() == 7


def test_duration_env_vars_are_read(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CUE_MEDIA_DURATION_SECONDS", raising=False)
    assert _max_source_media_duration_seconds() == DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS
    assert _max_cue_media_duration_seconds() == DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS

    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS", "120")
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_CUE_MEDIA_DURATION_SECONDS", "30")
    assert _max_source_media_duration_seconds() == 120.0
    assert _max_cue_media_duration_seconds() == 30.0


def test_composition_root_wires_overrides_into_the_constructed_use_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Confirms the override reaches the actually-constructed objects, not
    only the helper functions in isolation (a wiring bug could exist even
    if each helper independently reads its own environment variable
    correctly)."""

    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(tmp_path / "analysis.sqlite3")
    )
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS", "9.5")
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS", "111")
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_CUE_MEDIA_DURATION_SECONDS", "22")

    storage = _build_asset_storage()
    created_use_case = _build_create_analysis_use_case(storage)

    assert created_use_case._media_adapter._timeout_seconds == 9.5
    assert created_use_case._executor._worker_pool._max_workers == 2
    assert created_use_case.max_source_media_duration_seconds == 111.0
    assert created_use_case.max_cue_media_duration_seconds == 22.0


# --- S0013: audio audition duration/response-byte guardrails ---------------

_AUDITION_CANONICAL_RATE = CANONICAL_AUDIO_SPEC.sample_rate_hz
_AUDITION_SOURCE_ASSET_ID = "00000000-0000-4000-8000-000000000101"
_AUDITION_CUE_ASSET_ID = "00000000-0000-4000-8000-000000000102"
_AUDITION_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _audition_wav_bytes(
    frame_count: int, *, frame_rate: int = _AUDITION_CANONICAL_RATE
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(struct.pack(f"<{frame_count}h", *([0] * frame_count)))
    return buffer.getvalue()


def _audition_configuration() -> EffectiveConfigurationSnapshot:
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=_AUDITION_CANONICAL_RATE,
            channels=1,
            sample_format="float32",
            normalization=NormalizationSnapshot(
                enabled=True, method="peak", target_peak_amplitude=1.0
            ),
        ),
        matching=MatchingSnapshot(
            method="normalized_cross_correlation_v1", acceptance_threshold=0.7
        ),
        configuration_source_name="tests.operational.test_guardrails",
    )


def _audition_record() -> AnalysisRecord:
    timestamps = LifecycleTimestamps(queued_at=_AUDITION_NOW, running_at=_AUDITION_NOW, succeeded_at=_AUDITION_NOW)
    return AnalysisRecord(
        analysis_id="audition-analysis-1",
        state=AnalysisLifecycleState.SUCCEEDED,
        source_asset_id=_AUDITION_SOURCE_ASSET_ID,
        cues=(CueAssetReference(cue_id="cue-1", asset_id=_AUDITION_CUE_ASSET_ID),),
        effective_configuration=_audition_configuration(),
        lifecycle_timestamps=timestamps,
        result_reference="result:1",
    )


class _AuditionRepository:
    def __init__(self, record: AnalysisRecord) -> None:
        self._record = record

    def get(self, analysis_id: str) -> AnalysisRecord:
        if analysis_id != self._record.analysis_id:
            raise AnalysisNotFoundError(analysis_id)
        return self._record


class _AuditionResultReader:
    def __init__(self, serialized: str) -> None:
        self._serialized = serialized

    def read(self, reference: str) -> str:
        return self._serialized


class _AuditionAssetStorage:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = dict(contents)

    def read(self, identifier: str) -> bytes:
        return self._contents[identifier]


class _AuditionRenderer:
    """Constructor-level test seam: returns a fixed-size body without
    invoking FFmpeg, keeping these guardrail tests independent of the real
    subprocess boundary (already covered by
    `tests/test_media_processing_ffmpeg_adapter.py`)."""

    def __init__(self, output_size_bytes: int) -> None:
        self._output = b"\x00" * output_size_bytes
        self.called = False

    def render_wav_segment(
        self, media_bytes, *, start_seconds, duration_seconds, sample_rate_hz
    ) -> bytes:
        self.called = True
        return self._output


def _audition_use_case(
    *,
    cue_bytes: bytes,
    max_audition_duration_seconds: float = DEFAULT_MAX_AUDIO_AUDITION_DURATION_SECONDS,
    max_audition_response_bytes: int = DEFAULT_MAX_AUDIO_AUDITION_RESPONSE_BYTES,
    renderer: _AuditionRenderer | None = None,
) -> QueryAnalysisUseCase:
    record = _audition_record()
    result = AnalysisResult(
        analysis_id=record.analysis_id,
        method=_audition_configuration().matching.method,
        configuration=_audition_configuration(),
        cues=(CueResult(cue_id="cue-1", outcome=CueNoMatch()),),
    )
    return QueryAnalysisUseCase(
        _AuditionRepository(record),
        _AuditionResultReader(serialize_analysis_result(result)),
        asset_storage=_AuditionAssetStorage(
            {
                _AUDITION_CUE_ASSET_ID: cue_bytes,
                _AUDITION_SOURCE_ASSET_ID: b"source-bytes",
            }
        ),
        audition_renderer=renderer or _AuditionRenderer(len(cue_bytes)),
        max_audition_duration_seconds=max_audition_duration_seconds,
        max_audition_response_bytes=max_audition_response_bytes,
    )


def test_audition_duration_at_the_exact_configured_boundary_is_allowed():
    cue_bytes = _audition_wav_bytes(_AUDITION_CANONICAL_RATE)  # exactly 1.0s
    use_case = _audition_use_case(cue_bytes=cue_bytes, max_audition_duration_seconds=1.0)

    payload = use_case.get_cue_audition("audition-analysis-1", "cue-1")

    assert payload.content == cue_bytes


def test_audition_duration_above_the_configured_limit_is_rejected():
    cue_bytes = _audition_wav_bytes(2 * _AUDITION_CANONICAL_RATE)  # 2.0s
    use_case = _audition_use_case(cue_bytes=cue_bytes, max_audition_duration_seconds=1.0)

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_cue_audition("audition-analysis-1", "cue-1")


def test_audition_response_bytes_at_the_exact_configured_boundary_is_allowed():
    cue_bytes = _audition_wav_bytes(_AUDITION_CANONICAL_RATE)
    use_case = _audition_use_case(
        cue_bytes=cue_bytes, max_audition_response_bytes=len(cue_bytes)
    )

    payload = use_case.get_cue_audition("audition-analysis-1", "cue-1")

    assert payload.content == cue_bytes


def test_audition_response_bytes_above_the_configured_limit_is_rejected():
    cue_bytes = _audition_wav_bytes(_AUDITION_CANONICAL_RATE)
    use_case = _audition_use_case(
        cue_bytes=cue_bytes, max_audition_response_bytes=len(cue_bytes) - 1
    )

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_cue_audition("audition-analysis-1", "cue-1")


def test_rendered_occurrence_body_above_the_configured_limit_is_rejected_and_never_rendered_into_a_response():
    cue_bytes = _audition_wav_bytes(_AUDITION_CANONICAL_RATE)
    oversized_renderer = _AuditionRenderer(output_size_bytes=100)
    use_case = _audition_use_case(
        cue_bytes=cue_bytes,
        max_audition_response_bytes=50,
        renderer=oversized_renderer,
    )
    result = AnalysisResult(
        analysis_id="audition-analysis-1",
        method=_audition_configuration().matching.method,
        configuration=_audition_configuration(),
        cues=(
            CueResult(
                cue_id="cue-1",
                outcome=CueOccurrences(
                    occurrences=(
                        Occurrence(
                            cue_id="cue-1",
                            temporal_position=0.5,
                            score=0.9,
                            matching_method=_audition_configuration().matching.method,
                        ),
                    )
                ),
            ),
        ),
    )
    use_case._result_reader._serialized = serialize_analysis_result(result)

    # The route-level handler is exercised directly (mirroring this file's
    # own `_create_analysis` convention): a guardrail rejection must
    # propagate as the existing error-handling exception, never as a
    # `Response` carrying an over-limit or partial binary body.
    with pytest.raises(AudioAuditionResourceLimitError):
        _get_occurrence_audio("audition-analysis-1", "cue-1", 0, use_case)

    assert oversized_renderer.called is True


def test_audition_guardrails_do_not_alter_analysis_creation_or_matching_limits():
    """S0013 acceptance: the audition duration/response-byte guardrails are
    independent response-delivery bounds -- they never change the existing
    source/Cue upload, source/Cue matching-duration, or Asset retention
    limits."""

    assert DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS == 3600.0
    assert DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS == 600.0
    assert DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES == 500 * 1024 * 1024
    assert DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES == 50 * 1024 * 1024

    # A rejected audition request must not itself change these Application
    # constants nor the constructed `CreateAnalysisUseCase`'s own bounds.
    cue_bytes = _audition_wav_bytes(2 * _AUDITION_CANONICAL_RATE)
    use_case = _audition_use_case(cue_bytes=cue_bytes, max_audition_duration_seconds=1.0)
    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_cue_audition("audition-analysis-1", "cue-1")

    assert DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS == 3600.0
    assert DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS == 600.0
