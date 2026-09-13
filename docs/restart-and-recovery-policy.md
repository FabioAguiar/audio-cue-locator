# Restart and Crash Recovery Policy (M4-05)

Documents `src/audio_cue_locator/infrastructure/execution/restart_recovery.py`: the Infrastructure component that detects Analyses left in a non-terminal state after an interruption and resolves them, together with the project's initial retry policy.

## Problem

The M4-04 `LocalAnalysisExecutor` can be interrupted by a process crash or restart while an Analysis is `RUNNING`. Before this issue, nothing resolved an Analysis left in that state: it would remain reported as `RUNNING` indefinitely, making the system appear to still be processing work that no longer exists.

## Detection: discovery-by-state (G-01)

The M4-03 `AnalysisRepositoryPort` (`application/ports/analysis_repository.py`) previously exposed only `create`, `get`, and `transition`, keyed by a known `analysis_id`; it had no operation to discover which analyses were currently `RUNNING`. Unlike the M4-04 executor's own discovery gap, a caller cannot simply supply `analysis_id` values from memory here: no in-memory record of previously `RUNNING` values survives a process crash at all.

Resolution: this issue adds `list_by_state(state)` to `AnalysisRepositoryPort`, implemented only inside the existing SQLite adapter (`infrastructure/analysis_repository/sqlite_repository.py`) as a parameterized query against the adapter's existing `state` column — no new table or column. `run_startup_recovery` calls `repository.list_by_state(AnalysisLifecycleState.RUNNING)` to find its targets; it embeds no SQL of its own.

## Target state (G-02)

Only `RUNNING` is treated as "interrupted". A `QUEUED` Analysis surviving a restart is not touched: it was never claimed by an executor, so it remains legitimately claimable by a future `LocalAnalysisExecutor.submit` call. Recovering it would incorrectly treat un-started work as a failure.

## Resolution (G-03)

A detected `RUNNING` Analysis is transitioned to `FAILED` through the existing M4-01 `RUNNING`-to-`FAILED` transition (`core/analysis_lifecycle.py`), applied via the same `AnalysisRepositoryPort.transition()` operation the M4-04 executor already uses for its own `FAILED` outcome. No new `AnalysisLifecycleState` or Core lifecycle transition is introduced.

The `StructuredError` recorded uses the existing `FailureCategory.INTERNAL_FAILURE` (`core/analysis_result.py`) — that enum is documented as a closed set shared with Application's identical projection, with no dedicated "interrupted" member, and this issue does not add one. The interruption itself is identified by the error's `message`, fixed as:

> `interrupted: Analysis was left RUNNING by a prior process crash or restart; resolved to FAILED by the startup recovery routine`

## Invocation timing and the single-backend-process precondition

`run_startup_recovery` is designed to be called exactly once, synchronously, before any `QUEUED` Analysis is claimed by a `LocalAnalysisExecutor`. Its correctness depends on at most one backend process operating against the persisted state at a time — the milestone's no-horizontal-scaling assumption. This routine does not itself enforce or re-derive that assumption; it is a precondition it relies on, not something it verifies. If it is violated (a second backend process is genuinely still running an Analysis this routine can see), a still-`RUNNING` Analysis could be misclassified as interrupted.

If a discovered Analysis is no longer `RUNNING` by the time its own transition is attempted, `run_startup_recovery` skips it rather than aborting the remaining ones; this is defensive containment for one out-of-order transition, not a retry or re-validation mechanism.

## Startup wiring (G-04)

The formal issue states recovery "runs at backend process startup", but no application-startup or bootstrap entrypoint module exists anywhere in this repository — verified by inspection at implementation time: no `main`/bootstrap/startup module, and no console-script entry point declared in `pyproject.toml`. This issue's own deliverable is limited to `run_startup_recovery` as an independently callable function. Wiring an actual call to it into a real startup sequence is a later integration step this issue does not perform and does not invent a placeholder module for.

## Retry policy

**Decision: no automatic retry.** Once `run_startup_recovery` resolves an interrupted Analysis to `FAILED`, nothing in this codebase automatically re-queues or reprocesses it. The formal issue names "no automatic retry" as a sufficient, valid initial policy provided it is documented with its rationale; this is that documentation.

**Rationale:**

- A crash-interrupted Analysis's failure cause is, by definition, unknown at recovery time (the process that was running it no longer exists to report why). Automatically retrying it risks repeating whatever caused the crash, including a crash caused by the Analysis's own input.
- No automatic-retry mechanism, scheduler, or queue exists anywhere in this codebase yet; building one is a materially larger change than this issue's own scope (detect-and-resolve one Analysis at a time) authorizes.
- `FAILED` with a structured `interrupted` error is itself a fully informative, actionable outcome: a caller that wants to retry can create a new Analysis for the same work through the existing `AnalysisRepositoryPort.create` operation, using the ordinary caller-initiated path, rather than this routine reprocessing anything on its own.

This decision can be revisited by a future issue with evidence that automatic retry is needed; this routine's own contract (`run_startup_recovery` resolves every `RUNNING` Analysis it finds to `FAILED`, once, per invocation) would not need to change to add a retry mechanism layered on top of it.

## What this issue does not change

Per the formal issue's "Não inclui" and this issue's non-goals, this module does not: implement automatic retry beyond this documented policy; introduce a broker, distributed worker, remote storage, or distributed coordination mechanism; implement cancellation of in-progress analyses; add a new `AnalysisLifecycleState`, Core lifecycle transition, or `FailureCategory` member; or redefine the M3-03 orchestration/matching pipeline, the M4-01 lifecycle's existing states/transitions, the M4-03 repository's `create`/`get`/`transition` contract, or the M4-04 executor's claiming and completion behavior.

## Verification performed during implementation

Reduced, non-persisted verification exercised against a temporary SQLite-backed `SQLiteAnalysisRepository`:

- `list_by_state(AnalysisLifecycleState.RUNNING)` returns exactly the Analyses currently `RUNNING` and none in `QUEUED`, `SUCCEEDED`, or `FAILED`.
- `run_startup_recovery` transitions every `RUNNING` Analysis it discovers to `FAILED` with a structured error whose category is `FailureCategory.INTERNAL_FAILURE` and whose message matches `INTERRUPTED_ERROR_MESSAGE`.
- A `QUEUED` Analysis is left untouched by `run_startup_recovery` and remains claimable afterward.
- Calling `run_startup_recovery` when no Analysis is `RUNNING` returns an empty tuple and raises no error.

No raw runtime state, logs, or database contents were retained from this verification.
