# Applied Retention and Cleanup Wiring (M7-03, extended by S0012 and S0011)

## Purpose and boundary

This document records how M7-03 applies the two already-implemented,
already-documented mechanisms to the actual packaged runtime: M4-06's
ownership-aware Asset cleanup (`infrastructure/asset_storage/
retention_policy.py`, documented in full in
[`docs/artifact-retention-and-cleanup-policy.md`](artifact-retention-and-cleanup-policy.md))
and M4-05's `RUNNING`-interruption recovery (`infrastructure/execution/
restart_recovery.py`, documented in full in
[`docs/restart-and-recovery-policy.md`](restart-and-recovery-policy.md)).
Both of those documents already state that "scheduling or startup wiring is
a later integration decision" (M4-06) and that "wiring an actual call to it
into a real startup sequence is a later integration step this issue does
not perform" (M4-05). This document is that later integration step. It does
not restate either mechanism's ownership model, eligibility rules, or retry
policy -- consult the two documents above for those.

S0012 extends the same applied wiring with a third startup-only phase,
`cleanup_orphaned_assets` (also documented in
[`docs/artifact-retention-and-cleanup-policy.md`](artifact-retention-and-cleanup-policy.md),
"Orphan-upload cleanup (S0012)"), added immediately after the existing two
calls and, like them, strictly before `LocalAnalysisExecutor` is
constructed.

S0011 extends this document with the periodic maintenance schedule
described in "Periodic maintenance (S0011)" below: startup wiring and its
ordering guarantees are unchanged, but they are no longer the *only* time
the two cleanup policies run for a given process.

## Applied wiring order

`interfaces/rest_api/app.py`'s `_build_analysis_use_cases` -- called once by
`create_app()`, the project's sole composition root -- now runs the startup
lifecycle in this fixed order, immediately after the `AnalysisRepositoryPort`
implementation is constructed and strictly before `LocalAnalysisExecutor` is
constructed:

1. `run_startup_recovery(repository)` -- resolves every Analysis persisted
   `RUNNING` to `FAILED` with the existing structured `interrupted` error.
   Running this before `LocalAnalysisExecutor` exists preserves the
   documented "exactly once, synchronously, before any `QUEUED` Analysis is
   claimed" precondition: no code path in this process can call
   `LocalAnalysisExecutor.submit` before this line has already returned.
2. `cleanup_expired_assets(repository, storage)` -- deletes uniquely-owned,
   terminal, retention-eligible Asset bytes, using the existing default
   seven-day `MINIMUM_RETENTION_WINDOW` and no other override.
3. `cleanup_orphaned_assets(repository, storage)` (S0012) -- deletes
   physical Assets that remain unreferenced by every persisted Analysis
   after the default 24-hour `ORPHAN_UPLOAD_GRACE_WINDOW`, using the S0012
   local storage inventory and no other override.
4. one aggregate storage-usage observation (S0011) -- emits
   `asset_storage_usage_observed` with the current managed Asset count and
   total bytes from `AssetStoragePort.list_entries()`, after both cleanup
   phases have already applied their own deletions for this process start
   (`docs/observability.md`).

Recovery runs before both cleanup phases so that an Analysis this process
itself just resolved from `RUNNING` to `FAILED` is evaluated with its fresh
terminal timestamp -- it will not become owned-retention-eligible until the
seven-day window has independently elapsed from that resolution, not from
whatever time it was originally left `RUNNING`. Owned-retention cleanup runs
before orphan cleanup so that orphan cleanup sees the physical inventory and
every persisted reference exactly as they stand once owned cleanup has
already applied its own, narrower deletions for this same process start;
reversing the last two would not change which Assets are ultimately eligible
under either policy (each policy's own eligibility is timestamp-based, not
order-based, and the two use disjoint age signals), but this is the order
this issue applies and verifies. Executor construction happens only after
all four steps complete, so no code path in this process can claim a
`QUEUED` Analysis before startup maintenance has already finished.

## Startup trigger: exactly once, never rerun periodically

`run_startup_recovery` and the initial storage-usage observation (below)
still run exactly once, at composition-root build time, per process start,
and are never rerun by the S0011 periodic worker. This is unchanged from
M7-03/S0012: restart recovery's own "exactly once, synchronously, before
any `QUEUED` Analysis is claimed" precondition is a startup-only guarantee,
and the periodic mechanism described below never repeats it.

The two cleanup calls themselves, `cleanup_expired_assets` and
`cleanup_orphaned_assets`, also still run once at startup as part of this
same sequence -- but, as of S0011, they run again periodically thereafter.
See "Periodic maintenance (S0011)" immediately below.

## Periodic maintenance (S0011)

A continuously running process does not restart on its own, so an Asset
that becomes retention/orphan-eligible after startup was, before S0011,
never revisited. S0011 adds a private, standard-library-only periodic
worker (`interfaces/rest_api/app.py`'s `_PeriodicAssetMaintenance`, built
from `threading.Thread` and `threading.Event` only -- no third-party
scheduler, broker, or additional service/container) that repeats the same
two cleanup calls, in the same fixed order, on a configured interval for as
long as the process keeps running:

1. default interval: **86400 seconds (24 hours)**;
2. operator override: `AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS`
   (an explicit, non-secret environment variable, following this project's
   existing per-file `AUDIO_CUE_LOCATOR_*` configuration convention);
3. minimum permitted interval: **3600 seconds (one hour)**; a non-numeric,
   non-finite, or sub-one-hour value fails application construction
   explicitly (`create_app()` raises) rather than silently falling back to
   the default or clamping.

Each periodic cycle: enters the maintenance coordination boundary (below),
captures the currently protected Asset-id snapshot, runs
`cleanup_expired_assets(repository, storage, protected_asset_ids=...)`,
then `cleanup_orphaned_assets(repository, storage, protected_asset_ids=...)`
-- the same owned-then-orphan order startup already applies -- then emits
one aggregate storage-usage observation (`docs/observability.md`), then
releases the boundary. No eligibility rule, retention window, or grace
window is different for a periodic cycle than for the startup pass; only
`protected_asset_ids` is additive, and only startup calls omit it (no
request can hold a reservation before the process has finished starting).

The worker waits one full configured interval before its *first* cycle --
the composition root has already performed startup maintenance, so an
immediate duplicate cycle at worker start is deliberately avoided. If a
cycle raises an unexpected exception, the worker emits one sanitized
`asset_maintenance_cycle_failed` diagnostic (category only, never the raw
exception text) and returns to its wait loop; it does not tight-loop retry,
and it never terminates the API process. A later interval may retry.

### Asset reservation / maintenance coordination boundary (S0011)

The concurrency hazard periodic execution introduces -- and startup-only
execution never could, since no request was yet being served -- is a
periodic cycle racing an in-flight `CreateAnalysisUseCase.create(...)` that
has started validating/canonicalizing an Asset identifier before persisting
any reference to it. `interfaces/rest_api/app.py`'s private
`_AssetMaintenanceCoordinator` closes that race with one process-local
(never persisted) reservation boundary:

- `CreateAnalysisUseCase` is injected with a `reserve_asset_ids(asset_ids)`
  context-manager callable (defaulting to a no-op everywhere except this
  composition root); it reserves the complete deduplicated
  source-plus-every-Cue id set before its first `AssetStoragePort.read`,
  keeps the reservation active through validation/canonicalization and
  `AnalysisRepositoryPort.create`, and releases it -- on every exit path,
  success or failure -- once the Analysis is durably persisted or creation
  fails. `LocalAnalysisExecutor.submit` receives already-canonical arrays
  and therefore needs no reservation of its own.
- Reservation only briefly holds an internal lock to increment/decrement a
  reference-count map, so it never serializes Analysis canonicalization: two
  concurrent create requests reserve independently and canonicalize
  concurrently. A shared Asset id reserved by two requests at once stays
  protected until *both* release.
- The periodic worker's `maintenance_snapshot()` holds that same lock for
  its entire maintenance cycle, so a new reservation attempted while a cycle
  is running waits only until that cycle releases the boundary, and the
  cycle always sees every Asset id already reserved by that point.

See [`docs/artifact-retention-and-cleanup-policy.md`](artifact-retention-and-cleanup-policy.md)'s
"Process-local protected-ID rule (S0011)" for why this is explicitly not a
durable ownership mechanism.

### FastAPI lifespan start/stop (S0011)

`create_app()` alone -- including every existing test or script that calls
it without an ASGI server -- never starts the periodic worker thread; it
only constructs it. The worker starts exactly once real ASGI lifespan
startup runs (`runner.start()`), and stops deterministically on ASGI
lifespan shutdown (`runner.stop()`, which signals the worker's
`threading.Event` and joins the thread before returning) -- so the stop
event interrupts an in-progress interval wait immediately, and a graceful
shutdown never leaves an extra maintenance worker running. No maintenance
thread starts per request; at most one worker exists per running
application instance.

## Recorded scope decision: Analysis Result durability stays out of scope

`InMemoryResultReferenceStore` (`infrastructure/execution/
local_analysis_executor.py`) remains unchanged by this issue. It is an
explicit, self-documented, pre-existing placeholder that keeps every
serialized Analysis Result in an in-process dictionary: every Result is
already lost at process restart, before either mechanism this document
describes can run. Introducing durable Result storage would add a new
persistence mechanism beyond the gaps directly required for safe cleanup,
which this issue's own formal specification excludes ("Redesigning the
Analysis lifecycle beyond gaps directly required for safe cleanup" is out
of scope). This is recorded here as an accepted, documented limitation, not
an oversight: a future issue may reopen this decision if durable Result
availability across restarts is required.

## Version-control exclusion fix

`.gitignore` previously excluded `var/analysis_repository.sqlite3` (via its
existing `*.sqlite3` pattern) but had no pattern matching
`var/asset_storage/`'s UUID-named, extensionless upload and derived-artifact
files, even though `.dockerignore` already excludes the entire `var/`
directory from the build context. This issue adds a `var/asset_storage/`
pattern to `.gitignore`, closing that gap and matching the formal issue's
own constraint against versioning uploads.

## Operational caution: composition-root construction is no longer inert

Before this issue, constructing `interfaces.rest_api.app.create_app()` (or
calling `_build_analysis_use_cases` directly) had no observable side effect
beyond creating an empty SQLite file if one did not already exist at the
configured (or default `var/`-relative) path. After this issue, every such
construction also executes recovery and cleanup against whatever
`AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH` / `AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT`
resolve to at that moment -- the real `var/analysis_repository.sqlite3` and
`var/asset_storage/` when neither environment variable is set.

Most existing tests already isolate these paths under `tmp_path` via
`monkeypatch.setenv`, so this has no effect on them. `tests/test_api_errors.py`
and `tests/test_api_v1_contracts.py` construct `create_app()` without setting
either environment variable; on an empty `var/` (as in a clean checkout, and
as confirmed during this issue's own implementation) this remains a no-op,
but on a developer machine with genuine `var/` state -- a currently
`RUNNING` Analysis from a live dev server, or terminal-eligible Asset bytes
a developer still expects to inspect -- running those tests would now
silently resolve or delete that real state. This is a discovered
consequence of applying the composition-root wiring this issue's own title
requires, not a change to either test file (neither is an authorized edit
path for this issue). It is recorded here for human review and for a future,
separately authorized issue to add environment isolation to those two test
files, or an explicit opt-in guard, if judged necessary.

## Verification performed during implementation

Reduced, non-persisted verification performed while applying this wiring:

- `python -m py_compile` on the edited `interfaces/rest_api/app.py`
  succeeds.
- `git check-ignore -v` confirms `var/asset_storage/<uuid>` is now matched
  by the new `.gitignore` pattern, and that `var/analysis_repository.sqlite3`
  remains matched by the pre-existing `*.sqlite3` pattern.
- Direct inspection of every existing call site of `_build_analysis_use_cases`
  and `_build_create_analysis_use_case` (`tests/test_api_analysis_creation.py`,
  `tests/test_api_analysis_queries.py`, `tests/test_api_v1_integration.py`,
  `tests/operational/test_guardrails.py`) confirms each isolates
  `AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH` and `AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT`
  under a fresh `tmp_path`, so the added recovery/cleanup calls observe an
  empty repository and storage root and are no-ops in every one of those
  tests.
- `tests/operational/test_retention_cleanup.py` (this issue's own
  deliverable) exercises the actual wired composition root directly, under
  an isolated `tmp_path`, rather than relying on the above tests' incidental
  coverage.

No repository test suite was executed as part of this implementation; test
execution is a later ASF phase unless explicitly authorized.

## What this issue does not change

Per the formal issue's "Não inclui" and this issue's own non-goals, this
issue does not: modify `retention_policy.py`, `restart_recovery.py`, or
`local_analysis_executor.py`'s internals; add durable Analysis Result
storage; introduce a periodic or per-request cleanup trigger; introduce
object storage, a broker, or distributed garbage collection; or add
environment isolation to `tests/test_api_errors.py` or
`tests/test_api_v1_contracts.py`.

## S0012 addendum: orphan cleanup added to the same wiring

S0012 adds `cleanup_orphaned_assets` as the fixed wiring's third phase (see
"Applied wiring order" above) and does not otherwise revisit this document's
M7-03 conclusions: the recorded Analysis Result durability scope decision,
the `.gitignore` fix, and the operational caution about composition-root
construction no longer being inert all continue to apply unchanged, and now
also cover `cleanup_orphaned_assets`'s own storage/repository I/O. S0012
does not modify `retention_policy.py`'s existing `cleanup_expired_assets`
behavior, does not add a periodic or per-request trigger for either cleanup
phase, and does not add environment isolation beyond what M7-03 already
established.

## S0011 addendum: periodic execution and the reservation boundary

S0011 supersedes M7-03's and S0012's original "none of the three calls is
wired to a periodic ... trigger" conclusion with "Periodic maintenance
(S0011)" above: `cleanup_expired_assets` and `cleanup_orphaned_assets` now
also run periodically, in addition to the unchanged startup pass. This is
the one M7-03/S0012 conclusion S0011 revises; every other conclusion in this
document -- the recorded Analysis Result durability scope decision, the
`.gitignore` fix, the operational caution about composition-root
construction no longer being inert, and restart recovery running exactly
once per process start and never being rerun periodically -- continues to
apply unchanged.

S0011 does add `protected_asset_ids` support to both cleanup functions in
`retention_policy.py` (see "What this issue does not change" above, which
predates S0011 and no longer applies to that one file): an additive,
process-local exclusion set, never a change to either function's existing
eligibility rules, minimum windows, or public report shape. S0011 does not
add environment isolation beyond what M7-03/S0012 already established, does
not add a dependency, and does not introduce a third-party scheduler,
broker, or additional service/container -- see
[`docs/local-operation.md`](local-operation.md) for the operator-facing
summary and [`docs/observability.md`](observability.md) for the new
`asset_storage_usage_observed`/`asset_maintenance_cycle_failed` events.
