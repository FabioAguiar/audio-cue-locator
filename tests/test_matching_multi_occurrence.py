"""Focused S0009 tests for deterministic multi-occurrence matching."""

from __future__ import annotations

import math

import numpy as np
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import (
    MAX_OCCURRENCES_PER_CUE,
    MULTI_OCCURRENCE_METHOD,
    EffectiveConfiguration,
    MatchOutcome,
    match_cue_occurrences,
)
from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
    EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION,
)


def _canonical(values) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def _configuration(threshold: float = 0.7) -> EffectiveConfiguration:
    return EffectiveConfiguration(
        method=MULTI_OCCURRENCE_METHOD,
        acceptance_threshold=threshold,
    )


def test_repeated_exact_occurrences_are_returned_chronologically():
    cue = _canonical([0.2, -0.8, 0.4, 0.9, -0.3, -0.6])
    source = np.zeros(30, dtype=np.float32)
    source[3:9] = cue
    source[19:25] = cue

    result = match_cue_occurrences(
        source, cue, EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION
    )

    assert result.outcome is MatchOutcome.FOUND
    assert [round(candidate.timestamp_seconds * 48_000) for candidate in result.candidates] == [
        3,
        19,
    ]
    assert [candidate.score for candidate in result.candidates] == pytest.approx(
        [1.0, 1.0], abs=1e-6
    )


def test_exact_and_lower_similarity_occurrences_are_both_returned():
    cue = _canonical([1.0, 1.0, -1.0, -1.0])
    orthogonal = _canonical([1.0, -1.0, -1.0, 1.0])
    target_score = 0.8
    theta = math.acos(target_score)
    perturbed = (
        math.cos(theta) * cue.astype(np.float64)
        + math.sin(theta) * orthogonal.astype(np.float64)
    ).astype(np.float32)
    source = np.zeros(24, dtype=np.float32)
    source[2:6] = cue
    source[15:19] = perturbed

    result = match_cue_occurrences(source, cue, _configuration(0.7))

    assert result.outcome is MatchOutcome.FOUND
    lags = [round(candidate.timestamp_seconds * 48_000) for candidate in result.candidates]
    assert lags == [2, 15]
    assert result.candidates[0].score == pytest.approx(1.0, abs=1e-6)
    assert 0.7 <= result.candidates[1].score < 1.0


def test_overlap_suppression_prefers_higher_score_then_earlier_equal_lag():
    source = np.ones(12, dtype=np.float32)
    cue = np.ones(4, dtype=np.float32)

    def _scores(_source, _cue):
        return np.array(
            [0.8, 0.9, 0.9, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],
            dtype=np.float64,
        )

    result = match_cue_occurrences(
        source, cue, _configuration(0.7), score_calculator=_scores
    )

    assert result.outcome is MatchOutcome.FOUND
    assert [round(candidate.timestamp_seconds * 48_000) for candidate in result.candidates] == [
        1
    ]
    assert result.candidates[0].score == 0.9


def test_touching_full_cue_windows_are_not_suppressed():
    source = np.ones(8, dtype=np.float32)
    cue = np.ones(4, dtype=np.float32)

    def _scores(_source, _cue):
        return np.array([0.9, 0.0, 0.0, 0.0, 0.8], dtype=np.float64)

    result = match_cue_occurrences(
        source, cue, _configuration(0.7), score_calculator=_scores
    )

    assert [round(candidate.timestamp_seconds * 48_000) for candidate in result.candidates] == [
        0,
        4,
    ]


def test_no_match_retains_earliest_best_diagnostic():
    source = np.ones(8, dtype=np.float32)
    cue = np.ones(3, dtype=np.float32)

    def _scores(_source, _cue):
        return np.array([0.1, 0.4, 0.4, 0.2, 0.3, 0.1], dtype=np.float64)

    result = match_cue_occurrences(
        source, cue, _configuration(0.7), score_calculator=_scores
    )

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.candidates == ()
    assert result.diagnostic_best_score == 0.4
    assert result.diagnostic_best_timestamp_seconds == pytest.approx(1 / 48_000)


def test_cap_selects_by_score_priority_and_returns_chronologically():
    cue_length = 2
    candidate_count = MAX_OCCURRENCES_PER_CUE + 1
    source = np.ones(candidate_count * cue_length, dtype=np.float32)
    cue = np.ones(cue_length, dtype=np.float32)

    def _scores(_source, _cue):
        scores = np.zeros(source.shape[0] - cue.shape[0] + 1, dtype=np.float64)
        scores[::cue_length] = 0.8
        scores[200] = 0.9
        return scores

    result = match_cue_occurrences(
        source, cue, _configuration(0.7), score_calculator=_scores
    )

    selected_lags = [
        round(candidate.timestamp_seconds * 48_000) for candidate in result.candidates
    ]
    assert len(selected_lags) == MAX_OCCURRENCES_PER_CUE
    assert selected_lags == sorted(selected_lags)
    assert 200 in selected_lags
    assert 198 not in selected_lags


def test_degenerate_and_failure_paths_remain_explicit():
    invalid = match_cue_occurrences(
        _canonical([1.0]), _canonical([]), _configuration()
    )
    shorter = match_cue_occurrences(
        _canonical([1.0]), _canonical([1.0, -1.0]), _configuration()
    )

    def _broken(_source, _cue):
        raise RuntimeError("simulated selection failure")

    failed = match_cue_occurrences(
        _canonical([1.0, -1.0]),
        _canonical([1.0]),
        _configuration(),
        score_calculator=_broken,
    )

    assert invalid.outcome is MatchOutcome.INVALID_INPUT
    assert shorter.outcome is MatchOutcome.NO_MATCH
    assert shorter.diagnostic_best_score is None
    assert failed.outcome is MatchOutcome.PROCESSING_FAILURE


def test_selection_is_deterministic_across_repeated_runs():
    cue = _canonical([0.2, -0.8, 0.4, 0.9, -0.3, -0.6])
    source = np.concatenate([cue, np.zeros(8, dtype=np.float32), cue])

    first = match_cue_occurrences(source, cue, _configuration())
    second = match_cue_occurrences(source.copy(), cue.copy(), _configuration())

    assert first == second
