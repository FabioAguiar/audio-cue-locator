"""Ownership-scoped retention and cleanup for persisted Asset bytes (M4-06,
extended by S0012 with a second, separate orphan-upload cleanup pass).

``cleanup_expired_assets`` eligibility comes exclusively from persisted
``AnalysisRecord`` state, ownership references, and lifecycle timestamps; it
never inspects filesystem paths or timestamps and never executes SQL: it
discovers Analyses through ``AnalysisRepositoryPort`` and removes bytes
through ``AssetStoragePort``.

An Asset is deleted only when exactly one persisted Analysis references it,
that Analysis is ``SUCCEEDED`` or ``FAILED``, and its matching terminal
timestamp is at least the configured retention window old.  All references
are refreshed immediately before each deletion so ambiguity fails closed.

``cleanup_orphaned_assets`` (S0012) is a distinct, separately triggered pass
that answers a different question: not "may bytes owned by a terminal
Analysis be removed", but "may bytes that were uploaded but never became
referenced by any persisted Analysis be removed". It uses the S0012 local
storage inventory (``AssetStoragePort.list_entries``) as its only Asset-age
signal and never substitutes that age for `cleanup_expired_assets`'s own
terminal-timestamp eligibility. Both routines share the same conservative
"refresh references immediately before deleting" discipline.
"""

from __future__ import annotations

from collections.abc import Collection
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

ORPHAN_UPLOAD_GRACE_WINDOW = timedelta(hours=24)
"""Shortest permitted staging window (S0012) for a physical Asset that has
never been referenced by any persisted Analysis. Distinct from, and much
shorter than, `MINIMUM_RETENTION_WINDOW`: it bounds how long an upload may
sit unclaimed before `POST /analyses` links it, not how long an already
Analysis-owned Asset survives after that Analysis reaches a terminal state.
"""

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
    protected_asset_ids: Collection[str] = (),
) -> CleanupReport:
    """Delete uniquely owned Assets for terminal, retention-eligible Analyses.

    ``retention_window`` may be lengthened globally but never shortened below
    the documented seven-day minimum.  A missing stored object is reported,
    rather than treated as a successful deletion, which makes retries safe
    without concealing a broken persisted reference.

    ``protected_asset_ids`` (S0011) is an optional, additive process-local
    exclusion set: a candidate whose identifier is protected is always
    preserved, checked both at initial candidate construction and again
    immediately before delete, and never makes an otherwise-ineligible Asset
    eligible. Startup callers omit it (no request can yet reserve an Asset);
    the periodic maintenance runner supplies an immutable snapshot captured
    while it holds the maintenance coordination boundary.
    """

    effective_now = now if now is not None else datetime.now(timezone.utc)
    _validate_inputs(effective_now, retention_window)
    protected_ids = frozenset(protected_asset_ids)

    initial_records = _all_records(repository)
    eligible_records = tuple(
        record
        for record in initial_records
        if _is_eligible(record, effective_now, retention_window)
    )
    references = _references_by_asset(initial_records)
    preserved: list[str] = []
    unprotected_candidates: list[tuple[str, str]] = []
    for record in eligible_records:
        for asset_id in _asset_ids(record):
            if references[asset_id] != {record.analysis_id}:
                continue
            if asset_id in protected_ids:
                preserved.append(asset_id)
            else:
                unprotected_candidates.append((record.analysis_id, asset_id))
    candidates = sorted(
        unprotected_candidates, key=lambda candidate: (candidate[0], candidate[1])
    )

    deleted: list[str] = []
    missing: list[str] = []
    for analysis_id, asset_id in candidates:
        if asset_id in protected_ids:
            preserved.append(asset_id)
            continue
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


@dataclass(frozen=True)
class OrphanCleanupReport:
    """Reduced deterministic result of one orphan-upload cleanup pass (S0012)."""

    deleted_asset_ids: tuple[str, ...]
    preserved_referenced_asset_ids: tuple[str, ...]
    preserved_recent_asset_ids: tuple[str, ...]
    missing_asset_ids: tuple[str, ...] = ()


def cleanup_orphaned_assets(
    repository: AnalysisRepositoryPort,
    storage: AssetStoragePort,
    *,
    now: datetime | None = None,
    grace_window: timedelta = ORPHAN_UPLOAD_GRACE_WINDOW,
    protected_asset_ids: Collection[str] = (),
) -> OrphanCleanupReport:
    """Delete physical Assets that no persisted Analysis has ever referenced
    and that have sat unclaimed for at least ``grace_window`` (S0012).

    This is a distinct pass from `cleanup_expired_assets`: eligibility comes
    from the S0012 local storage inventory's own ``stored_at`` signal, not
    from any Analysis lifecycle timestamp, and a referenced Asset is never a
    candidate here regardless of age. ``grace_window`` may be lengthened for
    tests/future composition but never shortened below
    `ORPHAN_UPLOAD_GRACE_WINDOW`. References are refreshed immediately before
    each deletion, exactly like `cleanup_expired_assets`, so a reference
    introduced between enumeration and deletion still preserves the Asset. A
    missing stored object is reported, rather than treated as a successful
    deletion, which makes retries safe without concealing a broken storage
    state.

    ``protected_asset_ids`` (S0011): see `cleanup_expired_assets`'s own
    docstring -- same additive, process-local, both-before-and-immediately-
    before-delete exclusion semantics. A protected orphan candidate is
    reported through the existing ``preserved_recent_asset_ids`` field.
    """

    effective_now = now if now is not None else datetime.now(timezone.utc)
    _validate_orphan_inputs(effective_now, grace_window)
    protected_ids = frozenset(protected_asset_ids)

    initial_records = _all_records(repository)
    referenced_ids = set(_references_by_asset(initial_records))

    entries = storage.list_entries()

    preserved_referenced: list[str] = []
    preserved_recent: list[str] = []
    candidates: list[str] = []
    for entry in entries:
        if entry.identifier in referenced_ids:
            preserved_referenced.append(entry.identifier)
        elif entry.identifier in protected_ids:
            preserved_recent.append(entry.identifier)
        elif _is_orphan_age_eligible(entry.stored_at, effective_now, grace_window):
            candidates.append(entry.identifier)
        else:
            preserved_recent.append(entry.identifier)

    deleted: list[str] = []
    missing: list[str] = []
    for asset_id in sorted(candidates):
        if asset_id in protected_ids:
            preserved_recent.append(asset_id)
            continue
        current_records = _all_records(repository)
        current_referenced_ids = set(_references_by_asset(current_records))
        if asset_id in current_referenced_ids:
            preserved_referenced.append(asset_id)
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

    report = OrphanCleanupReport(
        deleted_asset_ids=tuple(sorted(deleted)),
        preserved_referenced_asset_ids=tuple(sorted(set(preserved_referenced))),
        preserved_recent_asset_ids=tuple(sorted(preserved_recent)),
        missing_asset_ids=tuple(sorted(missing)),
    )
    # S0012: one pass-level diagnostic event, carrying only how many physical
    # Assets this pass actually deleted -- never an Asset identifier, filename,
    # path, or media value, mirroring `cleanup_expired_assets`'s own
    # `asset_retention_cleanup_completed` event.
    emit_diagnostic_event(
        event="asset_orphan_cleanup_completed",
        boundary="cleanup",
        outcome="succeeded",
        count=len(report.deleted_asset_ids),
    )
    return report


def _validate_orphan_inputs(now: datetime, grace_window: timedelta) -> None:
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise InvalidRetentionPolicyError("now must be a timezone-aware datetime")
    if not isinstance(grace_window, timedelta):
        raise InvalidRetentionPolicyError("grace_window must be a timedelta")
    if grace_window < ORPHAN_UPLOAD_GRACE_WINDOW:
        raise InvalidRetentionPolicyError(
            "grace_window must be at least 24 complete hours"
        )


def _is_orphan_age_eligible(
    stored_at: datetime, now: datetime, grace_window: timedelta
) -> bool:
    if (
        not isinstance(stored_at, datetime)
        or stored_at.tzinfo is None
        or stored_at.utcoffset() is None
    ):
        return False
    if stored_at > now:
        return False
    try:
        return now - stored_at >= grace_window
    except (OverflowError, TypeError):
        return False


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
