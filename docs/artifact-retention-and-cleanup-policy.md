# Artifact Ownership, Retention, and Cleanup Policy

## Purpose and boundary

M4-06 bounds the lifetime of locally stored Asset bytes without allowing one
Analysis cleanup pass to remove bytes referenced by another Analysis. The
implementation uses the existing M4-02 Asset identifier and M4-03 persisted
Analysis metadata. It introduces no filesystem-derived identity, direct SQL in
Application, remote storage, broker, distributed worker, or deployment
scheduler.

The executable behavior is split across the established ports:

- `AnalysisRepositoryPort` is the only source of Analysis state, lifecycle
  timestamps, and Asset ownership references.
- `AssetStoragePort.delete(identifier)` is the only deletion boundary.
- `LocalFilesystemAssetStorage` alone resolves an Asset identifier to a local
  path and applies identifier, symlink, and root-containment checks.
- `infrastructure/asset_storage/retention_policy.py` coordinates the two ports;
  it performs no SQL and imports no filesystem API.

## Ownership model

A persisted `AnalysisRecord` owns or references Assets through three
existing-identity mechanisms:

1. `source_asset_id` identifies its source upload.
2. Each `cues[].asset_id` identifies a Cue upload used by the Analysis.
3. `owned_asset_ids` records additional Assets owned by the Analysis, including
   a derived artifact when a future processing path persists one. A future
   durable `AssetType.ANALYSIS_RESULT` Asset is recorded through the same
   collection.

`owned_asset_ids` stores only canonical M4-02 Asset identifiers. It is not a
filesystem path, a second identity scheme, or a replacement for source/Cue
references. `AnalysisRepositoryPort.add_owned_asset` records a new link
atomically and idempotently. The SQLite adapter stores the collection as a JSON
array and migrates an existing M4-03 table with a non-null empty-array default,
so legacy records acquire no invented ownership.

Additional ownership must be recorded before the Analysis reaches a terminal
state. The repository rejects a new ownership link on `SUCCEEDED` or `FAILED`,
which prevents the terminal-state retention clock from predating a newly linked
Asset. A processing path that persists a derived artifact or durable result
must therefore record the identifier before its terminal transition.

There is an unavoidable failure interval between storing new bytes and
recording their ownership. If a process fails in that interval, the bytes are
orphaned. Discovering those bytes is outside this issue because
`AssetStoragePort` deliberately exposes no storage-enumeration operation.

## Minimum retention window

The minimum retention window is **seven complete days**. A longer global
window may be supplied to the cleanup service, but a shorter window is
rejected. Per-user and per-tenant windows are outside scope.

For source uploads, Cue uploads, and persisted derived artifacts, the clock
starts at the timestamp at which the owning Analysis reached its current
terminal state:

- `lifecycle_timestamps.succeeded_at` for `SUCCEEDED`;
- `lifecycle_timestamps.failed_at` for `FAILED`.

An Asset is not age-eligible before `terminal timestamp + seven days`. Equality
at the exact boundary is eligible. `QUEUED` and `RUNNING` Analyses are never
eligible. Missing, malformed, or ambiguous state, ownership, or timestamp data
fails closed and preserves bytes. Filesystem modification time is never an
eligibility input.

The current `InMemoryResultReferenceStore` is explicitly non-durable, so this
policy does not claim that results are currently stored or retained. Once a
durable result is represented by an `AssetType.ANALYSIS_RESULT` identifier and
recorded in `owned_asset_ids`, the same seven-day terminal-state minimum applies
prospectively. Building that durable result store is outside M4-06.

## Cleanup eligibility and isolation

`cleanup_expired_assets` queries `AnalysisRepositoryPort.list_by_state` for all
four M4-01 states and derives an in-memory reference index from persisted
records. An Asset is deleted only when all of the following are true:

1. exactly one persisted Analysis references the identifier;
2. that Analysis is currently `SUCCEEDED` or `FAILED`;
3. the matching terminal timestamp exists and is at least the configured
   retention window old;
4. the identifier is present in `source_asset_id`, `cues`, or
   `owned_asset_ids` for that Analysis;
5. a fresh repository read immediately before deletion still satisfies every
   condition above.

Any Asset referenced by a different persisted Analysis is preserved, even if
both Analyses are terminal and old enough. This conservative shared-reference
rule prevents cleanup for one Analysis from affecting another. Assets owned by
nonterminal or too-recent Analyses are also preserved.

Deletion is requested only through `AssetStoragePort.delete`. The local adapter
returns `true` when bytes were removed and `false` when they were already
absent, making a cleanup pass safely retryable. Missing bytes are reported
separately and are not claimed as successful deletions. Storage errors remain
explicit rather than being converted into success.

## Known limitations

- Orphaned bytes with no persisted Analysis reference cannot be discovered or
  cleaned up.
- No derived-artifact ingestion pipeline is added. The ownership contract
  applies as and when such an Asset is persisted and linked.
- No durable Result store is added; result retention is prospective.
- Cleanup is an independently callable local routine. Scheduling or startup
  wiring is a later integration decision.
- Analysis records are not deleted or rewritten when their Asset bytes are
  removed. A repeated cleanup therefore reports already-missing bytes rather
  than claiming another deletion.

## Validation expectations

The separate ASF test phase should cover:

- idempotent deletion and rejection of malformed identifiers and symbolic-link
  storage entries;
- persisted `owned_asset_ids`, legacy-row migration, and preservation across
  lifecycle transitions;
- the exact seven-day boundary, both terminal states, nonterminal and recent
  records, missing timestamps, and rejection of a shorter window;
- shared identifiers across different Analyses and a reference change detected
  by the immediate pre-delete refresh;
- missing-byte reporting without a false successful-deletion claim.

No deployment, Docker, runtime-volume, remote-storage, broker, REST, WebUI, or
GitHub change is required.
