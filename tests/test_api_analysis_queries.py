"""Focused contract coverage for the M5-05 Analysis query endpoints.

The implementation phase creates these tests but does not execute them; test
execution belongs to the later ASF validation route.
"""

from __future__ import annotations

import ast
import inspect
import json
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
    QueryAnalysisUseCase,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueNoMatch,
    CueResult,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
    serialize_analysis_result,
)
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
    route_keys = {
        (route.path, tuple(sorted(route.methods or ()))) for route in create_app().routes
    }

    assert ("/api/v1/analyses/{analysis_id}", ("GET",)) in route_keys
    assert ("/api/v1/analyses/{analysis_id}/result", ("GET",)) in route_keys


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


# --- S0003: Cue labels and optional cue-local trim bounds --------------------


@pytest.mark.parametrize("state", list(AnalysisLifecycleState))
def test_public_status_preserves_cue_label_and_trim_fields_across_every_state(state):
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
