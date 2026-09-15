# Applied Retention and Cleanup Wiring (M7-03, extended by S0012)

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
all three complete, so no code path in this process can claim a `QUEUED`
Analysis before startup maintenance has already finished.

## Startup-only trigger decision

None of the three calls is wired to a periodic, per-request, or otherwise
recurring trigger. All three run exactly once, at composition-root build
time, per process start. This is the smallest viable mechanism consistent
with this local-first, single-process runtime having no existing
background-scheduler mechanism, and it avoids adding synchronous
repository/storage I/O latency to any request path. An operator who needs
cleanup to also run between process restarts (for a long-lived process that
is never restarted) must restart the process, or a future issue must add an
explicit, separately justified periodic mechanism; S0012, like M7-03 before
it, does not add one -- that evaluation, including any concurrency
coordination required to run cleanup safely while requests are active, is
left to S0011.

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
M7-03 conclusions: the startup-only trigger decision, the recorded Analysis
Result durability scope decision, the `.gitignore` fix, and the operational
caution about composition-root construction no longer being inert all
continue to apply unchanged, and now also cover `cleanup_orphaned_assets`'s
own storage/repository I/O. S0012 does not modify `retention_policy.py`'s
existing `cleanup_expired_assets` behavior, does not add a periodic or
per-request trigger for either cleanup phase, and does not add environment
isolation beyond what M7-03 already established.
