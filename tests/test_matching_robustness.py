"""Representative-media robustness evidence for the M2-02 baseline (M2-04).

Exercises the existing normalized-correlation match_cue implementation
(src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py) against
a small, curated representative evaluation set
(tests/fixtures/matching/representative-manifest.json) covering selected
amplitude and noise perturbation conditions and a genuine no_match
discrimination case, per issues/M2/M2-04/formal-issue.json section 8. This
module does not redefine docs/matching-contract.md's four-outcome taxonomy,
score semantics, or timestamp convention, docs/matching-baseline.md's
normalized cross-correlation method or score range, nor
docs/matching-regression.md's one-sample-period precision criterion or
M2-03's deterministic synthetic-fixture conventions -- it implements against
those already-fixed obligations while exercising a distinct, more
harmonically-shaped ("representative") signal set than M2-03's short
arbitrary arrays.

Score is asserted and reported exactly as produced by match_cue
(method-specific similarity, docs/matching-contract.md section 4); it is
never rescaled, clamped, or interpreted as a calibrated probability of
correct detection anywhere in this module or in docs/matching-robustness.md.

ffmpeg/ffprobe live probing/decode verification (formal issue section 8,
criterion 4): ffmpeg/ffprobe are confirmed unavailable in this environment
(see tests/fixtures/matching/representative-manifest.json's own
ffmpeg_probing_status, re-verified below); consequently every asset in this
evaluation is a synthetically constructed, already-canonical-shaped array,
not a decoded real-media asset, and no canonicalization/decode preprocessing
step is exercised here at all. Preprocessing effects are therefore reported
as explicitly absent from this evaluation, never conflated with the matcher
(match_cue) effects under amplitude/noise perturbation that this module does
measure. Compression perturbation is explicitly omitted (see the manifest's
perturbation_conditions.omitted), not silently absent.
"""

import copy
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import (
    MatchOutcome,
    match_cue,
)
from audio_cue_locator.infrastructure.acoustic_matching.baseline import (
    DEFAULT_CONFIGURATION,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "matching" / "representative-manifest.json"
)

ONE_SAMPLE_PERIOD_SECONDS = 1.0 / CANONICAL_AUDIO_SPEC.sample_rate_hz
"""The baseline precision criterion already justified by
docs/matching-regression.md (M2-03): the maximum acceptable absolute timing
error for a `found` result, derived from baseline.py's integer-lag search
resolution. This module exercises, and does not re-derive, that criterion."""

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
    and tests/test_matching_regression.py's own `_canonical()` convention."""

    return np.array(values, dtype=np.float32)


def test_fixture_manifest_is_present_and_well_formed():
    assert _MANIFEST_PATH.is_file(), f"missing fixture manifest: {_MANIFEST_PATH}"
    assert _MANIFEST["artifact_type"] == "matching_robustness_fixture_manifest"
    assert len(_CASES) == len(set(_CASE_IDS)), "case_id values must be unique"


def test_manifest_documents_asset_provenance_and_reuse_rights():
    """Formal issue section 8, criterion 5: curated fixtures must respect
    reuse rights, documented for every asset, not silently versioned."""

    provenance = _MANIFEST["provenance"]
    assert provenance["asset_source"] == "synthetically_generated_in_manifest"
    assert provenance["reuse_rights_statement"]
    assert provenance["existing_m1_era_fixtures_reuse_decision"] == "not_reused"
    assert provenance["existing_m1_era_fixtures_reuse_reason"]
    for case in _CASES:
        assert case["provenance"], f"{case['case_id']}: missing per-case provenance statement"


def test_manifest_covers_the_required_perturbation_categories():
    """Formal issue section 8, criterion 2: selected amplitude and noise
    perturbation conditions must be represented, reproducible, and
    explicitly enumerated, with omissions explicit rather than implied."""

    categories = {case["category"] for case in _CASES}
    assert "found_representative_clean" in categories
    assert "amplitude_perturbation" in categories
    assert "noise_perturbation" in categories
    assert "no_match_absent_cue" in categories

    included = _MANIFEST["perturbation_conditions"]["included"]
    omitted = _MANIFEST["perturbation_conditions"]["omitted"]
    assert len(included) >= 1
    included_categories = {condition["category"] for condition in included}
    assert {"amplitude", "noise"}.issubset(included_categories)

    # Formal issue section 8, criterion 2: compression is explicitly omitted,
    # not silently absent; this must remain a first-class, documented entry.
    omitted_ids = {condition["condition_id"] for condition in omitted}
    assert "lossy_compression_roundtrip" in omitted_ids
    compression_omission = next(
        c for c in omitted if c["condition_id"] == "lossy_compression_roundtrip"
    )
    assert compression_omission["category"] == "compression"
    assert compression_omission["reason"]


def test_ffmpeg_probing_status_matches_current_environment():
    """Formal issue section 8, criterion 4: the pending M1 live FFmpeg
    probing/decode verification's status must be resolved or its blocking
    impact explicitly recorded, not silently stale. This regression check
    fails loudly if ffmpeg/ffprobe become available without the manifest's
    documented status being updated to match."""

    status = _MANIFEST["ffmpeg_probing_status"]
    ffmpeg_available_now = shutil.which("ffmpeg") is not None
    ffprobe_available_now = shutil.which("ffprobe") is not None

    assert status["ffmpeg_available_at_authoring_time"] == ffmpeg_available_now, (
        "ffmpeg availability changed since this manifest was authored; "
        "re-evaluate the blocking impact and update "
        "tests/fixtures/matching/representative-manifest.json and "
        "docs/matching-robustness.md before trusting this evaluation's "
        "representative-matching claims (formal issue section 8, criterion 4)."
    )
    assert status["ffprobe_available_at_authoring_time"] == ffprobe_available_now, (
        "ffprobe availability changed since this manifest was authored; "
        "re-evaluate the blocking impact and update "
        "tests/fixtures/matching/representative-manifest.json and "
        "docs/matching-robustness.md before trusting this evaluation's "
        "representative-matching claims (formal issue section 8, criterion 4)."
    )
    assert status["blocking_impact"]
    assert status["resolution_status"] == "not_resolved_by_this_implementation"


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_matching_robustness_case(case):
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
        # Score is reported exactly as produced by match_cue -- method-specific
        # similarity, never a calibrated probability (docs/matching-contract.md
        # section 4; formal issue section 8, criterion 3).
        assert result.score == pytest.approx(
            case["expected_score"], abs=case["score_abs_tolerance"]
        )
        assert result.reason is None
        assert result.diagnostic_best_score is None
        assert result.diagnostic_best_timestamp_seconds is None
        return

    # no_match: a genuine discrimination case (absent cue against a
    # differently-shaped continuous tone); a candidate was evaluated and
    # scored below DEFAULT_CONFIGURATION.acceptance_threshold, so diagnostic
    # fields are populated and `reason` is not (docs/matching-contract.md
    # section 5.2).
    assert result.timestamp_seconds is None
    assert result.score is None
    assert result.reason is None
    assert result.diagnostic_best_score == pytest.approx(
        case["expected_diagnostic_best_score"],
        abs=case["diagnostic_best_score_abs_tolerance"],
    )
    assert result.diagnostic_best_score < DEFAULT_CONFIGURATION.acceptance_threshold, (
        f"{case['case_id']}: diagnostic_best_score must genuinely fall below "
        "the acceptance threshold to demonstrate real discrimination, not an "
        "artificially zero-sum construction"
    )
    expected_diagnostic_timestamp_seconds = (
        case["expected_diagnostic_best_sample"] / CANONICAL_AUDIO_SPEC.sample_rate_hz
    )
    assert result.diagnostic_best_timestamp_seconds == pytest.approx(
        expected_diagnostic_timestamp_seconds, abs=_FLOATING_POINT_SLACK_SECONDS
    )


def test_amplitude_perturbation_preserves_score_by_construction():
    """Formal issue section 8, criterion 2 (amplitude perturbation) and
    section 3 (score reported exactly, never as a probability): normalized
    cross-correlation is provably invariant to positive scalar amplitude
    scaling of an otherwise-unperturbed occurrence (Cauchy-Schwarz equality,
    the same argument already used by docs/matching-regression.md for
    M2-03's found cases). This is verified directly against match_cue's own
    output for every amplitude-perturbation case in the manifest, not merely
    asserted analytically."""

    amplitude_cases = [
        case
        for case in _CASES
        if case["category"] in ("found_representative_clean", "amplitude_perturbation")
    ]
    assert len(amplitude_cases) >= 3, "expected multiple amplitude scale conditions"

    scores = []
    for case in amplitude_cases:
        source = _as_canonical(case["source"])
        cue = _as_canonical(case["cue"])
        result = match_cue(source, cue)
        assert result.outcome is MatchOutcome.FOUND
        scores.append(result.score)

    for score in scores:
        assert score == pytest.approx(1.0, abs=1e-6), (
            "amplitude scaling of a verbatim-shaped occurrence is expected to "
            "leave the normalized-correlation score at its Cauchy-Schwarz "
            "upper bound of 1.0"
        )


def test_noise_perturbation_reduces_score_below_amplitude_only_cases():
    """Formal issue section 8, criterion 2 (noise perturbation): additive
    noise, unlike pure amplitude scaling, is expected to measurably reduce
    the score below the perfect-match upper bound, evidencing genuine
    robustness behavior rather than a construction that trivially always
    scores 1.0."""

    noise_cases = [case for case in _CASES if case["category"] == "noise_perturbation"]
    assert len(noise_cases) >= 2, "expected multiple noise perturbation conditions"

    for case in noise_cases:
        source = _as_canonical(case["source"])
        cue = _as_canonical(case["cue"])
        result = match_cue(source, cue)
        assert result.outcome is MatchOutcome.FOUND
        assert 0.0 < result.score < 1.0, (
            f"{case['case_id']}: noise perturbation is expected to produce a "
            "score strictly between 0 and the perfect-match upper bound"
        )


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_matching_robustness_case_is_deterministic_across_repeated_calls(case):
    """Formal issue section 10 lists non-determinism across repeated runs as
    an explicit expected failure; this mirrors
    tests/test_matching_baseline.py's and tests/test_matching_regression.py's
    own determinism precedent for every case in this manifest."""

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
