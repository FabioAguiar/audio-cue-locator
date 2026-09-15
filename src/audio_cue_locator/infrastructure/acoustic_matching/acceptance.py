"""Initial acceptance/no-match decision policy for the M2-02 baseline (M2-05).

Resolves the two architectural gaps intents/M2/M2-05/implementation-handoff.json
deliberately left for this module to decide (G1, G2), and the methodology/
configurability gaps it narrowed (G7, G8):

- G1 (integration mechanism): this module does not implement a second,
  downstream decision layer that inspects or overrides an already-produced
  MatchResult. It only defines a new, explicit EffectiveConfiguration value
  that a caller supplies *into* the existing, unmodified `match_cue` call
  (baseline.py's own `best_score >= configuration.acceptance_threshold`
  branch performs the actual accept/reject decision). This preserves
  docs/matching-contract.md section 5.2's boundary: a no_match result's
  retained diagnostic_best_score is evidence, never a route to reclassifying
  that same result as found after the fact.
- G2 (baseline modification): baseline.py, including its own
  DEFAULT_CONFIGURATION (acceptance_threshold=0.75), is not modified by this
  module. EVIDENCE_BASED_CONFIGURATION below is additive: a second, named
  EffectiveConfiguration instance, reusing the same dataclass baseline.py
  already defines and already accepts as a `match_cue` parameter.
- G7 (derivation methodology): the threshold is derived by an explicit,
  non-calibrated margin rule -- see `derive_acceptance_threshold` -- bounded
  by the highest observed no_match/diagnostic score and the lowest observed
  found score already recorded by docs/matching-regression.md (M2-03) and
  docs/matching-robustness.md (M2-04), never invented or assumed equal to
  baseline.py's own provisional 0.75.
- G8 (configurability): configurability is exposed the same way baseline.py
  itself already exposes DEFAULT_CONFIGURATION -- a traceable, inspectable
  module-level constant of the existing EffectiveConfiguration shape (method
  + acceptance_threshold) -- not a new configuration type or public API.

docs/matching-acceptance.md is the human-readable record of this derivation,
its cue-type/corpus-size limitations, and the G1/G2 integration decision;
this module is the executable expression of that same decision.

Score remains method-specific similarity, never a calibrated probability or a
value comparable across matching methods (docs/matching-contract.md, section
4). S0009 adds a second named configuration that reuses the historical
numeric cutoff for candidate acceptance without claiming new calibration.
"""

from __future__ import annotations

from audio_cue_locator.infrastructure.acoustic_matching.baseline import (
    EffectiveConfiguration,
)

_LOWEST_OBSERVED_FOUND_SCORE = 0.9380619505661985
"""docs/matching-robustness.md section 5: found_representative_noise_high_snr,
the lowest score among every `found` case already recorded by M2-03
(exact-match, score 1.0) and M2-04 (amplitude-perturbed, score 1.0; noise
low-SNR, score 0.9926253634032015; noise high-SNR, score
0.9380619505661985 -- the minimum of the four)."""

_HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE = 0.47327783604930573
"""docs/matching-robustness.md section 5: no_match_representative_absent_cue,
the highest diagnostic_best_score among every `no_match` case already
recorded by M2-03 (degenerate cases, diagnostic score exactly 0.0) and M2-04
(discrimination case, diagnostic score ≈0.47327783604930573 -- the maximum of
the two)."""


def derive_acceptance_threshold(
    lowest_observed_found_score: float = _LOWEST_OBSERVED_FOUND_SCORE,
    highest_observed_no_match_diagnostic_score: float = _HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE,
) -> float:
    """Derive an evidence-bounded acceptance threshold from recorded scores.

    Non-calibrated margin rule (state gap G7): place the threshold at the
    midpoint of the gap between the highest observed no_match/diagnostic
    score and the lowest observed found score already recorded by
    docs/matching-regression.md and docs/matching-robustness.md. This is a
    margin, not a statistical calibration: it makes no claim about the
    probability of a future score falling on either side, only that the
    chosen cutoff sits with an equal, explicit margin away from both boundary
    observations already in evidence.

    Raises ValueError if the two observations do not leave a positive gap for
    a threshold to be derived from -- that would mean the evidence itself is
    contradictory (a no_match case scored at or above a found case), which
    this module refuses to paper over with an arbitrary value.
    """

    if highest_observed_no_match_diagnostic_score >= lowest_observed_found_score:
        raise ValueError(
            "cannot derive an acceptance threshold: highest observed "
            f"no_match/diagnostic score ({highest_observed_no_match_diagnostic_score!r}) "
            "is not strictly below the lowest observed found score "
            f"({lowest_observed_found_score!r}); the evidence corpus does not "
            "leave a positive gap to place a threshold in"
        )

    return (highest_observed_no_match_diagnostic_score + lowest_observed_found_score) / 2.0


EVIDENCE_BASED_ACCEPTANCE_THRESHOLD = derive_acceptance_threshold()
"""The justified initial threshold: the midpoint between
_HIGHEST_OBSERVED_NO_MATCH_DIAGNOSTIC_SCORE (≈0.47327783604930573) and
_LOWEST_OBSERVED_FOUND_SCORE (≈0.9380619505661985), i.e. ≈0.7056698933077521.

This differs from baseline.py's own provisional DEFAULT_CONFIGURATION.acceptance_threshold
(0.75): 0.75 was never derived from the M2-03/M2-04 evidence (it predates
M2-04's evidence entirely) and is documented by docs/matching-baseline.md
itself as a non-calibrated placeholder "subject to review by M2-05". The
value derived here happens to be lower than 0.75, but still comfortably above
the highest observed no_match/diagnostic score and comfortably below the
lowest observed found score, so it does not change the outcome of any
already-recorded M2-03/M2-04 case (see docs/matching-acceptance.md for the
worked comparison)."""


EVIDENCE_BASED_CONFIGURATION = EffectiveConfiguration(
    method="normalized_cross_correlation_v1",
    acceptance_threshold=EVIDENCE_BASED_ACCEPTANCE_THRESHOLD,
)
"""This issue's evidence-justified EffectiveConfiguration (state gap G8):
a named, traceable, inspectable module-level constant, reusing baseline.py's
own EffectiveConfiguration dataclass and method identifier unchanged.

Callers who want the evidence-backed initial acceptance/no-match policy
instead of baseline.py's own provisional default must supply this value
explicitly to `match_cue`:

    from audio_cue_locator.infrastructure.acoustic_matching.acceptance import (
        EVIDENCE_BASED_CONFIGURATION,
    )
    from audio_cue_locator.infrastructure.acoustic_matching.baseline import match_cue

    result = match_cue(source, cue, EVIDENCE_BASED_CONFIGURATION)

This is the only sanctioned integration mechanism (state gap G1): a
parametric configuration supplied into the existing, unmodified match_cue
call. This module does not wrap, call, or re-decide match_cue's own already-
produced MatchResult.outcome; found vs. no_match remains entirely
baseline.py's own `best_score >= configuration.acceptance_threshold`
decision (docs/matching-contract.md section 5, section 5.2), applied here to
a different, explicit, evidence-justified acceptance_threshold value.

Scope: this configuration is specific to the "normalized_cross_correlation_v1"
method (docs/matching-baseline.md section 1) and its own recorded evidence
corpus. It is not a calibrated confidence value, not a universal threshold
across future matching methods, and not claimed comparable across methods
(docs/matching-contract.md section 4). It is derived from, and limited by,
a small corpus: six M2-03 synthetic cases and six M2-04 representative-
harmonic cases, covering one harmonic-burst cue morphology, two additive-
noise SNR levels, and no lossy-compression perturbation (docs/matching-
robustness.md section 3.2, section 10). See docs/matching-acceptance.md for
the full rationale and limitations.
"""


EVIDENCE_BASED_MULTI_OCCURRENCE_CONFIGURATION = EffectiveConfiguration(
    method="normalized_cross_correlation_multi_v1",
    acceptance_threshold=EVIDENCE_BASED_ACCEPTANCE_THRESHOLD,
)
"""Initial configuration for the S0009 multi-occurrence producer.

It deliberately reuses the numeric cutoff derived from the historical
single-occurrence evidence corpus. This is candidate acceptance policy, not
new calibration and not a probability or confidence interpretation.
"""
