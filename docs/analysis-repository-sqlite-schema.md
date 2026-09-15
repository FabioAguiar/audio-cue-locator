# Analysis Repository SQLite Schema

## Purpose and boundary

M4-03 persists an Analysis's identity, M4-01 lifecycle state, M4-02 logical
Asset references, effective configuration, lifecycle timestamps, result
reference, and structured error, so that status and result queries can be
answered from durable state instead of a single synchronous call
(`issues/M4/M4-03/formal-issue.json`, sections 2-3).

The executable contract is split across two authorized modules:

- `src/audio_cue_locator/application/ports/analysis_repository.py` defines
  the persisted value types (`AnalysisRecord`, `CueAssetReference`,
  `LifecycleTimestamps`) and the Application-facing `AnalysisRepositoryPort`
  Protocol (`create`, `get`, `add_owned_asset`, `transition`,
  `list_by_state`).
- `src/audio_cue_locator/infrastructure/analysis_repository/
  sqlite_repository.py` implements that port against a local, file-backed
  SQLite database.

Application code must depend on `AnalysisRepositoryPort`. It must not import
`SQLiteAnalysisRepository` as its contract, and it must not construct SQL of
its own; every statement, connection, and transaction lives inside the
adapter (`docs/architecture.md`, "SQLite": "Acesso a SQLite deve permanecer
atrás de uma responsabilidade de repository para evitar espalhar SQL pelo
Application").

This document does not redefine `Analysis`, `Cue`, `Occurrence`, or
`AnalysisResult` (`docs/analysis-core-contracts.md`,
`docs/analysis-result-schema.md`); it records how this repository persists
metadata that already belongs to those contracts. It does not persist media
bytes, large blobs, or raw runtime artifacts; the M4-02 Asset Storage
boundary keeps those in the filesystem, addressed only by the opaque
identifiers this repository stores as text
(`docs/asset-identity-and-storage.md`).

## `AnalysisRecord` contract

`AnalysisRecord` is a frozen dataclass with these fields:

| Field | Type and meaning |
|---|---|
| `analysis_id` | Non-empty, caller-supplied stable identifier for the Analysis. |
| `state` | `AnalysisLifecycleState` (`core.analysis_lifecycle`, M4-01): `queued`, `running`, `succeeded`, or `failed`. |
| `source_asset_id` | M4-02 canonical Asset identifier for the already-canonicalized source audio; validated, never a filesystem path. |
| `cues` | Non-empty tuple of `CueAssetReference(cue_id, asset_id, label, trim_start_seconds, trim_end_seconds)`, each `asset_id` an M4-02 Asset identifier; `cue_id` values are unique within the record. `label` is presentation metadata. The compatibility-preserved `trim_start_seconds`/`trim_end_seconds` keys are nullable per-Cue source-search bounds; they carry no media bytes, do not change matcher scoring/acceptance, and default to `None` for historical records. |
| `effective_configuration` | An `EffectiveConfigurationSnapshot` (`core.analysis_result`, M3-02), reused unchanged. |
| `lifecycle_timestamps` | A `LifecycleTimestamps` value: one optional, timezone-aware `datetime` per `AnalysisLifecycleState` member, present for every state actually reached so far. |
| `result_reference` | Optional opaque string pointing at an externally serialized `AnalysisResult` (M3-06); this repository never stores or interprets the Result body itself. |
| `structured_error` | Optional `StructuredError` (`core.analysis_result`); present if, and only if, `state` is `failed`. |
| `owned_asset_ids` | Tuple of additional M4-02 Asset identifiers owned by this Analysis, such as persisted derived artifacts. Source and Cue identifiers remain represented by `source_asset_id` and `cues` and cannot be duplicated here. Defaults to an empty tuple for legacy records. |

`AnalysisRecord.__post_init__` enforces every invariant above, including the
`state == failed <=> structured_error is not None` rule and that
`lifecycle_timestamps` records the instant the current `state` was reached.
An `AnalysisRecord` that violates any of these is never constructed, so an
invalid shape can never reach the database layer in the first place.

## `AnalysisRepositoryPort`

The port exposes five operations:

- `create(*, analysis_id, source_asset_id, cues, effective_configuration,
  queued_at) -> AnalysisRecord` — persists a new Analysis in the `queued`
  state. Raises `AnalysisAlreadyExistsError` if `analysis_id` already
  identifies a persisted Analysis; this is not an upsert.
- `get(analysis_id) -> AnalysisRecord` — returns the persisted record.
  Raises `AnalysisNotFoundError` if none exists.
- `add_owned_asset(analysis_id, asset_id) -> AnalysisRecord` — atomically and
  idempotently records an additional Asset owned by the Analysis. Passing its
  existing source or Cue Asset identifier returns the current record without
  duplicating the implicit ownership reference. Additional ownership must be
  recorded before the Analysis becomes terminal, ensuring the terminal-state
  retention clock never predates a newly linked Asset.
- `transition(analysis_id, to_state, *, at, result_reference=None,
  structured_error=None) -> AnalysisRecord` — validates the stored current
  state against `to_state` using the unmodified Core function
  `core.analysis_lifecycle.transition` before writing anything, then
  persists the new state, its timestamp, and any supplied
  `result_reference`/`structured_error` as one atomic operation. Raises
  `AnalysisNotFoundError` if the Analysis does not exist, and
  `core.analysis_lifecycle.InvalidLifecycleTransitionError` — reused, not
  redefined — if the stored current state does not permit `to_state`. A
  rejected transition leaves the stored record completely unchanged.
- `list_by_state(state) -> tuple[AnalysisRecord, ...]` — returns persisted
  records in the requested M4-01 state without exposing SQL to Application.

No method accepts a SQL fragment, a connection, or a filesystem path.

## SQLite schema

One table, `analyses`, keyed by `analysis_id`:

```sql
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
```

| Column | Encoding |
|---|---|
| `analysis_id` | Verbatim `AnalysisRecord.analysis_id` string; primary key. |
| `state` | The `AnalysisLifecycleState` string value (`"queued"`, `"running"`, `"succeeded"`, `"failed"`). |
| `source_asset_id` | Verbatim M4-02 Asset identifier string. |
| `cues_json` | JSON array of `{"cue_id": ..., "asset_id": ..., "label": ..., "trim_start_seconds": ..., "trim_end_seconds": ...}` objects, in the record's original cue order. The last three (S0003) are nullable and additive; see "Cue label and trim bounds (S0003)" below. |
| `effective_configuration_json` | JSON object mirroring `EffectiveConfigurationSnapshot`'s field structure (`canonicalization`, `matching`, `configuration_source_name`). |
| `lifecycle_timestamps_json` | JSON object with one key per lifecycle state (`queued_at`, `running_at`, `succeeded_at`, `failed_at`), each a timezone-aware ISO-8601 string or `null`. |
| `owned_asset_ids_json` | JSON array of additional M4-02 Asset identifier strings, in insertion order. An empty array means no additional persisted ownership links. |
| `result_reference` | Verbatim opaque string, or `NULL` when absent. |
| `structured_error_json` | JSON object `{"category": ..., "message": ...}`, or `NULL` when the Analysis has not failed. |

Every column is metadata: no column stores media bytes, a large blob, or a
raw runtime artifact. This satisfies the metadata-only boundary
(`docs/architecture.md`, "Analysis Repository": "O banco não deve armazenar
grandes blobs de mídia no baseline").

### Timestamp format

Every timestamp is stored as the value returned by a timezone-aware
`datetime.isoformat()` call and read back with `datetime.fromisoformat`.
`LifecycleTimestamps` rejects a naive `datetime` (no `tzinfo`) before it
ever reaches serialization, so there is no implicit local- or UTC-time
convention to get wrong.

### Migrations and versioning

The schema is created with `CREATE TABLE IF NOT EXISTS` on every adapter
construction. M4-06 adds the compatible `owned_asset_ids_json` column on
adapter startup when an existing M4-03 table does not yet contain it, using
`TEXT NOT NULL DEFAULT '[]'`. Existing rows therefore deserialize with no
additional owned Assets, while new and updated rows round-trip explicit
ownership. There is still no separate migration runner or schema-version
column; a future incompatible change is expected to introduce explicit
versioning rather than silently altering the contract.

### Cue label and source-search bounds (S0008)

`cues_json` gains three additive, nullable members per Cue entry --
`label`, `trim_start_seconds`, `trim_end_seconds` -- with **no SQLite
column or table-schema migration**: `cues_json` itself remains the sole
persistence owner of every Cue-level field, exactly as it already was for
`cue_id`/`asset_id`. `_serialize_cues` always writes all five keys (the
last three as JSON `null` when absent); `_deserialize_cues` reads each of
the three new keys with `dict.get`, so a `cues_json` entry written before
S0003 -- containing only `cue_id`/`asset_id` -- deserializes with `label`,
`trim_start_seconds`, and `trim_end_seconds` all `None`, identically to an
entry that explicitly stored `null` for them. `CueAssetReference`'s own
`__post_init__` still enforces every persisted-value invariant for a
non-null value (a normalized, <= 80-code-point `label`; a finite,
non-negative trim bound; `trim_start_seconds < trim_end_seconds` when both
are present) independently of `interfaces.rest_api.schemas`/Pydantic, so a
non-HTTP Application caller cannot persist an inconsistent record through
this adapter either. These three fields carry no media bytes, canonical
sample arrays, filenames, or filesystem paths. The two bounds constrain the
source interval searched for that Cue; they do not trim the Cue asset or
change matcher scoring and acceptance. Application rebases any found time by
the sample-aligned window start, so Result timestamps remain absolute to
source origin `0`. No SQLite column or table migration is needed,
`cues_json` retains the same keys, and historical missing keys continue to
deserialize as `None`.

## Transaction and concurrency behavior

`transition` opens one `BEGIN IMMEDIATE` transaction, re-reads the current
row inside it, validates the requested transition against the Core state
machine, and only then issues the `UPDATE`; any rejected transition rolls
the transaction back before any write occurs. `BEGIN IMMEDIATE` acquires
SQLite's single write lock at the start of the transaction, so a second
concurrent writer cannot interleave with an in-progress transition: it
either waits, bounded by `PRAGMA busy_timeout` (5000 ms, set once per
connection), or fails with `sqlite3.OperationalError` ("database is
locked"). This is the adapter's complete, intentionally bounded conflict
policy; a caller wanting different retry or queuing behavior builds it on
top of this adapter rather than inside it.

`create` uses the same `BEGIN IMMEDIATE` / validate / write / commit-or-
rollback pattern to make its existence check and insert atomic.

## Database configuration and connection lifetime

`SQLiteAnalysisRepository.__init__` takes a database file path with no
implicit default location; this module reads no environment variable and no
global configuration object. One adapter instance owns one open connection
for its lifetime; `close()` (also reachable through the context-manager
protocol) closes it explicitly. Two separate adapter instances opened
against the same database file observe each other's committed writes,
which is what makes a separate-call/separate-instance round trip possible
(formal issue acceptance criterion 5). Test-fixture isolation is a matter of
pointing each fixture at its own temporary file path; this module has no
built-in fixture or in-memory mode of its own beyond what SQLite itself
provides.

## SQL safety

Every statement in the adapter is a parameterized query (`?` placeholders
bound through `sqlite3`'s parameter substitution); no value is ever
interpolated into SQL text.

## Validation expectations

The separate ASF test phase should cover at least:

- round-trip persistence of every `AnalysisRecord` field, including the
  `owned_asset_ids` collection and nullable `result_reference` and
  `structured_error`;
- migration of an existing M4-03 row to an empty `owned_asset_ids` collection;
- idempotent additional-Asset ownership recording and preservation of that
  collection across lifecycle transitions;
- that allowed M4-01 transitions persist and disallowed transitions raise
  `InvalidLifecycleTransitionError` without changing the stored record;
- that SQL and `sqlite3` types do not leak into Application (import/dependency
  review);
- that the schema and its JSON columns contain no media bytes or blobs;
- a file-backed round trip across two separate `SQLiteAnalysisRepository`
  instances pointed at the same database path.

No deployment, Docker, broker, distributed worker, or remote storage change
is required for this implementation.
