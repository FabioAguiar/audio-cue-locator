"""Application: asynchronous Analysis creation (M5-04).

M5 must expose Analysis creation programmatically without letting an HTTP
request wait for acoustic matching to finish (`issues/M5/M5-04/formal-
issue.json`, sections 2-3; the formal issue's own top-severity risk is
"HTTP handlers execute matching directly and block long requests"). This
module is the Application-level use case that closes that gap: it
validates a request's cue count and Asset references, persists a `QUEUED`
`AnalysisRecord` through the existing M4-03 `AnalysisRepositoryPort`, and
schedules real acoustic matching on the existing M4-04
`LocalAnalysisExecutor` without waiting for it to finish.

Resolves the gaps `intents/M5/M5-04/implementation-handoff.json`
deliberately left open or only partially specified (G1, G2, G3, G4, G8, and
a newly-discovered G9):

- G2 (wrong-type Asset validation): `core.asset.AssetStoragePort` exposes
  no operation returning a previously-ingested Asset's `logical_type` by
  identifier (confirmed by the handoff's own repository inspection), so
  this module checks existence (`AssetStoragePort.read`, which raises
  `AssetNotFoundError`) and content-format compatibility with the
  declared role (reusing `application.asset_ingestion.sniff_media_type`,
  not reimplementing it) instead. A validly-ingested Asset used under the
  wrong logical role when both share a compatible container (for example,
  a real `cue` Asset supplied as `source_asset_id`, both `audio/wav`) is
  not detectable with any confirmed dependency and remains an explicit,
  accepted residual limitation (`docs/rest-api-v1-contract.md` documents
  it; it is not silently unhandled).
- G3 (idempotency): `AnalysisRepositoryPort.create` requires a caller-
  supplied `analysis_id` and offers no deduplication primitive, and
  `schemas.AnalysisCreateRequest` (M5-01) carries no client-supplied
  identifier or idempotency key. This module's explicit, documented
  baseline: `analysis_id` is always freshly server-generated
  (`uuid.uuid4`) for every request; there is no request-level
  deduplication, and a retried/duplicate submission produces a new,
  independent Analysis, exactly like any other request.
- G4 (default `EffectiveConfigurationSnapshot`): no function anywhere in
  `src/` built one before this issue. `docs/analysis-effective-
  configuration.md` section 3 names the choice between `baseline.
  DEFAULT_CONFIGURATION` and `acceptance.EVIDENCE_BASED_CONFIGURATION` as
  "a decision of a future Application layer" -- this module is that layer.
  `default_effective_configuration` below copies
  `infrastructure.media_processing.canonical_audio.CANONICAL_AUDIO_SPEC`
  into `canonicalization` and `infrastructure.acoustic_matching.
  acceptance.EVIDENCE_BASED_CONFIGURATION` (the evidence-based policy, not
  `baseline.py`'s provisional one) into `matching`.
- G1/G8/G9 (canonicalization timing, existence-check cost, and the lifecycle
  constraint that forced the final design): `LocalAnalysisExecutor.submit`
  requires already-canonical NumPy arrays and performs its own atomic
  `QUEUED`-to-`RUNNING` claim as the *first* thing it does
  (`infrastructure/execution/local_analysis_executor.py`). `core.
  analysis_lifecycle.VALID_TRANSITIONS` defines exactly three transitions
  -- `QUEUED`->`RUNNING`, `RUNNING`->`SUCCEEDED`, `RUNNING`->`FAILED` --
  and no `QUEUED`->`FAILED` transition exists. This means a canonicalization
  failure occurring *before* a `QUEUED` record is claimed cannot be
  persisted as `FAILED` through the existing, unmodified lifecycle contract
  (modifying `core.analysis_lifecycle` or `LocalAnalysisExecutor` is out of
  this issue's authorized scope). This module therefore performs Asset
  existence/content-format validation *and* canonicalization (WAV parsing,
  resampling, downmixing, and peak normalization to
  `CANONICAL_AUDIO_SPEC`, reusing `infrastructure.media_processing.
  FFmpegMediaAdapter.extract_audio` through one shared helper for every
  supported video source -- S0002, `specs/S0002-common-video-container-
  source-media-support/spec.md`, not only `video/mp4` -- before
  `AnalysisRepositoryPort.create` ever persists a `QUEUED` record -- not
  after, as `intents/M5/M5-04/implementation-handoff.json` had left open.
  A request whose Asset content cannot be canonicalized therefore fails the
  same way a missing or wrong-type Asset does (before any persistence or
  scheduling), and no Analysis is ever left permanently `QUEUED` with no
  path to a terminal state. Only the acoustic-matching correlation itself
  -- the formal issue's own named top risk, and typically the longest step
  for a long source recording -- remains scheduled on
  `LocalAnalysisExecutor`'s own bounded worker pool, never awaited by this
  module or by the HTTP request that reaches it.

This module depends on `core.asset.AssetStoragePort`,
`application.ports.analysis_repository.AnalysisRepositoryPort`, and --
mirroring `application.asset_ingestion`'s and `interfaces.rest_api.app`'s
own already-documented narrow exceptions, since no Application-owned port
wraps either -- `infrastructure.execution.local_analysis_executor.
LocalAnalysisExecutor`, `infrastructure.media_processing`, and
`infrastructure.acoustic_matching.acceptance` directly. Like
`application.asset_ingestion`, it cannot import
`interfaces.rest_api.errors` (`interfaces/rest_api/__init__.py` eagerly
imports `app.py`, which must import this module to build its composition
root -- a genuine circular import); `TooManyCuesError`,
`AssetContentIncompatibleError`, `AssetCanonicalizationError`,
`MediaDurationExceededError`, and `AssetProcessingTimeoutError` below are
this module's own Application-owned vocabulary for the five failure
classes only this module raises, translated to `interfaces.rest_api.
errors.ResourceLimitExceededError`/`UnsupportedMediaError` by
`interfaces.rest_api.analysis_routes` (M7-02 adds the latter two:
`MediaDurationExceededError` alongside `TooManyCuesError`, both mapped to
`ResourceLimitExceededError` as declared-limit violations, and
`AssetProcessingTimeoutError` alongside `AssetCanonicalizationError`, both
mapped to `UnsupportedMediaError` -- a deliberate, documented conflation
rather than a new `ErrorCode`, since `tests/test_api_v1_contracts.py`
outside this issue's authorized edit scope asserts that enum by exact
equality; see `docs/supported-media-and-limits.md`).
`core.asset.AssetNotFoundError`,
`core.asset.InvalidAssetIdentifierError`, and `application.ports.
analysis_repository.InvalidAnalysisRecordError`/`AnalysisAlreadyExistsError`
already exist, are already mapped by `interfaces.rest_api.errors`, and are
allowed to propagate unchanged.
"""

from __future__ import annotations

import io
import math
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import numpy as np
from scipy.signal import resample as scipy_resample

from audio_cue_locator.application.asset_ingestion import (
    CUE_SUPPORTED_MEDIA_TYPES,
    SOURCE_MEDIA_SUPPORTED_MEDIA_TYPES,
    sniff_media_type,
)
from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    AnalysisRepositoryPort,
    CueAssetReference,
)
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    MatchingSnapshot,
    NormalizationSnapshot,
)
from audio_cue_locator.core.asset import AssetStoragePort
from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
    EVIDENCE_BASED_CONFIGURATION,
)
from audio_cue_locator.infrastructure.execution.local_analysis_executor import (
    LocalAnalysisExecutor,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)
from audio_cue_locator.infrastructure.media_processing.errors import (
    FFmpegTimeoutError,
    MediaProcessingError,
)
from audio_cue_locator.infrastructure.media_processing.ffmpeg_adapter import (
    FFmpegMediaAdapter,
)
from audio_cue_locator.observability import emit_diagnostic_event

DEFAULT_MAX_CUE_COUNT = 20
"""Explicit, configurable maximum cue count per Analysis creation request
(gap G3 of `context-packs/M5/M5-04/issue-analysis.json`; the formal issue's
own "bounded non-empty cue-Asset set"). No empirical usage evidence exists
yet for this project (same evidence caveat as M5-03's own upload-size
defaults); 20 is a conservative, explicitly documented starting point sized
comfortably above typical multi-cue usage while still bounding the
worst-case per-Analysis matching cost. Overridable by
`interfaces.rest_api.app`'s composition-root wiring, exactly like M5-03's
upload limits."""

DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS = 3600.0
"""Explicit, configurable maximum probed duration (M7-02 gap G2: no
media-duration limit existed anywhere in the source tree before this
issue) for a `source_asset_id` Asset. No representative measurement of
real project usage exists yet (this issue's own test-execution phase is
separately authorized and not performed here); this starting point is
derived from the already-accepted 500 MiB upload-size limit
(`asset_ingestion.DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES`), which
already implies an approximate ~49.5-minute ceiling for uncompressed
16-bit/44.1kHz/stereo PCM WAV (500 MiB / 176,400 bytes-per-second), rounded
up to a communicable 60 minutes. A highly-compressed MP4 source is *not*
already bounded this way by the size limit alone, which is this guardrail's
primary justification (`states/M7/M7-02/issue-operational-state.json`
gap G2). Overridable by `interfaces.rest_api.app`'s composition-root
wiring, exactly like the existing upload/cue-count limits; must be revised
from representative local measurement once a future, separately authorized
test-execution phase runs `tests/operational/test_guardrails.py`."""

MAX_CUE_LABEL_CODEPOINTS = 80
"""S0003: the maximum length of a normalized, non-null Cue `label`, in
Unicode code points (`len()` on a Python `str` already counts code points,
not UTF-8 bytes or grapheme clusters). Matches the audited Home design's
own label-length bound
(`specs/S0003-cue-labels-and-optional-trim-bounds-contract/spec.md`)."""

DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS = 600.0
"""Explicit, configurable maximum probed duration for a cue Asset,
independently smaller than `DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS`
because a cue is this project's own short reference snippet searched for
inside the longer source recording (`docs/vision.md`), not itself expected
to be a long recording. Same evidence caveat as
`DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS`: a reasoned starting point, not
yet backed by representative measurement."""

_EFFECTIVE_CONFIGURATION_SOURCE_NAME = (
    "acoustic_matching.acceptance.EVIDENCE_BASED_CONFIGURATION"
)
"""Recorded verbatim in `EffectiveConfigurationSnapshot.configuration_source_name`,
naming the evidence-based M2-05 policy this module selects by default (gap
G4) -- never `baseline.DEFAULT_CONFIGURATION`, which `docs/matching-
acceptance.md` itself documents as provisional and non-calibrated."""

_PCM_DTYPE_BY_SAMPLE_WIDTH: dict[int, type] = {1: np.uint8, 2: np.int16, 4: np.int32}


class TooManyCuesError(ValueError):
    """Raised when a creation request's cue count exceeds the configured
    maximum. Translated to `interfaces.rest_api.errors.
    ResourceLimitExceededError` by `interfaces.rest_api.analysis_routes`."""


class AssetContentIncompatibleError(ValueError):
    """Raised when a referenced Asset's detected container is not
    compatible with its declared role in an Analysis creation request (for
    example, a `video/mp4` Asset supplied as a cue). Translated to
    `interfaces.rest_api.errors.UnsupportedMediaError` by
    `interfaces.rest_api.analysis_routes`."""


class AssetCanonicalizationError(ValueError):
    """Raised when a referenced Asset's content matches a supported
    container signature but cannot actually be decoded/canonicalized (a
    malformed WAV, or an MP4 ffmpeg/ffprobe cannot decode). Translated to
    `interfaces.rest_api.errors.UnsupportedMediaError` by
    `interfaces.rest_api.analysis_routes`, exactly like
    `AssetContentIncompatibleError`: both mean this issue's Analysis cannot
    be created from the supplied media."""


class MediaDurationExceededError(ValueError):
    """Raised when a referenced Asset's probed duration exceeds the
    configured `max_source_media_duration_seconds`/
    `max_cue_media_duration_seconds` limit (M7-02 acceptance criterion 1).
    Translated to `interfaces.rest_api.errors.ResourceLimitExceededError`
    by `interfaces.rest_api.analysis_routes`, exactly like
    `TooManyCuesError`: both mean a declared quantity/size/duration limit
    was exceeded, not that the media itself is invalid or unsupported."""


class AssetProcessingTimeoutError(RuntimeError):
    """Raised when probing or decoding a referenced Asset's media exceeds
    the configured FFmpeg subprocess timeout (M7-02 gap G3), kept distinct
    from `AssetCanonicalizationError` at this Application boundary so the
    two failure causes are never confused in this module's own code and
    exception handling. Translated to `interfaces.rest_api.errors.
    UnsupportedMediaError` by `interfaces.rest_api.analysis_routes`,
    exactly like `AssetCanonicalizationError`: `tests/
    test_api_v1_contracts.py` (outside this issue's authorized edit scope)
    asserts the external `ErrorCode` enum by exact equality, so this issue
    deliberately keeps the client-visible contract unchanged rather than
    adding a new code (`docs/supported-media-and-limits.md`)."""


class InvalidCueRequestError(ValueError):
    """Raised when a requested Cue's `label` or trim bounds are
    semantically invalid (S0003): a non-string label, an over-length
    label, a non-finite/negative trim bound, a reversed `[start, end)`
    pair, a `trim_start_seconds` not before the Cue's own duration, a
    `trim_end_seconds` exceeding it, or an effective interval containing
    no canonical sample. Owned by Application, not Core or Infrastructure
    (`specs/S0003-cue-labels-and-optional-trim-bounds-contract/spec.md`).
    Translated to `interfaces.rest_api.errors.ErrorCode.VALIDATION_ERROR`
    (400) by `interfaces.rest_api.errors`; no new `ErrorCode` is added."""


@dataclass(frozen=True)
class CueRequest:
    """Application's own input shape for one requested cue: a `cue_id`
    paired with the Asset identifier to resolve, plus S0003's optional,
    presentation-only `label` and optional Cue-local `trim_start_seconds`/
    `trim_end_seconds`. Declared independently of `interfaces.rest_api.
    schemas.AnalysisCueReference` so this module never depends on
    Interfaces (`docs/architecture.md`, Principle 2).

    `__post_init__` performs every *structural* validation that does not
    require knowing the Cue's own decoded duration: `label` is trimmed of
    surrounding whitespace (a blank-after-trim label normalizes to
    `None`) and bounded to `MAX_CUE_LABEL_CODEPOINTS`; each trim bound, if
    present, must be a finite, non-negative number, and if both are
    present, `trim_start_seconds` must be strictly less than
    `trim_end_seconds`. Duration-aware bounds (`start` before the Cue's
    own duration, `end` not exceeding it, and the effective interval
    containing at least one canonical sample) can only be checked once
    the Cue is decoded, so `CreateAnalysisUseCase` validates those
    separately."""

    cue_id: str
    asset_id: str
    label: str | None = None
    trim_start_seconds: float | None = None
    trim_end_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.label is not None:
            if not isinstance(self.label, str):
                raise InvalidCueRequestError("Cue label must be a string or None")
            normalized_label = self.label.strip() or None
            if (
                normalized_label is not None
                and len(normalized_label) > MAX_CUE_LABEL_CODEPOINTS
            ):
                raise InvalidCueRequestError(
                    "Cue label exceeds the maximum of "
                    f"{MAX_CUE_LABEL_CODEPOINTS} Unicode code points"
                )
            object.__setattr__(self, "label", normalized_label)

        for field_name in ("trim_start_seconds", "trim_end_seconds"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidCueRequestError(
                    f"Cue {field_name} must be a finite non-negative number "
                    "or None"
                )
            if not math.isfinite(value):
                raise InvalidCueRequestError(f"Cue {field_name} must be finite")
            if value < 0:
                raise InvalidCueRequestError(f"Cue {field_name} must be >= 0")

        if (
            self.trim_start_seconds is not None
            and self.trim_end_seconds is not None
            and not self.trim_start_seconds < self.trim_end_seconds
        ):
            raise InvalidCueRequestError(
                "Cue trim_start_seconds must be strictly less than "
                "trim_end_seconds"
            )


ClockFn = Callable[[], datetime]


def _default_clock() -> datetime:
    """Timezone-aware UTC `datetime`, matching `LifecycleTimestamps`'s own
    requirement (mirrors `infrastructure.execution.local_analysis_executor.
    _default_clock`)."""

    return datetime.now(timezone.utc)


def default_effective_configuration() -> EffectiveConfigurationSnapshot:
    """Build this issue's chosen default `EffectiveConfigurationSnapshot`
    (gap G4): `CANONICAL_AUDIO_SPEC` copied field-by-field into
    `canonicalization`, and `EVIDENCE_BASED_CONFIGURATION` copied
    field-by-field into `matching`. Never imports either Infrastructure
    type into a persisted value; only reads their already-fixed values once
    per call, exactly at the point of use (`docs/analysis-effective-
    configuration.md`, section 4)."""

    spec = CANONICAL_AUDIO_SPEC
    matching_policy = EVIDENCE_BASED_CONFIGURATION
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=spec.sample_rate_hz,
            channels=spec.channels,
            sample_format=spec.sample_format,
            normalization=NormalizationSnapshot(
                enabled=spec.normalization.enabled,
                method=spec.normalization.method,
                target_peak_amplitude=spec.normalization.target_peak_amplitude,
            ),
        ),
        matching=MatchingSnapshot(
            method=matching_policy.method,
            acceptance_threshold=matching_policy.acceptance_threshold,
        ),
        configuration_source_name=_EFFECTIVE_CONFIGURATION_SOURCE_NAME,
    )


def _pcm_bytes_to_float32(raw: bytes, sample_width: int) -> np.ndarray:
    """Convert raw little-endian PCM sample bytes to float32 samples in
    `[-1.0, 1.0]`. 8-bit WAV PCM is unsigned and centered at 128; wider
    widths are signed, per the WAV/PCM format `wave` already parsed."""

    dtype = _PCM_DTYPE_BY_SAMPLE_WIDTH.get(sample_width)
    if dtype is None:
        raise AssetCanonicalizationError(
            f"unsupported WAV PCM sample width: {sample_width} bytes"
        )
    integers = np.frombuffer(raw, dtype=dtype)
    if sample_width == 1:
        return (integers.astype(np.float32) - 128.0) / 128.0
    max_value = float(2 ** (8 * sample_width - 1))
    return integers.astype(np.float32) / max_value


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Resample `samples` from `source_rate` to `target_rate` using
    `scipy.signal.resample` (already an existing project dependency).
    A no-op when the rates already match."""

    if source_rate == target_rate:
        return samples
    if source_rate <= 0:
        raise AssetCanonicalizationError("WAV sample rate must be positive")
    if samples.shape[0] == 0:
        return samples
    target_length = max(1, round(samples.shape[0] * target_rate / source_rate))
    resampled = scipy_resample(samples, target_length)
    return resampled.astype(np.float32)


def _enforce_duration_limit(
    duration_seconds: float | None, max_duration_seconds: float
) -> None:
    """Reject media whose probed `duration_seconds` exceeds
    `max_duration_seconds` (M7-02 acceptance criterion 1). A `None`
    duration (never observed for a WAV parsed by `wave`, but possible for
    an MP4 `ProbeResult` if ffprobe reports no duration) is not enforced:
    this guardrail bounds a *known* excessive duration, it does not reject
    media whose duration could not be determined."""

    if duration_seconds is not None and duration_seconds > max_duration_seconds:
        raise MediaDurationExceededError(
            f"media duration {duration_seconds:.3f}s exceeds the configured "
            f"maximum of {max_duration_seconds:.3f}s"
        )


def _decode_and_resample_wav(
    wav_bytes: bytes,
    *,
    max_duration_seconds: float = DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
) -> np.ndarray:
    """Decode WAV PCM bytes into a mono, float32,
    `CANONICAL_AUDIO_SPEC.sample_rate_hz` NumPy array, using only the
    standard-library `wave` module -- no FFmpeg invocation is needed for a
    WAV input, since `wave` already parses the PCM container directly.
    `wave`'s own header parse already constitutes real structural probing
    beyond an extension/declared-type check (M7-02 gap G5: this is
    accepted as sufficient WAV probing rather than adding a second,
    ffprobe-based check; see docs/supported-media-and-limits.md).

    Deliberately does **not** apply `CANONICAL_AUDIO_SPEC`'s peak
    normalization (S0003 ordering requirement: `decode/downmix/resample ->
    select requested Cue interval -> canonical normalization -> matcher
    input`): a caller that needs to select a Cue-local sub-interval before
    normalizing calls this helper directly; `_wav_bytes_to_canonical_array`
    below is the unchanged, name-stable full pipeline for every caller
    that does not."""

    try:
        with io.BytesIO(wav_bytes) as buffer, wave.open(buffer, "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            frame_rate = reader.getframerate()
            frame_count = reader.getnframes()
            raw = reader.readframes(frame_count)
    except Exception as exc:
        # `wave`'s own chunk parser raises a variety of exception types for
        # malformed input beyond its documented `wave.Error`/`EOFError`
        # (confirmed by direct execution at implementation time: a
        # truncated/invalid chunk can also raise a bare `RuntimeError` from
        # `wave.Chunk.seek`). Any parse failure here means this Asset's
        # content cannot be canonicalized, regardless of the specific
        # internal exception type `wave` happens to raise for it.
        raise AssetCanonicalizationError(f"malformed WAV content: {exc}") from exc

    duration_seconds = (frame_count / frame_rate) if frame_rate else None
    _enforce_duration_limit(duration_seconds, max_duration_seconds)

    samples = _pcm_bytes_to_float32(raw, sample_width)
    if channels > 1:
        usable_length = (samples.shape[0] // channels) * channels
        samples = samples[:usable_length].reshape(-1, channels).mean(axis=1)
    samples = samples.astype(np.float32)

    samples = _resample(samples, frame_rate, CANONICAL_AUDIO_SPEC.sample_rate_hz)
    return samples.astype(np.float32)


def _normalize_canonical_segment(samples: np.ndarray) -> np.ndarray:
    """Apply `CANONICAL_AUDIO_SPEC`'s peak normalization to an already
    decoded/downmixed/resampled (and, for a Cue, already interval-selected
    -- S0003) segment, then reject a non-finite result: the same
    finiteness guarantee the pre-S0003 single-pass pipeline always
    provided, now enforced at the one point every canonical segment
    -- source or effective trimmed Cue -- passes through before reaching
    the matcher."""

    if CANONICAL_AUDIO_SPEC.normalization.enabled and samples.shape[0] > 0:
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 0.0:
            samples = (samples / peak) * CANONICAL_AUDIO_SPEC.normalization.target_peak_amplitude

    if not np.isfinite(samples).all():
        raise AssetCanonicalizationError(
            "canonicalized audio contains non-finite samples"
        )
    return samples.astype(np.float32)


def _wav_bytes_to_canonical_array(
    wav_bytes: bytes,
    *,
    max_duration_seconds: float = DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
) -> np.ndarray:
    """Decode WAV PCM bytes into a `CANONICAL_AUDIO_SPEC`-conformant
    (mono, float32, `CANONICAL_AUDIO_SPEC.sample_rate_hz`, peak-normalized)
    NumPy array: the unchanged, name-stable full `_decode_and_resample_wav`
    -> `_normalize_canonical_segment` pipeline, used for source media
    (never trimmed) and for a Cue when no Cue-local interval selection is
    needed (`_video_bytes_to_canonical_array` also reaches this for every
    S0002 video type's extracted WAV bytes)."""

    samples = _decode_and_resample_wav(
        wav_bytes, max_duration_seconds=max_duration_seconds
    )
    return _normalize_canonical_segment(samples)


_VIDEO_MEDIA_TYPE_TEMP_SUFFIXES: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
"""The one safe, fixed mapping the shared video canonicalization helper
below uses to name its own temporary input file -- keyed by the already
`sniff_media_type`-detected media type, never by a client-supplied
filename or extension (S0002 acceptance: "no untrusted client filename is
used as a temporary filesystem path"). `ffprobe`/`ffmpeg` do not require a
container-matching suffix to decode correctly, but a real suffix keeps
`FFmpegMediaAdapter`'s own diagnostics/logging container-identifiable."""


def _video_bytes_to_canonical_array(
    video_bytes: bytes,
    media_type: str,
    adapter: FFmpegMediaAdapter,
    *,
    max_duration_seconds: float = DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
) -> np.ndarray:
    """Decode any S0002-supported video container's audio stream to WAV via
    the confirmed-existing `FFmpegMediaAdapter.extract_audio` (this
    project's sole permitted ffmpeg/ffprobe invocation point), then
    canonicalize the resulting WAV bytes exactly like a native WAV upload.

    One shared helper serves every supported video media type
    (`_VIDEO_MEDIA_TYPE_TEMP_SUFFIXES`) rather than one helper per
    container -- `media_type` must already be an
    `application.asset_ingestion.sniff_media_type` result, not a
    client-supplied value.

    Probes the file first so an over-duration source is rejected before
    the (potentially slower) decode step runs, and so an FFmpeg/ffprobe
    subprocess timeout is raised as `AssetProcessingTimeoutError` --
    distinguishable from a genuinely malformed file -- at both the probe
    and the decode call (M7-02 gap G3)."""

    suffix = _VIDEO_MEDIA_TYPE_TEMP_SUFFIXES.get(media_type)
    if suffix is None:
        raise AssetCanonicalizationError(
            f"no FFmpeg-backed canonicalization path is defined for media "
            f"type {media_type!r}"
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        output_path = Path(tmp_dir) / "output.wav"
        input_path.write_bytes(video_bytes)
        try:
            probe_result = adapter.probe(str(input_path))
        except FFmpegTimeoutError as exc:
            raise AssetProcessingTimeoutError(
                f"timed out while probing {media_type} media: {exc}"
            ) from exc
        except MediaProcessingError as exc:
            raise AssetCanonicalizationError(
                f"could not probe {media_type} media: {exc}"
            ) from exc
        _enforce_duration_limit(probe_result.duration_seconds, max_duration_seconds)
        try:
            adapter.extract_audio(str(input_path), str(output_path))
        except FFmpegTimeoutError as exc:
            raise AssetProcessingTimeoutError(
                f"timed out while decoding {media_type} audio: {exc}"
            ) from exc
        except MediaProcessingError as exc:
            raise AssetCanonicalizationError(
                f"could not decode {media_type} audio: {exc}"
            ) from exc
        wav_bytes = output_path.read_bytes()
    return _wav_bytes_to_canonical_array(wav_bytes, max_duration_seconds=max_duration_seconds)


def _mp4_bytes_to_canonical_array(
    mp4_bytes: bytes,
    adapter: FFmpegMediaAdapter,
    *,
    max_duration_seconds: float = DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
) -> np.ndarray:
    """Preserved, name-stable thin wrapper over `_video_bytes_to_canonical_
    array` for `video/mp4` specifically. `tests/operational/
    test_guardrails.py` (a read-only reference for this issue -- outside
    `repository_context.allowed_edit_paths`) imports this private helper by
    its exact pre-S0002 name and calls it with its exact pre-S0002
    positional signature; every other caller in this module (including the
    real `video/mp4` dispatch path in `_resolve_and_canonicalize`) goes
    through the single shared `_video_bytes_to_canonical_array` helper
    directly, so this remains a compatibility shim, not a second
    per-container implementation."""

    return _video_bytes_to_canonical_array(
        mp4_bytes, "video/mp4", adapter, max_duration_seconds=max_duration_seconds
    )


def _select_cue_interval(
    samples: np.ndarray, cue: CueRequest, cue_duration_seconds: float
) -> np.ndarray:
    """Slice an already decoded/downmixed/resampled, not-yet-normalized
    Cue array (`_decode_and_resample_wav`) to `cue`'s requested half-open
    `[trim_start_seconds, trim_end_seconds)` interval (S0003), validating
    the duration-aware bounds that can only be checked once the Cue's own
    canonical `cue_duration_seconds` is known: `CueRequest.__post_init__`
    already rejected a non-finite/negative bound or a reversed pair
    without needing this. An omitted `trim_start_seconds` means `0`; an
    omitted `trim_end_seconds` means `cue_duration_seconds` (the full Cue).
    Never touches the source-media array: this function's only caller is
    `CreateAnalysisUseCase.create`'s per-cue loop."""

    sample_rate = CANONICAL_AUDIO_SPEC.sample_rate_hz
    start_seconds = (
        cue.trim_start_seconds if cue.trim_start_seconds is not None else 0.0
    )
    if start_seconds >= cue_duration_seconds:
        raise InvalidCueRequestError(
            f"cues[{cue.cue_id!r}].trim_start_seconds must be before the "
            f"Cue's own duration ({cue_duration_seconds:.6f}s)"
        )
    if cue.trim_end_seconds is not None and cue.trim_end_seconds > cue_duration_seconds:
        raise InvalidCueRequestError(
            f"cues[{cue.cue_id!r}].trim_end_seconds must not exceed the "
            f"Cue's own duration ({cue_duration_seconds:.6f}s)"
        )
    end_seconds = (
        cue.trim_end_seconds if cue.trim_end_seconds is not None else cue_duration_seconds
    )

    total_samples = samples.shape[0]
    start_index = min(max(0, round(start_seconds * sample_rate)), total_samples)
    end_index = min(max(0, round(end_seconds * sample_rate)), total_samples)
    if end_index - start_index < 1:
        raise InvalidCueRequestError(
            f"cues[{cue.cue_id!r}] effective trim interval must contain at "
            "least one canonical sample"
        )
    return samples[start_index:end_index]


class CreateAnalysisUseCase:
    """Application-level asynchronous Analysis creation: validates cue
    count and Asset references, canonicalizes their content, persists a
    `QUEUED` `AnalysisRecord`, and schedules acoustic matching on the
    injected `LocalAnalysisExecutor` without waiting for it."""

    def __init__(
        self,
        *,
        repository: AnalysisRepositoryPort,
        asset_storage: AssetStoragePort,
        executor: LocalAnalysisExecutor,
        max_cue_count: int = DEFAULT_MAX_CUE_COUNT,
        media_adapter: FFmpegMediaAdapter | None = None,
        max_source_media_duration_seconds: float = DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
        max_cue_media_duration_seconds: float = DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS,
        clock: ClockFn = _default_clock,
    ) -> None:
        if max_cue_count < 1:
            raise ValueError("max_cue_count must be a positive integer")
        if max_source_media_duration_seconds <= 0:
            raise ValueError("max_source_media_duration_seconds must be positive")
        if max_cue_media_duration_seconds <= 0:
            raise ValueError("max_cue_media_duration_seconds must be positive")
        self._repository = repository
        self._asset_storage = asset_storage
        self._executor = executor
        self._max_cue_count = max_cue_count
        self._media_adapter = media_adapter or FFmpegMediaAdapter()
        self._max_source_media_duration_seconds = max_source_media_duration_seconds
        self._max_cue_media_duration_seconds = max_cue_media_duration_seconds
        self._clock = clock

    @property
    def max_cue_count(self) -> int:
        return self._max_cue_count

    @property
    def max_source_media_duration_seconds(self) -> float:
        return self._max_source_media_duration_seconds

    @property
    def max_cue_media_duration_seconds(self) -> float:
        return self._max_cue_media_duration_seconds

    def create(
        self, *, source_asset_id: str, cues: Sequence[CueRequest]
    ) -> AnalysisRecord:
        """Validate, canonicalize, and persist one new Analysis, then
        schedule its matching run.

        Every rejection below (excessive cue count, a missing/invalid
        Asset identifier -- propagated unchanged from `AssetStoragePort.
        read` --, a content-incompatible Asset, or an uncanonicalizable
        Asset) happens before `AnalysisRepositoryPort.create` or
        `LocalAnalysisExecutor.submit` is ever called, so no Analysis is
        persisted or scheduled for a rejected request. `analysis_id` is
        always freshly generated here (no-idempotency baseline, gap G3):
        two calls with identical arguments persist two independent
        Analyses.
        """

        if len(cues) > self._max_cue_count:
            raise TooManyCuesError(
                f"request has {len(cues)} cues, exceeding the configured "
                f"maximum of {self._max_cue_count}"
            )

        try:
            canonical_source = self._resolve_and_canonicalize(
                source_asset_id,
                allowed_media_types=SOURCE_MEDIA_SUPPORTED_MEDIA_TYPES,
                role="source_asset_id",
                max_duration_seconds=self._max_source_media_duration_seconds,
            )

            cue_references: list[CueAssetReference] = []
            canonical_cues: dict[str, np.ndarray] = {}
            for cue in cues:
                # S0003: the cue-media duration guardrail
                # (`max_cue_media_duration_seconds`) still runs first,
                # inside `_resolve_and_canonicalize`'s decode step -- a
                # requested trim can never bypass it. `normalize=False`
                # defers peak normalization until after the requested
                # interval is selected below, so an out-of-interval sample
                # never influences the effective segment's own peak.
                raw_cue_samples = self._resolve_and_canonicalize(
                    cue.asset_id,
                    allowed_media_types=CUE_SUPPORTED_MEDIA_TYPES,
                    role=f"cues[{cue.cue_id!r}].asset_id",
                    max_duration_seconds=self._max_cue_media_duration_seconds,
                    normalize=False,
                )
                cue_duration_seconds = (
                    raw_cue_samples.shape[0] / CANONICAL_AUDIO_SPEC.sample_rate_hz
                )
                effective_samples = _select_cue_interval(
                    raw_cue_samples, cue, cue_duration_seconds
                )
                canonical_cues[cue.cue_id] = _normalize_canonical_segment(
                    effective_samples
                )
                cue_references.append(
                    CueAssetReference(
                        cue_id=cue.cue_id,
                        asset_id=cue.asset_id,
                        label=cue.label,
                        trim_start_seconds=cue.trim_start_seconds,
                        trim_end_seconds=cue.trim_end_seconds,
                    )
                )
        except Exception as exc:
            # M7-04: application/media-processing boundary diagnostic.
            # analysis_id is never available here: every rejection this
            # `except` observes happens before `AnalysisRepositoryPort.
            # create` ever persists a record (this method's own docstring,
            # "before ... ever called"), so no Analysis identifier exists
            # yet to attach.
            emit_diagnostic_event(
                event="media_canonicalization_failed",
                boundary="media_processing",
                outcome="failed",
                category=type(exc).__name__,
            )
            raise

        analysis_id = str(uuid4())
        record = self._repository.create(
            analysis_id=analysis_id,
            source_asset_id=source_asset_id,
            cues=tuple(cue_references),
            effective_configuration=default_effective_configuration(),
            queued_at=self._clock(),
        )

        # Fire-and-forget: LocalAnalysisExecutor.submit never blocks its
        # caller, and this use case does not wait on the returned Future
        # either, so acoustic matching never delays this method's return
        # (formal issue's own top-severity risk).
        self._executor.submit(analysis_id, canonical_source, canonical_cues)

        return record

    def _resolve_and_canonicalize(
        self,
        asset_id: str,
        *,
        allowed_media_types: frozenset[str],
        role: str,
        max_duration_seconds: float,
        normalize: bool = True,
    ) -> np.ndarray:
        """Read `asset_id` (raising `AssetNotFoundError`/
        `InvalidAssetIdentifierError` unchanged if it does not exist or is
        malformed -- both already mapped by `interfaces.rest_api.errors`),
        confirm its detected content matches `allowed_media_types` for
        `role`, enforce `max_duration_seconds` (M7-02 acceptance criterion
        1), and return its canonical array.

        `normalize=False` (S0003) returns the decoded/downmixed/resampled
        array *before* peak normalization, so a caller that still needs to
        select a Cue-local sub-interval can do so before normalizing.
        Every referenced Asset reaching the video branch below is a
        `source_asset_id` (a Cue's own `allowed_media_types` is always
        `{"audio/wav"}"`, `application.asset_ingestion.
        CUE_SUPPORTED_MEDIA_TYPES`), so `normalize=False` for that branch
        never actually arises in practice; it is honored for a WAV input
        either way and simply has no effect on the always-normalized video
        path."""

        content = self._asset_storage.read(asset_id)
        detected_media_type = sniff_media_type(content)
        if detected_media_type is None or detected_media_type not in allowed_media_types:
            raise AssetContentIncompatibleError(
                f"{role}={asset_id!r} does not have content compatible with "
                f"its role (detected {detected_media_type!r}, expected one "
                f"of {sorted(allowed_media_types)!r})"
            )
        if detected_media_type == "audio/wav":
            samples = _decode_and_resample_wav(
                content, max_duration_seconds=max_duration_seconds
            )
            return _normalize_canonical_segment(samples) if normalize else samples
        return _video_bytes_to_canonical_array(
            content,
            detected_media_type,
            self._media_adapter,
            max_duration_seconds=max_duration_seconds,
        )
