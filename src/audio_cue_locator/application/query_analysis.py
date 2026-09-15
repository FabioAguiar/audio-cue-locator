"""Application queries for persisted Analysis status and completed Results.

The REST interface consumes this module instead of reading SQLite or result
storage directly.  Persisted ``AnalysisRecord.state`` is authoritative for
every decision: querying never changes lifecycle state and never infers it
from the presence of a stored Result body.

S0013 extends this same read-only use case with two bounded audio audition
capabilities: playing back a completed Analysis's original Cue bytes, and
rendering the exact source-time window one accepted Result occurrence
represents. Both audition methods are gated by the existing ``get_result``
lifecycle semantics above and read Cue/source bytes through the
Application-owned ``core.asset.AssetStoragePort``; no filesystem path or
concrete Infrastructure adapter is imported here. Rendering a source-time
segment is delegated to the ``AudioAuditionRendererPort`` Protocol below,
structurally satisfied by ``infrastructure.media_processing.
FFmpegMediaAdapter.render_wav_segment`` in production
(``interfaces.rest_api.app``'s composition root), keeping every actual
ffmpeg/ffprobe invocation inside that one existing Infrastructure boundary.
"""

from __future__ import annotations

import io
import json
import wave
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    AnalysisRepositoryPort,
    CueAssetReference,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.asset import AssetStoragePort


class AnalysisResultNotReadyError(RuntimeError):
    """Raised when a Result is requested while an Analysis is nonterminal."""

    def __init__(self, analysis_id: str, state: AnalysisLifecycleState) -> None:
        self.analysis_id = analysis_id
        self.state = state
        super().__init__(
            f"Analysis {analysis_id!r} has no available Result while {state.value}"
        )


class AnalysisFailedError(RuntimeError):
    """Raised when a FAILED Analysis is asked for a successful Result body."""

    def __init__(self, analysis_id: str) -> None:
        self.analysis_id = analysis_id
        super().__init__(f"Analysis {analysis_id!r} failed and has no Result")


class AnalysisResultIntegrityError(RuntimeError):
    """Raised for an invalid Result reference/body on a SUCCEEDED Analysis.

    This exception is deliberately not part of the public error vocabulary.
    The REST catch-all translates it to the sanitized ``internal_error``
    response without exposing the reference, serialized body, or decoder
    details.
    """


@runtime_checkable
class ResultReferenceReaderPort(Protocol):
    """Read a canonical serialized Result through an opaque reference."""

    def read(self, reference: str) -> str:
        """Return the serialized Result stored under ``reference``."""

        ...


@runtime_checkable
class AudioAuditionRendererPort(Protocol):
    """Render one bounded mono PCM WAV segment from arbitrary source media.

    Application-owned: this Protocol contains no filesystem path and
    imports no concrete Infrastructure adapter. ``infrastructure.
    media_processing.FFmpegMediaAdapter.render_wav_segment`` structurally
    satisfies it in production without this module importing that class.
    """

    def render_wav_segment(
        self,
        media_bytes: bytes,
        *,
        start_seconds: float,
        duration_seconds: float,
        sample_rate_hz: int,
    ) -> bytes:
        """Return mono PCM16 WAV bytes for ``[start_seconds, start_seconds +
        duration_seconds)`` of ``media_bytes``, resampled to
        ``sample_rate_hz``. Applies no amplitude normalization."""

        ...


@dataclass(frozen=True)
class AudioAuditionPayload:
    """One bounded, transient audition response body (S0013).

    Never persisted: every audition payload is constructed fresh per
    request from already-authoritative persisted Analysis/Result metadata
    and Asset bytes, and is discarded once the REST response is sent.
    """

    content: bytes
    media_type: str = "audio/wav"


class AuditionTargetNotFoundError(LookupError):
    """Raised when a requested Cue/occurrence audition target does not
    exist for the requested Analysis: an unknown ``cue_id``, a ``cue_id``
    belonging to a different Analysis, a Cue with no occurrence outcome
    (``no_match``/``failure``), or an occurrence index outside that Cue's
    canonical occurrence array. Translated to the existing
    ``resource_not_found`` family by ``interfaces.rest_api.errors``; no new
    ``ErrorCode`` is added."""


class AudioAuditionResourceLimitError(ValueError):
    """Raised when a requested audition's duration or response body would
    exceed the configured audition guardrails. Independent from the
    source/Cue upload and matching-duration limits. Translated to the
    existing ``resource_limit_exceeded`` family by
    ``interfaces.rest_api.errors``; no new ``ErrorCode`` is added."""


class AudioAuditionRenderingError(RuntimeError):
    """Raised when audition Cue-duration decoding or source-segment
    rendering fails technically (malformed WAV timing metadata, or a
    renderer/FFmpeg failure). Deliberately not part of the public error
    vocabulary -- like ``AnalysisResultIntegrityError``, the REST catch-all
    translates it to the sanitized ``internal_error`` response. Never
    carries raw media bytes, a physical path, or FFmpeg stderr in its own
    message."""


class AuditionCollaboratorsNotConfiguredError(RuntimeError):
    """Raised when an audition method is invoked on a
    ``QueryAnalysisUseCase`` constructed without its required
    ``asset_storage``/``audition_renderer`` collaborators (an internal
    Application wiring error). The production composition root always
    supplies both; ordinary ``get_status``/``get_result`` callers are
    unaffected."""


DEFAULT_MAX_AUDIO_AUDITION_DURATION_SECONDS = 600.0
"""S0013: the fixed maximum audition duration, in seconds, for either a Cue
or an occurrence audition response. Independent of every existing source/
Cue upload or matching-duration guardrail; bounds response *delivery* only.
Exact equality is allowed -- only a duration strictly greater than this
value is rejected."""

DEFAULT_MAX_AUDIO_AUDITION_RESPONSE_BYTES = 64 * 1024 * 1024
"""S0013: the fixed maximum audition response body size, in bytes, for
either a Cue or a rendered occurrence WAV. Independent of every existing
upload-size guardrail; bounds response *delivery* only. Exact equality is
allowed -- only a size strictly greater than this value is rejected."""


class QueryAnalysisUseCase:
    """Read one persisted Analysis and its lifecycle-gated Result."""

    def __init__(
        self,
        repository: AnalysisRepositoryPort,
        result_reader: ResultReferenceReaderPort,
        *,
        asset_storage: AssetStoragePort | None = None,
        audition_renderer: AudioAuditionRendererPort | None = None,
        max_audition_duration_seconds: float = DEFAULT_MAX_AUDIO_AUDITION_DURATION_SECONDS,
        max_audition_response_bytes: int = DEFAULT_MAX_AUDIO_AUDITION_RESPONSE_BYTES,
    ) -> None:
        self._repository = repository
        self._result_reader = result_reader
        self._asset_storage = asset_storage
        self._audition_renderer = audition_renderer
        self._max_audition_duration_seconds = max_audition_duration_seconds
        self._max_audition_response_bytes = max_audition_response_bytes

    def get_status(self, analysis_id: str) -> AnalysisRecord:
        """Return the current authoritative persisted record without mutation."""

        return self._repository.get(analysis_id)

    def get_result(self, analysis_id: str) -> dict:
        """Return a canonical Result object only for a valid SUCCEEDED record.

        Missing identifiers propagate ``AnalysisNotFoundError`` from the
        repository.  QUEUED/RUNNING and FAILED records have distinct named
        outcomes.  A SUCCEEDED record whose opaque reference or stored body
        violates the executor/repository contract is an internal integrity
        failure, never a pending or no-match response.
        """

        record = self.get_status(analysis_id)
        if record.state in (
            AnalysisLifecycleState.QUEUED,
            AnalysisLifecycleState.RUNNING,
        ):
            raise AnalysisResultNotReadyError(analysis_id, record.state)
        if record.state is AnalysisLifecycleState.FAILED:
            raise AnalysisFailedError(analysis_id)

        reference = record.result_reference
        if not reference:
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis has no Result reference"
            )

        try:
            serialized = self._result_reader.read(reference)
        except LookupError as exc:
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis references an unavailable Result"
            ) from exc

        try:
            result = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result is not valid JSON"
            ) from exc

        if not isinstance(result, dict):
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result must be a JSON object"
            )
        schema_version = result.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result has no valid schema version"
            )
        if result.get("analysis_id") != analysis_id:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result does not belong to the requested Analysis"
            )
        if result.get("final_state") != "completed":
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis references a non-completed Result"
            )
        return result

    def get_cue_audition(self, analysis_id: str, cue_id: str) -> AudioAuditionPayload:
        """Return the complete original Cue WAV bytes for one completed
        Analysis Cue, subject to the S0013 audition guardrails.

        Gated by ``get_result`` exactly like ``get_occurrence_audition``:
        QUEUED/RUNNING raises ``AnalysisResultNotReadyError``, FAILED raises
        ``AnalysisFailedError``, and an invalid stored Result raises
        ``AnalysisResultIntegrityError``. A Cue whose own outcome is
        ``no_match``/``failure`` remains auditionable as long as the owning
        Analysis itself completed and the Cue Asset still exists. Never
        applies S0008 ``trim_start_seconds``/``trim_end_seconds`` -- those
        bound the source *search window*, not Cue playback -- and never
        normalizes amplitude.
        """

        self._require_audition_collaborators()
        self.get_result(analysis_id)
        record = self.get_status(analysis_id)
        cue_reference = self._find_cue_reference(record, cue_id)

        cue_bytes = self._asset_storage.read(cue_reference.asset_id)
        duration_seconds = self._canonical_wav_duration_seconds(cue_bytes, record)
        self._enforce_audition_duration_limit(duration_seconds)
        self._enforce_audition_response_byte_limit(len(cue_bytes))
        return AudioAuditionPayload(content=cue_bytes)

    def get_occurrence_audition(
        self, analysis_id: str, cue_id: str, occurrence_index: int
    ) -> AudioAuditionPayload:
        """Render the exact source-time window one accepted Result
        occurrence represents, subject to the S0013 audition guardrails.

        ``start_seconds`` is the occurrence's own raw, absolute
        ``temporal_position`` (S0008 already rebased it to source origin);
        it is never rounded, and never adjusted by
        ``trim_start_seconds``/``trim_end_seconds``. The render duration is
        the complete canonical Cue duration -- an audition-window concept
        entirely independent from ``Occurrence.end``, which is never read,
        populated, or reinterpreted here.
        """

        self._require_audition_collaborators()
        result = self.get_result(analysis_id)
        record = self.get_status(analysis_id)
        cue_reference = self._find_cue_reference(record, cue_id)
        occurrence = self._resolve_occurrence(result, cue_id, occurrence_index)

        cue_bytes = self._asset_storage.read(cue_reference.asset_id)
        duration_seconds = self._canonical_wav_duration_seconds(cue_bytes, record)
        self._enforce_audition_duration_limit(duration_seconds)

        source_bytes = self._asset_storage.read(record.source_asset_id)
        canonical_rate = record.effective_configuration.canonicalization.sample_rate_hz
        try:
            rendered = self._audition_renderer.render_wav_segment(
                source_bytes,
                start_seconds=float(occurrence["temporal_position"]),
                duration_seconds=duration_seconds,
                sample_rate_hz=canonical_rate,
            )
        except Exception as exc:
            raise AudioAuditionRenderingError(
                "audio audition rendering failed"
            ) from exc

        self._enforce_audition_response_byte_limit(len(rendered))
        return AudioAuditionPayload(content=rendered)

    def _require_audition_collaborators(self) -> None:
        if self._asset_storage is None or self._audition_renderer is None:
            raise AuditionCollaboratorsNotConfiguredError(
                "QueryAnalysisUseCase was constructed without audition "
                "collaborators (asset_storage/audition_renderer)"
            )

    @staticmethod
    def _find_cue_reference(
        record: AnalysisRecord, cue_id: str
    ) -> CueAssetReference:
        """Resolve Cue ownership only from the persisted Analysis record --
        never from a client-supplied Asset identifier, and never by
        searching another Analysis for the same ``cue_id``."""

        for cue_reference in record.cues:
            if cue_reference.cue_id == cue_id:
                return cue_reference
        raise AuditionTargetNotFoundError(
            f"Analysis {record.analysis_id!r} has no cue {cue_id!r}"
        )

    @staticmethod
    def _canonical_wav_duration_seconds(
        wav_bytes: bytes, record: AnalysisRecord
    ) -> float:
        """Reproduce the canonical Cue matching duration from the WAV
        header, mirroring the `_resample(...)` target-length rule applied
        at Analysis creation, without importing that private helper."""

        canonical_rate = record.effective_configuration.canonicalization.sample_rate_hz
        try:
            with io.BytesIO(wav_bytes) as buffer, wave.open(buffer, "rb") as reader:
                frame_count = reader.getnframes()
                frame_rate = reader.getframerate()
        except Exception as exc:
            raise AudioAuditionRenderingError(
                "could not parse audition WAV timing metadata"
            ) from exc

        if frame_rate <= 0 or frame_count <= 0:
            raise AudioAuditionRenderingError(
                "audition WAV has non-positive timing metadata"
            )

        target_length = max(1, round(frame_count * canonical_rate / frame_rate))
        return target_length / canonical_rate

    @staticmethod
    def _resolve_occurrence(
        result: dict, cue_id: str, occurrence_index: int
    ) -> dict:
        """Resolve one occurrence from the canonical Result only -- never
        from a client-supplied position/score.

        A negative index, an out-of-range index, a ``no_match``/``failure``
        Cue outcome, or a Cue missing from the Result are all a valid
        completed Result with no such audition target
        (``AuditionTargetNotFoundError``). A structurally invalid Result --
        an unrecognized outcome ``kind``, a non-list ``cues``/``occurrences``,
        or a malformed occurrence entry -- violates the documented Result
        contract instead (``AnalysisResultIntegrityError``).
        """

        if isinstance(occurrence_index, bool) or not isinstance(
            occurrence_index, int
        ):
            raise AuditionTargetNotFoundError(
                "occurrence_index must be a non-negative integer"
            )

        cues = result.get("cues")
        if not isinstance(cues, list):
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result cues must be a list"
            )

        cue_result = None
        for entry in cues:
            if not isinstance(entry, dict):
                raise AnalysisResultIntegrityError(
                    "Stored Analysis Result cue entry must be an object"
                )
            if entry.get("cue_id") == cue_id:
                cue_result = entry
                break
        if cue_result is None:
            raise AuditionTargetNotFoundError(
                f"cue {cue_id!r} is not present in the Analysis Result"
            )

        outcome = cue_result.get("outcome")
        if not isinstance(outcome, dict) or "kind" not in outcome:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result outcome is malformed"
            )
        kind = outcome.get("kind")
        if kind in ("no_match", "failure"):
            raise AuditionTargetNotFoundError(
                f"cue {cue_id!r} has no occurrence audition target"
                f" (outcome kind {kind!r})"
            )
        if kind != "occurrences":
            raise AnalysisResultIntegrityError(
                f"Stored Analysis Result has an unknown outcome kind {kind!r}"
            )

        occurrences = outcome.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result occurrences must be a non-empty list"
            )
        if occurrence_index < 0 or occurrence_index >= len(occurrences):
            raise AuditionTargetNotFoundError(
                f"occurrence index {occurrence_index} is out of range for "
                f"cue {cue_id!r}"
            )

        occurrence = occurrences[occurrence_index]
        temporal_position = (
            occurrence.get("temporal_position")
            if isinstance(occurrence, dict)
            else None
        )
        if isinstance(temporal_position, bool) or not isinstance(
            temporal_position, (int, float)
        ):
            raise AnalysisResultIntegrityError(
                "Stored Result occurrence has an invalid temporal_position"
            )
        return occurrence

    def _enforce_audition_duration_limit(self, duration_seconds: float) -> None:
        if duration_seconds > self._max_audition_duration_seconds:
            raise AudioAuditionResourceLimitError(
                f"audition duration {duration_seconds:.3f}s exceeds the "
                f"configured maximum of {self._max_audition_duration_seconds:.3f}s"
            )

    def _enforce_audition_response_byte_limit(self, size_bytes: int) -> None:
        if size_bytes > self._max_audition_response_bytes:
            raise AudioAuditionResourceLimitError(
                f"audition response body of {size_bytes} bytes exceeds the "
                f"configured maximum of {self._max_audition_response_bytes} bytes"
            )
