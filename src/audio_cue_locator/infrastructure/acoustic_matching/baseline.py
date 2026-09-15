"""Single-cue matching baseline: normalized cross-correlation (M2-02).

Implements the first NumPy-based single-cue matcher for Acoustic Matching
(docs/architecture.md, "Acoustic Matching"), against the internal contract
already fixed by M2-01 (docs/matching-contract.md): source and cue must
already satisfy CANONICAL_AUDIO_SPEC (M1-03) before this module is invoked;
this module does not canonicalize audio and does not redefine that contract.

Method: for each possible cue-length window of the canonicalized source, the
score is the amplitude-normalized correlation between that window and the
cue,

    score(lag) = dot(source[lag : lag + len(cue)], cue)
                 / (||source[lag : lag + len(cue)]|| * ||cue||)

a value in [-1.0, 1.0]: 1.0 is a perfect positive match (identical shape and
relative amplitude at that lag), 0.0 is no linear correlation, and -1.0 is a
perfect phase-inverted match. This is method-specific similarity, not
calibrated confidence (docs/matching-contract.md, section 4). The raw
correlation is computed with numpy.correlate's direct ("valid" mode)
definition rather than an FFT-accelerated method: this keeps the reference
implementation easy to verify by hand and review, at the cost of O(len(
source) * len(cue)) time; FFT-based or windowed-search performance
optimization is explicitly out of scope for this baseline (formal issue
M2-02, section 4, "Não inclui") and may be revisited later if a measured
need arises.

The best candidate is the lag with the highest score; converted to seconds
against CANONICAL_AUDIO_SPEC.sample_rate_hz, it is the best-candidate
timestamp (docs/matching-contract.md, section 3: origin 0 of the
canonicalized source, non-negative seconds, bounded by the source's
duration).

`found` vs `no_match` uses EffectiveConfiguration.acceptance_threshold, a
simple, explicit, non-calibrated cutoff for this baseline only. This is NOT
the evidence-based acceptance policy reserved for M2-05
(docs/matching-contract.md, sections 4 and 7): no threshold calibration,
empirical precision measurement, or confidence calibration is performed or
claimed by this module.

Degenerate inputs are handled deterministically and are never silently
converted into an undocumented outcome (docs/matching-contract.md, section
5; formal issue M2-02, sections 8 and 10):

- a non-array, wrong-dtype, wrong-shape, non-finite, or empty cue is
  `invalid_input` -- a precondition violation before matching starts
  (docs/matching-contract.md, section 1, lists non-canonicalized audio and
  an empty cue explicitly);
- a source shorter than the cue is `no_match`, not `invalid_input`:
  docs/matching-contract.md section 1 explicitly allows source duration to
  be less than the cue's, so this is a valid canonicalized input for which
  no full-length candidate window exists;
- a silent (zero-energy) cue, or a silent source window, makes the
  normalized-correlation score mathematically undefined (0/0) for that
  comparison; this baseline deterministically defines that undefined score
  as 0.0 ("no correlation"), the same convention used for both cases, which
  is never above a non-negative acceptance_threshold and therefore
  deterministically resolves to `no_match`, not a crash or a silent
  fabricated `found`;
- an unexpected failure during the correlation computation itself, or an
  internally inconsistent (out-of-bounds) candidate index, is
  `processing_failure`, distinct from both `invalid_input` and `no_match`.

docs/matching-baseline.md documents this behavior for human review.

All numerical operations stay within this Infrastructure module; no NumPy
type or algorithm detail is exposed to Core or Application
(docs/architecture.md, Princípios e Restrições #1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np

from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_ENERGY_EPSILON = 1e-9
"""Minimum sum-of-squares energy for a window/cue to be treated as non-silent.

Below this threshold, normalized correlation is mathematically undefined
(division by ~zero); see _locate_best_candidate, which deterministically
scores such comparisons as 0.0 rather than raising or dividing by zero.
"""


class MatchOutcome(str, Enum):
    """The four explicitly distinct result alternatives (docs/matching-contract.md, section 5)."""

    FOUND = "found"
    NO_MATCH = "no_match"
    INVALID_INPUT = "invalid_input"
    PROCESSING_FAILURE = "processing_failure"


@dataclass(frozen=True)
class EffectiveConfiguration:
    """Method identity and parameters associated with a match result.

    docs/matching-contract.md, section 2, requires every result to carry a
    stable method identifier and its effective configuration; it does not
    fix the method, its parameters, or any threshold value.
    """

    method: str
    acceptance_threshold: float


DEFAULT_CONFIGURATION = EffectiveConfiguration(
    method="normalized_cross_correlation_v1",
    acceptance_threshold=0.75,
)
"""This baseline's default configuration.

acceptance_threshold is a simple, explicit, non-calibrated cutoff used only
to let this baseline distinguish `found` from `no_match` for its own focused
checks; it is not, and does not anticipate, the evidence-based acceptance
policy reserved for M2-05 (docs/matching-contract.md, sections 4 and 7).
"""


@dataclass(frozen=True)
class MatchResult:
    """Single-cue matcher result (docs/matching-contract.md, section 5).

    Exactly one MatchOutcome is set. `timestamp_seconds` and `score` are
    populated only for `found`. `diagnostic_best_score` and
    `diagnostic_best_timestamp_seconds` are populated only for the `no_match`
    case where a candidate was actually evaluated and scored below the
    acceptance threshold -- the contract's optional best-candidate retention
    for diagnostics (section 5.2); they do not make a `no_match` result
    equivalent to `found`, and are left unset when no candidate window could
    be evaluated at all (source shorter than cue). `reason` is populated for
    `invalid_input` and `processing_failure`, and for the source-shorter-
    than-cue `no_match` case, to make the degenerate handling explicit.
    """

    outcome: MatchOutcome
    configuration: EffectiveConfiguration
    timestamp_seconds: float | None = None
    score: float | None = None
    reason: str | None = None
    diagnostic_best_timestamp_seconds: float | None = None
    diagnostic_best_score: float | None = None


def _validate_preconditions(source: np.ndarray, cue: np.ndarray) -> str | None:
    """Return an invalid_input reason, or None if the shared preconditions hold.

    Checks exactly what docs/matching-contract.md section 1 requires before
    matching may begin: source and cue must already be canonicalized (a
    1-D, float32, finite-valued array, per CANONICAL_AUDIO_SPEC), and the
    cue must be non-empty. It does not check the source/cue length
    relationship or cue/window energy: those are valid canonicalized inputs
    this baseline resolves deterministically to `no_match`, not
    `invalid_input` (module docstring).
    """

    for label, array in (("source", source), ("cue", cue)):
        if not isinstance(array, np.ndarray):
            return f"{label} must be a numpy.ndarray instance"
        if array.ndim != 1:
            return (
                f"{label} must be a 1-D (mono) array, per "
                f"CANONICAL_AUDIO_SPEC.channels={CANONICAL_AUDIO_SPEC.channels}"
            )
        if array.dtype != np.float32:
            return (
                f"{label} must have dtype float32, per "
                f"CANONICAL_AUDIO_SPEC.sample_format={CANONICAL_AUDIO_SPEC.sample_format!r}"
            )
        if not np.isfinite(array).all():
            return f"{label} contains non-finite values (NaN or Inf)"

    if cue.shape[0] == 0:
        return "cue must be non-empty (duration > 0), per docs/matching-contract.md section 1"

    return None


def _normalized_correlation_scores(
    source: np.ndarray, cue: np.ndarray
) -> np.ndarray:
    """Return the normalized-correlation score for every valid sample lag.

    Callers must first apply ``_validate_preconditions`` and ensure that the
    source is at least as long as the non-empty cue.  This package-internal
    helper is the single numerical implementation shared by the historical
    single-best matcher and additive multi-occurrence matchers.
    """
    source_f64 = source.astype(np.float64)
    cue_f64 = cue.astype(np.float64)
    window_length = cue_f64.shape[0]

    # Direct (non-FFT) correlation: raw_correlation[k] == dot(source[k:k+M], cue).
    raw_correlation = np.correlate(source_f64, cue_f64, mode="valid")

    # Sliding-window energy via a prefix sum, O(N) instead of materializing
    # every overlapping window: window_energy[k] == sum(source[k:k+M] ** 2).
    squared = source_f64 * source_f64
    prefix = np.concatenate(([0.0], np.cumsum(squared)))
    window_energy = prefix[window_length:] - prefix[:-window_length]
    window_norm = np.sqrt(window_energy)

    cue_norm = float(np.sqrt(np.sum(cue_f64 * cue_f64)))

    scores = np.zeros(raw_correlation.shape[0], dtype=np.float64)
    if cue_norm > _ENERGY_EPSILON:
        non_silent = window_norm > _ENERGY_EPSILON
        scores[non_silent] = raw_correlation[non_silent] / (window_norm[non_silent] * cue_norm)
        scores = np.clip(scores, -1.0, 1.0)
    # else: the cue itself carries ~zero energy, so normalized correlation is
    # undefined at every lag; every candidate's score deterministically
    # defaults to 0.0 ("no correlation"), the same fallback used above for
    # an individual silent window.

    return scores


def _locate_best_candidate(source: np.ndarray, cue: np.ndarray) -> tuple[int, float]:
    """Return (best_lag_index, best_score) for cue against source.

    Assumes the shared preconditions already hold and that
    len(source) >= len(cue) >= 1. Ties (equal maximum score, including the
    all-zero-score case) resolve to the earliest (lowest-index) lag, since
    numpy.argmax returns the first occurrence of the maximum value.
    """

    scores = _normalized_correlation_scores(source, cue)

    best_index = int(np.argmax(scores))
    best_score = float(scores[best_index])
    return best_index, best_score


def match_cue(
    source: np.ndarray,
    cue: np.ndarray,
    configuration: EffectiveConfiguration = DEFAULT_CONFIGURATION,
    *,
    candidate_locator: Callable[[np.ndarray, np.ndarray], tuple[int, float]] | None = None,
) -> MatchResult:
    """Locate the best occurrence of `cue` inside `source` (module docstring).

    `source` and `cue` must already satisfy CANONICAL_AUDIO_SPEC (mono,
    float32, 48000 Hz); this function does not canonicalize them
    (docs/matching-contract.md, section 1).

    `candidate_locator` is a testing/extension seam for the internal
    correlation computation (default: `_locate_best_candidate`); it is not
    part of the observable single-cue matcher contract.
    """

    if not (-1.0 <= configuration.acceptance_threshold <= 1.0):
        raise ValueError(
            "acceptance_threshold must be within [-1.0, 1.0] (normalized "
            f"cross-correlation's score range); got {configuration.acceptance_threshold!r}"
        )

    invalid_reason = _validate_preconditions(source, cue)
    if invalid_reason is not None:
        return MatchResult(
            outcome=MatchOutcome.INVALID_INPUT,
            configuration=configuration,
            reason=invalid_reason,
        )

    if source.shape[0] < cue.shape[0]:
        return MatchResult(
            outcome=MatchOutcome.NO_MATCH,
            configuration=configuration,
            reason=(
                "source is shorter than cue: no full-length candidate window "
                "exists (docs/matching-contract.md section 1 allows source "
                "duration to be less than the cue's)"
            ),
        )

    locate = candidate_locator or _locate_best_candidate
    try:
        best_index, best_score = locate(source, cue)
        max_valid_index = source.shape[0] - cue.shape[0]
        if not (0 <= best_index <= max_valid_index):
            raise AssertionError(
                f"candidate_locator returned out-of-bounds index {best_index}; "
                f"valid range is [0, {max_valid_index}]"
            )
    except Exception as exc:  # noqa: BLE001 - any unexpected matching failure
        # must surface as processing_failure, not propagate or become a
        # silent no_match (docs/matching-contract.md, section 5.4).
        return MatchResult(
            outcome=MatchOutcome.PROCESSING_FAILURE,
            configuration=configuration,
            reason=f"unexpected failure during normalized-correlation computation: {exc}",
        )

    best_timestamp_seconds = best_index / CANONICAL_AUDIO_SPEC.sample_rate_hz

    if best_score >= configuration.acceptance_threshold:
        return MatchResult(
            outcome=MatchOutcome.FOUND,
            configuration=configuration,
            timestamp_seconds=best_timestamp_seconds,
            score=best_score,
        )

    return MatchResult(
        outcome=MatchOutcome.NO_MATCH,
        configuration=configuration,
        diagnostic_best_timestamp_seconds=best_timestamp_seconds,
        diagnostic_best_score=best_score,
    )
