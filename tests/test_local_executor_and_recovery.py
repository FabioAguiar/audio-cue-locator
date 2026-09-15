"""Deterministic M4 local-executor, failure, and restart-recovery coverage."""

import json
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from audio_cue_locator.application.ports.analysis_repository import CueAssetReference
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.acoustic_matching import MatchOutcome, MatchResult
from audio_cue_locator.infrastructure.execution.local_analysis_executor import (
    InMemoryResultReferenceStore,
    LocalAnalysisExecutor,
)
from audio_cue_locator.infrastructure.execution.restart_recovery import (
    INTERRUPTED_ERROR_MESSAGE,
    run_startup_recovery,
)

QUEUED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUNNING_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)
TERMINAL_AT = datetime(2026, 1, 3, tzinfo=timezone.utc)


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


def _create_analysis(
    repository: SQLiteAnalysisRepository,
    analysis_id: str,
    *,
    cues: tuple[CueAssetReference, ...] | None = None,
) -> None:
    repository.create(
        analysis_id=analysis_id,
        source_asset_id=_asset_id(),
        cues=cues or (CueAssetReference(cue_id="cue-1", asset_id=_asset_id()),),
        effective_configuration=_configuration(),
        queued_at=QUEUED_AT,
    )


class _OpeningRepository:
    """Use a fresh real SQLite adapter per worker-thread operation."""

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


class _BlockingOpeningRepository(_OpeningRepository):
    """Block RUNNING claims to observe the executor's configured bound."""

    def __init__(self, database_path: Path, expected_parallel_claims: int) -> None:
        super().__init__(database_path)
        self.release_claims = threading.Event()
        self.parallel_claims_reached = threading.Event()
        self.worker_thread_ids: set[int] = set()
        self.claims_entered = 0
        self.max_parallel_claims = 0
        self._active_claims = 0
        self._expected_parallel_claims = expected_parallel_claims
        self._lock = threading.Lock()

    def transition(
        self,
        analysis_id: str,
        to_state: AnalysisLifecycleState,
        *,
        at: datetime,
        result_reference: str | None = None,
        structured_error: StructuredError | None = None,
    ):
        if to_state is AnalysisLifecycleState.RUNNING:
            with self._lock:
                self.worker_thread_ids.add(threading.get_ident())
                self.claims_entered += 1
                self._active_claims += 1
                self.max_parallel_claims = max(
                    self.max_parallel_claims, self._active_claims
                )
                if self._active_claims == self._expected_parallel_claims:
                    self.parallel_claims_reached.set()
            try:
                if not self.release_claims.wait(timeout=5):
                    raise TimeoutError("test did not release blocked Analysis claims")
            finally:
                with self._lock:
                    self._active_claims -= 1
        return super().transition(
            analysis_id,
            to_state,
            at=at,
            result_reference=result_reference,
            structured_error=structured_error,
        )


def test_submit_runs_outside_caller_and_enforces_configured_concurrency(
    tmp_path: Path,
):
    database_path = tmp_path / "analyses.sqlite"
    analysis_ids = ("analysis-1", "analysis-2", "analysis-3")
    with SQLiteAnalysisRepository(database_path) as repository:
        for analysis_id in analysis_ids:
            _create_analysis(repository, analysis_id)

    repository = _BlockingOpeningRepository(database_path, expected_parallel_claims=2)
    executor = LocalAnalysisExecutor(repository, max_concurrency=2)
    caller_thread_id = threading.get_ident()
    source = np.asarray([0.9, -0.6, 0.3, -0.9, 0.6], dtype=np.float32)
    cues = {"cue-1": source.copy()}

    try:
        futures = tuple(
            executor.submit(analysis_id, source, cues) for analysis_id in analysis_ids
        )

        assert all(isinstance(future, Future) for future in futures)
        assert repository.parallel_claims_reached.wait(timeout=5)
        assert repository.claims_entered == 2
        assert all(not future.done() for future in futures)
        assert caller_thread_id not in repository.worker_thread_ids

        repository.release_claims.set()
        outcomes = tuple(future.result(timeout=5) for future in futures)
    finally:
        repository.release_claims.set()
        executor.shutdown()

    assert repository.claims_entered == 3
    assert repository.max_parallel_claims == 2
    assert {outcome.analysis_id for outcome in outcomes} == set(analysis_ids)
    assert all(
        outcome.final_state is AnalysisLifecycleState.SUCCEEDED
        for outcome in outcomes
    )


def test_executor_uses_persisted_per_cue_source_windows_and_publishes_absolute_time(
    tmp_path: Path, monkeypatch
):
    import audio_cue_locator.application.multi_cue_orchestration as orchestration

    database_path = tmp_path / "analyses.sqlite"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(
            repository,
            "windowed-analysis",
            cues=(
                CueAssetReference(cue_id="cue-full", asset_id=_asset_id()),
                CueAssetReference(
                    cue_id="cue-windowed",
                    asset_id=_asset_id(),
                    trim_start_seconds=4 / 48_000,
                    trim_end_seconds=10 / 48_000,
                ),
            ),
        )

    source = np.arange(12, dtype=np.float32)
    cues = {
        "cue-full": np.asarray([0.1, 0.2], dtype=np.float32),
        "cue-windowed": np.asarray([0.3, 0.4], dtype=np.float32),
    }
    seen_sources: list[np.ndarray] = []

    def _found(source_window, cue, configuration):
        seen_sources.append(source_window)
        return MatchResult(
            outcome=MatchOutcome.FOUND,
            configuration=configuration,
            timestamp_seconds=2 / 48_000,
            score=0.9,
        )

    monkeypatch.setattr(orchestration, "match_cue", _found)
    result_store = InMemoryResultReferenceStore()
    executor = LocalAnalysisExecutor(
        _OpeningRepository(database_path),
        max_concurrency=1,
        result_store=result_store,
    )
    try:
        outcome = executor.submit("windowed-analysis", source, cues).result(timeout=5)
    finally:
        executor.shutdown()

    assert seen_sources[0] is source
    assert np.array_equal(seen_sources[1], source[4:10])
    serialized = json.loads(result_store.read(outcome.result_reference))
    positions = {
        cue["cue_id"]: cue["outcome"]["occurrences"][0]["temporal_position"]
        for cue in serialized["cues"]
    }
    assert positions["cue-full"] == 2 / 48_000
    assert positions["cue-windowed"] == 6 / 48_000


def test_controlled_matching_failure_is_persisted_as_structured_error(
    tmp_path: Path,
):
    database_path = tmp_path / "analyses.sqlite"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "failed-analysis")

    timestamps = iter((RUNNING_AT, TERMINAL_AT))
    executor = LocalAnalysisExecutor(
        _OpeningRepository(database_path),
        max_concurrency=1,
        clock=lambda: next(timestamps),
    )
    invalid_source = np.asarray([[0.1, 0.2]], dtype=np.float32)
    valid_cue = np.asarray([0.1, 0.2], dtype=np.float32)

    try:
        future = executor.submit(
            "failed-analysis", invalid_source, {"cue-1": valid_cue}
        )
        outcome = future.result(timeout=5)
    finally:
        executor.shutdown()

    with SQLiteAnalysisRepository(database_path) as reopened:
        persisted = reopened.get("failed-analysis")

    assert outcome.final_state is AnalysisLifecycleState.FAILED
    assert outcome.result_reference is None
    assert outcome.structured_error is not None
    assert outcome.structured_error.category is FailureCategory.INTERNAL_FAILURE
    assert outcome.structured_error.message
    assert persisted.state is AnalysisLifecycleState.FAILED
    assert persisted.result_reference is None
    assert persisted.structured_error == outcome.structured_error


def test_startup_recovery_resolves_reopened_running_analysis_without_retry(
    tmp_path: Path,
):
    database_path = tmp_path / "analyses.sqlite"
    with SQLiteAnalysisRepository(database_path) as repository:
        _create_analysis(repository, "interrupted")
        _create_analysis(repository, "still-queued")
        repository.transition(
            "interrupted", AnalysisLifecycleState.RUNNING, at=RUNNING_AT
        )

    with SQLiteAnalysisRepository(database_path) as reopened:
        recovered = run_startup_recovery(reopened, clock=lambda: TERMINAL_AT)
        interrupted = reopened.get("interrupted")
        queued = reopened.get("still-queued")
        second_pass = run_startup_recovery(reopened, clock=lambda: TERMINAL_AT)

    assert tuple(item.analysis_id for item in recovered) == ("interrupted",)
    assert interrupted.state is AnalysisLifecycleState.FAILED
    assert interrupted.result_reference is None
    assert interrupted.structured_error == StructuredError(
        category=FailureCategory.INTERNAL_FAILURE,
        message=INTERRUPTED_ERROR_MESSAGE,
    )
    assert queued.state is AnalysisLifecycleState.QUEUED
    assert second_pass == ()
