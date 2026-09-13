"""Application: the Analysis Repository port (M4-03).

M3 produced in-memory `AnalysisResult`/`EffectiveConfigurationSnapshot`/
`StructuredError` contracts (`core/analysis_result.py`) and M4-01 defines the
`Analysis` lifecycle state machine (`core/analysis_lifecycle.py`), but nothing
persists an Analysis's identity, state, asset references, effective
configuration, lifecycle timestamps, result reference, or structured error
across process boundaries (`issues/M4/M4-03/formal-issue.json`, sections 2-3).
This module closes that gap at the Application boundary: it defines the
persisted `AnalysisRecord` shape and the `AnalysisRepositoryPort` Protocol
that Application depends on, so that SQL, connections, and schema management
can remain confined to a concrete Infrastructure adapter
(`docs/architecture.md`, "Analysis Repository": "Acesso a SQLite deve
permanecer atrás de uma responsabilidade de repository para evitar espalhar
SQL pelo Application").

No Core `Analysis` Python type exists yet (`docs/analysis-core-
contracts.md`, section 8, gap G1; `multi_cue_orchestration.py` docstring,
same gap): that document fixes `Analysis`'s conceptual field vocabulary
(`analysis_id`, `state`, `source_asset`, `cues`, `effective_configuration`,
`lifecycle_timestamps`, `result_or_reference`, `structured_error`) without
implementing it as a dataclass. `AnalysisRecord` below is this issue's
concrete, persistence-facing implementation of exactly that vocabulary --
scoped to what a repository must durably store, not a redefinition of the
Core contract. It reuses `AnalysisLifecycleState` from `core.
analysis_lifecycle` (M4-01) so persisted states and transitions never
diverge from that one authoritative source, and it reuses
`EffectiveConfigurationSnapshot` and `StructuredError` from `core.
analysis_result` (M3-02/M3-06) rather than declaring competing types for the
same concepts. Per M4-02 (`core/asset.py`), an Asset reference here is
always the storage-independent Asset identifier string, never a filesystem
path or media byte.

This module is Application: like `core/asset.py`'s `AssetStoragePort`, it
declares a `Protocol` port plus the value types the port's methods accept
and return, so a caller can depend on this module alone without importing
`sqlite3` or any concrete adapter. It implements no SQL, no connection, and
no schema; those belong exclusively to `infrastructure/analysis_repository/
sqlite_repository.py`. Cancellation, a local executor, REST/WebUI exposure,
a broker, distributed workers, remote or object storage, and persistence of
media bytes or large blobs are all out of scope
(`issues/M4/M4-03/formal-issue.json`, section 4, "Não inclui";
`docs/architecture.md`, "Analysis Repository": "O banco não deve armazenar
grandes blobs de mídia no baseline").

`list_by_state` (M4-05) is this port's one discovery operation, added
because a startup recovery routine cannot otherwise find which analyses
were left `RUNNING` by a crash: no in-memory record of previously
`RUNNING` `analysis_id` values survives a process restart
(`states/M4/M4-05/issue-operational-state.json`, gap G-01). It is
read-only and confined to the same no-SQL-in-Application boundary as
every other method here.

`add_owned_asset` (M4-06) records an additional Asset identifier owned by an
Analysis, such as a persisted derived artifact.  Source and Cue ownership
continues to come from `source_asset_id` and `cues`; the new collection does
not replace or reinterpret either existing field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_result import (
    EffectiveConfigurationSnapshot,
    StructuredError,
)
from audio_cue_locator.core.asset import validate_asset_identifier


class AnalysisNotFoundError(LookupError):
    """Raised by an Analysis Repository port when no record exists for an
    Analysis identifier."""


class AnalysisAlreadyExistsError(RuntimeError):
    """Raised by `AnalysisRepositoryPort.create` when `analysis_id` already
    identifies a persisted Analysis. Creation is not an upsert: a caller
    that wants to change an existing Analysis must use `transition`."""


class InvalidAnalysisRecordError(ValueError):
    """Raised when a value cannot form a valid persisted `AnalysisRecord`."""


@dataclass(frozen=True)
class CueAssetReference:
    """One Cue's identity paired with its M4-02 logical Asset reference
    (`docs/analysis-core-contracts.md`, section 2, `Cue.asset_reference`).

    `asset_id` is validated as a canonical Asset identifier
    (`core.asset.validate_asset_identifier`); it is never a filesystem path,
    matching the M4-02 Asset Identity vs Storage Location boundary this
    repository must preserve.
    """

    cue_id: str
    asset_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.cue_id, str) or not self.cue_id.strip():
            raise InvalidAnalysisRecordError(
                "CueAssetReference.cue_id must be a non-empty string"
            )
        validate_asset_identifier(self.asset_id)


@dataclass(frozen=True)
class LifecycleTimestamps:
    """When an Analysis entered each M4-01 lifecycle state it has actually
    reached so far (`docs/analysis-core-contracts.md`, section 1,
    `Analysis.lifecycle_timestamps`).

    Deliberately one optional field per `AnalysisLifecycleState` member,
    not an open-ended mapping: `AnalysisLifecycleState` is a closed
    enumeration (`core.analysis_lifecycle`, "no fifth state ... is added
    here"), so this type's shape can only grow in lockstep with that one
    authoritative state set, never independently. Every timestamp must be
    timezone-aware (`tzinfo` set): this repository has no implicit local- or
    naive-time convention, so a naive `datetime` is rejected rather than
    silently assumed to be UTC.
    """

    queued_at: datetime
    running_at: datetime | None = None
    succeeded_at: datetime | None = None
    failed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("queued_at", "running_at", "succeeded_at", "failed_at"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise InvalidAnalysisRecordError(
                    f"LifecycleTimestamps.{field_name} must be a "
                    "timezone-aware datetime"
                )

    def at(self, state: AnalysisLifecycleState) -> datetime | None:
        """Return the recorded timestamp for `state`, or `None` if the
        Analysis has not yet reached it."""

        return {
            AnalysisLifecycleState.QUEUED: self.queued_at,
            AnalysisLifecycleState.RUNNING: self.running_at,
            AnalysisLifecycleState.SUCCEEDED: self.succeeded_at,
            AnalysisLifecycleState.FAILED: self.failed_at,
        }[state]

    def with_state_reached(
        self, state: AnalysisLifecycleState, at: datetime
    ) -> LifecycleTimestamps:
        """Return a new `LifecycleTimestamps` recording that `state` was
        reached `at` a given instant, leaving every other slot unchanged.
        Does not itself validate that reaching `state` is a legal M4-01
        transition; the repository adapter validates that separately
        before persisting."""

        field_name = {
            AnalysisLifecycleState.QUEUED: "queued_at",
            AnalysisLifecycleState.RUNNING: "running_at",
            AnalysisLifecycleState.SUCCEEDED: "succeeded_at",
            AnalysisLifecycleState.FAILED: "failed_at",
        }[state]
        values = {
            "queued_at": self.queued_at,
            "running_at": self.running_at,
            "succeeded_at": self.succeeded_at,
            "failed_at": self.failed_at,
        }
        values[field_name] = at
        return LifecycleTimestamps(**values)


@dataclass(frozen=True)
class AnalysisRecord:
    """The complete persisted state of one Analysis
    (`docs/analysis-core-contracts.md`, section 1;
    `docs/architecture.md`, "Analysis Repository").

    Fields are metadata only: `source_asset_id` and each `CueAssetReference.
    asset_id` are opaque M4-02 Asset identifiers, never media bytes or a
    filesystem path. `owned_asset_ids` contains additional opaque Asset
    identifiers explicitly linked to this Analysis, such as persisted derived
    artifacts, without duplicating source or Cue references.
    `effective_configuration` is the M3-02 snapshot already attached to the
    Analysis, reused unchanged; `result_reference` is an opaque pointer to an
    externally serialized `AnalysisResult` (M3-06) -- this repository never
    stores or interprets the Result body itself.

    `structured_error` is present if, and only if, `state` is `FAILED`
    (`core.analysis_lifecycle.AnalysisLifecycleState.FAILED`), mirroring
    the Core `AnalysisResult.final_state` derivation
    (`core/analysis_result.py`) applied to an Analysis record itself:
    a stored `FAILED` Analysis without a structured error, or a
    non-`FAILED` Analysis carrying one, is an invalid persisted state, not
    a partially-populated one.
    """

    analysis_id: str
    state: AnalysisLifecycleState
    source_asset_id: str
    cues: tuple[CueAssetReference, ...]
    effective_configuration: EffectiveConfigurationSnapshot
    lifecycle_timestamps: LifecycleTimestamps
    result_reference: str | None = None
    structured_error: StructuredError | None = None
    owned_asset_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_id, str) or not self.analysis_id.strip():
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.analysis_id must be a non-empty string"
            )
        if not isinstance(self.state, AnalysisLifecycleState):
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.state must be an AnalysisLifecycleState value"
            )
        validate_asset_identifier(self.source_asset_id)
        if not self.cues:
            raise InvalidAnalysisRecordError(
                "AnalysisRecord requires at least one Cue asset reference "
                "(docs/analysis-core-contracts.md, section 1: 'cues' is a "
                "non-empty sequence)"
            )
        seen_cue_ids: set[str] = set()
        for cue_reference in self.cues:
            if not isinstance(cue_reference, CueAssetReference):
                raise InvalidAnalysisRecordError(
                    "AnalysisRecord.cues entries must be CueAssetReference "
                    "values"
                )
            if cue_reference.cue_id in seen_cue_ids:
                raise InvalidAnalysisRecordError(
                    "duplicate cue_id in AnalysisRecord.cues: "
                    f"{cue_reference.cue_id!r}"
                )
            seen_cue_ids.add(cue_reference.cue_id)
        if not isinstance(self.owned_asset_ids, tuple):
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.owned_asset_ids must be a tuple"
            )
        implicit_asset_ids = {
            self.source_asset_id,
            *(cue.asset_id for cue in self.cues),
        }
        seen_owned_asset_ids: set[str] = set()
        for asset_id in self.owned_asset_ids:
            validate_asset_identifier(asset_id)
            if asset_id in seen_owned_asset_ids:
                raise InvalidAnalysisRecordError(
                    "duplicate Asset identifier in "
                    f"AnalysisRecord.owned_asset_ids: {asset_id!r}"
                )
            if asset_id in implicit_asset_ids:
                raise InvalidAnalysisRecordError(
                    "AnalysisRecord.owned_asset_ids must contain only "
                    "additional Assets, not source or Cue identifiers"
                )
            seen_owned_asset_ids.add(asset_id)
        if not isinstance(
            self.effective_configuration, EffectiveConfigurationSnapshot
        ):
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.effective_configuration must be an "
                "EffectiveConfigurationSnapshot value"
            )
        if not isinstance(self.lifecycle_timestamps, LifecycleTimestamps):
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.lifecycle_timestamps must be a "
                "LifecycleTimestamps value"
            )
        if self.lifecycle_timestamps.at(self.state) is None:
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.lifecycle_timestamps must record the "
                "instant the current state was reached"
            )
        is_failed = self.state is AnalysisLifecycleState.FAILED
        if is_failed and self.structured_error is None:
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.structured_error must be present when "
                "state is FAILED"
            )
        if not is_failed and self.structured_error is not None:
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.structured_error must be absent unless "
                "state is FAILED"
            )
        if self.result_reference is not None and not isinstance(
            self.result_reference, str
        ):
            raise InvalidAnalysisRecordError(
                "AnalysisRecord.result_reference must be a string or None"
            )


@runtime_checkable
class AnalysisRepositoryPort(Protocol):
    """Application-facing port for persisting and reading back Analysis
    lifecycle state and metadata.

    No method accepts a SQL fragment, a database connection, or a
    filesystem path: every persistence mechanic stays inside the concrete
    Infrastructure adapter implementing this Protocol
    (`infrastructure/analysis_repository/sqlite_repository.py`).
    """

    def create(
        self,
        *,
        analysis_id: str,
        source_asset_id: str,
        cues: tuple[CueAssetReference, ...],
        effective_configuration: EffectiveConfigurationSnapshot,
        queued_at: datetime,
    ) -> AnalysisRecord:
        """Persist a new Analysis in the `QUEUED` state and return its
        record. Raises `AnalysisAlreadyExistsError` if `analysis_id` is
        already persisted."""

        ...

    def get(self, analysis_id: str) -> AnalysisRecord:
        """Return the persisted record for `analysis_id`. Raises
        `AnalysisNotFoundError` if no such Analysis is persisted."""

        ...

    def add_owned_asset(self, analysis_id: str, asset_id: str) -> AnalysisRecord:
        """Atomically record an additional Asset owned by ``analysis_id``.

        The operation is idempotent.  Source and Cue Asset identifiers are
        already ownership references, so supplying either returns the current
        record without duplicating it in ``owned_asset_ids``. Additional
        ownership must be recorded before the Analysis reaches a terminal
        state, so its retention clock cannot predate the ownership link.
        """

        ...

    def transition(
        self,
        analysis_id: str,
        to_state: AnalysisLifecycleState,
        *,
        at: datetime,
        result_reference: str | None = None,
        structured_error: StructuredError | None = None,
    ) -> AnalysisRecord:
        """Validate the stored Analysis's current state against
        `to_state` using the M4-01 state machine
        (`core.analysis_lifecycle.transition`), and only if that
        transition is valid, persist the new state, its timestamp, and any
        supplied `result_reference`/`structured_error` as one atomic
        operation.

        Raises `AnalysisNotFoundError` if no such Analysis is persisted.
        Raises `core.analysis_lifecycle.InvalidLifecycleTransitionError`,
        reused unchanged rather than redefined, if the stored current
        state does not permit `to_state`; the stored record is left
        completely unchanged when this happens, so a rejected transition
        can never partially apply.
        """

        ...

    def list_by_state(
        self, state: AnalysisLifecycleState
    ) -> tuple[AnalysisRecord, ...]:
        """Return every persisted Analysis currently in `state`, in no
        particular order, or an empty tuple if none match.

        A read-only query: it never mutates any persisted Analysis and,
        unlike `get`, never raises `AnalysisNotFoundError` for an empty
        result -- an empty tuple is itself the well-formed answer "no
        Analysis is currently in this state".

        Added for the M4-05 startup recovery routine
        (`infrastructure/execution/restart_recovery.py`), which needs to
        discover every Analysis left `RUNNING` after a process crash or
        restart. No in-memory record of previously `RUNNING`
        `analysis_id` values survives a crash, so recovery has no way to
        find its targets except by querying persisted state directly
        through this port (`states/M4/M4-05/issue-operational-state.json`,
        gap G-01). This is the only method this port exposes for
        discovering analyses by state; it does not add filtering by any
        other field."""

        ...
