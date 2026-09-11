"""Tests for the M2-02 normalized-correlation single-cue matching baseline.

These validate the M2-02 acceptance criteria that can be checked on
controlled/synthetic examples, without live representative media: a
deterministic best-candidate timestamp/score/method identity for `found`;
`no_match` for a source with no acceptable candidate, for a source shorter
than the cue, and for silent cue/source inputs; `invalid_input` for
precondition violations; `processing_failure` for an unexpected failure
during matching; and that numerical operations stay out of Core and
Application (docs/matching-contract.md; docs/matching-baseline.md).
"""

import ast
import dataclasses
from pathlib import Path

import numpy as np
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import (
    DEFAULT_CONFIGURATION,
    EffectiveConfiguration,
    MatchOutcome,
    match_cue,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _canonical(values) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_found_returns_deterministic_timestamp_score_and_method():
    cue = _canonical([0.9, -0.6, 0.3, -0.9, 0.6])
    silence_before = np.zeros(10, dtype=np.float32)
    silence_after = np.zeros(7, dtype=np.float32)
    source = np.concatenate([silence_before, cue, silence_after])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.FOUND
    assert result.configuration == DEFAULT_CONFIGURATION
    assert result.configuration.method == "normalized_cross_correlation_v1"
    assert result.timestamp_seconds == pytest.approx(
        len(silence_before) / CANONICAL_AUDIO_SPEC.sample_rate_hz
    )
    assert result.score == pytest.approx(1.0, abs=1e-6)
    assert result.reason is None
    assert result.diagnostic_best_score is None
    assert result.diagnostic_best_timestamp_seconds is None


def test_match_cue_is_deterministic_across_repeated_calls():
    cue = _canonical([0.2, 0.8, -0.5, 0.1])
    source = np.concatenate(
        [np.full(6, 0.05, dtype=np.float32), cue, np.full(9, -0.05, dtype=np.float32)]
    )

    first = match_cue(source, cue)
    second = match_cue(source, cue)

    assert first == second


def test_no_match_when_no_candidate_meets_the_acceptance_threshold():
    # An alternating cue against a perfectly flat, non-silent source: every
    # window's dot product with the alternating cue is exactly zero, so the
    # normalized score is exactly 0.0 everywhere (well below the threshold).
    cue = _canonical([1.0, -1.0, 1.0, -1.0])
    source = np.full(40, 0.5, dtype=np.float32)

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.timestamp_seconds is None
    assert result.score is None
    assert result.reason is None
    assert result.diagnostic_best_score == pytest.approx(0.0, abs=1e-9)
    assert result.diagnostic_best_timestamp_seconds == pytest.approx(0.0)


def test_no_match_when_source_is_shorter_than_cue():
    cue = _canonical([0.1, 0.2, 0.3, 0.4, 0.5])
    source = _canonical([0.1, 0.2, 0.3])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.timestamp_seconds is None
    assert result.score is None
    assert result.diagnostic_best_score is None
    assert result.diagnostic_best_timestamp_seconds is None
    assert result.reason is not None
    assert "shorter than cue" in result.reason


def test_no_match_when_cue_is_silent():
    cue = np.zeros(4, dtype=np.float32)
    source = _canonical([0.5, -0.5, 0.5, -0.5, 0.1, 0.1, 0.1, 0.1])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.diagnostic_best_score == pytest.approx(0.0, abs=1e-9)


def test_no_match_when_source_is_entirely_silent():
    cue = _canonical([1.0, -1.0, 0.5])
    source = np.zeros(20, dtype=np.float32)

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.NO_MATCH
    assert result.diagnostic_best_score == pytest.approx(0.0, abs=1e-9)


def test_invalid_input_when_cue_is_empty():
    cue = _canonical([])
    source = _canonical([0.1, 0.2, 0.3])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert result.timestamp_seconds is None
    assert result.score is None
    assert result.reason is not None
    assert "non-empty" in result.reason


def test_invalid_input_when_source_is_not_float32():
    cue = _canonical([0.1, 0.2])
    source = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float64)

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert "float32" in result.reason


def test_invalid_input_when_source_contains_non_finite_values():
    cue = _canonical([0.1, 0.2])
    source = _canonical([0.1, float("nan"), 0.3, 0.4])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert "non-finite" in result.reason


def test_invalid_input_when_cue_is_not_one_dimensional():
    cue = np.zeros((4, 2), dtype=np.float32)
    source = _canonical([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert "1-D" in result.reason


def test_invalid_input_when_source_is_not_an_ndarray():
    cue = _canonical([0.1, 0.2])
    source = [0.1, 0.2, 0.3, 0.4]

    result = match_cue(source, cue)

    assert result.outcome is MatchOutcome.INVALID_INPUT
    assert "numpy.ndarray" in result.reason


def test_processing_failure_when_candidate_locator_raises():
    cue = _canonical([0.1, 0.2])
    source = _canonical([0.1, 0.2, 0.3, 0.4])

    def _broken_locator(_source, _cue):
        raise RuntimeError("simulated internal correlation failure")

    result = match_cue(source, cue, candidate_locator=_broken_locator)

    assert result.outcome is MatchOutcome.PROCESSING_FAILURE
    assert result.timestamp_seconds is None
    assert result.score is None
    assert result.reason is not None
    assert "simulated internal correlation failure" in result.reason


def test_processing_failure_when_candidate_locator_returns_out_of_bounds_index():
    cue = _canonical([0.1, 0.2])
    source = _canonical([0.1, 0.2, 0.3, 0.4])

    def _out_of_bounds_locator(_source, _cue):
        return 99, 0.5

    result = match_cue(source, cue, candidate_locator=_out_of_bounds_locator)

    assert result.outcome is MatchOutcome.PROCESSING_FAILURE
    assert "out-of-bounds" in result.reason


def test_match_cue_rejects_out_of_range_acceptance_threshold():
    bad_configuration = EffectiveConfiguration(
        method="normalized_cross_correlation_v1", acceptance_threshold=1.5
    )

    with pytest.raises(ValueError):
        match_cue(_canonical([0.1, 0.2]), _canonical([0.1, 0.2]), bad_configuration)


def test_default_configuration_has_a_stable_method_identifier_and_valid_threshold():
    assert DEFAULT_CONFIGURATION.method == "normalized_cross_correlation_v1"
    assert -1.0 <= DEFAULT_CONFIGURATION.acceptance_threshold <= 1.0


def test_match_result_and_effective_configuration_are_frozen():
    result = match_cue(_canonical([0.1, 0.2]), _canonical([0.1, 0.2]))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.score = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_CONFIGURATION.method = "changed"


def test_core_and_application_do_not_import_numerical_libraries():
    """Boundary regression guard: numerical operations must stay confined to
    Infrastructure (Acoustic Matching), never Core or Application
    (docs/architecture.md, Princípios e Restrições #1; formal issue M2-02,
    section 8, acceptance criterion 4)."""
    forbidden = {"numpy", "scipy"}
    for relative_path in (
        Path("src/audio_cue_locator/core/__init__.py"),
        Path("src/audio_cue_locator/application/__init__.py"),
    ):
        module_path = _REPO_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module.split(".")[0])

        assert not (imported_names & forbidden), (
            f"{relative_path} must not import numerical libraries "
            "(docs/architecture.md, Princípios e Restrições #1)"
        )
