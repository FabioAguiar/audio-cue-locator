"""Integrated M7-03 coverage for the applied composition-root wiring of
M4-05's `run_startup_recovery` and M4-06's `cleanup_expired_assets`, extended
by S0012 with the third `cleanup_orphaned_assets` startup phase.

`tests/test_local_executor_and_recovery.py` and `tests/test_artifact_retention.py`
already exhaustively cover all three mechanisms' own eligibility, isolation,
and recovery rules in isolation, against hand-built or directly-constructed
repository/storage pairs. This suite does not duplicate that coverage. It
instead exercises the actual wiring `docs/retention-and-cleanup.md` and
`interfaces/rest_api/app.py`'s `_build_analysis_use_cases` describe --
`run_startup_recovery`, then `cleanup_expired_assets`, then (S0012)
`cleanup_orphaned_assets`, called once at composition-root build time,
strictly before `LocalAnalysisExecutor` is constructed -- confirming the
wiring itself, not the underlying functions, is correct: recovery-before-
claim ordering, applied restart recovery, applied cross-Analysis-isolated
cleanup across success/failure/timeout terminal categories, applied orphan
cleanup across old/recent/referenced physical Assets, and safety across a
repeated ("restarted twice") composition-root build.

Every test isolates `AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH` and
`AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT` under `tmp_path`, mirroring every
other existing caller of `_build_analysis_use_cases`/`_build_create_analysis_use_case`
(`tests/test_api_analysis_creation.py`, `tests/test_api_analysis_queries.py`,
`tests/test_api_v1_integration.py`, `tests/operational/test_guardrails.py`);
`docs/retention-and-cleanup.md`'s "Operational caution" section records why
this isolation matters now that composition-root construction is no longer
inert.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

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
from audio_cue_locator.core.asset import AssetNotFoundError, AssetType
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.infrastructure.asset_storage.retention_policy import (
    CleanupReport,
    OrphanCleanupReport,
)
from audio_cue_locator.infrastructure.execution.restart_recovery import (
    INTERRUPTED_ERROR_MESSAGE,
)
from audio_cue_locator.observability.events import LOGGER_NAME

app_module = importlib.import_module("audio_cue_locator.interfaces.rest_api.app")


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


def _asset_id() -> str:
    return str(uuid4())


def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point the composition root at fresh, isolated `tmp_path` locations,
    matching every other existing caller of `_build_analysis_use_cases`.
    `docs/retention-and-cleanup.md`'s "Operational caution" section explains
    why this isolation is required now that composition-root construction is
    no longer inert."""

    db_path = tmp_path / "analyses.sqlite3"
    storage_root = tmp_path / "assets"
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(db_path))
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(storage_root))
    return db_path, storage_root


def _shutdown(create_use_case) -> None:
    create_use_case._executor.shutdown()


# --- ordering: recovery must run before the executor can claim anything -----


def test_recovery_runs_before_executor_is_constructed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Demonstrates, at the code level, the ordering
    `docs/restart-and-recovery-policy.md` requires: `run_startup_recovery`
    is called strictly before `LocalAnalysisExecutor` is constructed, so no
    code path in this process can claim a `QUEUED` Analysis before recovery
    has already run exactly once."""

    _isolate_env(monkeypatch, tmp_path)
    order: list[str] = []
    real_run_startup_recovery = app_module.run_startup_recovery
    real_executor_cls = app_module.LocalAnalysisExecutor

    def _recording_recovery(repository, **kwargs):
        order.append("recovery")
        return real_run_startup_recovery(repository, **kwargs)

    class _RecordingExecutor(real_executor_cls):
        def __init__(self, *args, **kwargs):
            order.append("executor_constructed")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_module, "run_startup_recovery", _recording_recovery)
    monkeypatch.setattr(app_module, "LocalAnalysisExecutor", _RecordingExecutor)

    storage = app_module._build_asset_storage()
    create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        assert order == ["recovery", "executor_constructed"]
    finally:
        _shutdown(create_use_case)


# --- applied restart recovery, through the wired composition root -----------


def test_composition_root_resolves_interrupted_analysis_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db_path, _storage_root = _isolate_env(monkeypatch, tmp_path)
    running_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    with SQLiteAnalysisRepository(db_path) as repository:
        repository.create(
            analysis_id="interrupted",
            source_asset_id=_asset_id(),
            cues=(CueAssetReference(cue_id="cue-1", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=running_at - timedelta(hours=1),
        )
        repository.create(
            analysis_id="still-queued",
            source_asset_id=_asset_id(),
            cues=(CueAssetReference(cue_id="cue-1", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=running_at,
        )
        repository.transition(
            "interrupted", AnalysisLifecycleState.RUNNING, at=running_at
        )

    storage = app_module._build_asset_storage()
    create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with SQLiteAnalysisRepository(db_path) as reopened:
            interrupted = reopened.get("interrupted")
            queued = reopened.get("still-queued")
    finally:
        _shutdown(create_use_case)

    assert interrupted.state is AnalysisLifecycleState.FAILED
    assert interrupted.result_reference is None
    assert interrupted.structured_error == StructuredError(
        category=FailureCategory.INTERNAL_FAILURE,
        message=INTERRUPTED_ERROR_MESSAGE,
    )
    assert queued.state is AnalysisLifecycleState.QUEUED


# --- applied cleanup across success/failure/timeout terminal categories -----


def test_composition_root_cleans_up_across_terminal_categories_and_isolates_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db_path, storage_root = _isolate_env(monkeypatch, tmp_path)
    storage = LocalFilesystemAssetStorage(storage_root)

    now = datetime.now(timezone.utc)
    eligible_terminal_at = now - timedelta(days=8)
    recent_terminal_at = now - timedelta(hours=1)

    succeeded_source = storage.ingest(
        b"succeeded source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="succeeded.wav",
        detected_media_type="audio/wav",
    )
    timed_out_source = storage.ingest(
        b"timed out source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="timed-out.wav",
        detected_media_type="audio/wav",
    )
    recent_source = storage.ingest(
        b"recent source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="recent.wav",
        detected_media_type="audio/wav",
    )
    shared_cue = storage.ingest(
        b"shared cue",
        logical_type=AssetType.CUE,
        informative_name="shared.wav",
        detected_media_type="audio/wav",
    )

    with SQLiteAnalysisRepository(db_path) as repository:
        # Success scenario: terminal SUCCEEDED, past the retention window,
        # uniquely owning its source but sharing its cue with a still-active
        # Analysis (cross-Analysis isolation, acceptance criterion "Verify
        # cleanup cannot delete an artifact owned by another active
        # Analysis").
        repository.create(
            analysis_id="succeeded-eligible",
            source_asset_id=succeeded_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=shared_cue.identifier),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "succeeded-eligible",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )
        repository.transition(
            "succeeded-eligible",
            AnalysisLifecycleState.SUCCEEDED,
            at=eligible_terminal_at,
            result_reference="result:succeeded-eligible",
        )

        # Failure/timeout scenario: terminal FAILED with a resource-limit
        # (timeout-representative) structured error, past the retention
        # window, uniquely owning its own source.
        repository.create(
            analysis_id="timed-out-eligible",
            source_asset_id=timed_out_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "timed-out-eligible",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )
        repository.transition(
            "timed-out-eligible",
            AnalysisLifecycleState.FAILED,
            at=eligible_terminal_at,
            structured_error=StructuredError(
                category=FailureCategory.RESOURCE_LIMIT,
                message="processing timed out",
            ),
        )

        # Not-yet-eligible scenario: terminal SUCCEEDED too recently.
        repository.create(
            analysis_id="recent-not-eligible",
            source_asset_id=recent_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=recent_terminal_at - timedelta(minutes=5),
        )
        repository.transition(
            "recent-not-eligible",
            AnalysisLifecycleState.RUNNING,
            at=recent_terminal_at - timedelta(minutes=2),
        )
        repository.transition(
            "recent-not-eligible",
            AnalysisLifecycleState.SUCCEEDED,
            at=recent_terminal_at,
            result_reference="result:recent-not-eligible",
        )

        # Active scenario: still RUNNING, sharing the eligible Analysis's
        # cue Asset -- this is the artifact cleanup must not delete.
        repository.create(
            analysis_id="still-active",
            source_asset_id=_asset_id(),
            cues=(CueAssetReference(cue_id="cue", asset_id=shared_cue.identifier),),
            effective_configuration=_configuration(),
            queued_at=now - timedelta(minutes=10),
        )
        repository.transition(
            "still-active", AnalysisLifecycleState.RUNNING, at=now - timedelta(minutes=5)
        )

    create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with pytest.raises(AssetNotFoundError):
            storage.read(succeeded_source.identifier)
        with pytest.raises(AssetNotFoundError):
            storage.read(timed_out_source.identifier)
        assert storage.read(recent_source.identifier) == b"recent source"
        assert storage.read(shared_cue.identifier) == b"shared cue"

        with SQLiteAnalysisRepository(db_path) as reopened:
            assert (
                reopened.get("succeeded-eligible").state
                is AnalysisLifecycleState.SUCCEEDED
            )
            assert reopened.get("still-active").state is AnalysisLifecycleState.RUNNING
    finally:
        _shutdown(create_use_case)


# --- repeated ("restarted twice") composition-root construction is safe -----


def test_repeated_composition_root_construction_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db_path, storage_root = _isolate_env(monkeypatch, tmp_path)
    storage = LocalFilesystemAssetStorage(storage_root)
    eligible_terminal_at = datetime.now(timezone.utc) - timedelta(days=8)

    eligible_source = storage.ingest(
        b"eligible source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="eligible.wav",
        detected_media_type="audio/wav",
    )

    with SQLiteAnalysisRepository(db_path) as repository:
        repository.create(
            analysis_id="eligible",
            source_asset_id=eligible_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.SUCCEEDED,
            at=eligible_terminal_at,
            result_reference="result:eligible",
        )
        repository.create(
            analysis_id="was-interrupted",
            source_asset_id=_asset_id(),
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "was-interrupted",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )

    # First "process start": recovery resolves the interrupted Analysis and
    # cleanup deletes the eligible source's bytes.
    first_create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with pytest.raises(AssetNotFoundError):
            storage.read(eligible_source.identifier)
    finally:
        _shutdown(first_create_use_case)

    # Second "process start" against the same database/storage: neither
    # call raises, recovery finds nothing left RUNNING, and cleanup reports
    # the already-deleted bytes as missing rather than erroring or
    # double-deleting (`retention_policy.py`'s missing-vs-deleted
    # distinction, already unit-tested by `tests/test_artifact_retention.py`,
    # now exercised through the actual wiring).
    second_create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with SQLiteAnalysisRepository(db_path) as reopened:
            assert reopened.get("was-interrupted").state is AnalysisLifecycleState.FAILED
            assert reopened.get("was-interrupted").structured_error == StructuredError(
                category=FailureCategory.INTERNAL_FAILURE,
                message=INTERRUPTED_ERROR_MESSAGE,
            )
    finally:
        _shutdown(second_create_use_case)


# --- the actual production entrypoint, end-to-end ---------------------------


def test_recovery_and_both_cleanup_phases_run_before_executor_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """S0012 extends the fixed startup order with a third maintenance phase:
    recovery, then owned-retention cleanup, then orphan cleanup, all strictly
    before `LocalAnalysisExecutor` is constructed."""

    _isolate_env(monkeypatch, tmp_path)
    order: list[str] = []
    real_run_startup_recovery = app_module.run_startup_recovery
    real_cleanup_expired_assets = app_module.cleanup_expired_assets
    real_cleanup_orphaned_assets = app_module.cleanup_orphaned_assets
    real_emit_storage_usage_observed = app_module._emit_storage_usage_observed
    real_executor_cls = app_module.LocalAnalysisExecutor

    def _recording_recovery(repository, **kwargs):
        order.append("recovery")
        return real_run_startup_recovery(repository, **kwargs)

    def _recording_owned_cleanup(repository, storage, **kwargs):
        order.append("owned_retention_cleanup")
        return real_cleanup_expired_assets(repository, storage, **kwargs)

    def _recording_orphan_cleanup(repository, storage, **kwargs):
        order.append("orphan_cleanup")
        return real_cleanup_orphaned_assets(repository, storage, **kwargs)

    def _recording_storage_usage_observed(storage):
        order.append("storage_usage_observed")
        return real_emit_storage_usage_observed(storage)

    class _RecordingExecutor(real_executor_cls):
        def __init__(self, *args, **kwargs):
            order.append("executor_constructed")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_module, "run_startup_recovery", _recording_recovery)
    monkeypatch.setattr(app_module, "cleanup_expired_assets", _recording_owned_cleanup)
    monkeypatch.setattr(app_module, "cleanup_orphaned_assets", _recording_orphan_cleanup)
    monkeypatch.setattr(
        app_module, "_emit_storage_usage_observed", _recording_storage_usage_observed
    )
    monkeypatch.setattr(app_module, "LocalAnalysisExecutor", _RecordingExecutor)

    storage = app_module._build_asset_storage()
    create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        assert order == [
            "recovery",
            "owned_retention_cleanup",
            "orphan_cleanup",
            "storage_usage_observed",
            "executor_constructed",
        ]
    finally:
        _shutdown(create_use_case)


# --- applied orphan cleanup, through the wired composition root -------------


def test_composition_root_deletes_old_unreferenced_and_preserves_recent_and_referenced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db_path, storage_root = _isolate_env(monkeypatch, tmp_path)
    storage = LocalFilesystemAssetStorage(storage_root)

    old_orphan = storage.ingest(
        b"old unreferenced upload",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="old-orphan.wav",
        detected_media_type="audio/wav",
    )
    recent_orphan = storage.ingest(
        b"recent unreferenced upload",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="recent-orphan.wav",
        detected_media_type="audio/wav",
    )
    referenced_source = storage.ingest(
        b"referenced source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="referenced.wav",
        detected_media_type="audio/wav",
    )

    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(
        storage_root / old_orphan.identifier, (old_timestamp, old_timestamp)
    )

    with SQLiteAnalysisRepository(db_path) as repository:
        repository.create(
            analysis_id="still-queued",
            source_asset_id=referenced_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=datetime.now(timezone.utc),
        )

    create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with pytest.raises(AssetNotFoundError):
            storage.read(old_orphan.identifier)
        assert storage.read(recent_orphan.identifier) == b"recent unreferenced upload"
        assert storage.read(referenced_source.identifier) == b"referenced source"
    finally:
        _shutdown(create_use_case)


def test_repeated_startup_orphan_cleanup_is_retry_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    db_path, storage_root = _isolate_env(monkeypatch, tmp_path)
    storage = LocalFilesystemAssetStorage(storage_root)

    old_orphan = storage.ingest(
        b"old unreferenced upload",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="old-orphan.wav",
        detected_media_type="audio/wav",
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(
        storage_root / old_orphan.identifier, (old_timestamp, old_timestamp)
    )

    first_create_use_case, _ = app_module._build_analysis_use_cases(storage)
    try:
        with pytest.raises(AssetNotFoundError):
            storage.read(old_orphan.identifier)
    finally:
        _shutdown(first_create_use_case)

    # Second "process start": the already-deleted upload is simply absent
    # from the inventory, so cleanup has nothing left to reconsider; neither
    # call raises.
    second_create_use_case, _ = app_module._build_analysis_use_cases(storage)
    _shutdown(second_create_use_case)


def test_create_app_applies_recovery_and_cleanup_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Satisfies acceptance criterion 6 ('Integrated cleanup tests pass in
    the M7 runtime') directly against `create_app()`, not only against the
    lower-level `_build_analysis_use_cases` seam the other tests in this
    module use."""

    db_path, storage_root = _isolate_env(monkeypatch, tmp_path)
    storage = LocalFilesystemAssetStorage(storage_root)
    eligible_terminal_at = datetime.now(timezone.utc) - timedelta(days=8)

    eligible_source = storage.ingest(
        b"eligible source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="eligible.wav",
        detected_media_type="audio/wav",
    )

    with SQLiteAnalysisRepository(db_path) as repository:
        repository.create(
            analysis_id="eligible",
            source_asset_id=eligible_source.identifier,
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.SUCCEEDED,
            at=eligible_terminal_at,
            result_reference="result:eligible",
        )
        repository.create(
            analysis_id="interrupted",
            source_asset_id=_asset_id(),
            cues=(CueAssetReference(cue_id="cue", asset_id=_asset_id()),),
            effective_configuration=_configuration(),
            queued_at=eligible_terminal_at - timedelta(hours=1),
        )
        repository.transition(
            "interrupted",
            AnalysisLifecycleState.RUNNING,
            at=eligible_terminal_at - timedelta(minutes=30),
        )

    app = app_module.create_app()
    assert app is not None  # construction itself applies recovery and cleanup

    with pytest.raises(AssetNotFoundError):
        storage.read(eligible_source.identifier)
    with SQLiteAnalysisRepository(db_path) as reopened:
        assert reopened.get("interrupted").state is AnalysisLifecycleState.FAILED
        assert reopened.get("interrupted").structured_error == StructuredError(
            category=FailureCategory.INTERNAL_FAILURE,
            message=INTERRUPTED_ERROR_MESSAGE,
        )


# --- S0011: _AssetMaintenanceCoordinator concurrency -------------------------


def test_reserved_id_appears_in_maintenance_snapshot():
    coordinator = app_module._AssetMaintenanceCoordinator()
    asset_id = _asset_id()

    with coordinator.reserve((asset_id,)):
        with coordinator.maintenance_snapshot() as protected:
            assert asset_id in protected


def test_maintenance_snapshot_is_empty_without_an_active_reservation():
    coordinator = app_module._AssetMaintenanceCoordinator()

    with coordinator.maintenance_snapshot() as protected:
        assert protected == frozenset()


def test_shared_reservation_reference_counting_keeps_protection_until_both_release():
    """S0011 4.3: if two requests reserve the same Asset simultaneously,
    protection remains active until *both* reservations release -- a plain
    set that unprotects on the first release would be insufficient."""

    coordinator = app_module._AssetMaintenanceCoordinator()
    asset_id = _asset_id()

    first = coordinator.reserve((asset_id,))
    second = coordinator.reserve((asset_id,))
    first.__enter__()
    second.__enter__()

    first.__exit__(None, None, None)
    with coordinator.maintenance_snapshot() as protected:
        assert asset_id in protected  # second reservation still held

    second.__exit__(None, None, None)
    with coordinator.maintenance_snapshot() as protected:
        assert asset_id not in protected


def test_maintenance_boundary_blocks_a_new_reservation_until_cycle_release():
    """S0011 4.7/4.11: a `reserve()` call registered while a maintenance
    cycle holds `maintenance_snapshot()` must wait until that snapshot's
    `with` block exits, and succeeds immediately afterward."""

    coordinator = app_module._AssetMaintenanceCoordinator()
    asset_id = _asset_id()
    entered_snapshot = threading.Event()
    release_snapshot = threading.Event()
    reservation_acquired = threading.Event()

    def _hold_snapshot():
        with coordinator.maintenance_snapshot():
            entered_snapshot.set()
            release_snapshot.wait(timeout=5.0)

    snapshot_thread = threading.Thread(target=_hold_snapshot)
    snapshot_thread.start()
    assert entered_snapshot.wait(timeout=5.0)

    def _reserve():
        with coordinator.reserve((asset_id,)):
            reservation_acquired.set()

    reserve_thread = threading.Thread(target=_reserve)
    reserve_thread.start()

    # The reservation must not have been able to proceed while the
    # maintenance boundary is still held.
    assert not reservation_acquired.wait(timeout=0.2)

    release_snapshot.set()
    snapshot_thread.join(timeout=5.0)

    # ... and must succeed promptly once the boundary is released.
    assert reservation_acquired.wait(timeout=5.0)
    reserve_thread.join(timeout=5.0)


# --- S0011: _PeriodicAssetMaintenance lifecycle ------------------------------


def _empty_cleanup_report(*_args, **_kwargs) -> CleanupReport:
    return CleanupReport((), (), (), ())


def _empty_orphan_report(*_args, **_kwargs) -> OrphanCleanupReport:
    return OrphanCleanupReport((), (), (), ())


def test_periodic_runner_does_not_run_a_cycle_immediately_on_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    cycle_started = threading.Event()

    def _recording_cleanup_expired(*args, **kwargs):
        cycle_started.set()
        return _empty_cleanup_report(*args, **kwargs)

    monkeypatch.setattr(app_module, "cleanup_expired_assets", _recording_cleanup_expired)
    monkeypatch.setattr(app_module, "cleanup_orphaned_assets", _empty_orphan_report)

    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=1000.0
    )
    runner.start()
    try:
        assert not cycle_started.wait(timeout=0.2)
    finally:
        runner.stop()


def test_periodic_runner_runs_a_cycle_after_the_configured_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A short `interval_seconds` supplied directly to the private runner's
    constructor -- a test-only seam -- proves the wait-then-run loop without
    weakening the one-hour production minimum enforced by
    `_asset_cleanup_interval_seconds`."""

    cycle_ran = threading.Event()

    def _recording_cleanup_expired(*args, **kwargs):
        cycle_ran.set()
        return _empty_cleanup_report(*args, **kwargs)

    monkeypatch.setattr(app_module, "cleanup_expired_assets", _recording_cleanup_expired)
    monkeypatch.setattr(app_module, "cleanup_orphaned_assets", _empty_orphan_report)

    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=0.05
    )
    runner.start()
    try:
        assert cycle_ran.wait(timeout=2.0)
    finally:
        runner.stop()


def test_periodic_cycle_order_is_owned_then_orphan_then_storage_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    order: list[str] = []

    def _recording_owned(*args, **kwargs):
        order.append("owned")
        return _empty_cleanup_report(*args, **kwargs)

    def _recording_orphan(*args, **kwargs):
        order.append("orphan")
        return _empty_orphan_report(*args, **kwargs)

    def _recording_storage_usage(storage):
        order.append("storage_usage")

    monkeypatch.setattr(app_module, "cleanup_expired_assets", _recording_owned)
    monkeypatch.setattr(app_module, "cleanup_orphaned_assets", _recording_orphan)
    monkeypatch.setattr(
        app_module, "_emit_storage_usage_observed", _recording_storage_usage
    )

    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=0.05
    )
    runner.start()
    try:
        deadline = time.monotonic() + 2.0
        while len(order) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        runner.stop()

    assert order[:3] == ["owned", "orphan", "storage_usage"]


def test_stop_interrupts_an_in_progress_wait_and_joins_the_worker(tmp_path: Path):
    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=1000.0
    )
    runner.start()

    started_at = time.monotonic()
    runner.stop()
    elapsed = time.monotonic() - started_at

    assert elapsed < 2.0  # far shorter than the configured 1000s interval
    assert runner._thread is None


def test_calling_start_twice_does_not_spawn_a_second_worker(tmp_path: Path):
    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=1000.0
    )
    runner.start()
    first_thread = runner._thread
    runner.start()
    try:
        assert runner._thread is first_thread
        assert sum(
            1 for t in threading.enumerate() if t.name == "asset-maintenance"
        ) == 1
    finally:
        runner.stop()


def test_failed_cycle_emits_diagnostic_and_a_later_cycle_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    call_count = {"n": 0}
    second_cycle_ran = threading.Event()

    def _flaky_cleanup_expired(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        second_cycle_ran.set()
        return _empty_cleanup_report(*args, **kwargs)

    monkeypatch.setattr(app_module, "cleanup_expired_assets", _flaky_cleanup_expired)
    monkeypatch.setattr(app_module, "cleanup_orphaned_assets", _empty_orphan_report)

    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    coordinator = app_module._AssetMaintenanceCoordinator()
    runner = app_module._PeriodicAssetMaintenance(
        repository=None, storage=storage, coordinator=coordinator, interval_seconds=0.05
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        runner.start()
        try:
            assert second_cycle_ran.wait(timeout=3.0)
        finally:
            runner.stop()

    failure_events = [
        record.audio_cue_locator_event
        for record in caplog.records
        if getattr(record, "audio_cue_locator_event", {}).get("event")
        == "asset_maintenance_cycle_failed"
    ]
    assert len(failure_events) >= 1
    assert failure_events[0]["boundary"] == "cleanup"
    assert failure_events[0]["outcome"] == "failed"
    assert failure_events[0]["category"] == "RuntimeError"
    assert call_count["n"] >= 2  # the failed cycle did not stop later retries


# --- S0011: FastAPI lifespan start/stop --------------------------------------


def _patch_periodic_maintenance_start_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    calls: list[str] = []
    real_start = app_module._PeriodicAssetMaintenance.start
    real_stop = app_module._PeriodicAssetMaintenance.stop
    monkeypatch.setattr(
        app_module._PeriodicAssetMaintenance,
        "start",
        lambda self: calls.append("start") or real_start(self),
    )
    monkeypatch.setattr(
        app_module._PeriodicAssetMaintenance,
        "stop",
        lambda self: calls.append("stop") or real_stop(self),
    )
    return calls


def _capture_executor_via_build_analysis_use_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> list:
    """`create_app()` builds a real `LocalAnalysisExecutor`
    (`_build_analysis_use_cases`, not returned to the caller); these
    lifespan tests never submit an Analysis, but must still shut that
    executor's worker pool down so no background thread survives past the
    test, mirroring every other caller's `_shutdown(create_use_case)` in
    this file."""

    executors: list = []
    real_builder = app_module._build_analysis_use_cases

    def _capturing_builder(storage):
        create_use_case, query_use_case = real_builder(storage)
        executors.append(create_use_case._executor)
        return create_use_case, query_use_case

    monkeypatch.setattr(app_module, "_build_analysis_use_cases", _capturing_builder)
    return executors


def test_create_app_alone_does_not_start_the_periodic_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _isolate_env(monkeypatch, tmp_path)
    calls = _patch_periodic_maintenance_start_stop(monkeypatch)
    executors = _capture_executor_via_build_analysis_use_cases(monkeypatch)

    app_module.create_app()

    try:
        assert calls == []
    finally:
        for executor in executors:
            executor.shutdown()


def test_asgi_lifespan_starts_exactly_one_worker_and_stops_it_on_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _isolate_env(monkeypatch, tmp_path)
    calls = _patch_periodic_maintenance_start_stop(monkeypatch)
    executors = _capture_executor_via_build_analysis_use_cases(monkeypatch)

    app = app_module.create_app()
    try:
        assert calls == []  # create_app() alone never starts the worker

        async def _scenario() -> None:
            async with app.router.lifespan_context(app):
                assert calls == ["start"]

        asyncio.run(_scenario())

        assert calls == ["start", "stop"]
    finally:
        for executor in executors:
            executor.shutdown()
