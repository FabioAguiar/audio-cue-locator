"""Deterministic multi-occurrence normalized-correlation matching.

This additive matcher shares the historical baseline's score-vector helper,
but owns candidate acceptance, full-cue-window overlap suppression, and the
fixed per-Cue output bound associated with its versioned method identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from audio_cue_locator.infrastructure.acoustic_matching.baseline import (
    EffectiveConfiguration,
    MatchOutcome,
    _normalized_correlation_scores,
    _validate_preconditions,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

MULTI_OCCURRENCE_METHOD = "normalized_cross_correlation_multi_v1"
MAX_OCCURRENCES_PER_CUE = 100


@dataclass(frozen=True)
class MatchCandidate:
    """One accepted, non-overlapping candidate on the source timeline."""

    timestamp_seconds: float
    score: float


@dataclass(frozen=True)
class MultiMatchResult:
    """Result of one bounded multi-occurrence matching operation."""

    outcome: MatchOutcome
    configuration: EffectiveConfiguration
    candidates: tuple[MatchCandidate, ...] = ()
    reason: str | None = None
    diagnostic_best_timestamp_seconds: float | None = None
    diagnostic_best_score: float | None = None


def _select_non_overlapping_candidates(
    scores: np.ndarray,
    cue_length_samples: int,
    acceptance_threshold: float,
) -> tuple[tuple[int, float], ...]:
    """Select accepted candidates by score/lag priority, then chronology."""

    accepted_lags = np.flatnonzero(scores >= acceptance_threshold)
    prioritized = sorted(
        ((int(lag), float(scores[lag])) for lag in accepted_lags),
        key=lambda candidate: (-candidate[1], candidate[0]),
    )

    selected: list[tuple[int, float]] = []
    for lag, score in prioritized:
        candidate_end = lag + cue_length_samples
        if any(
            not (
                candidate_end <= selected_lag
                or selected_lag + cue_length_samples <= lag
            )
            for selected_lag, _ in selected
        ):
            continue
        selected.append((lag, score))
        if len(selected) == MAX_OCCURRENCES_PER_CUE:
            break

    return tuple(sorted(selected, key=lambda candidate: candidate[0]))


def match_cue_occurrences(
    source: np.ndarray,
    cue: np.ndarray,
    configuration: EffectiveConfiguration,
    *,
    score_calculator: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
) -> MultiMatchResult:
    """Return all selected occurrences of ``cue`` from one score-vector pass.

    Each lag is accepted independently with ``score >= threshold``. Accepted
    candidates are considered by descending score and then earlier lag;
    candidates whose full cue-length half-open windows overlap a previously
    selected window are suppressed. At most 100 candidates are returned, in
    chronological order.
    """

    if not (-1.0 <= configuration.acceptance_threshold <= 1.0):
        raise ValueError(
            "acceptance_threshold must be within [-1.0, 1.0] (normalized "
            "cross-correlation's score range); got "
            f"{configuration.acceptance_threshold!r}"
        )

    invalid_reason = _validate_preconditions(source, cue)
    if invalid_reason is not None:
        return MultiMatchResult(
            outcome=MatchOutcome.INVALID_INPUT,
            configuration=configuration,
            reason=invalid_reason,
        )

    if source.shape[0] < cue.shape[0]:
        return MultiMatchResult(
            outcome=MatchOutcome.NO_MATCH,
            configuration=configuration,
            reason="source is shorter than cue: no full-length candidate window exists",
        )

    calculate_scores = score_calculator or _normalized_correlation_scores
    try:
        scores = calculate_scores(source, cue)
        expected_score_count = source.shape[0] - cue.shape[0] + 1
        if not isinstance(scores, np.ndarray) or scores.shape != (
            expected_score_count,
        ):
            raise AssertionError(
                "score_calculator returned an invalid score-vector shape"
            )
        if not np.isfinite(scores).all():
            raise AssertionError("score_calculator returned non-finite scores")

        selected = _select_non_overlapping_candidates(
            scores,
            cue.shape[0],
            configuration.acceptance_threshold,
        )
        best_index = int(np.argmax(scores))
        best_score = float(scores[best_index])
    except Exception as exc:  # noqa: BLE001 - preserve explicit failure branch
        return MultiMatchResult(
            outcome=MatchOutcome.PROCESSING_FAILURE,
            configuration=configuration,
            reason=(
                "unexpected failure during multi-occurrence selection: "
                f"{exc}"
            ),
        )

    if not selected:
        return MultiMatchResult(
            outcome=MatchOutcome.NO_MATCH,
            configuration=configuration,
            diagnostic_best_timestamp_seconds=(
                best_index / CANONICAL_AUDIO_SPEC.sample_rate_hz
            ),
            diagnostic_best_score=best_score,
        )

    return MultiMatchResult(
        outcome=MatchOutcome.FOUND,
        configuration=configuration,
        candidates=tuple(
            MatchCandidate(
                timestamp_seconds=lag / CANONICAL_AUDIO_SPEC.sample_rate_hz,
                score=score,
            )
            for lag, score in selected
        ),
    )
