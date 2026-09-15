"""Focused M4-06 coverage for retention eligibility and isolation, extended
by S0012 with a separate orphan-upload cleanup pass."""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    CueAssetReference,
    LifecycleTimestamps,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.core.asset import AssetNotFoundError, AssetStorageEntry, AssetType
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.infrastructure.asset_storage.retention_policy import (
    MINIMUM_RETENTION_WINDOW,
    ORPHAN_UPLOAD_GRACE_WINDOW,
    InvalidRetentionPolicyError,
    cleanup_expired_assets,
    cleanup_orphaned_assets,
)

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


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


def _record(
    analysis_id: str,
    state: AnalysisLifecycleState,
    *,
    source_asset_id: str | None = None,
    cue_asset_id: str | None = None,
    owned_asset_ids: tuple[str, ...] = (),
    terminal_at: datetime | None = None,
) -> AnalysisRecord:
    queued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    running_at = queued_at + timedelta(hours=1)
    timestamps = LifecycleTimestamps(
        queued_at=queued_at,
        running_at=running_at if state is not AnalysisLifecycleState.QUEUED else None,
        succeeded_at=(
            terminal_at if state is AnalysisLifecycleState.SUCCEEDED else None
        ),
        failed_at=terminal_at if state is AnalysisLifecycleState.FAILED else None,
    )
    return AnalysisRecord(
        analysis_id=analysis_id,
        state=state,
        source_asset_id=source_asset_id or _asset_id(),
        cues=(
            CueAssetReference(
                cue_id=f"{analysis_id}-cue",
                asset_id=cue_asset_id or _asset_id(),
            ),
        ),
        effective_configuration=_configuration(),
        lifecycle_timestamps=timestamps,
        owned_asset_ids=owned_asset_ids,
        structured_error=(
            StructuredError(
                category=FailureCategory.INTERNAL_FAILURE,
                message="terminal failure",
            )
            if state is AnalysisLifecycleState.FAILED
            else None
        ),
    )


class _Repository:
    def __init__(self, records: tuple[AnalysisRecord, ...]) -> None:
        self.records = records

    def list_by_state(
        self, state: AnalysisLifecycleState
    ) -> tuple[AnalysisRecord, ...]:
        return tuple(record for record in self.records if record.state is state)


class _Storage:
    def __init__(self, existing: set[str]) -> None:
        self.existing = set(existing)
        self.delete_calls: list[str] = []

    def delete(self, identifier: str) -> bool:
        self.delete_calls.append(identifier)
        if identifier not in self.existing:
            return False
        self.existing.remove(identifier)
        return True


def test_exact_seven_day_boundary_deletes_all_uniquely_owned_assets():
    owned_asset_id = _asset_id()
    record = _record(
        "eligible",
        AnalysisLifecycleState.SUCCEEDED,
        owned_asset_ids=(owned_asset_id,),
        terminal_at=NOW - MINIMUM_RETENTION_WINDOW,
    )
    asset_ids = {
        record.source_asset_id,
        record.cues[0].asset_id,
        owned_asset_id,
    }
    storage = _Storage(asset_ids)

    report = cleanup_expired_assets(_Repository((record,)), storage, now=NOW)

    assert set(report.deleted_asset_ids) == asset_ids
    assert report.eligible_analysis_ids == ("eligible",)
    assert storage.existing == set()


def test_nonterminal_and_too_recent_analyses_are_preserved():
    queued = _record("queued", AnalysisLifecycleState.QUEUED)
    recent = _record(
        "recent",
        AnalysisLifecycleState.SUCCEEDED,
        terminal_at=NOW - MINIMUM_RETENTION_WINDOW + timedelta(seconds=1),
    )
    asset_ids = {
        queued.source_asset_id,
        queued.cues[0].asset_id,
        recent.source_asset_id,
        recent.cues[0].asset_id,
    }
    storage = _Storage(asset_ids)

    report = cleanup_expired_assets(_Repository((queued, recent)), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert storage.delete_calls == []
    assert storage.existing == asset_ids


def test_failed_analysis_uses_failed_timestamp_for_eligibility():
    record = _record(
        "failed",
        AnalysisLifecycleState.FAILED,
        terminal_at=NOW - timedelta(days=8),
    )
    storage = _Storage({record.source_asset_id, record.cues[0].asset_id})

    report = cleanup_expired_assets(_Repository((record,)), storage, now=NOW)

    assert report.eligible_analysis_ids == ("failed",)
    assert set(report.deleted_asset_ids) == {
        record.source_asset_id,
        record.cues[0].asset_id,
    }


def test_asset_referenced_by_another_analysis_is_never_deleted():
    shared_asset_id = _asset_id()
    eligible = _record(
        "eligible",
        AnalysisLifecycleState.SUCCEEDED,
        source_asset_id=shared_asset_id,
        terminal_at=NOW - timedelta(days=8),
    )
    active = _record(
        "active",
        AnalysisLifecycleState.RUNNING,
        source_asset_id=shared_asset_id,
    )
    all_asset_ids = {
        shared_asset_id,
        eligible.cues[0].asset_id,
        active.cues[0].asset_id,
    }
    storage = _Storage(all_asset_ids)

    report = cleanup_expired_assets(
        _Repository((eligible, active)), storage, now=NOW
    )

    assert shared_asset_id not in report.deleted_asset_ids
    assert shared_asset_id not in storage.delete_calls
    assert shared_asset_id in storage.existing
    assert active.cues[0].asset_id in storage.existing


def test_reference_ownership_is_refreshed_immediately_before_delete():
    shared_asset_id = _asset_id()
    eligible = _record(
        "eligible",
        AnalysisLifecycleState.SUCCEEDED,
        source_asset_id=shared_asset_id,
        cue_asset_id=shared_asset_id,
        terminal_at=NOW - timedelta(days=8),
    )
    active = _record(
        "new-owner",
        AnalysisLifecycleState.RUNNING,
        source_asset_id=shared_asset_id,
    )

    class ChangingRepository(_Repository):
        def __init__(self) -> None:
            super().__init__((eligible,))
            self.calls = 0

        def list_by_state(
            self, state: AnalysisLifecycleState
        ) -> tuple[AnalysisRecord, ...]:
            records = (eligible,) if self.calls < 4 else (eligible, active)
            self.calls += 1
            return tuple(record for record in records if record.state is state)

    storage = _Storage({shared_asset_id})

    report = cleanup_expired_assets(ChangingRepository(), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert report.preserved_asset_ids == (shared_asset_id,)
    assert storage.delete_calls == []


def test_missing_bytes_are_reported_without_claiming_deletion():
    record = _record(
        "eligible",
        AnalysisLifecycleState.SUCCEEDED,
        terminal_at=NOW - timedelta(days=8),
    )

    report = cleanup_expired_assets(_Repository((record,)), _Storage(set()), now=NOW)

    assert set(report.missing_asset_ids) == {
        record.source_asset_id,
        record.cues[0].asset_id,
    }
    assert report.deleted_asset_ids == ()


def test_retention_window_cannot_be_shorter_than_seven_days():
    with pytest.raises(InvalidRetentionPolicyError):
        cleanup_expired_assets(
            _Repository(()),
            _Storage(set()),
            now=NOW,
            retention_window=timedelta(days=7) - timedelta(microseconds=1),
        )


def test_cleanup_requires_timezone_aware_now():
    with pytest.raises(InvalidRetentionPolicyError):
        cleanup_expired_assets(
            _Repository(()),
            _Storage(set()),
            now=datetime(2026, 1, 15),
        )


def test_real_adapters_isolate_cleanup_between_simultaneously_persisted_owners(
    tmp_path,
):
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)
    eligible_source = storage.ingest(
        b"eligible source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="eligible.wav",
        detected_media_type="audio/wav",
    )
    protected_source = storage.ingest(
        b"protected source",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="protected.wav",
        detected_media_type="audio/wav",
    )
    shared_cue = storage.ingest(
        b"shared cue",
        logical_type=AssetType.CUE,
        informative_name="shared.wav",
        detected_media_type="audio/wav",
    )

    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        repository.create(
            analysis_id="eligible",
            source_asset_id=eligible_source.identifier,
            cues=(
                CueAssetReference(
                    cue_id="eligible-cue", asset_id=shared_cue.identifier
                ),
            ),
            effective_configuration=_configuration(),
            queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        repository.create(
            analysis_id="protected",
            source_asset_id=protected_source.identifier,
            cues=(
                CueAssetReference(
                    cue_id="protected-cue", asset_id=shared_cue.identifier
                ),
            ),
            effective_configuration=_configuration(),
            queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.RUNNING,
            at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        repository.transition(
            "eligible",
            AnalysisLifecycleState.SUCCEEDED,
            at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            result_reference="result:eligible",
        )
        repository.transition(
            "protected",
            AnalysisLifecycleState.RUNNING,
            at=datetime(2026, 1, 14, tzinfo=timezone.utc),
        )

        report = cleanup_expired_assets(repository, storage, now=NOW)

        assert repository.get("eligible").state is AnalysisLifecycleState.SUCCEEDED
        assert repository.get("protected").state is AnalysisLifecycleState.RUNNING

    assert report.eligible_analysis_ids == ("eligible",)
    assert report.deleted_asset_ids == (eligible_source.identifier,)
    assert shared_cue.identifier not in report.deleted_asset_ids
    with pytest.raises(AssetNotFoundError):
        storage.read(eligible_source.identifier)
    assert storage.read(protected_source.identifier) == b"protected source"
    assert storage.read(shared_cue.identifier) == b"shared cue"
    assert {path.name for path in storage_root.iterdir()} == {
        protected_source.identifier,
        shared_cue.identifier,
    }


# --- S0012: orphan-upload cleanup -------------------------------------------


class _OrphanStorage:
    """A minimal `AssetStoragePort` test double exposing only the two
    operations `cleanup_orphaned_assets` uses: `list_entries` (a fixed,
    caller-supplied inventory) and `delete`."""

    def __init__(
        self, entries: tuple[AssetStorageEntry, ...], *, missing: frozenset[str] = frozenset()
    ) -> None:
        self._entries = entries
        self._missing = set(missing)
        self.delete_calls: list[str] = []

    def list_entries(self) -> tuple[AssetStorageEntry, ...]:
        return self._entries

    def delete(self, identifier: str) -> bool:
        self.delete_calls.append(identifier)
        if identifier in self._missing:
            return False
        self._missing.add(identifier)
        return True


def _entry(asset_id: str, *, stored_at: datetime, size_bytes: int = 1) -> AssetStorageEntry:
    return AssetStorageEntry(identifier=asset_id, size_bytes=size_bytes, stored_at=stored_at)


def test_orphan_grace_window_is_twenty_four_hours():
    assert ORPHAN_UPLOAD_GRACE_WINDOW == timedelta(hours=24)


def test_old_unreferenced_asset_is_deleted():
    asset_id = _asset_id()
    storage = _OrphanStorage((_entry(asset_id, stored_at=NOW - timedelta(hours=25)),))

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == (asset_id,)
    assert report.preserved_referenced_asset_ids == ()
    assert report.preserved_recent_asset_ids == ()


def test_exact_twenty_four_hour_boundary_is_deleted():
    asset_id = _asset_id()
    storage = _OrphanStorage(
        (_entry(asset_id, stored_at=NOW - ORPHAN_UPLOAD_GRACE_WINDOW),)
    )

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == (asset_id,)


def test_just_under_twenty_four_hours_is_preserved():
    asset_id = _asset_id()
    storage = _OrphanStorage(
        (
            _entry(
                asset_id,
                stored_at=NOW - ORPHAN_UPLOAD_GRACE_WINDOW + timedelta(seconds=1),
            ),
        )
    )

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert report.preserved_recent_asset_ids == (asset_id,)
    assert storage.delete_calls == []


def test_referenced_old_asset_is_preserved_regardless_of_age():
    record = _record(
        "active",
        AnalysisLifecycleState.QUEUED,
    )
    storage = _OrphanStorage(
        (_entry(record.source_asset_id, stored_at=NOW - timedelta(days=365)),)
    )

    report = cleanup_orphaned_assets(_Repository((record,)), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert report.preserved_referenced_asset_ids == (record.source_asset_id,)
    assert storage.delete_calls == []


@pytest.mark.parametrize(
    "state",
    [
        AnalysisLifecycleState.QUEUED,
        AnalysisLifecycleState.RUNNING,
        AnalysisLifecycleState.SUCCEEDED,
        AnalysisLifecycleState.FAILED,
    ],
)
def test_reference_from_every_lifecycle_state_protects_bytes(
    state: AnalysisLifecycleState,
):
    record = _record("analysis", state, terminal_at=NOW - timedelta(days=365))
    storage = _OrphanStorage(
        (_entry(record.source_asset_id, stored_at=NOW - timedelta(days=365)),)
    )

    report = cleanup_orphaned_assets(_Repository((record,)), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert record.source_asset_id in report.preserved_referenced_asset_ids


def test_source_cue_and_owned_asset_ids_all_count_as_references():
    owned_asset_id = _asset_id()
    record = _record("analysis", AnalysisLifecycleState.QUEUED, owned_asset_ids=(owned_asset_id,))
    old = NOW - timedelta(days=2)
    storage = _OrphanStorage(
        (
            _entry(record.source_asset_id, stored_at=old),
            _entry(record.cues[0].asset_id, stored_at=old),
            _entry(owned_asset_id, stored_at=old),
        )
    )

    report = cleanup_orphaned_assets(_Repository((record,)), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert set(report.preserved_referenced_asset_ids) == {
        record.source_asset_id,
        record.cues[0].asset_id,
        owned_asset_id,
    }


def test_future_dated_entry_is_preserved():
    asset_id = _asset_id()
    storage = _OrphanStorage((_entry(asset_id, stored_at=NOW + timedelta(hours=1)),))

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert asset_id in report.preserved_recent_asset_ids
    assert storage.delete_calls == []


def test_reference_introduced_by_pre_delete_refresh_preserves_candidate():
    asset_id = _asset_id()
    new_owner = _record("new-owner", AnalysisLifecycleState.QUEUED, source_asset_id=asset_id)

    class ChangingRepository:
        def __init__(self) -> None:
            self.calls = 0

        def list_by_state(self, state: AnalysisLifecycleState) -> tuple[AnalysisRecord, ...]:
            records = () if self.calls < 4 else (new_owner,)
            self.calls += 1
            return tuple(record for record in records if record.state is state)

    storage = _OrphanStorage((_entry(asset_id, stored_at=NOW - timedelta(hours=25)),))

    report = cleanup_orphaned_assets(ChangingRepository(), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert asset_id in report.preserved_referenced_asset_ids
    assert storage.delete_calls == []


def test_multiple_old_orphans_produce_a_deterministic_deletion_report():
    first, second = sorted((_asset_id(), _asset_id()))
    old = NOW - timedelta(hours=48)
    storage = _OrphanStorage((_entry(first, stored_at=old), _entry(second, stored_at=old)))

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == (first, second)


def test_storage_delete_false_is_reported_without_claiming_deletion():
    asset_id = _asset_id()
    storage = _OrphanStorage(
        (_entry(asset_id, stored_at=NOW - timedelta(hours=25)),),
        missing=frozenset({asset_id}),
    )

    report = cleanup_orphaned_assets(_Repository(()), storage, now=NOW)

    assert report.deleted_asset_ids == ()
    assert report.missing_asset_ids == (asset_id,)


def test_orphan_grace_window_cannot_be_shorter_than_twenty_four_hours():
    with pytest.raises(InvalidRetentionPolicyError):
        cleanup_orphaned_assets(
            _Repository(()),
            _OrphanStorage(()),
            now=NOW,
            grace_window=ORPHAN_UPLOAD_GRACE_WINDOW - timedelta(microseconds=1),
        )


def test_orphan_cleanup_requires_timezone_aware_now():
    with pytest.raises(InvalidRetentionPolicyError):
        cleanup_orphaned_assets(
            _Repository(()),
            _OrphanStorage(()),
            now=datetime(2026, 1, 15),
        )


def test_orphan_cleanup_real_adapters_preserve_recent_and_delete_old(tmp_path):
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)
    old_asset = storage.ingest(
        b"old orphan",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="old.wav",
        detected_media_type="audio/wav",
    )
    recent_asset = storage.ingest(
        b"recent upload",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="recent.wav",
        detected_media_type="audio/wav",
    )
    old_timestamp = (NOW - timedelta(hours=25)).timestamp()
    os.utime(storage_root / old_asset.identifier, (old_timestamp, old_timestamp))

    with SQLiteAnalysisRepository(tmp_path / "analyses.sqlite") as repository:
        report = cleanup_orphaned_assets(repository, storage, now=NOW)

    assert report.deleted_asset_ids == (old_asset.identifier,)
    assert recent_asset.identifier in report.preserved_recent_asset_ids
    with pytest.raises(AssetNotFoundError):
        storage.read(old_asset.identifier)
    assert storage.read(recent_asset.identifier) == b"recent upload"
