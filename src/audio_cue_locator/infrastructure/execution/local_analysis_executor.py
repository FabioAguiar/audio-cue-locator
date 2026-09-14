"""Infrastructure: the Local Analysis Executor (M4-04).

M4's objective requires an Analysis to be processed independently of the
caller's connection lifetime, but Application only exposes an orchestration
(M3-03's `run_multi_cue_analysis`) that runs synchronously while a caller
waits, and the M4-03 Analysis Repository persists lifecycle state without
anything that claims a `QUEUED` Analysis, drives it to completion, or bounds
how many analyses run at once. This module closes that gap: it defines
`LocalAnalysisExecutor`, which claims one persisted `QUEUED` Analysis by
transitioning it to `RUNNING` exclusively through the M4-03
`AnalysisRepositoryPort`, invokes the existing M3-03 orchestration unmodified
exactly once, and persists exactly one of `SUCCEEDED`-with-result or
`FAILED`-with-structured-error back through that same port, all bounded by
an injected `concurrent.futures.Executor` (`docs/architecture.md`, "Local
Analysis Executor": "concorrência limitada").

This module resolves the API-shape gaps the controlled context (`context-
packs/M4/M4-04/issue-analysis.json`) deliberately left open, in the same
spirit `multi_cue_orchestration.py` (M3-03) resolved its own G1/G2/G7:

- G-01 (claiming/discovery mechanism): the M4-03 `AnalysisRepositoryPort`
  exposes only `create`/`get`/`transition` keyed by a known `analysis_id`,
  with no operation to discover or list which analyses are `QUEUED`
  (confirmed by inspecting that port at handoff and implementation time).
  Rather than adding a discovery/list operation to that port -- which this
  issue's authorized scope does not include -- `LocalAnalysisExecutor.submit`
  takes a caller-supplied `analysis_id`: the caller (whatever component
  creates or schedules analyses) is responsible for knowing which
  `analysis_id` values exist, exactly as the formal issue's own recommended
  resolution for this gap allows ("have a caller supply analysis_id values
  from elsewhere").
- Asset resolution and canonicalization: `docs/architecture.md`'s "Fluxo
  interno de uma análise" narrative names "resolve mídia e cues" and
  "canonicaliza source/cues" as later steps of one Analysis's full internal
  flow, but this issue's own controlled context never lists the Asset
  Storage adapter, the FFmpeg media-processing adapter, or the canonical
  audio spec among its files to consult, read, or create, and the formal
  issue's acceptance criteria never name asset resolution or canonicalization
  as this issue's own deliverable. `LocalAnalysisExecutor.submit` therefore
  takes already-canonicalized `source`/`cues` arrays directly, mirroring
  `run_multi_cue_analysis`'s own parameterization exactly, and does not read
  or depend on `core.asset`, `infrastructure.asset_storage`, or
  `infrastructure.media_processing`. Wiring asset-identifier resolution and
  canonicalization ahead of a claimed Analysis remains an open integration
  step for a future issue to authorize explicitly.
- G-02 (concurrency-limit mechanism): bounded by a `concurrent.futures.
  Executor` supplied by the caller (`worker_pool`), or a `ThreadPoolExecutor`
  built from `max_concurrency` by default. No configuration file or
  environment-variable convention exists yet anywhere in this codebase
  (verified by inspection), so this module does not invent one; a future
  issue that introduces project-wide configuration may wire `max_concurrency`
  to it without changing this module's contract.
- G-03 (thread/process/worker technology): deliberately not chosen here.
  `concurrent.futures.Executor` is the stdlib abstraction both
  `ThreadPoolExecutor` and `ProcessPoolExecutor` already implement, so a
  future benchmark can swap the default without any change to
  `LocalAnalysisExecutor` itself (formal issue acceptance criterion 5;
  operational-state risk R-01). A `ProcessPoolExecutor` additionally
  requires a picklable `repository`/`result_store`, a constraint left for
  that future benchmark to evaluate, not assumed here.
- Result-body storage: `AnalysisRecord.result_reference`
  (`application/ports/analysis_repository.py`) is "an opaque pointer to an
  externally serialized `AnalysisResult`... this repository never stores or
  interprets the Result body itself." No result-storage port exists yet and
  adding a durable one is outside this issue's two authorized paths, so this
  module defines the minimal `ResultReferenceStore` protocol it needs and a
  non-durable `InMemoryResultReferenceStore` default. This default is
  explicitly a placeholder, not a durability decision: it keeps this issue
  local and dependency-free (no broker, distributed worker, remote storage,
  or object storage, per the formal issue's "Não inclui"), and a future
  issue may supply a different `ResultReferenceStore` without changing this
  module's contract.
- Claim atomicity: relies exclusively on the M4-03 repository's own atomic
  `transition` validation. A rejected `QUEUED`-to-`RUNNING` transition
  raises `AnalysisAlreadyClaimedError` rather than retrying or introducing a
  second, independent locking mechanism (operational-state risk R-05;
  decision "a rejected QUEUED-to-RUNNING transition is treated as 'already
  claimed by another worker' rather than triggering new locking logic").

Per-cue outcome adaptation: `run_multi_cue_analysis` returns Application's
own `PerCueOutcome` union (`CueOccurrences`/`CueNoMatch`/`CueFailure` in
`multi_cue_orchestration.py`), while `core.analysis_result.AnalysisResult`
(M3-06) requires its own, independently declared `CueOutcome` union of the
same shape. `core/analysis_result.py`'s own docstring names adapting one
into the other, and attaching a result to a full Analysis lifecycle, as
"deliberately left to a future integration issue" -- this module is that
integration point, and `_to_core_cue_outcome` performs exactly that
adaptation without recomputing or reinterpreting any matched value.

Out of scope, matching the formal issue's own "Não inclui" and this issue's
non-goals: choosing a final thread/process/worker technology; any broker,
distributed worker, remote storage, or object storage; recovery/restart
policy for an Analysis interrupted mid-execution (a future M4-05-style
draft); REST/WebUI exposure; Analysis cancellation; and any redefinition of
the M4-01 lifecycle vocabulary, the M4-03 repository contract, or the M3-03
orchestration/matching pipeline. This module embeds no SQL and reaches
persisted Analysis state exclusively through `AnalysisRepositoryPort`.

`docs/local-analysis-executor.md` documents this module's claiming
mechanism, concurrency-limit mechanism, and scope decisions for human
review.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

import numpy as np

from audio_cue_locator.application.multi_cue_orchestration import (
    CueFailure as AppCueFailure,
)
from audio_cue_locator.application.multi_cue_orchestration import (
    CueNoMatch as AppCueNoMatch,
)
from audio_cue_locator.application.multi_cue_orchestration import (
    CueOccurrences as AppCueOccurrences,
)
from audio_cue_locator.application.multi_cue_orchestration import (
    PerCueOutcome,
    run_multi_cue_analysis,
)
from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    AnalysisRepositoryPort,
)
from audio_cue_locator.core.analysis_lifecycle import (
    AnalysisLifecycleState,
    InvalidLifecycleTransitionError,
)
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CueFailure,
    CueNoMatch,
    CueOccurrences,
    CueOutcome,
    CueResult,
    FailureCategory,
    Occurrence,
    StructuredError,
    serialize_analysis_result,
)
from audio_cue_locator.infrastructure.acoustic_matching import EffectiveConfiguration
from audio_cue_locator.observability import emit_diagnostic_event

_SAFE_MATCHING_STAGE_FAILURE_MESSAGE = (
    "The matching stage failed due to an unexpected internal error."
)
"""The only message text ever persisted for a matching-stage failure
(M7-04). Replaces this module's previous `f"{type(exc).__name__}: {exc}"`,
which persisted, and then leaked through `interfaces.rest_api.schemas.
analysis_record_to_public` to the public REST API and WebUI, the caught
exception's own raw text verbatim
(`states/M7/M7-04/issue-operational-state.json#/risks/0`). `schemas.py`
also independently redacts `structured_error.message` before any value --
including this one -- reaches a client, so this fix and that one are
deliberate defense-in-depth, not alternatives."""

DEFAULT_MAX_CONCURRENCY = 4
"""Provisional in-process concurrency bound used only to size the default
`ThreadPoolExecutor` when a caller supplies neither `worker_pool` nor a
different `max_concurrency`. This value is not derived from any benchmark
(formal issue acceptance criterion 5 explicitly defers that choice); it
exists so `LocalAnalysisExecutor` has *some* enforced, finite bound out of
the box rather than an unbounded default, and a caller with benchmark
evidence should override it or supply their own `worker_pool` entirely."""


ClockFn = Callable[[], datetime]


def _default_clock() -> datetime:
    """Return the current instant as a timezone-aware UTC `datetime`,
    matching `LifecycleTimestamps`'s requirement that every recorded
    timestamp be timezone-aware."""

    return datetime.now(timezone.utc)


def _to_duration_ms(duration_seconds: float | None) -> float | None:
    """Convert `LifecycleTimestamps.duration_seconds`'s seconds-based
    result to the milliseconds `observability.events.emit_diagnostic_event`
    expects, preserving `None` (M7-04)."""

    return duration_seconds * 1000.0 if duration_seconds is not None else None


@runtime_checkable
class ResultReferenceStore(Protocol):
    """The minimal capability `LocalAnalysisExecutor` needs to turn a
    completed `AnalysisResult` into the opaque `result_reference` string
    `AnalysisRepositoryPort.transition` persists. Like
    `AnalysisRepositoryPort` itself, no method here accepts or exposes a
    storage mechanism; a caller may supply any implementation (filesystem,
    object storage, a future dedicated result-storage adapter) without
    `LocalAnalysisExecutor` changing."""

    def save(self, analysis_id: str, result: AnalysisResult) -> str:
        """Durably record `result` and return an opaque reference string
        `AnalysisRepositoryPort.transition` can persist as
        `result_reference`. Never raises for a well-formed `result`."""

        ...


class InMemoryResultReferenceStore:
    """Non-durable Result writer and read-side adapter.

    Keeps serialized results in an in-process, thread-safe dict keyed by a
    generated reference.  ``save`` satisfies ``ResultReferenceStore`` for
    execution, while ``read`` structurally satisfies Application's
    ``ResultReferenceReaderPort`` without making Application depend on this
    Infrastructure type.

    This is explicitly a placeholder, not a durability decision: this issue
    authorizes no new persistent storage path or adapter, and introducing
    one (a file, a table, an object store) is out of scope here (formal
    issue "Não inclui": no broker, distributed worker, remote storage, or
    object storage). Results stored here do not survive process restart and
    are only reachable through this same instance; a caller that needs
    durable, cross-process result storage must supply its own
    `ResultReferenceStore`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._serialized_results: dict[str, str] = {}

    def save(self, analysis_id: str, result: AnalysisResult) -> str:
        serialized = serialize_analysis_result(result)
        reference = f"in-memory-analysis-result:{analysis_id}:{uuid4()}"
        with self._lock:
            self._serialized_results[reference] = serialized
        return reference

    def read(self, reference: str) -> str:
        """Return the canonical JSON string previously `save`d under
        `reference`.

        A missing opaque reference raises ``KeyError``; the Application query
        boundary converts that adapter detail into a sanitized internal
        integrity failure before it reaches transport.
        """

        with self._lock:
            return self._serialized_results[reference]


class AnalysisAlreadyClaimedError(RuntimeError):
    """Raised by `LocalAnalysisExecutor` when a `QUEUED`-to-`RUNNING`
    transition is rejected by the M4-03 repository's own atomic validation.

    This is the expected, named outcome of two workers racing to claim the
    same Analysis, or of a caller submitting an `analysis_id` that is not
    currently `QUEUED` -- never a signal to retry or to add a second,
    independent locking mechanism around claiming (operational-state risk
    R-05). The original `InvalidLifecycleTransitionError` is preserved as
    `__cause__` for callers that need the rejected `(from_state, to_state)`
    pair.
    """

    def __init__(self, analysis_id: str, cause: InvalidLifecycleTransitionError) -> None:
        self.analysis_id = analysis_id
        super().__init__(
            f"Analysis {analysis_id!r} could not be claimed; it is not "
            f"currently QUEUED or was already claimed by another worker: {cause}"
        )
        self.__cause__ = cause


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one `LocalAnalysisExecutor` claim-and-run attempt that
    reached a terminal lifecycle state. Never constructed for a rejected
    claim -- that path raises `AnalysisAlreadyClaimedError` instead."""

    analysis_id: str
    final_state: AnalysisLifecycleState
    result_reference: str | None = None
    structured_error: StructuredError | None = None


def _to_infrastructure_configuration(record: AnalysisRecord) -> EffectiveConfiguration:
    """Project the Core `EffectiveConfigurationSnapshot.matching` already
    attached to `record` into the Infrastructure `EffectiveConfiguration`
    `run_multi_cue_analysis` requires. The two types are declared
    independently but are structurally identical by design
    (`application/ports/analysis_repository.py` docstring); this function
    performs no re-derivation or default substitution, only field-by-field
    projection, so the executor never reads a matching configuration from
    anywhere but the claimed Analysis's own persisted snapshot."""

    matching = record.effective_configuration.matching
    return EffectiveConfiguration(
        method=matching.method,
        acceptance_threshold=matching.acceptance_threshold,
    )


def _to_core_cue_outcome(outcome: PerCueOutcome) -> CueOutcome:
    """Adapt one Application `PerCueOutcome` (`multi_cue_orchestration.py`)
    into the structurally equivalent Core `CueOutcome`
    (`core/analysis_result.py`), performing no recomputation of any matched
    value -- see this module's docstring, "Per-cue outcome adaptation"."""

    if isinstance(outcome, AppCueOccurrences):
        return CueOccurrences(
            occurrences=tuple(
                Occurrence(
                    cue_id=occurrence.cue_id,
                    temporal_position=occurrence.temporal_position,
                    score=occurrence.score,
                    matching_method=occurrence.matching_method,
                    end=occurrence.end,
                )
                for occurrence in outcome.occurrences
            )
        )
    if isinstance(outcome, AppCueNoMatch):
        return CueNoMatch()
    if isinstance(outcome, AppCueFailure):
        return CueFailure(
            category=FailureCategory(outcome.category.value),
            message=outcome.message,
        )
    raise TypeError(f"unknown PerCueOutcome variant: {type(outcome)!r}")


def _build_analysis_result(
    record: AnalysisRecord, per_cue: Mapping[str, PerCueOutcome]
) -> AnalysisResult:
    """Build the versioned M3-06 `AnalysisResult` for a completed run.

    `structured_error` is always `None` here: a per-cue `CueFailure` does
    not, by itself, make the Analysis-level result `failed`
    (`core/analysis_result.py`, `AnalysisResult.final_state` docstring) --
    only a shared-source failure that prevents `run_multi_cue_analysis`
    from returning at all does, and that path never reaches this function
    (see `LocalAnalysisExecutor._claim_and_run`)."""

    return AnalysisResult(
        analysis_id=record.analysis_id,
        method=record.effective_configuration.matching.method,
        configuration=record.effective_configuration,
        cues=tuple(
            CueResult(cue_id=cue_id, outcome=_to_core_cue_outcome(outcome))
            for cue_id, outcome in per_cue.items()
        ),
        structured_error=None,
    )


class LocalAnalysisExecutor:
    """Claims one persisted `QUEUED` Analysis at a time and drives it to a
    terminal state, exclusively through `AnalysisRepositoryPort`, under a
    bounded `concurrent.futures.Executor`.

    Usage:

        executor = LocalAnalysisExecutor(repository)
        future = executor.submit(analysis_id, source, cues)
        outcome = future.result()  # raises AnalysisAlreadyClaimedError if
                                    # analysis_id was not claimable

    `submit` never blocks the caller: it hands `_claim_and_run` to the
    injected `worker_pool` and returns immediately, matching the
    architecture's "isolamento do event loop HTTP" expectation. Concurrency
    is bounded by that pool's own `max_workers` -- an already-enforced
    stdlib guarantee, not merely a documented intention (formal issue
    acceptance criterion 4).
    """

    def __init__(
        self,
        repository: AnalysisRepositoryPort,
        *,
        worker_pool: Executor | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        result_store: ResultReferenceStore | None = None,
        clock: ClockFn = _default_clock,
    ) -> None:
        """`max_concurrency` is only consulted when `worker_pool` is
        omitted, to size the default `ThreadPoolExecutor`; a caller-supplied
        `worker_pool` is used exactly as given, including its own
        concurrency bound. `LocalAnalysisExecutor` owns and will `shutdown`
        a pool it built itself, but never shuts down a caller-supplied
        `worker_pool`, since the caller may share it with other work."""

        if max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")

        self._repository = repository
        self._owns_worker_pool = worker_pool is None
        self._worker_pool: Executor = worker_pool or ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="local-analysis-executor",
        )
        self._result_store: ResultReferenceStore = (
            result_store or InMemoryResultReferenceStore()
        )
        self._clock = clock

    def submit(
        self,
        analysis_id: str,
        source: np.ndarray,
        cues: Mapping[str, np.ndarray],
    ) -> Future[ExecutionOutcome]:
        """Submit `analysis_id` to be claimed and run on the bounded worker
        pool. `source` and every value in `cues` must already satisfy
        `CANONICAL_AUDIO_SPEC`, exactly as `run_multi_cue_analysis` itself
        requires; this method performs no asset resolution or
        canonicalization of its own (see this module's docstring).

        Returns immediately with a `Future[ExecutionOutcome]`. Calling
        `.result()` on it raises `AnalysisAlreadyClaimedError` if
        `analysis_id` was not `QUEUED` at claim time, or propagates
        `AnalysisNotFoundError` (`application/ports/analysis_repository.py`)
        unchanged if no such Analysis is persisted at all.
        """

        return self._worker_pool.submit(
            self._claim_and_run, analysis_id, source, cues
        )

    def shutdown(self, *, wait: bool = True) -> None:
        """Shut down the worker pool this executor built for itself. Does
        nothing if a `worker_pool` was supplied at construction time --
        that pool's lifecycle belongs to its caller, not to this executor."""

        if self._owns_worker_pool:
            self._worker_pool.shutdown(wait=wait)

    def _claim_and_run(
        self,
        analysis_id: str,
        source: np.ndarray,
        cues: Mapping[str, np.ndarray],
    ) -> ExecutionOutcome:
        """Claim `analysis_id`, run it, and persist its terminal state.

        No processing occurs unless the `QUEUED`-to-`RUNNING` transition
        below succeeds first (formal issue acceptance criterion 1; explicit
        failure mode, section 10: "The executor runs an Analysis without
        first claiming it through the repository..."). `run_multi_cue_
        analysis` is invoked exactly once, unmodified, per claimed Analysis
        (acceptance criterion 2)."""

        try:
            record = self._repository.transition(
                analysis_id,
                AnalysisLifecycleState.RUNNING,
                at=self._clock(),
            )
        except InvalidLifecycleTransitionError as exc:
            raise AnalysisAlreadyClaimedError(analysis_id, exc) from exc

        try:
            per_cue_outcomes = run_multi_cue_analysis(
                source, cues, _to_infrastructure_configuration(record)
            )
        except Exception as exc:
            # A shared-source precondition failure (or any other exception
            # `run_multi_cue_analysis` propagates) is an Analysis-scoped
            # failure, distinct from a per-cue CueFailure: it prevented the
            # whole run, so it becomes the Analysis's own structured_error
            # rather than a fabricated per-cue outcome (acceptance
            # criterion 3). The persisted message is a fixed, safe
            # constant (M7-04) -- never the caught exception's own raw
            # text (see `_SAFE_MATCHING_STAGE_FAILURE_MESSAGE`'s docstring).
            structured_error = StructuredError(
                category=FailureCategory.INTERNAL_FAILURE,
                message=_SAFE_MATCHING_STAGE_FAILURE_MESSAGE,
            )
            failed_record = self._repository.transition(
                analysis_id,
                AnalysisLifecycleState.FAILED,
                at=self._clock(),
                structured_error=structured_error,
            )
            emit_diagnostic_event(
                event="matching_stage_failed",
                boundary="executor",
                outcome="failed",
                category=type(exc).__name__,
                analysis_id=analysis_id,
                duration_ms=_to_duration_ms(
                    failed_record.lifecycle_timestamps.duration_seconds(
                        AnalysisLifecycleState.FAILED
                    )
                ),
            )
            return ExecutionOutcome(
                analysis_id=analysis_id,
                final_state=failed_record.state,
                structured_error=structured_error,
            )

        analysis_result = _build_analysis_result(record, per_cue_outcomes)
        result_reference = self._result_store.save(analysis_id, analysis_result)
        succeeded_record = self._repository.transition(
            analysis_id,
            AnalysisLifecycleState.SUCCEEDED,
            at=self._clock(),
            result_reference=result_reference,
        )
        emit_diagnostic_event(
            event="analysis_execution_succeeded",
            boundary="executor",
            outcome="succeeded",
            analysis_id=analysis_id,
            duration_ms=_to_duration_ms(
                succeeded_record.lifecycle_timestamps.duration_seconds(
                    AnalysisLifecycleState.SUCCEEDED
                )
            ),
        )
        return ExecutionOutcome(
            analysis_id=analysis_id,
            final_state=succeeded_record.state,
            result_reference=result_reference,
        )
