# Artifact Ownership, Retention, and Cleanup Policy

## Purpose and boundary

M4-06 bounds the lifetime of locally stored Asset bytes without allowing one
Analysis cleanup pass to remove bytes referenced by another Analysis. The
implementation uses the existing M4-02 Asset identifier and M4-03 persisted
Analysis metadata. It introduces no filesystem-derived identity, direct SQL in
Application, remote storage, broker, distributed worker, or deployment
scheduler.

S0012 adds a second, separate policy in the same module: orphan-upload
cleanup for physical Assets that were never referenced by any persisted
Analysis at all. The two policies answer different questions and use
different age signals -- see "Orphan-upload cleanup (S0012)" below -- and
this document keeps them clearly distinguished throughout.

The executable behavior is split across the established ports:

- `AnalysisRepositoryPort` is the only source of Analysis state, lifecycle
  timestamps, and Asset ownership references.
- `AssetStoragePort.delete(identifier)` is the only deletion boundary.
- `AssetStoragePort.list_entries()` (S0012) is the only physical-inventory
  boundary; it exposes identifier, size, and a timezone-aware `stored_at`,
  never a filesystem path.
- `LocalFilesystemAssetStorage` alone resolves an Asset identifier to a local
  path and applies identifier, symlink, and root-containment checks.
- `infrastructure/asset_storage/retention_policy.py` coordinates these ports;
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
orphaned. `AssetStoragePort.list_entries()` (S0012) closes the discovery gap
this document originally left open here -- see "Orphan-upload cleanup
(S0012)" below for the separate, conservative policy that governs those
bytes.

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

## Orphan-upload cleanup (S0012)

The current upload workflow persists source/Cue bytes before an Analysis
exists (`POST /assets/... ` then, later, `POST /analyses`). If Analysis
creation fails, the browser closes, or `/analyses` is never called, those
already-persisted bytes have zero Analysis references and the policy above
cannot discover them: its candidate set begins exclusively from persisted
`AnalysisRecord` references, so a physical Asset nothing ever referenced is
invisible to it, at any age.

`cleanup_orphaned_assets(repository, storage, *, now=None, grace_window=
ORPHAN_UPLOAD_GRACE_WINDOW)` closes that gap with a separate, conservative
rule:

1. every persisted Analysis is enumerated across every lifecycle state
   (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`) to build one reference set
   from `source_asset_id`, every Cue `asset_id`, and every `owned_asset_id`;
2. `AssetStoragePort.list_entries()` enumerates every managed physical Asset;
3. an Asset referenced by any persisted Analysis, in any state, is preserved
   regardless of age -- old age never overrides an existing reference;
4. an unreferenced Asset younger than `ORPHAN_UPLOAD_GRACE_WINDOW`
   (**24 complete hours**, the documented minimum) is preserved as normal
   staging behavior for the ordinary upload-then-create-Analysis flow;
5. an unreferenced Asset that is at least 24 complete hours old is a
   deletion candidate;
6. references are refreshed immediately before each deletion, exactly like
   `cleanup_expired_assets`'s own pre-delete refresh: if the candidate is now
   referenced, it is preserved instead of deleted.

`ORPHAN_UPLOAD_GRACE_WINDOW` age comes exclusively from the S0012 local
storage inventory's own `stored_at` signal (the local adapter's write-once
`st_mtime`). It is never derived from, and never substitutes for, an
Analysis lifecycle timestamp. A longer grace window may be supplied for
tests or future composition, but a shorter one is rejected -- exactly the
same "may lengthen, never shorten" discipline `MINIMUM_RETENTION_WINDOW`
already applies to `cleanup_expired_assets`.

A future-dated or otherwise malformed `stored_at` (clock skew, an
inconsistent adapter) is never clamped backward: `cleanup_orphaned_assets`
fails closed and preserves the Asset. Deletion is requested only through
`AssetStoragePort.delete`, using the same idempotent, missing-vs-deleted
reporting `cleanup_expired_assets` already uses.

This is intentionally the *only* difference between the two policies. Every
other invariant above -- SQL-free coordination through the two ports,
conservative preserve-on-ambiguity, no repair of referenced-but-missing
records, no deletion of Analysis rows -- applies identically to orphan
cleanup.

### Two policies, two age signals

| | `cleanup_expired_assets` | `cleanup_orphaned_assets` (S0012) |
|---|---|---|
| Governs | Assets owned by a terminal Analysis | Assets never referenced by any Analysis |
| Age signal | `lifecycle_timestamps` terminal timestamp | Local storage inventory `stored_at` |
| Minimum window | 7 complete days | 24 complete hours |
| Reference scope | Exactly one owning Analysis | Any Analysis, any state |

Orphan age never substitutes for, shortens, or otherwise influences the
seven-day Analysis-owned terminal retention window, and vice versa. An
Asset that becomes Analysis-referenced before its 24-hour orphan grace
elapses is thereafter governed exclusively by the terminal-retention policy
above, never by orphan age again.

## Eligibility policy vs. execution schedule (S0011)

Everything above this section is *eligibility policy*: the fixed rules that
decide whether a given Asset identifier may be deleted right now. S0011 does
not change a single one of those rules -- the seven-day terminal-retention
minimum, the 24-hour orphan grace minimum, the shared-reference and
pre-delete-refresh discipline, and the "preserve on ambiguity" default all
apply exactly as documented above, whichever caller happens to invoke
`cleanup_expired_assets`/`cleanup_orphaned_assets`.

*Execution schedule* is a separate, later concern: when these two eligibility
policies actually run. Before S0011 they ran exactly once, at process
startup (see "Both cleanup passes are startup-only" below, now superseded).
As of S0011 (`docs/retention-and-cleanup.md` has the full applied wiring),
the API process also repeats the same startup order -- owned-retention
cleanup, then orphan cleanup -- on a configured recurring interval (default
24 hours, minimum one hour, `AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS`)
for as long as it keeps running, in addition to the one startup pass. No
threshold, window, or eligibility rule changes because periodic execution
exists; periodic execution only means eligible Assets are revisited sooner
than the next process restart.

### Process-local protected-ID rule (S0011)

Running cleanup while the API also serves requests introduces one narrow
race periodic execution must guard against: an in-flight
`CreateAnalysisUseCase.create(...)` request can start validating/
canonicalizing an Asset identifier *before* that Analysis is durably
persisted, and a periodic cleanup cycle running at that exact moment would
otherwise see zero persisted references (or an elapsed orphan grace window)
and delete bytes the request is still using.

S0011 closes that race with one process-local, in-memory reservation:
`interfaces.rest_api.app`'s composition root reserves every Asset id a
create request references before its first physical read, and periodic
cleanup receives an immutable snapshot of every currently-reserved id
(`protected_asset_ids`) while it holds the maintenance coordination
boundary. A protected identifier is always preserved, on top of every
existing check above -- it is purely additive and never makes an otherwise
ineligible Asset eligible.

This reservation is **not** a durable ownership mechanism. It is held only
in process memory, only for the duration of one in-flight creation request,
and is released the moment that Analysis is persisted (its persisted
references become the durable ownership authority from then on, exactly as
described in "Ownership model" above) or the request fails validation. A
process restart clears every reservation; nothing about it is written to
SQLite or to Asset storage. Startup cleanup calls omit
`protected_asset_ids` entirely, since no request can yet hold a reservation
before the process has finished starting.

## Known limitations

- No derived-artifact ingestion pipeline is added. The ownership contract
  applies as and when such an Asset is persisted and linked.
- No durable Result store is added; result retention is prospective.
- Both cleanup passes run at startup and, as of S0011, periodically for as
  long as the process keeps running -- see "Eligibility policy vs.
  execution schedule (S0011)" above and `docs/retention-and-cleanup.md` for
  the full applied wiring.
- Analysis records are not deleted or rewritten when their Asset bytes are
  removed. A repeated cleanup therefore reports already-missing bytes rather
  than claiming another deletion.
- An upload that remains unreferenced for at least 24 hours is no longer
  guaranteed to remain available for a later Analysis-creation attempt; this
  is accepted staging behavior, not an error condition.

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
- missing-byte reporting without a false successful-deletion claim;
- (S0012) the exact 24-hour orphan boundary, a 23h59m59s-old unreferenced
  Asset staying preserved, references from every lifecycle state protecting
  bytes, a future-dated entry staying preserved, a reference introduced by
  the immediate pre-delete refresh preserving the candidate, and rejection of
  a grace window shorter than 24 hours.

No deployment, Docker, runtime-volume, remote-storage, broker, REST, WebUI, or
GitHub change is required.
