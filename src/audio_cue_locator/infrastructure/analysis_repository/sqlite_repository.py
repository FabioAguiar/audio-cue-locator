"""Infrastructure: the SQLite-backed Analysis Repository adapter (M4-03).

Implements `audio_cue_locator.application.ports.analysis_repository.
AnalysisRepositoryPort` against a local, file-backed SQLite database
(`docs/architecture.md`, "SQLite": "Será o armazenamento inicial de estado e
metadata"). Every SQL statement, connection, transaction, and schema
decision lives in this module; Application depends only on the port and
its value types (`AnalysisRecord`, `CueAssetReference`,
`LifecycleTimestamps`), never on `sqlite3` or this module
(`issues/M4/M4-03/formal-issue.json`, section 4, "Confine SQL access to the
Analysis Repository adapter; Application must not embed queries
directly").

Design decisions this module makes, each resolving an unresolved gap left
open by the M4-03 issue operational state
(`states/M4/M4-03/issue-operational-state.json#/gaps`):

- **Schema (G-01).** One table, `analyses`, keyed by `analysis_id`. Every
  structured value (`cues`, `effective_configuration`,
  `lifecycle_timestamps`, `owned_asset_ids`, `structured_error`) is stored as
  a `TEXT` column holding JSON built from that value's own dataclass fields --
  metadata
  only, never a media byte or blob (`docs/architecture.md`, "Analysis
  Repository": "O banco não deve armazenar grandes blobs de mídia no
  baseline"). Every timestamp is stored as its timezone-aware ISO-8601
  string (`datetime.isoformat`) and parsed back with
  `datetime.fromisoformat`; a naive `datetime` is never accepted (enforced
  by `LifecycleTimestamps` itself).
- **Atomic transition validation (G-04, integration risk).** `transition`
  opens one `BEGIN IMMEDIATE` transaction, re-reads the stored row inside
  it, validates the requested transition with the unmodified Core
  function `core.analysis_lifecycle.transition`, and only performs the
  `UPDATE` if that validation succeeds; any rejection rolls the
  transaction back before any write, so an invalid transition can never
  partially persist. `BEGIN IMMEDIATE` acquires SQLite's single write lock
  before the read, so a second concurrent writer is not interleaved: it
  either waits (`PRAGMA busy_timeout`, below) or fails with
  `sqlite3.OperationalError` ("database is locked"), a reproducible,
  documented conflict signal rather than an undefined race.
- **Concurrency/locking (G-04).** `PRAGMA busy_timeout` is set on connect
  so a second connection's write attempt blocks briefly for the first
  transaction to finish before raising, instead of failing immediately.
  This module does not implement multi-process queuing or retry policy
  beyond that bounded wait; a caller wanting different conflict behavior
  must build it on top of this adapter, not inside it.
- **Database lifetime/configuration (G-05).** The database path is a
  constructor parameter with no implicit default location, so callers (or
  a later configuration layer) own where the file lives; this module does
  not read environment variables or a global configuration object. One
  adapter instance owns one open connection for its lifetime; `close()`
  (also reachable via the context-manager protocol) closes it. Two
  separate adapter instances opened against the same database path observe
  each other's committed writes, which is what makes a separate-call/
  separate-instance round trip (formal issue acceptance criterion 5)
  possible.
- **SQL safety.** Every statement below is a parameterized query
  (`?` placeholders); no value is ever interpolated into SQL text.

`list_by_state` (M4-05) is a later addition, not part of the original M4-03
schema decisions above: a plain `SELECT ... WHERE state = ?` against the
existing `state` column, requiring no new table or column. It resolves the
M4-05 startup-recovery discovery gap
(`states/M4/M4-05/issue-operational-state.json`, gap G-01) while keeping
every enumeration query, like every other query in this module, confined to
this one adapter.

`add_owned_asset` and `owned_asset_ids_json` (M4-06) add the durable ownership
link required for derived artifacts.  Adapter startup adds the non-null JSON
column with an empty-array default to an existing M4-03 database, so legacy
records remain readable without inventing an owner.

Out of scope, unchanged from the port module: cancellation, a local
executor, REST/WebUI exposure, a broker, distributed workers, and remote or
object storage (`issues/M4/M4-03/formal-issue.json`, section 4).
`docs/analysis-repository-sqlite-schema.md` documents this module's schema,
serialization, transaction, and configuration decisions for human review.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    AnalysisRecord,
    CueAssetReference,
    InvalidAnalysisRecordError,
    LifecycleTimestamps,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
from audio_cue_locator.core.analysis_lifecycle import (
    transition as validate_lifecycle_transition,
)
from audio_cue_locator.core.analysis_result import (
    CanonicalizationSnapshot,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.core.asset import validate_asset_identifier

_BUSY_TIMEOUT_MS = 5_000
"""How long a second writer waits for `BEGIN IMMEDIATE` to acquire SQLite's
single write lock before raising `sqlite3.OperationalError` (G-04
conflict-behavior decision, documented in the module docstring above)."""

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    cues_json TEXT NOT NULL,
    effective_configuration_json TEXT NOT NULL,
    lifecycle_timestamps_json TEXT NOT NULL,
    owned_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    result_reference TEXT,
    structured_error_json TEXT
)
"""

_SELECT_COLUMNS = (
    "analysis_id, state, source_asset_id, cues_json, "
    "effective_configuration_json, lifecycle_timestamps_json, "
    "owned_asset_ids_json, result_reference, structured_error_json"
)


def _serialize_cues(cues: tuple[CueAssetReference, ...]) -> str:
    return json.dumps(
        [{"cue_id": cue.cue_id, "asset_id": cue.asset_id} for cue in cues]
    )


def _deserialize_cues(text: str) -> tuple[CueAssetReference, ...]:
    return tuple(
        CueAssetReference(cue_id=entry["cue_id"], asset_id=entry["asset_id"])
        for entry in json.loads(text)
    )


def _serialize_owned_asset_ids(asset_ids: tuple[str, ...]) -> str:
    return json.dumps(list(asset_ids))


def _deserialize_owned_asset_ids(text: str) -> tuple[str, ...]:
    return tuple(json.loads(text))


def _serialize_configuration(configuration: EffectiveConfigurationSnapshot) -> str:
    canonicalization = configuration.canonicalization
    matching = configuration.matching
    return json.dumps(
        {
            "canonicalization": {
                "sample_rate_hz": canonicalization.sample_rate_hz,
                "channels": canonicalization.channels,
                "sample_format": canonicalization.sample_format,
                "normalization": {
                    "enabled": canonicalization.normalization.enabled,
                    "method": canonicalization.normalization.method,
                    "target_peak_amplitude": (
                        canonicalization.normalization.target_peak_amplitude
                    ),
                },
            },
            "matching": {
                "method": matching.method,
                "acceptance_threshold": matching.acceptance_threshold,
            },
            "configuration_source_name": configuration.configuration_source_name,
        }
    )


def _deserialize_configuration(text: str) -> EffectiveConfigurationSnapshot:
    payload = json.loads(text)
    canonicalization_payload = payload["canonicalization"]
    normalization_payload = canonicalization_payload["normalization"]
    matching_payload = payload["matching"]
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=canonicalization_payload["sample_rate_hz"],
            channels=canonicalization_payload["channels"],
            sample_format=canonicalization_payload["sample_format"],
            normalization=NormalizationSnapshot(
                enabled=normalization_payload["enabled"],
                method=normalization_payload["method"],
                target_peak_amplitude=normalization_payload["target_peak_amplitude"],
            ),
        ),
        matching=MatchingSnapshot(
            method=matching_payload["method"],
            acceptance_threshold=matching_payload["acceptance_threshold"],
        ),
        configuration_source_name=payload["configuration_source_name"],
    )


def _serialize_timestamps(timestamps: LifecycleTimestamps) -> str:
    return json.dumps(
        {
            "queued_at": timestamps.queued_at.isoformat(),
            "running_at": (
                timestamps.running_at.isoformat()
                if timestamps.running_at is not None
                else None
            ),
            "succeeded_at": (
                timestamps.succeeded_at.isoformat()
                if timestamps.succeeded_at is not None
                else None
            ),
            "failed_at": (
                timestamps.failed_at.isoformat()
                if timestamps.failed_at is not None
                else None
            ),
        }
    )


def _deserialize_timestamps(text: str) -> LifecycleTimestamps:
    payload = json.loads(text)
    return LifecycleTimestamps(
        queued_at=datetime.fromisoformat(payload["queued_at"]),
        running_at=(
            datetime.fromisoformat(payload["running_at"])
            if payload["running_at"] is not None
            else None
        ),
        succeeded_at=(
            datetime.fromisoformat(payload["succeeded_at"])
            if payload["succeeded_at"] is not None
            else None
        ),
        failed_at=(
            datetime.fromisoformat(payload["failed_at"])
            if payload["failed_at"] is not None
            else None
        ),
    )


def _serialize_structured_error(error: StructuredError | None) -> str | None:
    if error is None:
        return None
    return json.dumps({"category": error.category.value, "message": error.message})


def _deserialize_structured_error(text: str | None) -> StructuredError | None:
    if text is None:
        return None
    payload = json.loads(text)
    return StructuredError(
        category=FailureCategory(payload["category"]), message=payload["message"]
    )


class SQLiteAnalysisRepository:
    """SQLite-backed implementation of `AnalysisRepositoryPort`.

    Structurally satisfies the `AnalysisRepositoryPort` Protocol (`create`,
    `get`, `add_owned_asset`, `transition`, `list_by_state`); it does not
    import that Protocol as a base class, matching how
    `core.asset.AssetStoragePort` is consumed elsewhere in this codebase.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._connection = sqlite3.connect(
            str(self._database_path), isolation_level=None
        )
        self._connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(_CREATE_TABLE_SQL)
        self._ensure_owned_asset_ids_column()

    def _ensure_owned_asset_ids_column(self) -> None:
        """Upgrade an existing M4-03 table without invalidating old rows."""

        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(analyses)")
        }
        if "owned_asset_ids_json" not in columns:
            self._connection.execute(
                "ALTER TABLE analyses ADD COLUMN "
                "owned_asset_ids_json TEXT NOT NULL DEFAULT '[]'"
            )

    def close(self) -> None:
        """Close the owned connection. Safe to call more than once."""

        self._connection.close()

    def __enter__(self) -> "SQLiteAnalysisRepository":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def create(
        self,
        *,
        analysis_id: str,
        source_asset_id: str,
        cues: tuple[CueAssetReference, ...],
        effective_configuration: EffectiveConfigurationSnapshot,
        queued_at: datetime,
    ) -> AnalysisRecord:
        record = AnalysisRecord(
            analysis_id=analysis_id,
            state=AnalysisLifecycleState.QUEUED,
            source_asset_id=source_asset_id,
            cues=cues,
            effective_configuration=effective_configuration,
            lifecycle_timestamps=LifecycleTimestamps(queued_at=queued_at),
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT 1 FROM analyses WHERE analysis_id = ?",
                (record.analysis_id,),
            ).fetchone()
            if existing is not None:
                raise AnalysisAlreadyExistsError(
                    f"Analysis already persisted: {record.analysis_id!r}"
                )
            self._connection.execute(
                "INSERT INTO analyses "
                "(analysis_id, state, source_asset_id, cues_json, "
                "effective_configuration_json, lifecycle_timestamps_json, "
                "owned_asset_ids_json, result_reference, structured_error_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.analysis_id,
                    record.state.value,
                    record.source_asset_id,
                    _serialize_cues(record.cues),
                    _serialize_configuration(record.effective_configuration),
                    _serialize_timestamps(record.lifecycle_timestamps),
                    _serialize_owned_asset_ids(record.owned_asset_ids),
                    record.result_reference,
                    _serialize_structured_error(record.structured_error),
                ),
            )
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")
        return record

    def get(self, analysis_id: str) -> AnalysisRecord:
        row = self._connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
        if row is None:
            raise AnalysisNotFoundError(
                f"no Analysis persisted for {analysis_id!r}"
            )
        return self._row_to_record(row)

    def add_owned_asset(self, analysis_id: str, asset_id: str) -> AnalysisRecord:
        validate_asset_identifier(asset_id)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
            if row is None:
                raise AnalysisNotFoundError(
                    f"no Analysis persisted for {analysis_id!r}"
                )
            current = self._row_to_record(row)
            implicit_asset_ids = {
                current.source_asset_id,
                *(cue.asset_id for cue in current.cues),
            }
            if asset_id in implicit_asset_ids or asset_id in current.owned_asset_ids:
                updated = current
            elif current.state in {
                AnalysisLifecycleState.SUCCEEDED,
                AnalysisLifecycleState.FAILED,
            }:
                raise InvalidAnalysisRecordError(
                    "additional Asset ownership must be recorded before "
                    "the Analysis reaches a terminal state"
                )
            else:
                updated = AnalysisRecord(
                    analysis_id=current.analysis_id,
                    state=current.state,
                    source_asset_id=current.source_asset_id,
                    cues=current.cues,
                    effective_configuration=current.effective_configuration,
                    lifecycle_timestamps=current.lifecycle_timestamps,
                    owned_asset_ids=(*current.owned_asset_ids, asset_id),
                    result_reference=current.result_reference,
                    structured_error=current.structured_error,
                )
                self._connection.execute(
                    "UPDATE analyses SET owned_asset_ids_json = ? "
                    "WHERE analysis_id = ?",
                    (
                        _serialize_owned_asset_ids(updated.owned_asset_ids),
                        updated.analysis_id,
                    ),
                )
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")
        return updated

    def transition(
        self,
        analysis_id: str,
        to_state: AnalysisLifecycleState,
        *,
        at: datetime,
        result_reference: str | None = None,
        structured_error: StructuredError | None = None,
    ) -> AnalysisRecord:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
            if row is None:
                raise AnalysisNotFoundError(
                    f"no Analysis persisted for {analysis_id!r}"
                )
            current = self._row_to_record(row)

            # Validated against the one authoritative Core state machine
            # before any write is attempted; raises without mutating
            # `current` or issuing an UPDATE if the transition is invalid.
            validate_lifecycle_transition(current.state, to_state)

            new_record = AnalysisRecord(
                analysis_id=current.analysis_id,
                state=to_state,
                source_asset_id=current.source_asset_id,
                cues=current.cues,
                effective_configuration=current.effective_configuration,
                lifecycle_timestamps=current.lifecycle_timestamps.with_state_reached(
                    to_state, at
                ),
                owned_asset_ids=current.owned_asset_ids,
                result_reference=(
                    result_reference
                    if result_reference is not None
                    else current.result_reference
                ),
                structured_error=structured_error,
            )
            self._connection.execute(
                "UPDATE analyses SET "
                "state = ?, lifecycle_timestamps_json = ?, "
                "result_reference = ?, structured_error_json = ? "
                "WHERE analysis_id = ?",
                (
                    new_record.state.value,
                    _serialize_timestamps(new_record.lifecycle_timestamps),
                    new_record.result_reference,
                    _serialize_structured_error(new_record.structured_error),
                    new_record.analysis_id,
                ),
            )
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")
        return new_record

    def list_by_state(
        self, state: AnalysisLifecycleState
    ) -> tuple[AnalysisRecord, ...]:
        rows = self._connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM analyses WHERE state = ?",
            (state.value,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def _row_to_record(self, row: tuple) -> AnalysisRecord:
        (
            analysis_id,
            state_value,
            source_asset_id,
            cues_json,
            effective_configuration_json,
            lifecycle_timestamps_json,
            owned_asset_ids_json,
            result_reference,
            structured_error_json,
        ) = row
        return AnalysisRecord(
            analysis_id=analysis_id,
            state=AnalysisLifecycleState(state_value),
            source_asset_id=source_asset_id,
            cues=_deserialize_cues(cues_json),
            effective_configuration=_deserialize_configuration(
                effective_configuration_json
            ),
            lifecycle_timestamps=_deserialize_timestamps(lifecycle_timestamps_json),
            owned_asset_ids=_deserialize_owned_asset_ids(owned_asset_ids_json),
            result_reference=result_reference,
            structured_error=_deserialize_structured_error(structured_error_json),
        )
