"""Application: multi-cue orchestration and explicit per-cue outcomes (M3-03/M3-05).

M3's objective requires an Analysis to coordinate more than one Cue; M2 only
validated locating a single cue in source audio (`match_cue`,
`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`). This
module closes that gap at the Application level, without building a second,
parallel matching pipeline (formal issue M3-03, high-severity risk) and
without introducing any new numeric matching, scoring, correlation, or
acceptance logic of its own: `run_multi_cue_analysis` below calls the
existing, unmodified `match_cue` exactly once per cue and projects each
returned value into the Core outcome union without recomputing its numbers.

Resolves the gaps `intents/M3/M3-03/implementation-handoff.json` deliberately
left for this module to decide (G1, G2, G7):

- G1 (orchestration API shape): a single function, not a class. No Core
  `Analysis`/`Cue` Python type exists yet to accept as a parameter --
  `docs/analysis-core-contracts.md` (M3-01) and
  `docs/analysis-effective-configuration.md` (M3-02) fix those contracts
  only at the conceptual/documentary level, and `src/audio_cue_locator/core/`
  defines no corresponding dataclasses. `run_multi_cue_analysis` therefore
  takes the three concrete inputs an Analysis conceptually carries --
  the canonicalized source audio, its cues, and its effective matching
  configuration -- as plain parameters, rather than requiring a caller to
  first construct an object this codebase does not yet define.
- G2 (per-cue outcome representation): M3-05 now fixes the normative Core
  distinction in `docs/analysis-core-contracts.md`. Each Infrastructure
  `MatchResult` is projected into exactly one immutable variant:
  `CueOccurrences`, `CueNoMatch`, or `CueFailure`. Application consumes
  and preserves that contract; it does not infer no-match from an empty
  collection or expose the Infrastructure outcome enum as Core semantics.
- G7 (configuration re-read per cue vs. per Analysis): `configuration` is a
  single parameter for the whole call, read once by the caller from the
  Analysis's own `effective_configuration.matching`
  (`docs/analysis-effective-configuration.md`, section 2) and passed
  unchanged to every `match_cue` invocation. This mirrors that document's
  own point-of-use-capture principle (section 4) instead of re-deriving a
  competing per-cue re-read rule.

`cue_id` attribution (formal issue acceptance criterion 2; operational-state
risk `validation_risk`, "cues are processed but their outcomes are not
correctly attributed back to the originating cue") is structural, not
positional: cues are supplied as a `cue_id -> already-canonicalized cue
audio` mapping, and the result is a `cue_id -> PerCueOutcome` mapping built by
iterating that same mapping's items -- there is no separate index-based or
order-dependent step where a mismatch could be introduced.

This module holds `numpy.ndarray` values purely as opaque, already-
canonicalized audio buffers (`src/audio_cue_locator/infrastructure/
media_processing/canonical_audio.py`, M1-03) to pass through to `match_cue`;
it performs no numeric computation of its own -- no correlation, no scoring,
no threshold comparison -- and does not import `scipy` or reimplement any
part of `baseline.py`. Out of scope, per the formal issue and the
operational state alike: any new matching algorithm or change to the M2
baseline; parallel or asynchronous execution of cues; multiple-occurrence
selection/deduplication policy (M3-04); a REST API, a WebUI, or persistence
of orchestration state; and the versioned Analysis Result and its
serialization (M3-06).

docs/multi-cue-orchestration.md documents this module for human review.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, TypeAlias

import numpy as np

from audio_cue_locator.infrastructure.acoustic_matching import (
    EffectiveConfiguration,
    MatchOutcome,
    MatchResult,
    match_cue,
)


class FailureCategory(str, Enum):
    """Architecture failure categories that may apply at this boundary.

    These values are stable machine identifiers for the existing categories
    in docs/architecture.md ("Rastreabilidade de falhas"); they do not add
    categories. Persistence failure is intentionally absent because it is an
    Analysis/result concern, never the outcome of matching one cue.
    """

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MEDIA = "unsupported_media"
    DECODE_OR_CANONICALIZATION_FAILURE = "decode_or_canonicalization_failure"
    MATCHING_FAILURE = "matching_failure"
    RESOURCE_LIMIT = "resource_limit"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True)
class Occurrence:
    """Transport-independent projection of one accepted matcher occurrence."""

    cue_id: str
    temporal_position: float
    score: float
    matching_method: str
    end: float | None = None


@dataclass(frozen=True)
class CueOccurrences:
    """The matched branch of the Core per-cue outcome union."""

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
    """A legitimate completed match attempt with no accepted occurrence."""

    kind: Literal["no_match"] = field(default="no_match", init=False)


@dataclass(frozen=True)
class CueFailure:
    """A safe, structured technical failure owned by one cue."""

    category: FailureCategory
    message: str
    kind: Literal["failure"] = field(default="failure", init=False)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("CueFailure.message must be non-empty")


PerCueOutcome: TypeAlias = CueOccurrences | CueNoMatch | CueFailure


class AnalysisSourceInputError(ValueError):
    """Shared-source precondition failure, intentionally not copied per cue."""

    category = FailureCategory.INVALID_INPUT


def _validate_shared_source(source: np.ndarray) -> None:
    """Validate shared M1/M2 array invariants once before cue iteration."""

    if not isinstance(source, np.ndarray):
        raise AnalysisSourceInputError("source must be a numpy.ndarray instance")
    if source.ndim != 1:
        raise AnalysisSourceInputError("source must be a 1-D canonical mono array")
    if source.dtype != np.float32:
        raise AnalysisSourceInputError("source must use canonical float32 samples")
    if not np.isfinite(source).all():
        raise AnalysisSourceInputError("source contains non-finite samples")


def _to_per_cue_outcome(cue_id: str, result: MatchResult) -> PerCueOutcome:
    """Project one M2 result into exactly one M3-05 Core outcome variant."""

    if result.outcome is MatchOutcome.FOUND:
        if result.timestamp_seconds is None or result.score is None:
            return CueFailure(
                category=FailureCategory.INTERNAL_FAILURE,
                message="single-cue matcher returned an incomplete found result",
            )
        return CueOccurrences(
            occurrences=(
                Occurrence(
                    cue_id=cue_id,
                    temporal_position=result.timestamp_seconds,
                    score=result.score,
                    matching_method=result.configuration.method,
                    # M3-04 fixes end as absent for the current method.
                    end=None,
                ),
            )
        )

    if result.outcome is MatchOutcome.NO_MATCH:
        # Rejected-candidate diagnostics remain diagnostics; they are never
        # promoted to an Occurrence or used as an implicit failure.
        return CueNoMatch()

    if result.outcome is MatchOutcome.INVALID_INPUT:
        return CueFailure(
            category=FailureCategory.INVALID_INPUT,
            message=result.reason or "cue input is invalid",
        )

    if result.outcome is MatchOutcome.PROCESSING_FAILURE:
        # Do not carry a raw exception string across the Core boundary.
        return CueFailure(
            category=FailureCategory.MATCHING_FAILURE,
            message="single-cue matching failed while processing this cue",
        )

    return CueFailure(
        category=FailureCategory.INTERNAL_FAILURE,
        message="single-cue matcher returned an unknown outcome",
    )


def run_multi_cue_analysis(
    source: np.ndarray,
    cues: Mapping[str, np.ndarray],
    configuration: EffectiveConfiguration,
) -> dict[str, PerCueOutcome]:
    """Locate every cue of an Analysis within its shared source audio.

    Calls `match_cue` (`baseline.py`, M2-02) exactly once per entry of
    `cues`, against the same `source`, using `configuration` unchanged for
    every call -- never a module-level default such as
    `baseline.DEFAULT_CONFIGURATION` or `acceptance.EVIDENCE_BASED_CONFIGURATION`
    read directly by this function. Callers must supply the
    `EffectiveConfiguration` already attached to the Analysis itself (its
    `effective_configuration.matching`, `docs/analysis-effective-
    configuration.md` section 2), so that two runs of the same Analysis
    cannot silently diverge by reading a global default at a different time
    (operational-state execution risk, high severity).

    `source` and every value in `cues` must already satisfy
    `CANONICAL_AUDIO_SPEC` (mono, float32, 48000 Hz); like `match_cue`
    itself, this function does not canonicalize them
    (`docs/matching-contract.md`, section 1).

    Returns a `dict` mapping each input `cue_id` to exactly one explicit
    Core outcome variant. FOUND becomes a non-empty `CueOccurrences`;
    NO_MATCH becomes `CueNoMatch`; INVALID_INPUT and PROCESSING_FAILURE
    become structured `CueFailure` values with architecture-derived
    categories. No cue is silently dropped based on its matcher outcome.

    Shared-source validation happens once before cue iteration. A shared
    source precondition failure raises `AnalysisSourceInputError` and is not
    duplicated as one failure per cue. Media/decode/canonicalization failures
    before this already-canonicalized boundary remain source-scoped.

    Raises `ValueError` if `cues` is empty: an Analysis is defined as
    coordinating a non-empty sequence of Cue (`docs/analysis-core-
    contracts.md`, section 1), so an empty mapping is a caller precondition
    violation, not a valid zero-cue Analysis to represent as an empty
    result.
    """

    if not cues:
        raise ValueError(
            "An Analysis must coordinate at least one Cue "
            "(docs/analysis-core-contracts.md, section 1: 'cues' is a "
            "non-empty sequence); received an empty cues mapping."
        )

    _validate_shared_source(source)

    return {
        cue_id: _to_per_cue_outcome(
            cue_id,
            match_cue(source, cue_asset, configuration),
        )
        for cue_id, cue_asset in cues.items()
    }
