# Local Analysis Executor (M4-04)

Documents `src/audio_cue_locator/infrastructure/execution/local_analysis_executor.py`: the Infrastructure component that claims one persisted `QUEUED` Analysis, drives it to completion outside the caller's connection lifetime, and bounds how many analyses run concurrently.

## Responsibility

`LocalAnalysisExecutor`:

1. Claims a caller-supplied `analysis_id` by transitioning it from `QUEUED` to `RUNNING`, exclusively through the M4-03 `AnalysisRepositoryPort` (`application/ports/analysis_repository.py`).
2. Invokes the existing M3-03 orchestration (`application/multi_cue_orchestration.py`'s `run_multi_cue_analysis`) exactly once, unmodified.
3. Persists exactly one of `SUCCEEDED`-with-result or `FAILED`-with-structured-error back through the same repository port.
4. Runs every claim-and-run attempt on a bounded `concurrent.futures.Executor`, so no more than the configured limit execute at once.

It embeds no SQL and never reads or writes persisted Analysis state by any route other than `AnalysisRepositoryPort`.

## Claiming and discovery (G-01)

The M4-03 `AnalysisRepositoryPort` exposes only `create`, `get`, and `transition`, keyed by a known `analysis_id`; it has no operation to list or discover which analyses are currently `QUEUED`. This issue's authorized scope is limited to two files (this module and this document) and does not include adding a discovery/list operation to that port.

Resolution: `LocalAnalysisExecutor.submit(analysis_id, source, cues)` takes the `analysis_id` as a caller-supplied argument. Whatever component creates or schedules analyses is responsible for knowing which `analysis_id` values exist and calling `submit` for each one; the executor performs no scanning, listing, or SQL of its own. This matches the resolution the controlled analysis itself named as acceptable for this gap ("have a caller supply analysis_id values from elsewhere").

A rejected `QUEUED`-to-`RUNNING` transition (the Analysis was not `QUEUED`, or another worker already claimed it) raises `AnalysisAlreadyClaimedError` from the returned `Future`. This is the sole concurrency-safety mechanism for claiming: the executor relies entirely on the M4-03 repository's own atomic transition validation and never adds a second, independent lock.

## Asset resolution and canonicalization: explicitly out of this module

`docs/architecture.md`'s "Fluxo interno de uma análise" narrative names "resolve mídia e cues" and "canonicaliza source/cues" as steps of one Analysis's full internal flow. This issue's controlled context, however, never lists the Asset Storage adapter, the FFmpeg media-processing adapter, or the canonical audio spec among its files to consult, read, or create, and the formal issue's five acceptance criteria never name asset resolution or canonicalization as this issue's own deliverable.

`LocalAnalysisExecutor.submit` therefore takes already-canonicalized `source`/`cues` NumPy arrays directly, mirroring `run_multi_cue_analysis`'s own parameterization exactly. Resolving `AnalysisRecord.source_asset_id` / `CueAssetReference.asset_id` into canonical audio bytes ahead of calling `submit` is an open integration step left for a future issue to authorize explicitly; this module does not read or depend on `core.asset`, `infrastructure.asset_storage`, or `infrastructure.media_processing`.

## Concurrency-limit mechanism (G-02, G-03, AC-04, AC-05)

The concurrency limit is enforced by an injected [`concurrent.futures.Executor`](https://docs.python.org/3/library/concurrent.futures.html) (`worker_pool`): `submit` hands each claim-and-run attempt to that pool, which is itself responsible for running at most its own `max_workers` tasks at a time — an already-enforced stdlib guarantee, not merely a documented intention.

- If no `worker_pool` is supplied, `LocalAnalysisExecutor` builds a `ThreadPoolExecutor` sized by the `max_concurrency` constructor parameter (default `DEFAULT_MAX_CONCURRENCY = 4`).
- No configuration-file or environment-variable convention exists anywhere else in this codebase (verified by inspection at implementation time), so this module does not invent one. A future issue that introduces project-wide configuration can wire a value into `max_concurrency` without changing this module's contract.
- The concrete thread/process/worker technology remains deliberately unchosen: because `ThreadPoolExecutor` and `ProcessPoolExecutor` both implement the same `concurrent.futures.Executor` interface, a caller with benchmark evidence can supply either (or a custom `Executor`) as `worker_pool` without any change to `LocalAnalysisExecutor` itself. A `ProcessPoolExecutor` additionally requires a picklable `repository` and `result_store`; that constraint is left for a future benchmark to evaluate, not assumed here.
- `LocalAnalysisExecutor.shutdown()` shuts down only a pool it built for itself; a caller-supplied `worker_pool`'s lifecycle remains the caller's responsibility.

This was verified under concurrent load during implementation: submitting more analyses than the configured limit never allowed more than that limit to execute `run_multi_cue_analysis` simultaneously.

## Completion persistence and the per-cue outcome adaptation (AC-03)

On completion, the executor persists exactly one of:

- **`SUCCEEDED`**, with `result_reference` set — when `run_multi_cue_analysis` returns per-cue outcomes (regardless of whether individual cues found occurrences, had no match, or reported an independent `CueFailure`; a per-cue failure never makes the Analysis-level result `failed` on its own, per `core/analysis_result.py`'s `AnalysisResult.final_state` derivation).
- **`FAILED`**, with `structured_error` set — when `run_multi_cue_analysis` raises (a shared-source precondition failure or any other exception), meaning no per-cue outcome was produced at all.

`run_multi_cue_analysis` returns Application's own `PerCueOutcome` union (`multi_cue_orchestration.py`), while the M3-06 `AnalysisResult` (`core/analysis_result.py`) requires its own, independently declared `CueOutcome` union of the same shape. `core/analysis_result.py`'s own docstring names this adaptation, and attaching a result to a full Analysis lifecycle, as "deliberately left to a future integration issue." This module is that integration point: `_to_core_cue_outcome` projects each Application outcome into the structurally equivalent Core outcome without recomputing or reinterpreting any matched value.

## Result-body storage (`ResultReferenceStore`)

`AnalysisRecord.result_reference` is documented as "an opaque pointer to an externally serialized `AnalysisResult`... this repository never stores or interprets the Result body itself." No result-storage port exists yet, and adding a durable one is outside this issue's two authorized paths.

This module defines the minimal `ResultReferenceStore` protocol it needs (`save(analysis_id, result) -> str`) and a non-durable default, `InMemoryResultReferenceStore`, which serializes each `AnalysisResult` with `serialize_analysis_result` (M3-06's canonical JSON form) and keeps it in an in-process, thread-safe dict keyed by a generated reference string.

This default is explicitly a placeholder, not a durability decision:

- It introduces no broker, distributed worker, remote storage, or object storage, matching the formal issue's "Não inclui".
- Results stored this way do not survive a process restart and are reachable only through the same `LocalAnalysisExecutor`/store instance.
- A caller that needs durable, cross-process result storage supplies its own `ResultReferenceStore` implementation via the `result_store` constructor parameter; `LocalAnalysisExecutor`'s own contract does not change.

## What this issue does not change

Per the formal issue's "Não inclui" and this issue's non-goals, this module does not: choose a final thread/process/worker technology; introduce a broker, distributed worker, remote storage, or object storage; implement recovery/restart policy for an Analysis left `RUNNING` after an ungraceful shutdown (an explicitly deferred, separate draft); expose REST/WebUI triggers; implement Analysis cancellation; or redefine the M4-01 lifecycle vocabulary, the M4-03 repository contract, or the M3-03 orchestration/matching pipeline.

## Verification performed during implementation

Reduced, non-persisted verification exercised, against an in-memory fake `AnalysisRepositoryPort`:

- A `QUEUED` Analysis claimed and completed reaches `SUCCEEDED` with a non-`None` `result_reference`.
- Re-submitting an already-terminal `analysis_id` raises `AnalysisAlreadyClaimedError` without any processing occurring.
- An Analysis whose `source` fails `run_multi_cue_analysis`'s own precondition validation reaches `FAILED` with a `structured_error`, and never reaches `SUCCEEDED`.
- Submitting more analyses than a configured `max_concurrency` never allows more than that many to execute `run_multi_cue_analysis` at the same time.

No raw runtime state, logs, or database contents were retained from this verification.
