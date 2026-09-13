"""Focused M4-06 coverage for persisted additional Asset ownership."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from audio_cue_locator.application.ports.analysis_repository import (
    CueAssetReference,
    InvalidAnalysisRecordError,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    MatchingSnapshot,
    NormalizationSnapshot,
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


def _create_record(repository: SQLiteAnalysisRepository):
    return repository.create(
        analysis_id="analysis-1",
        source_asset_id=_asset_id(),
        cues=(CueAssetReference(cue_id="cue-1", asset_id=_asset_id()),),
        effective_configuration=_configuration(),
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


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
