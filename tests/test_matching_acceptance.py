"""Tests for the M2-05 initial acceptance/no-match decision policy.

Exercises the existing, unmodified M2-02 baseline (match_cue,
baseline.py) using the new, additive EffectiveConfiguration defined by
acceptance.py (EVIDENCE_BASED_CONFIGURATION), never a second, competing
decision path: docs/matching-contract.md's four-outcome taxonomy, score
semantics and timestamp convention are exercised here, not redefined
(intents/M2/M2-05/implementation-handoff.json, scope.in_scope).

Covers the formal issue's acceptance criteria that can be checked on
controlled/synthetic examples: the evidence-based threshold derivation
itself (derive_acceptance_threshold); that baseline.py's own
DEFAULT_CONFIGURATION is untouched (state gap G2); representative
accepted/rejected outcomes drawn from the same construction style as
tests/test_matching_baseline.py; exact equality-at-threshold decision-
boundary behavior; two newly authored borderline (near-threshold) cases,
one on each side of the evidence-based threshold (state gap G6); and that
invalid_input/processing_failure remain distinct from no_match under the
new policy.

Fixture construction note: to make every score exactly, analytically
predictable (as tests/test_matching_baseline.py already does for its own
fixtures) rather than merely approximately verified, the boundary-behavior
fixtures below use an orthogonal-decomposition construction: for a fixed
cue vector `_CUE_BASIS` and an orthogonal vector `_ORTHOGONAL_BASIS` of
equal L2 norm, a one-window source built as
`k * _CUE_BASIS + m * _ORTHOGONAL_BASIS` (k, m real) has normalized
correlation score against `_CUE_BASIS` of exactly `k / sqrt(k**2 + m**2)`,
by direct application of the Cauchy-Schwarz/orthogonal-decomposition
identity already used by docs/matching-baseline.md and
docs/matching-regression.md to justify their own fixtures. The source is
built with length exactly equal to the cue's length (a single valid
candidate window, lag 0 only), which avoids any competing partial-overlap
window and keeps the target window's score the only score `match_cue` can
produce.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import (
    DEFAULT_CONFIGURATION,
    EffectiveConfiguration,
    MatchOutcome,
    match_cue,
)
from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
    EVIDENCE_BASED_ACCEPTANCE_THRESHOLD,
    EVIDENCE_BASED_CONFIGURATION,
    EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION,
    derive_acceptance_threshold,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ACCEPTANCE_MODULE_PATH = (
    _REPO_ROOT
    / "src"
    / "audio_cue_locator"
    / "infrastructure"
    / "acoustic_matching"
    / "acceptance.py"
)

_HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE = 0.47327783604930573
"""docs/matching-robustness.md section 5, no_match_representative_absent_cue."""

_LOWEST_OBSERVED_FOUND_SCORE = 0.9380619505661985
"""docs/matching-robustness.md section 5, found_representative_noise_high_snr."""

_CUE_BASIS = np.array([1.0, 1.0, -1.0, -1.0], dtype=np.float32)
_ORTHOGONAL_BASIS = np.array([1.0, -1.0, -1.0, 1.0], dtype=np.float32)
"""Two orthogonal, equal-L2-norm vectors (norm 2.0, dot product 0.0), used to
build single-window sources with an exactly predictable normalized-
correlation score against `_CUE_BASIS` (module docstring)."""


def _canonical(values) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def _single_window_source_for_score(target_score: float) -> np.ndarray:
    """Build a source with exactly one candidate window (len == len(cue))
    whose normalized-correlation score against `_CUE_BASIS` is
    `target_score`, via the orthogonal-decomposition construction described
    in the module docstring."""

    theta = math.acos(target_score)
    window = math.cos(theta) * _CUE_BASIS.astype(np.float64) + math.sin(theta) * (
        _ORTHOGONAL_BASIS.astype(np.float64)
    )
    return window.astype(np.float32)


# --- Threshold derivation (formal issue acceptance criteria 1, 3, 5; state gaps G7, G8) ---


def test_derive_acceptance_threshold_places_value_at_midpoint_of_evidence_gap():
    derived = derive_acceptance_threshold(
        lowest_observed_found_score=_LOWEST_OBSERVED_FOUND_SCORE,
        highest_observed_no_match_diagnostic_score=_HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE,
    )

    expected_midpoint = (
        _HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE + _LOWEST_OBSERVED_FOUND_SCORE
    ) / 2.0
    assert derived == pytest.approx(expected_midpoint, abs=1e-12)
    # Strictly bounded by the two recorded observations: a margin rule, not a
    # value equal to (or straddling outside) either boundary observation.
    assert _HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE < derived < _LOWEST_OBSERVED_FOUND_SCORE


def test_derive_acceptance_threshold_rejects_a_non_positive_evidence_gap():
    with pytest.raises(ValueError):
        derive_acceptance_threshold(
            lowest_observed_found_score=0.5,
            highest_observed_no_match_diagnostic_score=0.6,
        )
    with pytest.raises(ValueError):
        derive_acceptance_threshold(
            lowest_observed_found_score=0.5,
            highest_observed_no_match_diagnostic_score=0.5,
        )


def test_evidence_based_threshold_module_constant_matches_the_derivation():
    assert EVIDENCE_BASED_ACCEPTANCE_THRESHOLD == pytest.approx(
        derive_acceptance_threshold(), abs=1e-12
    )
    assert -1.0 <= EVIDENCE_BASED_ACCEPTANCE_THRESHOLD <= 1.0


def test_evidence_based_configuration_reuses_baselines_configuration_shape():
    assert isinstance(EVIDENCE_BASED_CONFIGURATION, EffectiveConfiguration)
    assert EVIDENCE_BASED_CONFIGURATION.method == "normalized_cross_correlation_v1"
    assert EVIDENCE_BASED_CONFIGURATION.acceptance_threshold == pytest.approx(
        EVIDENCE_BASED_ACCEPTANCE_THRESHOLD
    )


def test_multi_occurrence_configuration_reuses_the_historical_numeric_cutoff():
    assert EVIDENCE_BASED_CONFIGURATION.method == "normalized_cross_correlation_v1"
    assert (
        EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.method
        == "normalized_cross_correlation_multi_v1"
    )
    assert EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION.acceptance_threshold == (
        EVIDENCE_BASED_CONFIGURATION.acceptance_threshold
    )


def test_evidence_based_configuration_is_additive_not_a_default_override():
    # state gap G2: baseline.py's own DEFAULT_CONFIGURATION must remain
    # untouched; this issue's configuration is a distinct, explicitly
    # supplied value, never a silent redefinition of the existing default.
    assert DEFAULT_CONFIGURATION.method == "normalized_cross_correlation_v1"
    assert DEFAULT_CONFIGURATION.acceptance_threshold == pytest.approx(0.75)
    assert EVIDENCE_BASED_CONFIGURATION.acceptance_threshold != pytest.approx(
        DEFAULT_CONFIGURATION.acceptance_threshold
    )


def test_acceptance_module_does_not_wrap_or_reimplement_match_cues_decision():
    # state gap G1: the policy must integrate by supplying a new
    # EffectiveConfiguration into the existing match_cue call, never by
    # calling match_cue itself or inspecting/overriding a MatchResult from a
    # second, competing decision layer.
    tree = ast.parse(_ACCEPTANCE_MODULE_PATH.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "match_cue" not in imported_names
    assert "MatchResult" not in imported_names
    assert "MatchOutcome" not in imported_names


# --- Representative accepted/rejected outcomes (formal issue acceptance criterion 1) ---


def test_found_exact_match_is_accepted_under_the_evidence_based_configuration():
    cue = _canonical([0.9, -0.6, 0.3, -0.9, 0.6])
    source = np.concatenate(
        [np.zeros(10, dtype=np.float32), cue, np.zeros(7, dtype=np.float32)]
    )

    result = match_cue(source, cue, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.FOUND
    assert result.configuration == EVIDENCE_BASED_CONFIGURATION
    assert result.score == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("scale", [1.0, 0.5, 0.25])
def test_amplitude_scaled_occurrences_remain_accepted_under_the_evidence_based_configuration(
    scale,
):
    # Normalized correlation is invariant to positive amplitude scaling
    # (docs/matching-robustness.md section 5); this must still hold once the
    # evidence-based configuration, not just DEFAULT_CONFIGURATION, is used.
    cue = _canonical([0.9, -0.6, 0.3, -0.9, 0.6])
    scaled_occurrence = (cue * scale).astype(np.float32)
    source = np.concatenate(
        [np.zeros(10, dtype=np.float32), scaled_occurrence, np.zeros(7, dtype=np.float32)]
    )

    result = match_cue(source, cue, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.FOUND
    assert result.score == pytest.approx(1.0, abs=1e-6)


def test_no_match_absent_cue_is_rejected_under_the_evidence_based_configuration():
    cue = _canonical([1.0, -1.0, 1.0, -1.0])
    source = np.full(40, 0.5, dtype=np.float32)

    result = match_cue(source, cue, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.score is None
    # Retained only as diagnostic evidence (docs/matching-contract.md section
    # 5.2); this policy must not reclassify it as found.
    assert result.diagnostic_best_score == pytest.approx(0.0, abs=1e-9)


# --- Decision-boundary equality behavior (formal issue acceptance criterion 2) ---


def test_score_exactly_equal_to_threshold_is_found():
    # A dedicated, exactly-constructible threshold (0.6) is used here instead
    # of EVIDENCE_BASED_ACCEPTANCE_THRESHOLD itself: the evidence-based value
    # is an irrational midpoint that cannot be hit bit-for-bit through
    # float32 storage, so this test instead verifies the general `>=`
    # equality-at-threshold rule baseline.py's own decision branch applies
    # (and this policy reuses unmodified, per state gap G1) with a threshold
    # value exact equality can actually be constructed against: a 3-4-5
    # Pythagorean-ratio window (dot/[(|window|)(|cue|)] == 3/5 exactly, by
    # construction over integers), scaled by the amplitude-invariance
    # property already exercised above.
    cue = _CUE_BASIS
    window_at_threshold = (3 * _CUE_BASIS + 4 * _ORTHOGONAL_BASIS).astype(np.float32)
    configuration = EffectiveConfiguration(
        method="normalized_cross_correlation_v1", acceptance_threshold=0.6
    )

    result = match_cue(window_at_threshold, cue, configuration)

    assert result.score == 0.6
    assert result.outcome is MatchOutcome.FOUND


def test_score_just_below_threshold_is_no_match():
    cue = _CUE_BASIS
    # cos(theta) for a window mixed with a slightly larger orthogonal
    # component than the at-threshold case above, deterministically below
    # 0.6 (verified analytically: dot/[(|window|)(|cue|)] < 3/5).
    window_below_threshold = (3 * _CUE_BASIS + 4.02 * _ORTHOGONAL_BASIS).astype(np.float32)
    configuration = EffectiveConfiguration(
        method="normalized_cross_correlation_v1", acceptance_threshold=0.6
    )

    result = match_cue(window_below_threshold, cue, configuration)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.score is None
    assert result.diagnostic_best_score < 0.6


# --- Newly authored borderline (near-threshold) cases, one per side (state gap G6) ---


def test_borderline_case_just_above_the_evidence_based_threshold_is_found():
    source = _single_window_source_for_score(EVIDENCE_BASED_ACCEPTANCE_THRESHOLD + 0.02)

    result = match_cue(source, _CUE_BASIS, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.FOUND
    assert result.score == pytest.approx(
        EVIDENCE_BASED_ACCEPTANCE_THRESHOLD + 0.02, abs=1e-4
    )
    assert result.score >= EVIDENCE_BASED_ACCEPTANCE_THRESHOLD


def test_borderline_case_just_below_the_evidence_based_threshold_is_no_match():
    source = _single_window_source_for_score(EVIDENCE_BASED_ACCEPTANCE_THRESHOLD - 0.02)

    result = match_cue(source, _CUE_BASIS, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.score is None
    assert result.diagnostic_best_score == pytest.approx(
        EVIDENCE_BASED_ACCEPTANCE_THRESHOLD - 0.02, abs=1e-4
    )
    assert result.diagnostic_best_score < EVIDENCE_BASED_ACCEPTANCE_THRESHOLD


def test_borderline_cases_are_deterministic_across_repeated_calls():
    source_above = _single_window_source_for_score(EVIDENCE_BASED_ACCEPTANCE_THRESHOLD + 0.02)

    first = match_cue(source_above, _CUE_BASIS, EVIDENCE_BASED_CONFIGURATION)
    second = match_cue(source_above, _CUE_BASIS, EVIDENCE_BASED_CONFIGURATION)

    assert first == second


# --- invalid_input / processing_failure remain distinct from no_match (formal issue acceptance criterion 4) ---


def test_invalid_input_is_not_reclassified_as_no_match_under_the_evidence_based_configuration():
    empty_cue = _canonical([])
    source = _canonical([0.1, 0.2, 0.3])

    result = match_cue(source, empty_cue, EVIDENCE_BASED_CONFIGURATION)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert result.outcome is not MatchOutcome.NO_MATCH
    assert result.diagnostic_best_score is None


def test_processing_failure_is_not_reclassified_as_no_match_under_the_evidence_based_configuration():
    cue = _canonical([0.1, 0.2])
    source = _canonical([0.1, 0.2, 0.3, 0.4])

    def _broken_locator(_source, _cue):
        raise RuntimeError("simulated internal correlation failure")

    result = match_cue(
        source, cue, EVIDENCE_BASED_CONFIGURATION, candidate_locator=_broken_locator
    )

    assert result.outcome is MatchOutcome.PROCESSING_FAILURE
    assert result.outcome is not MatchOutcome.NO_MATCH
    assert result.diagnostic_best_score is None
