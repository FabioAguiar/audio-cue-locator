"""Integration tests for the full M3-03 through M3-06 multi-cue flow, and a
Core boundary review's Definition-of-Done evidence (M3-07).

These exercise, together and through the existing, unmodified production
code (never a parallel implementation of it), the four predecessor
deliverables the formal issue requires this issue to demonstrate jointly
(`issues/M3/M3-07/formal-issue.json`, acceptance criterion 2):

- M3-03/M3-05 multi-cue orchestration (`run_multi_cue_analysis`,
  `src/audio_cue_locator/application/multi_cue_orchestration.py`), driven
  against `tests/fixtures/multi_cue/manifest.json`'s synthetic fixtures
  covering a repeated cue, an absent cue, and a cross-cue temporal-overlap
  case (acceptance criterion 1);
- M3-04's occurrence temporal contract, by scoping the overlap/near-
  duplicate case to two distinct cues whose occurrences overlap in time,
  never same-cue multiplicity, which the current
  `normalized_cross_correlation_v1` matcher does not support
  (`docs/occurrence-temporal-semantics-and-policy.md`);
- M3-05's per-cue outcome distinction, by asserting the exact
  `CueOccurrences`/`CueNoMatch` kind each cue resolves to (per-cue no-match/
  failure distinction is already directly and thoroughly covered by
  M3-05's own `tests/test_multi_cue_orchestration.py`; this module does not
  duplicate that coverage, only exercises it as part of one integrated
  flow);
- M3-06's versioned Analysis Result and its canonical serializer
  (`src/audio_cue_locator/core/analysis_result.py`), by mapping the
  orchestration's Application-level `PerCueOutcome` values into Core's
  `CueOutcome` union and constructing and serializing a real
  `AnalysisResult`.

No new production module exists, or is added by this issue, between
Application's `PerCueOutcome` union and Core's `CueOutcome` union: per
`intents/M3/M3-07/implementation-handoff.json` scope_notes G2, the mapping
below (`_to_core_outcome`) is a private, non-exported, test-local helper.
`analysis_result.py`'s own module docstring already states that adapting an
already-produced Application outcome mapping into Core types is deliberately
left to a future integration issue; this module is not that issue.

The determinism test below (acceptance criterion 3) exercises the full flow
end-to-end -- orchestration, the test-local Core mapping, and
`serialize_analysis_result` -- and is additional to, not a replacement for,
M3-06's own `tests/test_analysis_result_serialization.py`, which only
serializes hand-constructed `AnalysisResult` objects and never calls
`run_multi_cue_analysis`.

None of `src/audio_cue_locator/core/analysis_result.py`,
`src/audio_cue_locator/application/multi_cue_orchestration.py`,
`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
`src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`,
`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`,
or any predecessor documentation is modified by this issue; they are
read-only compatibility references.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from audio_cue_locator.application.multi_cue_orchestration import (
    CueFailure as ApplicationCueFailure,
    CueNoMatch as ApplicationCueNoMatch,
    CueOccurrences as ApplicationCueOccurrences,
    PerCueOutcome,
    run_multi_cue_analysis,
)
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueFailure,
    CueNoMatch,
    CueOccurrences,
    CueOutcome,
    CueResult,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    Occurrence,
    serialize_analysis_result,
)
from audio_cue_locator.infrastructure.acoustic_matching.baseline import (
    DEFAULT_CONFIGURATION,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_MANIFEST_PATH = Path(__file__).parent / "fixtures" / "multi_cue" / "manifest.json"


def _load_manifest() -> dict:
    with _MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _case(manifest: dict, case_id: str) -> dict:
    for case in manifest["cases"]:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"no case with case_id {case_id!r} in {_MANIFEST_PATH}")


def _canonical(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def _source_and_cues(case: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    source = _canonical(case["source"])
    cues = {cue_id: _canonical(values) for cue_id, values in case["cues"].items()}
    return source, cues


def _to_core_outcome(application_outcome: PerCueOutcome) -> CueOutcome:
    """Map one Application `PerCueOutcome` into the equivalent Core
    `CueOutcome`. Private and test-local by design (scope_notes G2): not a
    new production Application-to-Core adapter module.
    """

    if isinstance(application_outcome, ApplicationCueOccurrences):
        return CueOccurrences(
            occurrences=tuple(
                Occurrence(
                    cue_id=occurrence.cue_id,
                    temporal_position=occurrence.temporal_position,
                    score=occurrence.score,
                    matching_method=occurrence.matching_method,
                    end=occurrence.end,
                )
                for occurrence in application_outcome.occurrences
            )
        )
    if isinstance(application_outcome, ApplicationCueNoMatch):
        return CueNoMatch()
    if isinstance(application_outcome, ApplicationCueFailure):
        return CueFailure(
            category=FailureCategory(application_outcome.category.value),
            message=application_outcome.message,
        )
    raise TypeError(
        f"unmapped Application PerCueOutcome variant: {type(application_outcome)!r}"
    )


def _configuration_snapshot() -> EffectiveConfigurationSnapshot:
    normalization = CANONICAL_AUDIO_SPEC.normalization
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=CANONICAL_AUDIO_SPEC.sample_rate_hz,
            channels=CANONICAL_AUDIO_SPEC.channels,
            sample_format=CANONICAL_AUDIO_SPEC.sample_format,
            normalization=NormalizationSnapshot(
                enabled=normalization.enabled,
                method=normalization.method,
                target_peak_amplitude=normalization.target_peak_amplitude,
            ),
        ),
        matching=MatchingSnapshot(
            method=DEFAULT_CONFIGURATION.method,
            acceptance_threshold=DEFAULT_CONFIGURATION.acceptance_threshold,
        ),
        configuration_source_name="baseline.DEFAULT_CONFIGURATION",
    )


def _run_full_flow(
    source: np.ndarray, cues: dict[str, np.ndarray], analysis_id: str
) -> AnalysisResult:
    """Run the real, unmodified orchestration entry point, map its output
    into Core types via the test-local helper above, and construct a real
    `AnalysisResult` -- the full M3-03 through M3-06 flow this issue must
    exercise together.
    """

    per_cue_outcomes = run_multi_cue_analysis(source, cues, DEFAULT_CONFIGURATION)
    cue_results = tuple(
        CueResult(cue_id=cue_id, outcome=_to_core_outcome(outcome))
        for cue_id, outcome in per_cue_outcomes.items()
    )
    return AnalysisResult(
        analysis_id=analysis_id,
        method=DEFAULT_CONFIGURATION.method,
        configuration=_configuration_snapshot(),
        cues=cue_results,
    )


def test_repeated_cue_and_absent_cue_are_correctly_attributed():
    manifest = _load_manifest()
    case = _case(manifest, "repeated_cue_and_absent_cue")
    source, cues = _source_and_cues(case)
    expected = case["expected_per_cue_outcomes"]

    results = run_multi_cue_analysis(source, cues, DEFAULT_CONFIGURATION)

    for cue_id in ("cue-repeat-first", "cue-repeat-second"):
        outcome = results[cue_id]
        assert isinstance(outcome, ApplicationCueOccurrences)
        assert len(outcome.occurrences) == 1
        occurrence = outcome.occurrences[0]
        expected_occurrence = expected[cue_id]["occurrences"][0]
        assert occurrence.cue_id == cue_id
        assert occurrence.temporal_position == pytest.approx(
            expected_occurrence["temporal_position"]
        )
        assert occurrence.score == pytest.approx(expected_occurrence["score"])
        assert occurrence.matching_method == expected_occurrence["matching_method"]
        assert occurrence.end is None

    # Repeated cue (acceptance criterion 1): two independent cue_ids
    # submitting the identical cue array resolve to the same position and
    # score, each correctly attributed to its own cue_id -- attribution
    # follows cue_id, not array identity or iteration order.
    first_occurrence = results["cue-repeat-first"].occurrences[0]
    second_occurrence = results["cue-repeat-second"].occurrences[0]
    assert first_occurrence.temporal_position == second_occurrence.temporal_position
    assert first_occurrence.score == second_occurrence.score

    # Absent cue (acceptance criterion 1): a legitimate no_match, never a
    # failure and never collapsed into the repeated cue's outcome.
    assert isinstance(results["cue-absent"], ApplicationCueNoMatch)


def test_cross_cue_overlap_is_two_distinct_cues_not_same_cue_multiplicity():
    manifest = _load_manifest()
    case = _case(manifest, "cross_cue_temporal_overlap")
    source, cues = _source_and_cues(case)
    expected = case["expected_per_cue_outcomes"]

    results = run_multi_cue_analysis(source, cues, DEFAULT_CONFIGURATION)

    windows: dict[str, set[int]] = {}
    for cue_id in ("cue-overlap-b", "cue-overlap-c"):
        outcome = results[cue_id]
        assert isinstance(outcome, ApplicationCueOccurrences)
        # Never same-cue multiplicity: exactly one occurrence per cue, per
        # the current matcher's 0..1-occurrence-per-cue-per-call contract
        # (docs/occurrence-temporal-semantics-and-policy.md).
        assert len(outcome.occurrences) == 1
        occurrence = outcome.occurrences[0]
        expected_occurrence = expected[cue_id]["occurrences"][0]
        assert occurrence.temporal_position == pytest.approx(
            expected_occurrence["temporal_position"]
        )
        assert occurrence.score == pytest.approx(expected_occurrence["score"])
        assert occurrence.score >= DEFAULT_CONFIGURATION.acceptance_threshold
        assert occurrence.end is None

        best_index = round(occurrence.temporal_position * CANONICAL_AUDIO_SPEC.sample_rate_hz)
        cue_length = len(case["cues"][cue_id])
        windows[cue_id] = set(range(best_index, best_index + cue_length))

    # Cross-cue temporal overlap (acceptance criterion 1): the two distinct
    # cues' occurrence windows intersect -- occurrences of different cues
    # are never grouped or deduplicated against each other, even though
    # they overlap in time.
    overlap = windows["cue-overlap-b"] & windows["cue-overlap-c"]
    assert overlap, "expected cue-overlap-b and cue-overlap-c windows to intersect"


def test_full_flow_maps_application_outcomes_into_core_analysis_result():
    manifest = _load_manifest()
    case = _case(manifest, "repeated_cue_and_absent_cue")
    source, cues = _source_and_cues(case)

    result = _run_full_flow(source, cues, analysis_id="m3-07-repeated-and-absent")

    assert result.final_state == "completed"
    outcomes_by_cue_id = {
        cue_result.cue_id: cue_result.outcome for cue_result in result.cues
    }
    assert isinstance(outcomes_by_cue_id["cue-repeat-first"], CueOccurrences)
    assert isinstance(outcomes_by_cue_id["cue-repeat-second"], CueOccurrences)
    assert isinstance(outcomes_by_cue_id["cue-absent"], CueNoMatch)

    # The mapping preserves values verbatim; it does not recompute them.
    application_results = run_multi_cue_analysis(source, cues, DEFAULT_CONFIGURATION)
    mapped_occurrence = outcomes_by_cue_id["cue-repeat-first"].occurrences[0]
    original_occurrence = application_results["cue-repeat-first"].occurrences[0]
    assert mapped_occurrence.temporal_position == original_occurrence.temporal_position
    assert mapped_occurrence.score == original_occurrence.score
    assert mapped_occurrence.matching_method == original_occurrence.matching_method


def test_full_flow_serialization_is_deterministic_across_two_independent_runs():
    """Acceptance criterion 3: runs the same Analysis input/configuration
    twice and compares the serialized result. Additional to, and
    independent of, M3-06's own tests/test_analysis_result_serialization.py.
    """

    manifest = _load_manifest()
    case = _case(manifest, "cross_cue_temporal_overlap")

    source_first, cues_first = _source_and_cues(case)
    result_first = _run_full_flow(
        source_first, cues_first, analysis_id="m3-07-overlap-determinism"
    )
    serialized_first = serialize_analysis_result(result_first)

    # Rebuilt from the manifest independently (by value, not by object
    # identity) for the second run.
    source_second, cues_second = _source_and_cues(case)
    assert source_first is not source_second
    result_second = _run_full_flow(
        source_second, cues_second, analysis_id="m3-07-overlap-determinism"
    )
    serialized_second = serialize_analysis_result(result_second)

    assert serialized_first == serialized_second
    # The serialized form must remain valid, parseable JSON.
    assert json.loads(serialized_first)["final_state"] == "completed"


def test_manifest_declares_exactly_the_three_required_fixture_cases():
    manifest = _load_manifest()
    case_ids = {case["case_id"] for case in manifest["cases"]}
    assert case_ids == {"repeated_cue_and_absent_cue", "cross_cue_temporal_overlap"}
    repeated_case = _case(manifest, "repeated_cue_and_absent_cue")
    assert set(repeated_case["cues"]) == {
        "cue-repeat-first",
        "cue-repeat-second",
        "cue-absent",
    }
    overlap_case = _case(manifest, "cross_cue_temporal_overlap")
    assert set(overlap_case["cues"]) == {"cue-overlap-b", "cue-overlap-c"}
