"""Core: the versioned Analysis Result contract and its deterministic JSON
serialization (M3-06).

M3's objective requires one authoritative, versioned, exportable JSON
representation of an Analysis outcome that later persistence and API
milestones (M3-07 and beyond) adopt instead of redefining
(`issues/M3/M3-06/formal-issue.json`). This module closes that gap: it
defines `AnalysisResult` and its supporting Core value types, and a
canonical serializer that produces byte-identical JSON for repeated calls
on the same input (formal issue acceptance criterion 4).

This module is additive Core: it does not modify, and does not import,
`src/audio_cue_locator/application/multi_cue_orchestration.py` (M3-03/
M3-05), `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`
(M2-02), `src/audio_cue_locator/infrastructure/acoustic_matching/
acceptance.py` (M2-05), or `src/audio_cue_locator/infrastructure/
media_processing/canonical_audio.py` (M1-03). Application already holds an
executable projection of the per-cue outcome union
(`CueOccurrences`/`CueNoMatch`/`CueFailure` in `multi_cue_orchestration.py`)
that is structurally equivalent to `CueOccurrences`/`CueNoMatch`/
`CueFailure` below; Core declares its own equivalent types here, rather
than importing Application's, because Application depends on Core and not
the reverse (`docs/architecture.md`, Principle 1; `docs/analysis-core-
contracts.md`, section 5). Adapting an already-produced Application
per-cue outcome mapping into these Core types, and attaching an
`AnalysisResult` to a full `Analysis` lifecycle, is deliberately left to a
future integration issue; this issue defines and serializes the
export contract itself, not the end-to-end wiring
(`issues/M3/M3-06/formal-issue.json`, section 4, "Não inclui").

The field vocabulary below is not invented here: `Occurrence`, the
`CueOutcome` union, and `FailureCategory` mirror the normative Core
contract already fixed by `docs/analysis-core-contracts.md` (M3-01/M3-05)
and `docs/occurrence-temporal-semantics-and-policy.md` (M3-04);
`EffectiveConfigurationSnapshot` mirrors `docs/analysis-effective-
configuration.md` (M3-02) verbatim. `docs/analysis-result-schema.md`
documents the schema and the canonical-serialization decisions below
(final-state derivation, analysis_id ownership, canonical JSON rules,
configuration-metadata reuse, structured-error taxonomy, and the initial
`schema_version` value) for human review; this module is their executable
form.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Union

SCHEMA_VERSION = "analysis_result.v1"
"""Initial contract_version-style identifier for this schema (`docs/
analysis-result-schema.md`). A future backward-incompatible change to the
fields, semantics, or canonical-serialization rules fixed by this module
requires a new value (for example `"analysis_result.v2"`); this schema is
never silently redefined under the same value (formal issue acceptance
criterion 5)."""


class FailureCategory(str, Enum):
    """Stable architecture failure-category identifiers (`docs/
    architecture.md`, "Rastreabilidade de falhas"), reused unchanged by the
    Result contract (`docs/analysis-core-contracts.md`, section 3.1). This
    is the same closed set of values as Application's identical projection
    in `multi_cue_orchestration.py`; the two enums are declared
    independently (Core does not import Application) but compare equal by
    string value, and only the string value is ever serialized."""

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MEDIA = "unsupported_media"
    DECODE_OR_CANONICALIZATION_FAILURE = "decode_or_canonicalization_failure"
    MATCHING_FAILURE = "matching_failure"
    RESOURCE_LIMIT = "resource_limit"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True)
class StructuredError:
    """An Analysis-scoped technical failure (`docs/analysis-core-
    contracts.md`, section 1, `Analysis.structured_error`): present only
    when the shared, pre-per-cue processing could not proceed. Distinct
    from a per-cue `CueFailure`; the two are never aggregated into one
    another."""

    category: FailureCategory
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("StructuredError.message must be non-empty")


@dataclass(frozen=True)
class Occurrence:
    """One accepted matcher occurrence for a specific cue (`docs/analysis-
    core-contracts.md`, section 3; `docs/occurrence-temporal-semantics-
    and-policy.md`, section 1). `temporal_position` is mandatory; `end` is
    optional and method-dependent, absent for `normalized_cross_
    correlation_v1` today."""

    cue_id: str
    temporal_position: float
    score: float
    matching_method: str
    end: float | None = None


@dataclass(frozen=True)
class CueOccurrences:
    """The matched branch of the per-cue outcome union. Must contain one
    or more occurrences; an empty collection is not a valid alias for
    `CueNoMatch` (`docs/analysis-core-contracts.md`, section 3.1)."""

    occurrences: tuple[Occurrence, ...]
    kind: Literal["occurrences"] = field(default="occurrences", init=False)

    def __post_init__(self) -> None:
        if not self.occurrences:
            raise ValueError(
                "CueOccurrences requires at least one occurrence; use "
                "CueNoMatch or CueFailure for the other outcome branches"
            )


@dataclass(frozen=True)
class CueNoMatch:
    """A legitimate completed matching conclusion with no accepted
    occurrence for this cue. Never inferred from an empty collection and
    never equivalent to a technical failure."""

    kind: Literal["no_match"] = field(default="no_match", init=False)


@dataclass(frozen=True)
class CueFailure:
    """A safe, structured technical failure owned by exactly one cue."""

    category: FailureCategory
    message: str
    kind: Literal["failure"] = field(default="failure", init=False)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("CueFailure.message must be non-empty")


CueOutcome = Union[CueOccurrences, CueNoMatch, CueFailure]
"""Closed, mutually exclusive per-cue outcome union (`docs/analysis-core-
contracts.md`, section 3.1)."""


@dataclass(frozen=True)
class CueResult:
    """One cue's identity paired with its independent outcome, in the
    order it appears within the owning `AnalysisResult` (canonical
    serialization order rule, `docs/analysis-result-schema.md`)."""

    cue_id: str
    outcome: CueOutcome


@dataclass(frozen=True)
class NormalizationSnapshot:
    """Canonicalization normalization parameters in effect (`docs/
    analysis-effective-configuration.md`, section 2; structurally
    compatible with `CanonicalAudioSpec.normalization`)."""

    enabled: bool
    method: str
    target_peak_amplitude: float


@dataclass(frozen=True)
class CanonicalizationSnapshot:
    """Copy of the M1 canonicalization parameters in effect for this
    Analysis (`docs/analysis-effective-configuration.md`, section 2;
    structurally compatible with `CanonicalAudioSpec`, never importing that
    Infrastructure type)."""

    sample_rate_hz: int
    channels: int
    sample_format: str
    normalization: NormalizationSnapshot


@dataclass(frozen=True)
class MatchingSnapshot:
    """Copy of the M2 matching method identity and its acceptance/no-match
    policy in effect for this Analysis (`docs/analysis-effective-
    configuration.md`, section 2; structurally compatible with
    `EffectiveConfiguration`, never importing that Infrastructure type)."""

    method: str
    acceptance_threshold: float


@dataclass(frozen=True)
class EffectiveConfigurationSnapshot:
    """The complete effective-configuration snapshot attached to an
    Analysis: canonicalization + matching, captured as a value at the
    point of use, plus the named source of the matching/acceptance policy
    (`docs/analysis-effective-configuration.md`, sections 2 and 4)."""

    canonicalization: CanonicalizationSnapshot
    matching: MatchingSnapshot
    configuration_source_name: str


FinalState = Literal["completed", "failed"]
"""Closed final-state vocabulary for a serialized Analysis Result (`docs/
analysis-result-schema.md`). Derived, never supplied directly -- see
`AnalysisResult.final_state`."""


@dataclass(frozen=True)
class AnalysisResult:
    """The versioned, transport- and persistence-independent Analysis
    Result (`issues/M3/M3-06/formal-issue.json`, sections 3 and 4).

    `analysis_id` is a caller-supplied opaque, non-empty, stable
    identifier: this type and its serializer never generate, hash, or
    otherwise derive it (`docs/analysis-result-schema.md`, identifier
    ownership decision). `method` must equal
    `configuration.matching.method`; the two are validated to agree so
    they cannot silently diverge.

    `final_state` is not a constructor input: it is derived from whether
    `structured_error` is present, mirroring the SUCCEEDED/FAILED
    distinction already fixed by `docs/analysis-core-contracts.md`,
    section 4. A per-cue `CueFailure` inside `cues` does not, by itself,
    make `final_state` `"failed"`: only an Analysis-scoped
    `structured_error` does, so that one cue's independent technical
    failure never hides another cue's legitimate occurrences or no_match
    outcome.
    """

    analysis_id: str
    method: str
    configuration: EffectiveConfigurationSnapshot
    cues: tuple[CueResult, ...]
    structured_error: StructuredError | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.analysis_id.strip():
            raise ValueError("AnalysisResult.analysis_id must be non-empty")
        if not self.cues:
            raise ValueError(
                "AnalysisResult requires at least one cue result "
                "(docs/analysis-core-contracts.md, section 1: 'cues' is a "
                "non-empty sequence)"
            )
        seen_cue_ids: set[str] = set()
        for cue_result in self.cues:
            if cue_result.cue_id in seen_cue_ids:
                raise ValueError(
                    f"duplicate cue_id in AnalysisResult.cues: {cue_result.cue_id!r}"
                )
            seen_cue_ids.add(cue_result.cue_id)
        if self.method != self.configuration.matching.method:
            raise ValueError(
                "AnalysisResult.method must equal "
                "configuration.matching.method "
                f"({self.method!r} != {self.configuration.matching.method!r})"
            )

    @property
    def final_state(self) -> FinalState:
        return "failed" if self.structured_error is not None else "completed"


def to_canonical_dict(result: AnalysisResult) -> dict:
    """Project an `AnalysisResult` into the canonical JSON-ready structure.

    Field order, key presence, and null-for-absence rules below are the
    canonical serialization policy documented in `docs/analysis-result-
    schema.md`: every structurally defined field is always present in its
    object (never omitted based on its value), using JSON `null` for a
    structurally absent value. Exposed for callers (a future REST layer,
    for example) that need the canonical structure embedded inside a
    larger document without re-deriving these rules; `serialize_analysis_
    result` is the byte-level canonical form of this same structure.
    """

    return {
        "schema_version": result.schema_version,
        "analysis_id": result.analysis_id,
        "final_state": result.final_state,
        "method": result.method,
        "configuration": _configuration_to_dict(result.configuration),
        "cues": [_cue_result_to_dict(cue_result) for cue_result in result.cues],
        "structured_error": (
            _structured_error_to_dict(result.structured_error)
            if result.structured_error is not None
            else None
        ),
    }


def serialize_analysis_result(result: AnalysisResult) -> str:
    """Serialize `result` to its canonical JSON string.

    Deterministic by construction (`docs/analysis-result-schema.md`):
    fixed key order (dict literals below, `sort_keys=False`), fixed cue
    order (`result.cues` order, preserved by the tuple), stdlib
    shortest-round-trip float encoding, `allow_nan=False` (raises rather
    than emitting non-finite JSON numbers), ASCII-safe output
    (`ensure_ascii=True`), and compact separators with no incidental
    whitespace or trailing newline. Two calls on the same input (by value,
    not by object identity) produce byte-identical output.
    """

    payload = to_canonical_dict(result)
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _configuration_to_dict(configuration: EffectiveConfigurationSnapshot) -> dict:
    canonicalization = configuration.canonicalization
    matching = configuration.matching
    return {
        "canonicalization": {
            "sample_rate_hz": canonicalization.sample_rate_hz,
            "channels": canonicalization.channels,
            "sample_format": canonicalization.sample_format,
            "normalization": {
                "enabled": canonicalization.normalization.enabled,
                "method": canonicalization.normalization.method,
                "target_peak_amplitude": canonicalization.normalization.target_peak_amplitude,
            },
        },
        "matching": {
            "method": matching.method,
            "acceptance_threshold": matching.acceptance_threshold,
        },
        "configuration_source_name": configuration.configuration_source_name,
    }


def _occurrence_to_dict(occurrence: Occurrence) -> dict:
    return {
        "cue_id": occurrence.cue_id,
        "temporal_position": occurrence.temporal_position,
        "score": occurrence.score,
        "matching_method": occurrence.matching_method,
        "end": occurrence.end,
    }


def _failure_category_value(category: FailureCategory) -> str:
    return category.value if isinstance(category, FailureCategory) else str(category)


def _structured_error_to_dict(structured_error: StructuredError) -> dict:
    return {
        "category": _failure_category_value(structured_error.category),
        "message": structured_error.message,
    }


def _cue_outcome_to_dict(outcome: CueOutcome) -> dict:
    if isinstance(outcome, CueOccurrences):
        return {
            "kind": "occurrences",
            "occurrences": [_occurrence_to_dict(o) for o in outcome.occurrences],
            "failure": None,
        }
    if isinstance(outcome, CueNoMatch):
        return {"kind": "no_match", "occurrences": None, "failure": None}
    if isinstance(outcome, CueFailure):
        return {
            "kind": "failure",
            "occurrences": None,
            "failure": {
                "category": _failure_category_value(outcome.category),
                "message": outcome.message,
            },
        }
    raise TypeError(f"unknown CueOutcome variant: {type(outcome)!r}")


def _cue_result_to_dict(cue_result: CueResult) -> dict:
    return {
        "cue_id": cue_result.cue_id,
        "outcome": _cue_outcome_to_dict(cue_result.outcome),
    }
