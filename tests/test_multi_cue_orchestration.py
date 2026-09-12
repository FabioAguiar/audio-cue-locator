"""Tests for the M3-03 Application-level multi-cue orchestration.

These validate the M3-03 acceptance criteria that can be checked on
controlled/synthetic examples, without live representative media: an
Analysis with more than one cue produces a coherent, cue_id-attributed set
of per-cue outcomes (`MatchResult`); `match_cue` is invoked exactly once per
cue, against the shared source, with the caller-supplied
`EffectiveConfiguration` -- never a module-level default -- passed through
unchanged; no cue outcome is silently dropped, regardless of its
`MatchOutcome`; and an empty Analysis (no cues) is rejected rather than
silently producing an empty result (`docs/multi-cue-orchestration.md`;
`docs/analysis-core-contracts.md`).
"""

import numpy as np
import pytest

from audio_cue_locator.application.multi_cue_orchestration import (
    run_multi_cue_analysis,
)
from audio_cue_locator.infrastructure.acoustic_matching import (
    DEFAULT_CONFIGURATION,
    EffectiveConfiguration,
    MatchOutcome,
)


def _canonical(values) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def _shared_source() -> np.ndarray:
    # A single cue-length burst embedded in silence, reused as the one
    # shared source every cue in these tests is matched against -- mirroring
    # tests/test_matching_baseline.py's own found-case fixture, but shared
    # across multiple cues here rather than owned by a single test.
    burst = _canonical([0.9, -0.6, 0.3, -0.9, 0.6])
    silence_before = np.zeros(10, dtype=np.float32)
    silence_after = np.zeros(7, dtype=np.float32)
    return np.concatenate([silence_before, burst, silence_after])


_FOUND_CUE = _canonical([0.9, -0.6, 0.3, -0.9, 0.6])
"""Identical to the burst embedded in `_shared_source`: a perfect (score
1.0) match at every window aligned with the burst."""

_NO_MATCH_CUE = _canonical([-0.9, 0.6, -0.3, 0.9, -0.6])
"""The exact phase-inversion of `_FOUND_CUE`: at the burst's own position
this scores a perfect negative correlation (-1.0, far below any acceptance
threshold in [-1.0, 1.0]); everywhere else in `_shared_source` the window is
silent, which baseline.py deterministically scores 0.0. The maximum score
across the whole source is therefore 0.0, below DEFAULT_CONFIGURATION's
0.75 threshold: a deterministic NO_MATCH, not a near-miss."""

_INVALID_CUE = _canonical([])
"""An empty cue: a precondition violation (docs/matching-contract.md,
section 1), deterministically INVALID_INPUT regardless of `source`."""


def test_two_or_more_cues_produce_a_coherent_set_of_per_cue_outcomes():
    source = _shared_source()

    results = run_multi_cue_analysis(
        source,
        cues={
            "cue-found": _FOUND_CUE,
            "cue-no-match": _NO_MATCH_CUE,
            "cue-invalid": _INVALID_CUE,
        },
        configuration=DEFAULT_CONFIGURATION,
    )

    assert set(results.keys()) == {"cue-found", "cue-no-match", "cue-invalid"}
    assert results["cue-found"].outcome is MatchOutcome.FOUND
    assert results["cue-found"].score == pytest.approx(1.0, abs=1e-6)
    assert results["cue-no-match"].outcome is MatchOutcome.NO_MATCH
    assert results["cue-invalid"].outcome is MatchOutcome.INVALID_INPUT


def test_cue_id_attribution_matches_the_originating_cue_not_input_order():
    source = _shared_source()

    # The no-match cue is listed first here (test_two_or_more_cues... lists
    # the found cue first) to guard against any positional/index-based
    # mismatch: attribution must follow the cue_id key, not iteration order.
    results = run_multi_cue_analysis(
        source,
        cues={"cue-b": _NO_MATCH_CUE, "cue-a": _FOUND_CUE},
        configuration=DEFAULT_CONFIGURATION,
    )

    assert results["cue-a"].outcome is MatchOutcome.FOUND
    assert results["cue-b"].outcome is MatchOutcome.NO_MATCH


def test_match_cue_is_invoked_exactly_once_per_cue(monkeypatch):
    import audio_cue_locator.application.multi_cue_orchestration as orchestration

    calls: list[tuple[np.ndarray, np.ndarray, EffectiveConfiguration]] = []
    real_match_cue = orchestration.match_cue

    def _spy(source, cue, configuration):
        calls.append((source, cue, configuration))
        return real_match_cue(source, cue, configuration)

    monkeypatch.setattr(orchestration, "match_cue", _spy)

    custom_configuration = EffectiveConfiguration(
        method="normalized_cross_correlation_v1", acceptance_threshold=0.5
    )
    source = _shared_source()
    cues = {"cue-found": _FOUND_CUE, "cue-no-match": _NO_MATCH_CUE}

    orchestration.run_multi_cue_analysis(source, cues, custom_configuration)

    assert len(calls) == len(cues)
    called_cue_arrays = [call[1] for call in calls]
    assert any(np.array_equal(arr, _FOUND_CUE) for arr in called_cue_arrays)
    assert any(np.array_equal(arr, _NO_MATCH_CUE) for arr in called_cue_arrays)
    assert all(call[2] == custom_configuration for call in calls)


def test_configuration_used_is_the_one_supplied_not_a_module_level_default():
    source = _shared_source()

    custom_configuration = EffectiveConfiguration(
        method="test_only_custom_method", acceptance_threshold=0.5
    )
    assert custom_configuration != DEFAULT_CONFIGURATION

    results = run_multi_cue_analysis(
        source,
        cues={"cue-found": _FOUND_CUE},
        configuration=custom_configuration,
    )

    assert results["cue-found"].configuration == custom_configuration
    assert results["cue-found"].configuration != DEFAULT_CONFIGURATION


def test_no_cue_outcome_is_silently_dropped_regardless_of_match_outcome():
    source = _shared_source()

    results = run_multi_cue_analysis(
        source,
        cues={
            "cue-found": _FOUND_CUE,
            "cue-no-match": _NO_MATCH_CUE,
            "cue-invalid": _INVALID_CUE,
        },
        configuration=DEFAULT_CONFIGURATION,
    )

    assert len(results) == 3
    outcomes = {cue_id: result.outcome for cue_id, result in results.items()}
    assert outcomes == {
        "cue-found": MatchOutcome.FOUND,
        "cue-no-match": MatchOutcome.NO_MATCH,
        "cue-invalid": MatchOutcome.INVALID_INPUT,
    }


def test_empty_cues_raises_value_error_instead_of_an_empty_result():
    source = _shared_source()

    with pytest.raises(ValueError, match="non-empty"):
        run_multi_cue_analysis(source, cues={}, configuration=DEFAULT_CONFIGURATION)


def test_single_cue_analysis_is_a_valid_degenerate_case():
    source = _shared_source()

    results = run_multi_cue_analysis(
        source,
        cues={"only-cue": _FOUND_CUE},
        configuration=DEFAULT_CONFIGURATION,
    )

    assert set(results.keys()) == {"only-cue"}
    assert results["only-cue"].outcome is MatchOutcome.FOUND
