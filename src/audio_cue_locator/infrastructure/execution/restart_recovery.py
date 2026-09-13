"""Infrastructure: the startup recovery routine (M4-05).

The M4-04 `LocalAnalysisExecutor` can be interrupted by a process crash or
restart while an Analysis is `RUNNING`
(`infrastructure/execution/local_analysis_executor.py`), but nothing
resolves an Analysis left in that state: no in-memory record of which
`analysis_id` values were `RUNNING` survives a crash, and the M4-03
`AnalysisRepositoryPort` (`application/ports/analysis_repository.py`)
previously exposed only `create`/`get`/`transition` keyed by a known
`analysis_id`, with no way to discover them
(`states/M4/M4-05/issue-operational-state.json`, gap G-01). This module
closes that gap: `run_startup_recovery` uses the port's `list_by_state`
operation (added alongside this module for exactly this purpose) to find
every Analysis persisted `RUNNING`, and resolves each through the existing
M4-01 `RUNNING`-to-`FAILED` transition with a structured `interrupted`
error, applied through the same `AnalysisRepositoryPort.transition()`
operation the M4-04 executor already uses for its own `FAILED` outcome.

Scope decisions this module makes, each resolving an unresolved gap left
open by the M4-05 issue operational state
(`states/M4/M4-05/issue-operational-state.json#/gaps`):

- **Target state (G-02).** Only `RUNNING` is treated as "interrupted". A
  `QUEUED` Analysis surviving a restart is not touched: it was never
  claimed, so it remains legitimately claimable by a future
  `LocalAnalysisExecutor.submit` call, and this routine does not query for
  it.
- **Resolution mechanism (G-03).** No new `AnalysisLifecycleState` or Core
  lifecycle transition is introduced. Every discovered `RUNNING` Analysis
  is transitioned to `FAILED` with a `StructuredError` whose category is
  the existing `FailureCategory.INTERNAL_FAILURE` (`core/analysis_result.py`)
  and whose message identifies the interruption explicitly.
  `FailureCategory` is documented in that module as a closed set shared
  with Application's identical projection; this routine reuses that
  existing member rather than adding a new one, exactly as
  `LocalAnalysisExecutor._claim_and_run` already does for its own
  shared-source-failure outcome.
- **Invocation timing.** This routine is designed to run exactly once,
  synchronously, before any `QUEUED` Analysis is claimed by a
  `LocalAnalysisExecutor` -- correctness depends on at most one backend
  process operating against the persisted state at a time (the milestone's
  no-horizontal-scaling assumption). `run_startup_recovery` does not
  itself enforce or re-derive that single-backend-process assumption; it
  is a precondition this routine relies on, not something it verifies.
- **Startup wiring (G-04).** No application-startup or bootstrap
  entrypoint module exists anywhere in this repository (verified by
  inspection at implementation time: no `main`/bootstrap/startup module or
  console-script entry point). This issue's own deliverable is therefore
  limited to `run_startup_recovery` as an independently callable function;
  wiring an actual call to it into a real startup sequence is a later
  integration step this module does not perform.
- **Retry policy.** No automatic retry of a recovered Analysis is
  implemented here; `docs/restart-and-recovery-policy.md` documents that
  decision and its rationale. This routine performs exactly one detect-
  and-resolve pass per invocation and never re-queues or reschedules
  anything.

Out of scope, matching the formal issue's own "Não inclui" and this
issue's non-goals: automatic retry beyond the documented policy; any
broker, distributed worker, remote storage, or distributed coordination;
cancellation of in-progress analyses; and any redefinition of the M3-03
orchestration/matching pipeline, the M4-01 lifecycle's existing
states/transitions, the M4-03 repository's create/get/transition contract,
or the M4-04 executor's claiming and completion behavior. This module
embeds no SQL and reaches persisted Analysis state exclusively through
`AnalysisRepositoryPort`.

`docs/restart-and-recovery-policy.md` documents this module's detection
mechanism, its resolution, the exactly-once-at-startup timing requirement,
and the retry-policy decision for human review.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRepositoryPort,
)
from audio_cue_locator.core.analysis_lifecycle import (
    AnalysisLifecycleState,
    InvalidLifecycleTransitionError,
)
from audio_cue_locator.core.analysis_result import FailureCategory, StructuredError

INTERRUPTED_ERROR_MESSAGE = (
    "interrupted: Analysis was left RUNNING by a prior process crash or "
    "restart; resolved to FAILED by the startup recovery routine"
)
"""Fixed `StructuredError.message` recorded for every Analysis this routine
resolves. `FailureCategory` has no dedicated "interrupted" member (it is a
closed set, `core/analysis_result.py`); this message is what actually
identifies the interruption, alongside the existing
`FailureCategory.INTERNAL_FAILURE` category."""


ClockFn = Callable[[], datetime]


def _default_clock() -> datetime:
    """Return the current instant as a timezone-aware UTC `datetime`,
    matching `LifecycleTimestamps`'s requirement that every recorded
    timestamp be timezone-aware."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RecoveredAnalysis:
    """One Analysis this routine found `RUNNING` and resolved to `FAILED`."""

    analysis_id: str
    structured_error: StructuredError


def run_startup_recovery(
    repository: AnalysisRepositoryPort,
    *,
    clock: ClockFn = _default_clock,
) -> tuple[RecoveredAnalysis, ...]:
    """Detect every Analysis persisted `RUNNING` and resolve each to
    `FAILED` with a structured `interrupted` error, through `repository`
    exclusively.

    Intended to be called exactly once, synchronously, before any
    `QUEUED` Analysis is claimed (see this module's docstring,
    "Invocation timing"). Returns the tuple of Analyses actually resolved,
    in the order `repository.list_by_state` returned them; returns an
    empty tuple if none were `RUNNING`, which is not an error.

    If a discovered Analysis is no longer `RUNNING` by the time its own
    transition is attempted (`repository.transition` raises
    `InvalidLifecycleTransitionError`), that Analysis is skipped rather
    than aborting the remaining ones: this can only happen if the
    single-backend-process precondition this routine relies on did not
    hold, and this routine does not retry or re-validate that case, only
    avoids letting one such Analysis prevent recovering the rest.
    """

    interrupted = repository.list_by_state(AnalysisLifecycleState.RUNNING)
    recovered: list[RecoveredAnalysis] = []
    for record in interrupted:
        structured_error = StructuredError(
            category=FailureCategory.INTERNAL_FAILURE,
            message=INTERRUPTED_ERROR_MESSAGE,
        )
        try:
            repository.transition(
                record.analysis_id,
                AnalysisLifecycleState.FAILED,
                at=clock(),
                structured_error=structured_error,
            )
        except InvalidLifecycleTransitionError:
            continue
        recovered.append(
            RecoveredAnalysis(
                analysis_id=record.analysis_id, structured_error=structured_error
            )
        )
    return tuple(recovered)
