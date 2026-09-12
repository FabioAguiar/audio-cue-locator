"""Tests for the M4-01 Core Analysis lifecycle state machine.

These validate the formal-issue acceptance criteria that can be checked
without SQLite persistence or a local executor (`docs/analysis-lifecycle-
state-machine.md`): every transition in the documented valid set succeeds;
every transition out of a terminal state (`SUCCEEDED`, `FAILED`) is
rejected, including a self-transition; every pair not in the valid set is
rejected; and `is_terminal`/`is_valid_transition` agree with `transition`.
"""

import itertools

import pytest

from audio_cue_locator.core.analysis_lifecycle import (
    AnalysisLifecycleState,
    InvalidLifecycleTransitionError,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    is_terminal,
    is_valid_transition,
    transition,
)

ALL_STATES = tuple(AnalysisLifecycleState)


@pytest.mark.parametrize("from_state,to_state", sorted(VALID_TRANSITIONS, key=str))
def test_transition_succeeds_for_every_documented_valid_transition(from_state, to_state):
    assert transition(from_state, to_state) == to_state
    assert is_valid_transition(from_state, to_state) is True


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        (from_state, to_state)
        for from_state, to_state in itertools.product(ALL_STATES, ALL_STATES)
        if (from_state, to_state) not in VALID_TRANSITIONS
    ],
)
def test_transition_rejects_every_pair_outside_the_valid_set(from_state, to_state):
    assert is_valid_transition(from_state, to_state) is False
    with pytest.raises(InvalidLifecycleTransitionError) as excinfo:
        transition(from_state, to_state)
    assert excinfo.value.from_state == from_state
    assert excinfo.value.to_state == to_state


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES, key=str))
def test_no_transition_originates_in_a_terminal_state(terminal_state):
    outgoing = {to_state for (from_state, to_state) in VALID_TRANSITIONS if from_state == terminal_state}
    assert outgoing == set()


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES, key=str))
def test_terminal_state_rejects_self_transition(terminal_state):
    assert is_valid_transition(terminal_state, terminal_state) is False
    with pytest.raises(InvalidLifecycleTransitionError):
        transition(terminal_state, terminal_state)


def test_is_terminal_matches_documented_terminal_states():
    assert is_terminal(AnalysisLifecycleState.SUCCEEDED) is True
    assert is_terminal(AnalysisLifecycleState.FAILED) is True
    assert is_terminal(AnalysisLifecycleState.QUEUED) is False
    assert is_terminal(AnalysisLifecycleState.RUNNING) is False


def test_valid_transitions_is_exactly_the_documented_three_pairs():
    assert VALID_TRANSITIONS == {
        (AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.RUNNING),
        (AnalysisLifecycleState.RUNNING, AnalysisLifecycleState.SUCCEEDED),
        (AnalysisLifecycleState.RUNNING, AnalysisLifecycleState.FAILED),
    }


def test_invalid_transition_error_message_names_both_states():
    with pytest.raises(InvalidLifecycleTransitionError) as excinfo:
        transition(AnalysisLifecycleState.QUEUED, AnalysisLifecycleState.SUCCEEDED)
    message = str(excinfo.value)
    assert "'queued'" in message
    assert "'succeeded'" in message
