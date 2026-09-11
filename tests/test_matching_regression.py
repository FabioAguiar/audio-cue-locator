"""Deterministic timestamp/score regression evidence for the M2-02 baseline (M2-03).

Exercises the existing normalized-correlation match_cue implementation
(src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py) against
a deterministic fixture manifest (tests/fixtures/matching/manifest.json)
covering known offsets, an absent cue, and selected silence/length boundary
cases, per issues/M2/M2-03/formal-issue.json section 8. This module does not
redefine docs/matching-contract.md's four-outcome taxonomy, score semantics,
or timestamp convention, nor docs/matching-baseline.md's normalized
cross-correlation method, score range, or degenerate-input table -- it
implements against those already-fixed obligations.

Baseline precision criterion (formal issue section 8, criterion 3): the
maximum acceptable absolute timing error for a `found` result is one sample
period at the canonical sample rate,
`1 / CANONICAL_AUDIO_SPEC.sample_rate_hz` (~2.0833e-5 s at 48000 Hz). This is
not an invented tolerance: baseline.py's `_locate_best_candidate` selects the
best-candidate lag as an integer sample index via `numpy.argmax` over
`numpy.correlate(..., mode="valid")` (docs/matching-baseline.md section 1);
no sub-sample interpolation is implemented or claimed, so one sample is the
algorithm's own best-achievable temporal resolution, not an empirically
fitted number. Every `found` fixture below embeds its cue verbatim,
unmodified, into an otherwise silent source at a known integer sample
offset, so the observed timestamp is expected to match the expected
timestamp exactly (zero timing error), evaluating this criterion against
the fixtures per docs/matching-regression.md.

Absent-cue diagnostics (formal issue section 8, criterion 4): for `no_match`
cases where a candidate window was actually evaluated,
`diagnostic_best_score`/`diagnostic_best_timestamp_seconds` are asserted and
recorded as regression evidence only (docs/matching-contract.md section
5.2); this module never uses them to reclassify a result as `found` or to
implement any acceptance-policy logic reserved for M2-05.
"""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import (
    MatchOutcome,
    match_cue,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "matching" / "manifest.json"

ONE_SAMPLE_PERIOD_SECONDS = 1.0 / CANONICAL_AUDIO_SPEC.sample_rate_hz
"""The baseline precision criterion (see module docstring): the maximum
acceptable absolute timing error for a `found` result, derived from
baseline.py's integer-lag search resolution, not an invented number."""

_FLOATING_POINT_SLACK_SECONDS = 1e-9
"""Negligible slack for float64-to-float division rounding only; it is not
part of the precision criterion itself, which is exactly one sample."""


def _load_manifest() -> dict:
    with _MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


_MANIFEST = _load_manifest()
_CASES = _MANIFEST["cases"]
_CASE_IDS = [case["case_id"] for case in _CASES]


def _as_canonical(values: list) -> np.ndarray:
    """Build a CANONICAL_AUDIO_SPEC-conformant (mono, float32) array from a
    plain list of finite floats, following tests/test_matching_baseline.py's
    own `_canonical()` convention."""

    return np.array(values, dtype=np.float32)


def test_fixture_manifest_is_present_and_well_formed():
    assert _MANIFEST_PATH.is_file(), f"missing fixture manifest: {_MANIFEST_PATH}"
    assert _MANIFEST["artifact_type"] == "matching_regression_fixture_manifest"
    assert len(_CASES) == len(set(_CASE_IDS)), "case_id values must be unique"


def test_manifest_covers_the_required_fixture_categories():
    """Formal issue section 8, criterion 1: known offsets (plural), an
    absent cue, and selected silence/length boundary cases must all be
    represented."""

    categories = [case["category"] for case in _CASES]
    assert categories.count("found_known_offset") >= 2, (
        "at least two distinct known-offset found cases are required"
    )
    for required_category in (
        "no_match_absent_cue",
        "no_match_silent_cue",
        "no_match_silent_source",
        "no_match_source_shorter_than_cue",
    ):
        assert required_category in categories, f"missing required category: {required_category}"


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_matching_regression_case(case):
    source = _as_canonical(case["source"])
    cue = _as_canonical(case["cue"])

    result = match_cue(source, cue)

    assert result.outcome.value == case["expected_outcome"]

    if case["expected_outcome"] == "found":
        assert result.outcome is MatchOutcome.FOUND
        expected_timestamp_seconds = (
            case["cue_start_sample"] / CANONICAL_AUDIO_SPEC.sample_rate_hz
        )
        assert result.timestamp_seconds is not None
        absolute_timing_error_seconds = abs(
            result.timestamp_seconds - expected_timestamp_seconds
        )
        assert absolute_timing_error_seconds <= (
            ONE_SAMPLE_PERIOD_SECONDS + _FLOATING_POINT_SLACK_SECONDS
        ), (
            f"{case['case_id']}: absolute timing error "
            f"{absolute_timing_error_seconds!r}s exceeds the one-sample-period "
            f"precision criterion ({ONE_SAMPLE_PERIOD_SECONDS!r}s)"
        )
        assert result.score == pytest.approx(
            case["expected_score"], abs=case["score_abs_tolerance"]
        )
        assert result.reason is None
        assert result.diagnostic_best_score is None
        assert result.diagnostic_best_timestamp_seconds is None
        return

    # no_match: distinguish the source-shorter-than-cue branch (no candidate
    # window ever evaluated; only `reason` is populated) from the
    # threshold-based branches (a candidate was evaluated and scored below
    # DEFAULT_CONFIGURATION.acceptance_threshold; diagnostic fields are
    # populated, `reason` is not).
    assert result.timestamp_seconds is None
    assert result.score is None

    if case["expect_reason_substring"] is not None:
        assert result.reason is not None
        assert case["expect_reason_substring"] in result.reason
        assert result.diagnostic_best_score is None
        assert result.diagnostic_best_timestamp_seconds is None
        return

    assert result.reason is None
    assert result.diagnostic_best_score == pytest.approx(
        case["expected_diagnostic_best_score"],
        abs=case["diagnostic_best_score_abs_tolerance"],
    )
    expected_diagnostic_timestamp_seconds = (
        case["expected_diagnostic_best_sample"] / CANONICAL_AUDIO_SPEC.sample_rate_hz
    )
    assert result.diagnostic_best_timestamp_seconds == pytest.approx(
        expected_diagnostic_timestamp_seconds, abs=_FLOATING_POINT_SLACK_SECONDS
    )


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_matching_regression_case_is_deterministic_across_repeated_calls(case):
    """Formal issue section 10 lists non-determinism across repeated runs as
    an explicit expected failure; this mirrors
    tests/test_matching_baseline.py's own determinism precedent for every
    fixture in this manifest."""

    source = _as_canonical(case["source"])
    cue = _as_canonical(case["cue"])

    first = match_cue(source, cue)
    second = match_cue(source, cue)

    assert first == second


def test_manifest_fixtures_are_not_mutated_by_matching():
    """match_cue must not mutate its inputs; a defensive regression check
    since this manifest's arrays are shared, read-only ground truth."""

    for case in _CASES:
        source = _as_canonical(case["source"])
        cue = _as_canonical(case["cue"])
        source_before = copy.deepcopy(source)
        cue_before = copy.deepcopy(cue)

        match_cue(source, cue)

        assert np.array_equal(source, source_before), (
            f"{case['case_id']}: source array was mutated by match_cue"
        )
        assert np.array_equal(cue, cue_before), (
            f"{case['case_id']}: cue array was mutated by match_cue"
        )
