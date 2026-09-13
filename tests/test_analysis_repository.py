"""M4 repository persistence, lifecycle, ownership, and separation coverage."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    CueAssetReference,
    InvalidAnalysisRecordError,
)
from audio_cue_locator.core.analysis_lifecycle import (
    AnalysisLifecycleState,
    InvalidLifecycleTransitionError,
)
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.core.asset import InvalidAssetIdentifierError
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)


def _asset_id() -> str:
    return str(uuid4())


def _configuration() -> EffectiveConfigurationSnapshot:
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=48_000,
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
        configuration_source_name="test",
    )


def _create_record(
    repository: SQLiteAnalysisRepository,
    *,
    analysis_id: str = "analysis-1",
    source_asset_id: str | None = None,
    cue_asset_id: str | None = None,
):
    return repository.create(
        analysis_id=analysis_id,
        source_asset_id=source_asset_id or _asset_id(),
        cues=(CueAssetReference(cue_id="cue-1", asset_id=cue_asset_id or _asset_id()),),
        effective_configuration=_configuration(),
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_create_get_round_trip_survives_repository_reopen(tmp_path: Path):
    database_path = tmp_path / "analyses.sqlite"
    with SQLiteAnalysisRepository(database_path) as repository:
        created = _create_record(repository)

    with SQLiteAnalysisRepository(database_path) as reopened:
        persisted = reopened.get(created.analysis_id)

    assert persisted == created


def test_create_rejects_duplicate_without_replacing_original(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        original = _create_record(repository)

        with pytest.raises(AnalysisAlreadyExistsError):
            _create_record(repository)

        assert repository.get(original.analysis_id) == original


def test_get_and_transition_report_missing_analysis(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        with pytest.raises(AnalysisNotFoundError):
            repository.get("missing")
        with pytest.raises(AnalysisNotFoundError):
            repository.transition(
                "missing",
                AnalysisLifecycleState.RUNNING,
                at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )


def test_list_by_state_returns_only_records_in_requested_state(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        queued = _create_record(repository, analysis_id="queued")
        running = _create_record(repository, analysis_id="running")
        running = repository.transition(
            running.analysis_id,
            AnalysisLifecycleState.RUNNING,
            at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        assert repository.list_by_state(AnalysisLifecycleState.QUEUED) == (queued,)
        assert repository.list_by_state(AnalysisLifecycleState.RUNNING) == (running,)
        assert repository.list_by_state(AnalysisLifecycleState.SUCCEEDED) == ()
        assert repository.list_by_state(AnalysisLifecycleState.FAILED) == ()


def test_terminal_result_and_error_data_survive_reopen(tmp_path: Path):
    database_path = tmp_path / "analyses.sqlite"
    failure = StructuredError(
        category=FailureCategory.INTERNAL_FAILURE,
        message="controlled failure",
    )
    with SQLiteAnalysisRepository(database_path) as repository:
        succeeded = _create_record(repository, analysis_id="succeeded")
        failed = _create_record(repository, analysis_id="failed")
        for record in (succeeded, failed):
            repository.transition(
                record.analysis_id,
                AnalysisLifecycleState.RUNNING,
                at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        repository.transition(
            succeeded.analysis_id,
            AnalysisLifecycleState.SUCCEEDED,
            at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            result_reference="result:succeeded",
        )
        repository.transition(
            failed.analysis_id,
            AnalysisLifecycleState.FAILED,
            at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            structured_error=failure,
        )

    with SQLiteAnalysisRepository(database_path) as reopened:
        persisted_success = reopened.get(succeeded.analysis_id)
        persisted_failure = reopened.get(failed.analysis_id)

    assert persisted_success.state is AnalysisLifecycleState.SUCCEEDED
    assert persisted_success.result_reference == "result:succeeded"
    assert persisted_success.structured_error is None
    assert persisted_failure.state is AnalysisLifecycleState.FAILED
    assert persisted_failure.result_reference is None
    assert persisted_failure.structured_error == failure


def test_invalid_transition_is_atomic_and_leaves_row_unchanged(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        original = _create_record(repository)

        with pytest.raises(InvalidLifecycleTransitionError):
            repository.transition(
                original.analysis_id,
                AnalysisLifecycleState.SUCCEEDED,
                at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                result_reference="must-not-persist",
            )

        assert repository.get(original.analysis_id) == original


def test_sqlite_schema_and_values_are_metadata_only(tmp_path: Path):
    database_path = tmp_path / "analyses.sqlite"
    with SQLiteAnalysisRepository(database_path) as repository:
        created = _create_record(repository)
        repository.add_owned_asset(created.analysis_id, _asset_id())

    with sqlite3.connect(database_path) as connection:
        columns = connection.execute("PRAGMA table_info(analyses)").fetchall()
        persisted_types = connection.execute(
            "SELECT "
            "typeof(analysis_id), typeof(state), typeof(source_asset_id), "
            "typeof(cues_json), typeof(effective_configuration_json), "
            "typeof(lifecycle_timestamps_json), typeof(owned_asset_ids_json), "
            "typeof(result_reference), typeof(structured_error_json) "
            "FROM analyses WHERE analysis_id = ?",
            (created.analysis_id,),
        ).fetchone()
        persisted_values = connection.execute(
            "SELECT analysis_id, state, source_asset_id, cues_json, "
            "effective_configuration_json, lifecycle_timestamps_json, "
            "owned_asset_ids_json, result_reference, structured_error_json "
            "FROM analyses WHERE analysis_id = ?",
            (created.analysis_id,),
        ).fetchone()

    assert {column[2].upper() for column in columns} == {"TEXT"}
    assert all("blob" not in column[1].lower() for column in columns)
    assert set(persisted_types) <= {"text", "null"}
    assert all(not isinstance(value, bytes) for value in persisted_values)


def test_add_owned_asset_is_idempotent_and_survives_reopen(tmp_path: Path):
    database_path = tmp_path / "analyses.sqlite"
    owned_asset_id = _asset_id()
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_record(repository)
        first = repository.add_owned_asset("analysis-1", owned_asset_id)
        second = repository.add_owned_asset("analysis-1", owned_asset_id)

        assert first.owned_asset_ids == (owned_asset_id,)
        assert second == first

    with SQLiteAnalysisRepository(database_path) as reopened:
        assert reopened.get("analysis-1").owned_asset_ids == (owned_asset_id,)


def test_transition_preserves_owned_asset_identifiers(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        _create_record(repository)
        owned_asset_id = _asset_id()
        repository.add_owned_asset("analysis-1", owned_asset_id)

        transitioned = repository.transition(
            "analysis-1",
            AnalysisLifecycleState.RUNNING,
            at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        assert transitioned.owned_asset_ids == (owned_asset_id,)
        assert repository.get("analysis-1").owned_asset_ids == (owned_asset_id,)


def test_implicit_source_or_cue_ownership_is_not_duplicated(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        created = _create_record(repository)

        source_result = repository.add_owned_asset(
            created.analysis_id, created.source_asset_id
        )
        cue_result = repository.add_owned_asset(
            created.analysis_id, created.cues[0].asset_id
        )

        assert source_result.owned_asset_ids == ()
        assert cue_result.owned_asset_ids == ()


def test_add_owned_asset_rejects_invalid_asset_identity(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        _create_record(repository)

        with pytest.raises(InvalidAssetIdentifierError):
            repository.add_owned_asset("analysis-1", "../derived.wav")


def test_add_owned_asset_rejects_new_link_after_terminal_state(tmp_path: Path):
    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        _create_record(repository)
        existing_owned_asset_id = _asset_id()
        repository.add_owned_asset("analysis-1", existing_owned_asset_id)
        repository.transition(
            "analysis-1",
            AnalysisLifecycleState.RUNNING,
            at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        repository.transition(
            "analysis-1",
            AnalysisLifecycleState.SUCCEEDED,
            at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )

        with pytest.raises(InvalidAnalysisRecordError):
            repository.add_owned_asset("analysis-1", _asset_id())
        assert (
            repository.add_owned_asset("analysis-1", existing_owned_asset_id)
            .owned_asset_ids
            == (existing_owned_asset_id,)
        )


def test_existing_m4_03_row_migrates_with_empty_owned_assets(tmp_path: Path):
    database_path = tmp_path / "legacy.sqlite"
    source_asset_id = _asset_id()
    cue_asset_id = _asset_id()
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE analyses ("
        "analysis_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
        "source_asset_id TEXT NOT NULL, cues_json TEXT NOT NULL, "
        "effective_configuration_json TEXT NOT NULL, "
        "lifecycle_timestamps_json TEXT NOT NULL, result_reference TEXT, "
        "structured_error_json TEXT)"
    )
    connection.execute(
        "INSERT INTO analyses VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-analysis",
            "queued",
            source_asset_id,
            json.dumps([{"cue_id": "cue-1", "asset_id": cue_asset_id}]),
            json.dumps(
                {
                    "canonicalization": {
                        "sample_rate_hz": 48_000,
                        "channels": 1,
                        "sample_format": "float32",
                        "normalization": {
                            "enabled": True,
                            "method": "peak",
                            "target_peak_amplitude": 1.0,
                        },
                    },
                    "matching": {
                        "method": "normalized_cross_correlation_v1",
                        "acceptance_threshold": 0.7,
                    },
                    "configuration_source_name": "legacy-test",
                }
            ),
            json.dumps(
                {
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "running_at": None,
                    "succeeded_at": None,
                    "failed_at": None,
                }
            ),
            None,
            None,
        ),
    )
    connection.commit()
    connection.close()

    with SQLiteAnalysisRepository(database_path) as repository:
        record = repository.get("legacy-analysis")

    assert record.owned_asset_ids == ()
