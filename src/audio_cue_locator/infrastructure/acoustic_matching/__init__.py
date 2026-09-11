"""Acoustic Matching: single-cue matching baseline (M2-02).

Implements the first NumPy-based single-cue matcher against the internal
contract fixed by M2-01 (docs/matching-contract.md). Numerical matching
stays confined to this Infrastructure package; Core and Application must not
implement matching algorithms directly (docs/architecture.md, "Acoustic
Matching"; Princípios e Restrições #1).
"""

from .baseline import (
    DEFAULT_CONFIGURATION,
    EffectiveConfiguration,
    MatchOutcome,
    MatchResult,
    match_cue,
)

__all__ = [
    "DEFAULT_CONFIGURATION",
    "EffectiveConfiguration",
    "MatchOutcome",
    "MatchResult",
    "match_cue",
]
