"""Core: the Analysis lifecycle state machine and its complete set of valid
transitions (M4-01).

M4 requires a SQLite-backed Analysis Repository (M4-03) and a Local
Analysis Executor (M4-04) to both apply lifecycle transitions consistently,
but per the architecture's Principle 1 the rules governing those
transitions must not be defined inside SQLite or the executor, or the two
could silently diverge on what is a valid transition
(`issues/M4/M4-01/formal-issue.json`, sections 2-3). This module closes
that gap: it defines `AnalysisLifecycleState`, the complete
`VALID_TRANSITIONS` set, and `InvalidLifecycleTransitionError`, so M4-03
and M4-04 can validate and apply transitions against one authoritative
Core contract instead of each re-implementing lifecycle rules
independently.

The four states already appear conceptually in `docs/analysis-core-
contracts.md` (M3-01), section 4 ("Estados de Lifecycle da `Analysis`"),
without persistence, scheduling, or cancellation semantics. This module
does not redefine those four states or any other contract fixed by that
document (`Analysis`, `Cue`, `Occurrence`, `PerCueOutcome`); it adds the
executable transition-validation layer that document explicitly leaves to
a later issue. `docs/analysis-lifecycle-state-machine.md` documents this
module's states, transitions, and invariants in prose for human review.

This module is additive Core: it does not import, and is not imported by,
`src/audio_cue_locator/core/analysis_result.py` (M3-06); the two Core
modules are independent artifacts that may both describe an `Analysis`
without one depending on the other. Like every other Core module, it
imports nothing beyond the Python standard library -- no SQLite, no
filesystem, no executor-specific type (`docs/architecture.md`, Principio
1: "O Core nao conhece interfaces externas").
"""

from __future__ import annotations

from enum import Enum


class AnalysisLifecycleState(str, Enum):
    """The four Analysis lifecycle states (`docs/analysis-core-
    contracts.md`, section 4; `docs/architecture.md`, "Analysis"). This is
    a closed enumeration: no fifth state (for example, cancellation) is
    added here, mirroring the architecture's explicit decision that
    "Cancelamento nao faz parte do contrato inicial"."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATES: frozenset[AnalysisLifecycleState] = frozenset(
    {AnalysisLifecycleState.SUCCEEDED, AnalysisLifecycleState.FAILED}
)
"""States that accept no outgoing transition (terminal-state finality,
formal issue acceptance criterion 2). Membership here, not a per-call
special case, is what `is_valid_transition` and `transition` consult, so
the invariant cannot be bypassed by adding a transition elsewhere without
also updating this set."""


VALID_TRANSITIONS: frozenset[tuple[AnalysisLifecycleState, AnalysisLifecycleState]] = frozenset(
    {
        (AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.RUNNING),
        (AnalysisLifecycleState.RUNNING, AnalysisLifecycleState.SUCCEEDED),
        (AnalysisLifecycleState.RUNNING, AnalysisLifecycleState.FAILED),
    }
)
"""The complete set of valid Analysis lifecycle transitions (formal issue
acceptance criterion 1; `docs/analysis-core-contracts.md`, section 4).
No transition originates in `SUCCEEDED` or `FAILED` (terminal-state
finality, including no self-transition); every pair not listed here,
between these four states or from/to any of them, is invalid.

Reserved for a future issue (M4-05): a restart/interruption-related
transition (for example, an `Analysis` left `RUNNING` when the executor is
interrupted) is intentionally not defined here. This set, and the states
above, are the extension point: M4-05 must add to `VALID_TRANSITIONS`
(and, if warranted, to `AnalysisLifecycleState`) rather than this module
guessing that transition's shape now (`issues/M4/M4-01/formal-issue.json`,
section 4, "Restricoes"; acceptance criterion 4)."""


class InvalidLifecycleTransitionError(ValueError):
    """Raised by `transition` when `(from_state, to_state)` is not in
    `VALID_TRANSITIONS` -- including any transition attempted from a
    terminal state (formal issue acceptance criterion 2)."""

    def __init__(
        self, from_state: AnalysisLifecycleState, to_state: AnalysisLifecycleState
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            "invalid Analysis lifecycle transition: "
            f"{from_state.value!r} -> {to_state.value!r}"
        )


def is_terminal(state: AnalysisLifecycleState) -> bool:
    """Return whether `state` accepts no outgoing transition."""

    return state in TERMINAL_STATES


def is_valid_transition(
    from_state: AnalysisLifecycleState, to_state: AnalysisLifecycleState
) -> bool:
    """Return whether `from_state -> to_state` is one of the transitions in
    `VALID_TRANSITIONS`. Never true when `from_state` is terminal, because
    no terminal-state pair is a member of that set."""

    return (from_state, to_state) in VALID_TRANSITIONS


def transition(
    from_state: AnalysisLifecycleState, to_state: AnalysisLifecycleState
) -> AnalysisLifecycleState:
    """Return `to_state` if `from_state -> to_state` is valid; otherwise
    raise `InvalidLifecycleTransitionError`. This function does not mutate
    any state itself -- it is a pure validation/projection step that a
    future caller (the M4-03 repository adapter or the M4-04 executor)
    uses before persisting or acting on a new state; this module owns no
    persistence or execution of its own."""

    if not is_valid_transition(from_state, to_state):
        raise InvalidLifecycleTransitionError(from_state, to_state)
    return to_state
