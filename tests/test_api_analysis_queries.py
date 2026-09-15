"""Focused contract coverage for the M5-05 Analysis query endpoints.

The implementation phase creates these tests but does not execute them; test
execution belongs to the later ASF validation route.

S0013 adds coverage for the two audio audition capabilities
``QueryAnalysisUseCase.get_cue_audition``/``get_occurrence_audition`` at the
bottom of this module, using fake ``AssetStoragePort``/
``AudioAuditionRendererPort`` collaborators -- no real FFmpeg or filesystem
I/O is required for these Application-level tests.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisNotFoundError,
    AnalysisRecord,
    CueAssetReference,
    LifecycleTimestamps,
)
from audio_cue_locator.application.query_analysis import (
    AnalysisFailedError,
    AnalysisResultIntegrityError,
    AnalysisResultNotReadyError,
    AuditionCollaboratorsNotConfiguredError,
    AuditionTargetNotFoundError,
    AudioAuditionRenderingError,
    AudioAuditionResourceLimitError,
    QueryAnalysisUseCase,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueFailure,
    CueNoMatch,
    CueOccurrences,
    CueResult,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    Occurrence,
    StructuredError,
    serialize_analysis_result,
)
from audio_cue_locator.core.asset import AssetNotFoundError
from audio_cue_locator.interfaces.rest_api import analysis_routes
from audio_cue_locator.interfaces.rest_api.analysis_routes import (
    _get_analysis_result,
    _get_analysis_status,
)
from audio_cue_locator.interfaces.rest_api.app import (
    _build_analysis_use_cases,
    _build_asset_storage,
    create_app,
)
from audio_cue_locator.interfaces.rest_api.schemas import AnalysisStatus, ErrorCode

SOURCE_ASSET_ID = "00000000-0000-4000-8000-000000000001"
CUE_ASSET_ID = "00000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _configuration() -> EffectiveConfigurationSnapshot:
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=48000,
            channels=1,
            sample_format="float32",
            normalization=NormalizationSnapshot(
                enabled=True,
                method="peak",
                target_peak_amplitude=1.0,
            ),
        ),
        matching=MatchingSnapshot(
            method="normalized_cross_correlation_v1",
            acceptance_threshold=0.7,
        ),
        configuration_source_name="tests.test_api_analysis_queries",
    )


def _record(
    state: AnalysisLifecycleState,
    *,
    analysis_id: str = "analysis-1",
    result_reference: str | None = None,
    cues: tuple[CueAssetReference, ...] | None = None,
) -> AnalysisRecord:
    timestamps = LifecycleTimestamps(
        queued_at=NOW,
        running_at=NOW if state is not AnalysisLifecycleState.QUEUED else None,
        succeeded_at=NOW if state is AnalysisLifecycleState.SUCCEEDED else None,
        failed_at=NOW if state is AnalysisLifecycleState.FAILED else None,
    )
    structured_error = None
    if state is AnalysisLifecycleState.FAILED:
        structured_error = StructuredError(
            category=FailureCategory.INTERNAL_FAILURE,
            message="matching failed internally",
        )
    return AnalysisRecord(
        analysis_id=analysis_id,
        state=state,
        source_asset_id=SOURCE_ASSET_ID,
        cues=cues or (CueAssetReference(cue_id="cue-1", asset_id=CUE_ASSET_ID),),
        effective_configuration=_configuration(),
        lifecycle_timestamps=timestamps,
        result_reference=result_reference,
        structured_error=structured_error,
    )


def _serialized_no_match_result(
    analysis_id: str = "analysis-1",
    *,
    schema_version: str | None = None,
) -> str:
    result = AnalysisResult(
        analysis_id=analysis_id,
        method=_configuration().matching.method,
        configuration=_configuration(),
        cues=(CueResult(cue_id="cue-1", outcome=CueNoMatch()),),
    )
    body = json.loads(serialize_analysis_result(result))
    if schema_version is not None:
        body["schema_version"] = schema_version
    return json.dumps(body)


class _Repository:
    def __init__(self, records: list[AnalysisRecord]) -> None:
        self.records = {record.analysis_id: record for record in records}
        self.get_calls: list[str] = []

    def get(self, analysis_id: str) -> AnalysisRecord:
        self.get_calls.append(analysis_id)
        try:
            return self.records[analysis_id]
        except KeyError as exc:
            raise AnalysisNotFoundError(analysis_id) from exc


class _ResultReader:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def read(self, reference: str) -> str:
        return self.values[reference]


def _query(record: AnalysisRecord, values: dict[str, str] | None = None):
    return QueryAnalysisUseCase(_Repository([record]), _ResultReader(values or {}))


@pytest.mark.parametrize("state", list(AnalysisLifecycleState))
def test_status_returns_every_authoritative_persisted_state_without_mutation(state):
    record = _record(
        state,
        result_reference="result:1" if state is AnalysisLifecycleState.SUCCEEDED else None,
    )
    repository = _Repository([record])
    use_case = QueryAnalysisUseCase(repository, _ResultReader({}))

    returned = use_case.get_status(record.analysis_id)

    assert returned is record
    assert repository.get_calls == [record.analysis_id]
    assert returned.state is state


@pytest.mark.parametrize(
    "state", [AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.RUNNING]
)
def test_result_is_not_ready_for_nonterminal_analysis(state):
    use_case = _query(_record(state))

    with pytest.raises(AnalysisResultNotReadyError) as exc_info:
        use_case.get_result("analysis-1")

    assert exc_info.value.state is state


def test_failed_analysis_has_a_distinct_result_outcome():
    use_case = _query(_record(AnalysisLifecycleState.FAILED))

    with pytest.raises(AnalysisFailedError):
        use_case.get_result("analysis-1")


def test_missing_analysis_propagates_repository_not_found():
    use_case = QueryAnalysisUseCase(_Repository([]), _ResultReader({}))

    with pytest.raises(AnalysisNotFoundError):
        use_case.get_result("missing-analysis")


def test_successful_no_match_result_is_returned_as_canonical_data():
    reference = "result:1"
    use_case = _query(
        _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference),
        {reference: _serialized_no_match_result()},
    )

    result = use_case.get_result("analysis-1")

    assert result["final_state"] == "completed"
    assert result["cues"][0]["outcome"]["kind"] == "no_match"
    assert result["cues"][0]["outcome"]["occurrences"] is None


@pytest.mark.parametrize(
    "reference, stored",
    [
        (None, {}),
        ("missing", {}),
        ("invalid-json", {"invalid-json": "{"}),
        ("array", {"array": "[]"}),
        (
            "wrong-analysis",
            {"wrong-analysis": _serialized_no_match_result("analysis-2")},
        ),
    ],
)
def test_succeeded_record_with_invalid_stored_result_is_an_integrity_failure(
    reference, stored
):
    use_case = _query(
        _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference), stored
    )

    with pytest.raises(AnalysisResultIntegrityError):
        use_case.get_result("analysis-1")


def test_transport_status_mapping_reuses_existing_public_analysis_schema():
    use_case = _query(_record(AnalysisLifecycleState.RUNNING))

    public = _get_analysis_status("analysis-1", use_case)

    assert public.status is AnalysisStatus.RUNNING
    assert public.analysis_id == "analysis-1"


def test_result_envelope_preserves_stored_schema_version_independently_from_api():
    reference = "result:versioned"
    use_case = _query(
        _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference),
        {
            reference: _serialized_no_match_result(
                schema_version="analysis_result.v-next"
            )
        },
    )

    envelope = _get_analysis_result("analysis-1", use_case)

    assert envelope.api_version == "v1"
    assert envelope.result_schema_version == "analysis_result.v-next"
    assert envelope.result["schema_version"] == "analysis_result.v-next"


def test_pending_and_failed_application_errors_use_distinct_safe_409_codes():
    app = create_app()

    pending_response = app.exception_handlers[AnalysisResultNotReadyError](
        None,
        AnalysisResultNotReadyError(
            "analysis-1", AnalysisLifecycleState.RUNNING
        ),
    )
    failed_response = app.exception_handlers[AnalysisFailedError](
        None, AnalysisFailedError("analysis-1")
    )

    assert pending_response.status_code == 409
    assert json.loads(pending_response.body)["error_code"] == ErrorCode.RESULT_NOT_READY
    assert failed_response.status_code == 409
    assert json.loads(failed_response.body)["error_code"] == ErrorCode.ANALYSIS_FAILED


def test_router_registers_both_query_paths():
    paths = create_app().openapi()["paths"]

    assert "get" in paths["/api/v1/analyses/{analysis_id}"]
    assert "get" in paths["/api/v1/analyses/{analysis_id}/result"]


def test_openapi_declares_result_success_and_shared_error_responses():
    operation = create_app().openapi()["paths"][
        "/api/v1/analyses/{analysis_id}/result"
    ]["get"]

    assert set(operation["responses"]) >= {"200", "404", "409", "500"}
    error_schema = operation["responses"]["409"]["content"][
        "application/json"
    ]["schema"]
    assert error_schema == {"$ref": "#/components/schemas/ErrorPublic"}


def test_composition_root_shares_exact_result_store_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(tmp_path / "analysis.sqlite3")
    )
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(tmp_path / "assets"))

    create_use_case, query_use_case = _build_analysis_use_cases(
        _build_asset_storage()
    )

    assert create_use_case._executor._result_store is query_use_case._result_reader


def test_route_module_has_no_direct_infrastructure_or_storage_imports():
    tree = ast.parse(inspect.getsource(analysis_routes))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules |= {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        module.startswith("audio_cue_locator.infrastructure")
        for module in imported_modules
    )
    assert "sqlite3" not in imported_modules
    assert "pathlib" not in imported_modules


# --- Cue labels and optional per-Cue source-search windows -------------------


@pytest.mark.parametrize("state", list(AnalysisLifecycleState))
def test_public_status_preserves_cue_label_and_source_window_fields_across_every_state(state):
    cue = CueAssetReference(
        cue_id="cue-1",
        asset_id=CUE_ASSET_ID,
        label="Drop hit",
        trim_start_seconds=0.5,
        trim_end_seconds=1.5,
    )
    record = _record(
        state,
        cues=(cue,),
        result_reference="result:1" if state is AnalysisLifecycleState.SUCCEEDED else None,
    )
    use_case = QueryAnalysisUseCase(_Repository([record]), _ResultReader({}))

    public = _get_analysis_status(record.analysis_id, use_case)

    assert public.cues[0].label == "Drop hit"
    assert public.cues[0].trim_start_seconds == 0.5
    assert public.cues[0].trim_end_seconds == 1.5


def test_public_status_serializes_absent_cue_label_and_trim_fields_as_null():
    record = _record(
        AnalysisLifecycleState.QUEUED,
        cues=(CueAssetReference(cue_id="cue-1", asset_id=CUE_ASSET_ID),),
    )
    use_case = QueryAnalysisUseCase(_Repository([record]), _ResultReader({}))

    public = _get_analysis_status(record.analysis_id, use_case)

    assert public.cues[0].label is None
    assert public.cues[0].trim_start_seconds is None
    assert public.cues[0].trim_end_seconds is None
    dumped = json.loads(public.model_dump_json())
    assert dumped["cues"][0]["label"] is None
    assert dumped["cues"][0]["trim_start_seconds"] is None
    assert dumped["cues"][0]["trim_end_seconds"] is None


# --- S0013: Analysis audio audition API and bounded preview delivery -------

_CANONICAL_RATE = 48000
"""Matches `_configuration().canonicalization.sample_rate_hz` above."""


def _wav_bytes(frame_count: int, frame_rate: int) -> bytes:
    """A real, minimal mono 16-bit PCM WAV with an exact frame count/rate,
    for deterministic canonical-duration assertions."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(struct.pack(f"<{frame_count}h", *([0] * frame_count)))
    return buffer.getvalue()


class _AssetStorage:
    """Fake `core.asset.AssetStoragePort`: an in-memory identifier->bytes
    map raising the real `AssetNotFoundError` for a missing identifier."""

    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = dict(contents)
        self.read_calls: list[str] = []

    def read(self, identifier: str) -> bytes:
        self.read_calls.append(identifier)
        try:
            return self.contents[identifier]
        except KeyError:
            raise AssetNotFoundError(
                f"No Asset bytes exist for identifier {identifier!r}"
            ) from None


class _Renderer:
    """Fake `AudioAuditionRendererPort` recording every call it receives."""

    def __init__(
        self, *, output: bytes | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[dict] = []
        self._output = b"RIFF-rendered-wav" if output is None else output
        self._error = error

    def render_wav_segment(
        self,
        media_bytes: bytes,
        *,
        start_seconds: float,
        duration_seconds: float,
        sample_rate_hz: int,
    ) -> bytes:
        self.calls.append(
            {
                "media_bytes": media_bytes,
                "start_seconds": start_seconds,
                "duration_seconds": duration_seconds,
                "sample_rate_hz": sample_rate_hz,
            }
        )
        if self._error is not None:
            raise self._error
        return self._output


def _occurrences_result(
    analysis_id: str, *, cue_id: str = "cue-1", occurrences: tuple[Occurrence, ...]
) -> str:
    result = AnalysisResult(
        analysis_id=analysis_id,
        method=_configuration().matching.method,
        configuration=_configuration(),
        cues=(
            CueResult(cue_id=cue_id, outcome=CueOccurrences(occurrences=occurrences)),
        ),
    )
    return serialize_analysis_result(result)


def _failure_result(analysis_id: str, *, cue_id: str = "cue-1") -> str:
    result = AnalysisResult(
        analysis_id=analysis_id,
        method=_configuration().matching.method,
        configuration=_configuration(),
        cues=(
            CueResult(
                cue_id=cue_id,
                outcome=CueFailure(
                    category=FailureCategory.MATCHING_FAILURE,
                    message="controlled matching failure",
                ),
            ),
        ),
    )
    return serialize_analysis_result(result)


def _audition_query(
    record: AnalysisRecord,
    *,
    result_reference: str | None = None,
    stored_results: dict[str, str] | None = None,
    asset_storage: _AssetStorage | None = None,
    audition_renderer: _Renderer | None = None,
    **guardrails,
) -> QueryAnalysisUseCase:
    del result_reference  # documents intent only; the record already carries it
    return QueryAnalysisUseCase(
        _Repository([record]),
        _ResultReader(stored_results or {}),
        asset_storage=asset_storage,
        audition_renderer=audition_renderer,
        **guardrails,
    )


def test_cue_audition_returns_exact_original_cue_bytes():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes})

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=storage,
        audition_renderer=_Renderer(),
    )

    payload = use_case.get_cue_audition("analysis-1", "cue-1")

    assert payload.content == cue_bytes
    assert payload.media_type == "audio/wav"
    assert storage.read_calls == [CUE_ASSET_ID]


def test_cue_audition_ignores_label_when_resolving_asset_ownership():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    cue = CueAssetReference(cue_id="cue-1", asset_id=CUE_ASSET_ID, label="Drop hit")
    record = _record(
        AnalysisLifecycleState.SUCCEEDED, result_reference=reference, cues=(cue,)
    )
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes})

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=storage,
        audition_renderer=_Renderer(),
    )

    payload = use_case.get_cue_audition("analysis-1", "cue-1")

    assert payload.content == cue_bytes


def test_cue_audition_ignores_s0008_trim_bounds():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    cue = CueAssetReference(
        cue_id="cue-1",
        asset_id=CUE_ASSET_ID,
        trim_start_seconds=0.1,
        trim_end_seconds=0.5,
    )
    record = _record(
        AnalysisLifecycleState.SUCCEEDED, result_reference=reference, cues=(cue,)
    )
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes})

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=storage,
        audition_renderer=_Renderer(),
    )

    payload = use_case.get_cue_audition("analysis-1", "cue-1")

    assert payload.content == cue_bytes


def test_cue_audition_rejects_a_cue_id_belonging_to_a_different_analysis():
    reference_1 = "result:1"
    reference_2 = "result:2"
    other_cue_asset_id = "00000000-0000-4000-8000-000000000003"
    record_1 = _record(
        AnalysisLifecycleState.SUCCEEDED,
        analysis_id="analysis-1",
        result_reference=reference_1,
        cues=(CueAssetReference(cue_id="cue-1", asset_id=CUE_ASSET_ID),),
    )
    record_2 = _record(
        AnalysisLifecycleState.SUCCEEDED,
        analysis_id="analysis-2",
        result_reference=reference_2,
        cues=(CueAssetReference(cue_id="shared-cue-id", asset_id=other_cue_asset_id),),
    )
    use_case = QueryAnalysisUseCase(
        _Repository([record_1, record_2]),
        _ResultReader(
            {
                reference_1: _serialized_no_match_result("analysis-1"),
                reference_2: _serialized_no_match_result("analysis-2"),
            }
        ),
        asset_storage=_AssetStorage(
            {CUE_ASSET_ID: b"cue-bytes", other_cue_asset_id: b"other-cue-bytes"}
        ),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_cue_audition("analysis-1", "shared-cue-id")


def test_canonical_cue_duration_mirrors_the_resample_target_length_rule():
    frame_count, frame_rate = 5, 7
    expected_target_length = round(frame_count * _CANONICAL_RATE / frame_rate)
    expected_duration = expected_target_length / _CANONICAL_RATE
    cue_bytes = _wav_bytes(frame_count, frame_rate)
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=2.5,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source-bytes"})
    renderer = _Renderer()

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=storage,
        audition_renderer=renderer,
    )

    use_case.get_occurrence_audition("analysis-1", "cue-1", 0)

    assert renderer.calls[0]["duration_seconds"] == pytest.approx(expected_duration)


def test_occurrence_audition_uses_exact_raw_temporal_position():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=12.345678,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source-bytes"})
    renderer = _Renderer()

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=storage,
        audition_renderer=renderer,
    )

    use_case.get_occurrence_audition("analysis-1", "cue-1", 0)

    assert renderer.calls[0]["start_seconds"] == pytest.approx(12.345678)


def test_occurrence_audition_renders_from_the_owning_source_asset():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    source_bytes = b"source-media-bytes"
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: source_bytes})
    renderer = _Renderer(output=b"rendered-occurrence-wav")

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=storage,
        audition_renderer=renderer,
    )

    payload = use_case.get_occurrence_audition("analysis-1", "cue-1", 0)

    assert renderer.calls[0]["media_bytes"] == source_bytes
    assert payload.content == b"rendered-occurrence-wav"
    assert payload.media_type == "audio/wav"


def test_occurrence_index_is_zero_based_and_selects_the_correct_occurrence():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    occurrences = (
        Occurrence(
            cue_id="cue-1",
            temporal_position=1.0,
            score=0.9,
            matching_method=_configuration().matching.method,
        ),
        Occurrence(
            cue_id="cue-1",
            temporal_position=5.0,
            score=0.8,
            matching_method=_configuration().matching.method,
        ),
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    storage = _AssetStorage({CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source"})
    renderer = _Renderer()

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=occurrences)
        },
        asset_storage=storage,
        audition_renderer=renderer,
    )

    use_case.get_occurrence_audition("analysis-1", "cue-1", 1)

    assert renderer.calls[0]["start_seconds"] == pytest.approx(5.0)


def test_occurrence_audition_no_match_cue_is_not_found():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_occurrence_audition_cue_failure_is_not_found():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _failure_result("analysis-1")},
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_cue_audition_unknown_cue_id_is_not_found():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_cue_audition("analysis-1", "unknown-cue")


def test_occurrence_audition_unknown_cue_id_is_not_found():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_occurrence_audition("analysis-1", "unknown-cue", 0)


@pytest.mark.parametrize("index", [-1, 1])
def test_occurrence_audition_out_of_range_index_is_not_found(index):
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AuditionTargetNotFoundError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", index)


@pytest.mark.parametrize(
    "state", [AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.RUNNING]
)
def test_cue_audition_nonterminal_analysis_uses_existing_result_not_ready(state):
    record = _record(state)

    use_case = _audition_query(
        record, asset_storage=_AssetStorage({}), audition_renderer=_Renderer()
    )

    with pytest.raises(AnalysisResultNotReadyError):
        use_case.get_cue_audition("analysis-1", "cue-1")


@pytest.mark.parametrize(
    "state", [AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.RUNNING]
)
def test_occurrence_audition_nonterminal_analysis_uses_existing_result_not_ready(
    state,
):
    record = _record(state)

    use_case = _audition_query(
        record, asset_storage=_AssetStorage({}), audition_renderer=_Renderer()
    )

    with pytest.raises(AnalysisResultNotReadyError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_cue_audition_failed_analysis_uses_existing_analysis_failed():
    record = _record(AnalysisLifecycleState.FAILED)

    use_case = _audition_query(
        record, asset_storage=_AssetStorage({}), audition_renderer=_Renderer()
    )

    with pytest.raises(AnalysisFailedError):
        use_case.get_cue_audition("analysis-1", "cue-1")


def test_occurrence_audition_failed_analysis_uses_existing_analysis_failed():
    record = _record(AnalysisLifecycleState.FAILED)

    use_case = _audition_query(
        record, asset_storage=_AssetStorage({}), audition_renderer=_Renderer()
    )

    with pytest.raises(AnalysisFailedError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_cue_audition_missing_retained_asset_propagates_asset_not_found():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AssetNotFoundError):
        use_case.get_cue_audition("analysis-1", "cue-1")


def test_occurrence_audition_missing_source_asset_propagates_asset_not_found():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=_AssetStorage({CUE_ASSET_ID: cue_bytes}),  # source bytes absent
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AssetNotFoundError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_cue_audition_duration_over_bound_raises_audition_limit():
    cue_bytes = _wav_bytes(2 * _CANONICAL_RATE, _CANONICAL_RATE)  # 2.0s canonical
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({CUE_ASSET_ID: cue_bytes}),
        audition_renderer=_Renderer(),
        max_audition_duration_seconds=1.0,
    )

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_cue_audition("analysis-1", "cue-1")


def test_cue_audition_duration_at_the_exact_bound_is_allowed():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)  # 1.0s canonical
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({CUE_ASSET_ID: cue_bytes}),
        audition_renderer=_Renderer(),
        max_audition_duration_seconds=1.0,
    )

    payload = use_case.get_cue_audition("analysis-1", "cue-1")

    assert payload.content == cue_bytes


def test_occurrence_audition_duration_over_bound_raises_audition_limit_before_render():
    cue_bytes = _wav_bytes(2 * _CANONICAL_RATE, _CANONICAL_RATE)  # 2.0s canonical
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    renderer = _Renderer()

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=_AssetStorage(
            {CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source"}
        ),
        audition_renderer=renderer,
        max_audition_duration_seconds=1.0,
    )

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)

    assert renderer.calls == []  # never asked FFmpeg to render an unbounded segment


def test_cue_audition_response_bytes_over_bound_raises_audition_limit():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({CUE_ASSET_ID: cue_bytes}),
        audition_renderer=_Renderer(),
        max_audition_response_bytes=len(cue_bytes) - 1,
    )

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_cue_audition("analysis-1", "cue-1")


def test_occurrence_audition_rendered_body_over_bound_raises_audition_limit():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    renderer = _Renderer(output=b"x" * 100)

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=_AssetStorage(
            {CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source"}
        ),
        audition_renderer=renderer,
        max_audition_response_bytes=50,
    )

    with pytest.raises(AudioAuditionResourceLimitError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)


def test_cue_audition_malformed_wav_bytes_is_an_audition_processing_error():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)

    use_case = _audition_query(
        record,
        stored_results={reference: _serialized_no_match_result()},
        asset_storage=_AssetStorage({CUE_ASSET_ID: b"not-a-real-wav-file"}),
        audition_renderer=_Renderer(),
    )

    with pytest.raises(AudioAuditionRenderingError):
        use_case.get_cue_audition("analysis-1", "cue-1")


def test_occurrence_audition_renderer_failure_is_an_audition_processing_error():
    cue_bytes = _wav_bytes(_CANONICAL_RATE, _CANONICAL_RATE)
    reference = "result:1"
    occurrence = Occurrence(
        cue_id="cue-1",
        temporal_position=1.0,
        score=0.9,
        matching_method=_configuration().matching.method,
    )
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    renderer = _Renderer(
        error=RuntimeError("ffmpeg exited with stderr: /tmp/secret/input.media")
    )

    use_case = _audition_query(
        record,
        stored_results={
            reference: _occurrences_result("analysis-1", occurrences=(occurrence,))
        },
        asset_storage=_AssetStorage(
            {CUE_ASSET_ID: cue_bytes, SOURCE_ASSET_ID: b"source"}
        ),
        audition_renderer=renderer,
    )

    with pytest.raises(AudioAuditionRenderingError) as exc_info:
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)

    assert "/tmp/secret/input.media" not in str(exc_info.value)


def test_ordinary_status_and_result_queries_are_unaffected_by_missing_audition_collaborators():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    use_case = QueryAnalysisUseCase(
        _Repository([record]),
        _ResultReader({reference: _serialized_no_match_result()}),
    )

    assert use_case.get_status("analysis-1") is record
    result = use_case.get_result("analysis-1")
    assert result["final_state"] == "completed"


def test_audition_without_configured_collaborators_is_an_internal_wiring_error():
    reference = "result:1"
    record = _record(AnalysisLifecycleState.SUCCEEDED, result_reference=reference)
    use_case = QueryAnalysisUseCase(
        _Repository([record]),
        _ResultReader({reference: _serialized_no_match_result()}),
    )

    with pytest.raises(AuditionCollaboratorsNotConfiguredError):
        use_case.get_cue_audition("analysis-1", "cue-1")

    with pytest.raises(AuditionCollaboratorsNotConfiguredError):
        use_case.get_occurrence_audition("analysis-1", "cue-1", 0)
