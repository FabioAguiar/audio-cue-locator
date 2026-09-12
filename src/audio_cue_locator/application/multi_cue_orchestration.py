"""Application: multi-cue orchestration over the M2 single-cue matcher (M3-03).

M3's objective requires an Analysis to coordinate more than one Cue; M2 only
validated locating a single cue in source audio (`match_cue`,
`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`). This
module closes that gap at the Application level, without building a second,
parallel matching pipeline (formal issue M3-03, high-severity risk) and
without introducing any new numeric matching, scoring, correlation, or
acceptance logic of its own: `run_multi_cue_analysis` below calls the
existing, unmodified `match_cue` exactly once per cue and does nothing else
with the numbers it returns beyond collecting them.

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
- G2 (per-cue non-FOUND outcome representation): no new occurrence-selection
  or no-match-classification type is introduced. The per-cue outcome is
  `MatchResult` itself (`baseline.py`), unmodified and re-exported by
  reference, for every one of its four `MatchOutcome` values -- FOUND,
  NO_MATCH, INVALID_INPUT, and PROCESSING_FAILURE alike. This is the
  narrowest representation that satisfies the formal issue's own
  acceptance criterion 2 (expose per-cue outcomes through the M3-01
  Occurrence/Analysis contracts "without inventing a parallel result
  shape"): `Occurrence` (`docs/analysis-core-contracts.md`, section 3)
  already documents that it represents only the `found` case and that the
  per-cue no-match/processing-failure distinction at the
  `Analysis.result_or_reference`/`structured_error` level is M3-05's own
  scope, not this issue's. Converting a `dict[str, MatchResult]` into actual
  `Occurrence`/`structured_error` values is exactly that M3-05 conversion
  step, deliberately left undone here.
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
audio` mapping, and the result is a `cue_id -> MatchResult` mapping built by
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
selection/deduplication policy (M3-04); the per-cue no-match/processing-
failure distinction beyond what `MatchResult` already exposes (M3-05); a
REST API, a WebUI, or persistence of orchestration state; and the versioned
Analysis Result and its serialization (M3-06).

docs/multi-cue-orchestration.md documents this module for human review.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from audio_cue_locator.infrastructure.acoustic_matching import (
    EffectiveConfiguration,
    MatchResult,
    match_cue,
)


def run_multi_cue_analysis(
    source: np.ndarray,
    cues: Mapping[str, np.ndarray],
    configuration: EffectiveConfiguration,
) -> dict[str, MatchResult]:
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

    Returns a `dict` mapping each input `cue_id` to the `MatchResult`
    `match_cue` produced for that cue -- one call, one attributed result,
    for every cue, regardless of that cue's outcome (`FOUND`, `NO_MATCH`,
    `INVALID_INPUT`, or `PROCESSING_FAILURE`); no cue is silently dropped or
    filtered out of the returned mapping based on its outcome.

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

    return {
        cue_id: match_cue(source, cue_asset, configuration)
        for cue_id, cue_asset in cues.items()
    }
