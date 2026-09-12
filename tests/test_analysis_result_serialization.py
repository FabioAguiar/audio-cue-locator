"""Tests for the M3-06 Core versioned Analysis Result and its canonical
JSON serialization.

These validate the formal-issue acceptance criteria that can be checked
without live representative media or a full Analysis/Application wiring
(`docs/analysis-result-schema.md`): repeated serialization of the same
input is byte-identical; every cue outcome variant (occurrences, no_match,
failure) round-trips through the canonical structure without collapsing
into another variant; `final_state` is derived from `structured_error`
presence, not from per-cue outcomes; `method` and
`configuration.matching.method` are required to agree; and the documented
canonical-JSON rules (fixed key order, null-for-absent fields, rejection
of non-finite numbers) hold.
"""

import json

import pytest

from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueFailure,
    CueNoMatch,
    CueOccurrences,
    CueResult,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    Occurrence,
    SCHEMA_VERSION,
    StructuredError,
    serialize_analysis_result,
    to_canonical_dict,
)


def _configuration() -> EffectiveConfigurationSnapshot:
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=48000,
            channels=1,
            sample_format="float32",
            normalization=NormalizationSnapshot(
                enabled=True,
                method="peak",
                target_peak_amplitude=1.0,
            ),
        ),
        matching=MatchingSnapshot(
            method="normalized_cross_correlation_v1",
            acceptance_threshold=0.7056698933077521,
        ),
        configuration_source_name="acoustic_matching.acceptance.EVIDENCE_BASED_CONFIGURATION",
    )


def _mixed_outcome_result() -> AnalysisResult:
    configuration = _configuration()
    return AnalysisResult(
        analysis_id="analysis-0001",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(
            CueResult(
                cue_id="cue-found",
                outcome=CueOccurrences(
                    occurrences=(
                        Occurrence(
                            cue_id="cue-found",
                            temporal_position=1.5,
                            score=0.912345,
                            matching_method="normalized_cross_correlation_v1",
                            end=None,
                        ),
                    )
                ),
            ),
            CueResult(cue_id="cue-no-match", outcome=CueNoMatch()),
            CueResult(
                cue_id="cue-failed",
                outcome=CueFailure(
                    category=FailureCategory.MATCHING_FAILURE,
                    message="single-cue matching failed while processing this cue",
                ),
            ),
        ),
    )


def test_repeated_serialization_of_the_same_instance_is_byte_identical():
    result = _mixed_outcome_result()

    first = serialize_analysis_result(result)
    second = serialize_analysis_result(result)

    assert first == second


def test_repeated_serialization_of_equal_but_distinct_instances_is_byte_identical():
    first = serialize_analysis_result(_mixed_outcome_result())
    second = serialize_analysis_result(_mixed_outcome_result())

    assert first == second


def test_serialized_output_is_valid_json_with_fixed_top_level_key_order():
    result = _mixed_outcome_result()

    serialized = serialize_analysis_result(result)
    parsed = json.loads(serialized)

    assert list(parsed.keys()) == [
        "schema_version",
        "analysis_id",
        "final_state",
        "method",
        "configuration",
        "cues",
        "structured_error",
    ]


def test_schema_version_defaults_to_the_initial_contract_value():
    result = _mixed_outcome_result()

    assert result.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == "analysis_result.v1"


def test_completed_final_state_has_no_structured_error():
    result = _mixed_outcome_result()

    payload = to_canonical_dict(result)

    assert result.final_state == "completed"
    assert payload["final_state"] == "completed"
    assert payload["structured_error"] is None


def test_failed_final_state_is_derived_from_structured_error_presence():
    configuration = _configuration()
    result = AnalysisResult(
        analysis_id="analysis-0002",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(CueResult(cue_id="cue-a", outcome=CueNoMatch()),),
        structured_error=StructuredError(
            category=FailureCategory.INVALID_INPUT,
            message="source must be a 1-D canonical mono array",
        ),
    )

    payload = to_canonical_dict(result)

    assert result.final_state == "failed"
    assert payload["final_state"] == "failed"
    assert payload["structured_error"] == {
        "category": "invalid_input",
        "message": "source must be a 1-D canonical mono array",
    }


def test_a_per_cue_failure_does_not_force_the_analysis_to_failed():
    configuration = _configuration()
    result = AnalysisResult(
        analysis_id="analysis-0003",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(
            CueResult(
                cue_id="cue-ok",
                outcome=CueOccurrences(
                    occurrences=(
                        Occurrence(
                            cue_id="cue-ok",
                            temporal_position=0.0,
                            score=0.99,
                            matching_method="normalized_cross_correlation_v1",
                        ),
                    )
                ),
            ),
            CueResult(
                cue_id="cue-broken",
                outcome=CueFailure(
                    category=FailureCategory.MATCHING_FAILURE,
                    message="single-cue matching failed while processing this cue",
                ),
            ),
        ),
    )

    assert result.final_state == "completed"


def test_occurrence_kind_serializes_occurrences_and_nulls_out_failure():
    result = _mixed_outcome_result()

    payload = to_canonical_dict(result)
    found_cue = next(c for c in payload["cues"] if c["cue_id"] == "cue-found")

    assert found_cue["outcome"]["kind"] == "occurrences"
    assert found_cue["outcome"]["failure"] is None
    assert found_cue["outcome"]["occurrences"] == [
        {
            "cue_id": "cue-found",
            "temporal_position": 1.5,
            "score": 0.912345,
            "matching_method": "normalized_cross_correlation_v1",
            "end": None,
        }
    ]


def test_no_match_kind_nulls_out_both_occurrences_and_failure():
    result = _mixed_outcome_result()

    payload = to_canonical_dict(result)
    no_match_cue = next(c for c in payload["cues"] if c["cue_id"] == "cue-no-match")

    assert no_match_cue["outcome"] == {
        "kind": "no_match",
        "occurrences": None,
        "failure": None,
    }


def test_failure_kind_nulls_out_occurrences_and_serializes_category_value():
    result = _mixed_outcome_result()

    payload = to_canonical_dict(result)
    failed_cue = next(c for c in payload["cues"] if c["cue_id"] == "cue-failed")

    assert failed_cue["outcome"] == {
        "kind": "failure",
        "occurrences": None,
        "failure": {
            "category": "matching_failure",
            "message": "single-cue matching failed while processing this cue",
        },
    }


def test_cues_preserve_construction_order_in_the_serialized_list():
    result = _mixed_outcome_result()

    payload = to_canonical_dict(result)

    assert [c["cue_id"] for c in payload["cues"]] == [
        "cue-found",
        "cue-no-match",
        "cue-failed",
    ]


def test_cue_occurrences_rejects_an_empty_collection():
    with pytest.raises(ValueError):
        CueOccurrences(occurrences=())


def test_analysis_id_must_be_non_empty():
    configuration = _configuration()
    with pytest.raises(ValueError):
        AnalysisResult(
            analysis_id="   ",
            method=configuration.matching.method,
            configuration=configuration,
            cues=(CueResult(cue_id="cue-a", outcome=CueNoMatch()),),
        )


def test_cues_must_be_non_empty():
    configuration = _configuration()
    with pytest.raises(ValueError):
        AnalysisResult(
            analysis_id="analysis-0004",
            method=configuration.matching.method,
            configuration=configuration,
            cues=(),
        )


def test_duplicate_cue_ids_are_rejected():
    configuration = _configuration()
    with pytest.raises(ValueError):
        AnalysisResult(
            analysis_id="analysis-0005",
            method=configuration.matching.method,
            configuration=configuration,
            cues=(
                CueResult(cue_id="cue-a", outcome=CueNoMatch()),
                CueResult(cue_id="cue-a", outcome=CueNoMatch()),
            ),
        )


def test_method_must_equal_configuration_matching_method():
    configuration = _configuration()
    with pytest.raises(ValueError):
        AnalysisResult(
            analysis_id="analysis-0006",
            method="a_different_method",
            configuration=configuration,
            cues=(CueResult(cue_id="cue-a", outcome=CueNoMatch()),),
        )


def test_structured_error_message_must_be_non_empty():
    with pytest.raises(ValueError):
        StructuredError(category=FailureCategory.INTERNAL_FAILURE, message="  ")


def test_cue_failure_message_must_be_non_empty():
    with pytest.raises(ValueError):
        CueFailure(category=FailureCategory.INTERNAL_FAILURE, message="")


def test_non_finite_score_is_rejected_at_serialization_time():
    configuration = _configuration()
    result = AnalysisResult(
        analysis_id="analysis-0007",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(
            CueResult(
                cue_id="cue-a",
                outcome=CueOccurrences(
                    occurrences=(
                        Occurrence(
                            cue_id="cue-a",
                            temporal_position=0.0,
                            score=float("nan"),
                            matching_method="normalized_cross_correlation_v1",
                        ),
                    )
                ),
            ),
        ),
    )

    with pytest.raises(ValueError):
        serialize_analysis_result(result)
