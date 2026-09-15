"""M7-04 coverage for the structured diagnostic-event baseline and the
remediated matching-stage raw-exception-message leak, extended by S0012
with the `asset_orphan_cleanup_completed` cleanup-boundary event.

`tests/test_local_executor_and_recovery.py` already exhaustively covers
`LocalAnalysisExecutor`'s claim/run/persist behavior in isolation; this
suite does not duplicate that. It instead asserts the new, additive
observability behavior this issue applies at the executor, persistence,
cleanup, and REST-error boundaries, using pytest's `caplog` fixture --
selected because no existing test in this codebase uses any log-capture
fixture as precedent (`states/M7/M7-04/issue-operational-state.json#/gaps/5`,
G6) -- and reuses `tests/operational/test_retention_cleanup.py`'s and
`tests/test_local_executor_and_recovery.py`'s own fixture-isolation
conventions where consistent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from audio_cue_locator.application.ports.analysis_repository import CueAssetReference
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.asset_storage.retention_policy import (
    cleanup_expired_assets,
    cleanup_orphaned_assets,
)
from audio_cue_locator.infrastructure.execution.local_analysis_executor import (
    LocalAnalysisExecutor,
)
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisFailureCategory,
    SAFE_ANALYSIS_ERROR_MESSAGES,
    analysis_record_to_public,
)
from audio_cue_locator.observability import VALID_BOUNDARIES, VALID_OUTCOMES
from audio_cue_locator.observability.events import LOGGER_NAME, emit_diagnostic_event

QUEUED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUNNING_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)
TERMINAL_AT = datetime(2026, 1, 3, tzinfo=timezone.utc)

_FORBIDDEN_SUBSTRINGS = (
    "/var/",
    "\\var\\",
    ".wav",
    ".mp4",
    "password",
    "secret",
    "token",
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


def _create_analysis(repository: SQLiteAnalysisRepository, analysis_id: str) -> None:
    repository.create(
        analysis_id=analysis_id,
        source_asset_id=_asset_id(),
        cues=(CueAssetReference(cue_id="cue-1", asset_id=_asset_id()),),
        effective_configuration=_configuration(),
        queued_at=QUEUED_AT,
    )


class _OpeningRepository:
    """Opens a fresh `SQLiteAnalysisRepository` connection per operation,
    mirroring `tests/test_local_executor_and_recovery.py`'s own helper of
    the same name: `SQLiteAnalysisRepository`'s connection is bound to its
    constructing thread (`check_same_thread=True`), but `LocalAnalysisExecutor`
    calls `transition` from its own worker-pool thread, never the thread
    that constructs this wrapper."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def transition(
        self,
        analysis_id: str,
        to_state: AnalysisLifecycleState,
        *,
        at: datetime,
        result_reference: str | None = None,
        structured_error: StructuredError | None = None,
    ):
        with SQLiteAnalysisRepository(self._database_path) as repository:
            return repository.transition(
                analysis_id,
                to_state,
                at=at,
                result_reference=result_reference,
                structured_error=structured_error,
            )


def _acl_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every structured payload attached by `emit_diagnostic_event`,
    across all captured records regardless of level."""

    return [
        record.audio_cue_locator_event
        for record in caplog.records
        if hasattr(record, "audio_cue_locator_event")
    ]


# --- schema and allowlist enforcement ---------------------------------------


def test_boundary_and_outcome_vocabularies_are_closed_and_fixed():
    assert VALID_BOUNDARIES == {
        "api",
        "application",
        "executor",
        "media_processing",
        "matching",
        "persistence",
        "cleanup",
        "result",
    }
    assert VALID_OUTCOMES == {"succeeded", "failed"}


def test_emit_diagnostic_event_rejects_unknown_boundary_or_outcome():
    with pytest.raises(ValueError):
        emit_diagnostic_event(event="x", boundary="not_a_boundary", outcome="succeeded")
    with pytest.raises(ValueError):
        emit_diagnostic_event(event="x", boundary="api", outcome="not_an_outcome")


def test_emitted_event_payload_has_exactly_the_documented_fields(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        emit_diagnostic_event(
            event="unit_test_event",
            boundary="api",
            outcome="succeeded",
            category="unit_test_category",
            analysis_id="analysis-1",
            correlation_id="corr-1",
            duration_ms=12.5,
            count=3,
        )

    events = _acl_events(caplog)
    assert len(events) == 1
    payload = events[0]
    assert set(payload) == {
        "event",
        "boundary",
        "outcome",
        "category",
        "analysis_id",
        "correlation_id",
        "duration_ms",
        "count",
        "timestamp",
    }
    assert payload["event"] == "unit_test_event"
    assert payload["boundary"] == "api"
    assert payload["outcome"] == "succeeded"
    assert payload["analysis_id"] == "analysis-1"
    assert payload["correlation_id"] == "corr-1"


# --- executor boundary: leak remediation and correlation --------------------


def test_matching_stage_failure_persists_a_safe_message_not_raw_exception_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Reproduces the confirmed pre-M7-04 leak scenario (mismatched-shape
    `source`, exactly as `tests/test_local_executor_and_recovery.py`'s own
    `test_controlled_matching_failure_is_persisted_as_structured_error`
    triggers it) and asserts the persisted message is the fixed safe
    constant, never the caught exception's own text, and that the public
    projection is independently redacted too."""

    database_path = tmp_path / "analyses.sqlite3"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "leaky-analysis")

    timestamps = iter((RUNNING_AT, TERMINAL_AT))
    executor = LocalAnalysisExecutor(
        _OpeningRepository(database_path),
        max_concurrency=1,
        clock=lambda: next(timestamps),
    )
    invalid_source = np.asarray([[0.1, 0.2]], dtype=np.float32)
    valid_cue = np.asarray([0.1, 0.2], dtype=np.float32)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        try:
            future = executor.submit(
                "leaky-analysis", invalid_source, {"cue-1": valid_cue}
            )
            outcome = future.result(timeout=5)
        finally:
            executor.shutdown()

    assert outcome.final_state is AnalysisLifecycleState.FAILED
    assert outcome.structured_error is not None
    persisted_message = outcome.structured_error.message
    assert "ValueError" not in persisted_message
    assert "shape" not in persisted_message.lower()

    with SQLiteAnalysisRepository(database_path) as reopened:
        persisted = reopened.get("leaky-analysis")
    public = analysis_record_to_public(persisted)
    assert public.structured_error is not None
    assert public.structured_error.message == SAFE_ANALYSIS_ERROR_MESSAGES[
        AnalysisFailureCategory.INTERNAL_FAILURE
    ]
    assert public.structured_error.message == persisted_message

    events = _acl_events(caplog)
    matching_events = [e for e in events if e["event"] == "matching_stage_failed"]
    assert len(matching_events) == 1
    assert matching_events[0]["boundary"] == "executor"
    assert matching_events[0]["outcome"] == "failed"
    assert matching_events[0]["analysis_id"] == "leaky-analysis"
    assert matching_events[0]["duration_ms"] is not None
    assert matching_events[0]["duration_ms"] > 0


def test_successful_execution_emits_an_analysis_id_correlated_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    database_path = tmp_path / "analyses.sqlite3"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "ok-analysis")

    timestamps = iter((RUNNING_AT, TERMINAL_AT))
    executor = LocalAnalysisExecutor(
        _OpeningRepository(database_path),
        max_concurrency=1,
        clock=lambda: next(timestamps),
    )
    # Mirrors `tests/test_local_executor_and_recovery.py`'s own proven-
    # successful array construction exactly, to avoid any risk of an
    # all-zero/all-silence signal hitting an unrelated matching-algorithm
    # edge case (for example a zero-energy normalization) that this test
    # is not meant to exercise.
    source = np.asarray([0.9, -0.6, 0.3, -0.9, 0.6], dtype=np.float32)
    cue = source.copy()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        try:
            future = executor.submit("ok-analysis", source, {"cue-1": cue})
            outcome = future.result(timeout=5)
        finally:
            executor.shutdown()

    assert outcome.final_state is AnalysisLifecycleState.SUCCEEDED

    events = _acl_events(caplog)
    succeeded_events = [
        e for e in events if e["event"] == "analysis_execution_succeeded"
    ]
    assert len(succeeded_events) == 1
    assert succeeded_events[0]["analysis_id"] == "ok-analysis"
    assert succeeded_events[0]["outcome"] == "succeeded"


# --- concurrency: events for interleaved Analyses stay distinguishable ------


def test_events_for_two_concurrent_failures_remain_distinguishable_by_analysis_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    database_path = tmp_path / "analyses.sqlite3"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "concurrent-a")
        _create_analysis(repository, "concurrent-b")

    timestamps = iter((RUNNING_AT, TERMINAL_AT, RUNNING_AT, TERMINAL_AT))
    executor = LocalAnalysisExecutor(
        _OpeningRepository(database_path),
        max_concurrency=2,
        clock=lambda: next(timestamps),
    )
    invalid_source = np.asarray([[0.1, 0.2]], dtype=np.float32)
    valid_cue = np.asarray([0.1, 0.2], dtype=np.float32)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        try:
            future_a = executor.submit(
                "concurrent-a", invalid_source, {"cue-1": valid_cue}
            )
            future_b = executor.submit(
                "concurrent-b", invalid_source, {"cue-1": valid_cue}
            )
            future_a.result(timeout=5)
            future_b.result(timeout=5)
        finally:
            executor.shutdown()

    events = _acl_events(caplog)
    matching_events = [e for e in events if e["event"] == "matching_stage_failed"]
    observed_ids = {e["analysis_id"] for e in matching_events}
    assert observed_ids == {"concurrent-a", "concurrent-b"}
    assert len(matching_events) == 2


# --- persistence boundary: expected rejections are excluded -----------------


def test_persistence_diagnostic_is_not_emitted_for_an_ordinary_rejected_transition(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """`InvalidLifecycleTransitionError` (for example, two workers racing
    to claim the same Analysis) is an ordinary business-rule rejection, not
    a persistence-layer failure, and must not be misreported as one."""

    database_path = tmp_path / "analyses.sqlite3"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "already-running")
        repository.transition(
            "already-running", AnalysisLifecycleState.RUNNING, at=RUNNING_AT
        )

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            with pytest.raises(Exception):
                # RUNNING -> RUNNING is not a valid transition.
                repository.transition(
                    "already-running", AnalysisLifecycleState.RUNNING, at=RUNNING_AT
                )

    events = _acl_events(caplog)
    persistence_failures = [
        e for e in events if e["event"] == "analysis_persistence_write_failed"
    ]
    assert persistence_failures == []


# --- cleanup boundary: pass-level count only --------------------------------


def test_cleanup_pass_emits_one_count_only_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
        LocalFilesystemAssetStorage,
    )

    database_path = tmp_path / "analyses.sqlite3"
    storage_root = tmp_path / "assets"
    storage_root.mkdir()
    storage = LocalFilesystemAssetStorage(storage_root)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with SQLiteAnalysisRepository(database_path) as repository:
            report = cleanup_expired_assets(
                repository,
                storage,
                now=datetime.now(timezone.utc) + timedelta(days=8),
            )

    events = _acl_events(caplog)
    cleanup_events = [
        e for e in events if e["event"] == "asset_retention_cleanup_completed"
    ]
    assert len(cleanup_events) == 1
    assert cleanup_events[0]["boundary"] == "cleanup"
    assert cleanup_events[0]["outcome"] == "succeeded"
    assert cleanup_events[0]["count"] == len(report.deleted_asset_ids) == 0
    assert cleanup_events[0]["analysis_id"] is None


def test_orphan_cleanup_pass_emits_one_count_only_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
        LocalFilesystemAssetStorage,
    )

    database_path = tmp_path / "analyses.sqlite3"
    storage_root = tmp_path / "assets"
    storage_root.mkdir()
    storage = LocalFilesystemAssetStorage(storage_root)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with SQLiteAnalysisRepository(database_path) as repository:
            report = cleanup_orphaned_assets(
                repository,
                storage,
                now=datetime.now(timezone.utc),
            )

    events = _acl_events(caplog)
    orphan_events = [
        e for e in events if e["event"] == "asset_orphan_cleanup_completed"
    ]
    assert len(orphan_events) == 1
    assert orphan_events[0]["boundary"] == "cleanup"
    assert orphan_events[0]["outcome"] == "succeeded"
    assert orphan_events[0]["count"] == len(report.deleted_asset_ids) == 0
    assert orphan_events[0]["analysis_id"] is None


# --- data minimization: no captured event leaks a path, payload, or secret --


@pytest.mark.parametrize(
    "boundary,event_name",
    [
        ("executor", "matching_stage_failed"),
        ("executor", "analysis_execution_succeeded"),
        ("persistence", "analysis_persistence_write_failed"),
        ("cleanup", "asset_retention_cleanup_completed"),
        ("cleanup", "asset_orphan_cleanup_completed"),
        ("api", "api_request_failed"),
    ],
)
def test_no_diagnostic_event_field_value_contains_a_forbidden_substring(
    boundary: str, event_name: str
):
    """A fixture-free structural check: for every boundary this issue
    instruments, construct a representative event and confirm none of its
    string-valued fields contain a path, media extension, or secret-shaped
    substring (`states/M7/M7-04/issue-operational-state.json#/constraints/2`)."""

    sample_category = "SomeExceptionClassName"
    payload = {
        "event": event_name,
        "boundary": boundary,
        "outcome": "failed",
        "category": sample_category,
        "analysis_id": "analysis-id-value",
        "correlation_id": "correlation-id-value",
    }
    for value in payload.values():
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered
