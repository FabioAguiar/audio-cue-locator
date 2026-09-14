"""Ownership-scoped retention and cleanup for persisted Asset bytes (M4-06).

Eligibility comes exclusively from persisted ``AnalysisRecord`` state,
ownership references, and lifecycle timestamps.  This module never inspects
filesystem paths or timestamps and never executes SQL: it discovers Analyses
through ``AnalysisRepositoryPort`` and removes bytes through
``AssetStoragePort``.

An Asset is deleted only when exactly one persisted Analysis references it,
that Analysis is ``SUCCEEDED`` or ``FAILED``, and its matching terminal
timestamp is at least the configured retention window old.  All references
are refreshed immediately before each deletion so ambiguity fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    AnalysisRepositoryPort,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.asset import AssetStoragePort
from audio_cue_locator.observability import emit_diagnostic_event

MINIMUM_RETENTION_WINDOW = timedelta(days=7)
"""Shortest permitted window for uploads and persisted owned artifacts."""

_TERMINAL_STATES = (
    AnalysisLifecycleState.SUCCEEDED,
    AnalysisLifecycleState.FAILED,
)


class InvalidRetentionPolicyError(ValueError):
    """Raised when cleanup inputs cannot support a safe eligibility decision."""


@dataclass(frozen=True)
class CleanupReport:
    """Reduced deterministic result of one cleanup pass."""

    eligible_analysis_ids: tuple[str, ...]
    deleted_asset_ids: tuple[str, ...]
    missing_asset_ids: tuple[str, ...]
    preserved_asset_ids: tuple[str, ...]


def cleanup_expired_assets(
    repository: AnalysisRepositoryPort,
    storage: AssetStoragePort,
    *,
    now: datetime | None = None,
    retention_window: timedelta = MINIMUM_RETENTION_WINDOW,
) -> CleanupReport:
    """Delete uniquely owned Assets for terminal, retention-eligible Analyses.

    ``retention_window`` may be lengthened globally but never shortened below
    the documented seven-day minimum.  A missing stored object is reported,
    rather than treated as a successful deletion, which makes retries safe
    without concealing a broken persisted reference.
    """

    effective_now = now if now is not None else datetime.now(timezone.utc)
    _validate_inputs(effective_now, retention_window)

    initial_records = _all_records(repository)
    eligible_records = tuple(
        record
        for record in initial_records
        if _is_eligible(record, effective_now, retention_window)
    )
    references = _references_by_asset(initial_records)
    candidates = sorted(
        (
            (record.analysis_id, asset_id)
            for record in eligible_records
            for asset_id in _asset_ids(record)
            if references[asset_id] == {record.analysis_id}
        ),
        key=lambda candidate: (candidate[0], candidate[1]),
    )

    deleted: list[str] = []
    missing: list[str] = []
    preserved: list[str] = []
    for analysis_id, asset_id in candidates:
        current_records = _all_records(repository)
        current_by_id = {record.analysis_id: record for record in current_records}
        current_owner = current_by_id.get(analysis_id)
        current_references = _references_by_asset(current_records).get(asset_id, set())
        if (
            current_owner is None
            or not _is_eligible(current_owner, effective_now, retention_window)
            or current_references != {analysis_id}
        ):
            preserved.append(asset_id)
            continue

        removed = storage.delete(asset_id)
        if removed is True:
            deleted.append(asset_id)
        elif removed is False:
            missing.append(asset_id)
        else:
            raise InvalidRetentionPolicyError(
                "AssetStoragePort.delete must return a boolean"
            )

    report = CleanupReport(
        eligible_analysis_ids=tuple(
            sorted(record.analysis_id for record in eligible_records)
        ),
        deleted_asset_ids=tuple(deleted),
        missing_asset_ids=tuple(missing),
        preserved_asset_ids=tuple(preserved),
    )
    # M7-04: one pass-level diagnostic event per cleanup run, derived only
    # from this report's own counts -- never a per-Asset identifier list
    # (`states/M7/M7-04/issue-operational-state.json#/known_facts/11`,
    # gap G3). `count` reports how many Assets were actually deleted this
    # pass; a missing/preserved distinction, if needed, belongs to a
    # future issue's own reduced evidence, not this diagnostic event.
    emit_diagnostic_event(
        event="asset_retention_cleanup_completed",
        boundary="cleanup",
        outcome="succeeded",
        count=len(report.deleted_asset_ids),
    )
    return report


def _validate_inputs(now: datetime, retention_window: timedelta) -> None:
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise InvalidRetentionPolicyError("now must be a timezone-aware datetime")
    if not isinstance(retention_window, timedelta):
        raise InvalidRetentionPolicyError("retention_window must be a timedelta")
    if retention_window < MINIMUM_RETENTION_WINDOW:
        raise InvalidRetentionPolicyError(
            "retention_window must be at least seven complete days"
        )


def _all_records(
    repository: AnalysisRepositoryPort,
) -> tuple[AnalysisRecord, ...]:
    records_by_id: dict[str, AnalysisRecord] = {}
    for requested_state in AnalysisLifecycleState:
        for record in repository.list_by_state(requested_state):
            if record.state is not requested_state:
                raise InvalidRetentionPolicyError(
                    "repository returned an Analysis under the wrong state"
                )
            existing = records_by_id.get(record.analysis_id)
            if existing is not None and existing != record:
                raise InvalidRetentionPolicyError(
                    "repository returned conflicting records for one Analysis"
                )
            records_by_id[record.analysis_id] = record
    return tuple(records_by_id.values())


def _is_eligible(
    record: AnalysisRecord,
    now: datetime,
    retention_window: timedelta,
) -> bool:
    if record.state not in _TERMINAL_STATES:
        return False
    terminal_at = record.lifecycle_timestamps.at(record.state)
    if (
        terminal_at is None
        or terminal_at.tzinfo is None
        or terminal_at.utcoffset() is None
    ):
        return False
    try:
        return now - terminal_at >= retention_window
    except (OverflowError, TypeError):
        return False


def _asset_ids(record: AnalysisRecord) -> tuple[str, ...]:
    identifiers = (
        record.source_asset_id,
        *(cue.asset_id for cue in record.cues),
        *record.owned_asset_ids,
    )
    return tuple(dict.fromkeys(identifiers))


def _references_by_asset(
    records: tuple[AnalysisRecord, ...],
) -> dict[str, set[str]]:
    references: dict[str, set[str]] = {}
    for record in records:
        for asset_id in _asset_ids(record):
            references.setdefault(asset_id, set()).add(record.analysis_id)
    return references
