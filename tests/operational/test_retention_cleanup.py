"""Integrated M7-03 coverage for the applied composition-root wiring of
M4-05's `run_startup_recovery` and M4-06's `cleanup_expired_assets`.

`tests/test_local_executor_and_recovery.py` and `tests/test_artifact_retention.py`
already exhaustively cover both mechanisms' own eligibility, isolation, and
recovery rules in isolation, against hand-built or directly-constructed
repository/storage pairs. This suite does not duplicate that coverage. It
instead exercises the actual wiring `docs/retention-and-cleanup.md` and
`interfaces/rest_api/app.py`'s `_build_analysis_use_cases` describe --
`run_startup_recovery` then `cleanup_expired_assets`, called once at
composition-root build time, strictly before `LocalAnalysisExecutor` is
constructed -- confirming the wiring itself, not the underlying functions,
is correct: recovery-before-claim ordering, applied restart recovery,
applied cross-Analysis-isolated cleanup across success/failure/timeout
terminal categories, and safety across a repeated ("restarted twice")
composition-root build.

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

import importlib
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
from audio_cue_locator.infrastructure.execution.restart_recovery import (
    INTERRUPTED_ERROR_MESSAGE,
)

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
