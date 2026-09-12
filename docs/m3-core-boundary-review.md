# M3 Core Boundary Review

Status: reviewed. Scope: `issues/M3/M3-07/formal-issue.json`, acceptance
criterion 4 ("A review checklist confirms Core types remain free of
FastAPI, Pydantic-as-transport, SQLite, filesystem, and FFmpeg
dependencies") and criterion 5 ("The evidence produced is reproducible and
versioned, not based on manual inspection alone").

This document is evidence/tooling-only, per
`intents/M3/M3-07/implementation-handoff.json` scope_notes G6: it records a
per-file import-scan review of `src/audio_cue_locator/core/`, a narrative
confirmation that `src/audio_cue_locator/application/multi_cue_orchestration.py`
performs no matching logic of its own, and a Definition-of-Done evidence
summary. It does not modify, and is not itself, any Core or Application
source file.

## 1. Per-file import-scan table

Verification method for every row below: direct source inspection of the
current file's `import`/`from ... import` statements (this review), not
static analysis tooling and not a claim about transitive dependencies of
the standard library itself.

| File | Full import list | FastAPI | Pydantic (as transport) | SQLite | Filesystem access | FFmpeg / subprocess | Conclusion |
|---|---|---|---|---|---|---|---|
| `src/audio_cue_locator/core/__init__.py` | *(none)* | No | No | No | No | No | Pass -- no imports at all. |
| `src/audio_cue_locator/core/analysis_result.py` | `__future__.annotations`, `json`, `dataclasses.dataclass`, `dataclasses.field`, `enum.Enum`, `typing.Literal`, `typing.Union` | No | No | No | No | No | Pass -- standard-library only (`json`, `dataclasses`, `enum`, `typing`); no third-party or infrastructure import. |

`src/audio_cue_locator/core/` currently contains exactly these two files
(confirmed by direct directory listing at review time); there is no other
Core module to review.

## 2. Application boundary confirmation

`src/audio_cue_locator/application/multi_cue_orchestration.py` (M3-03/
M3-05) imports `numpy` and
`audio_cue_locator.infrastructure.acoustic_matching` (`EffectiveConfiguration`,
`MatchOutcome`, `MatchResult`, `match_cue`), which is the expected shape for
the Application layer: it coordinates a use case on top of Infrastructure,
it does not implement matching itself
(`docs/architecture.md`, "Princípios e Restrições" #1).

Confirmed by direct source inspection of `run_multi_cue_analysis` and
`_to_per_cue_outcome` (the module's only two functions that touch a
`MatchResult`):

- The module calls the existing, unmodified `match_cue` exactly once per
  cue (`_to_per_cue_outcome(cue_id, match_cue(source, cue_asset,
  configuration))` inside `run_multi_cue_analysis`'s dict comprehension); it
  never calls `numpy.correlate`, `numpy.dot`, or any other correlation/
  scoring primitive directly.
- `_validate_shared_source` performs only precondition checks (instance
  type, dimensionality, dtype, finiteness) on the shared source array; it
  computes no score, threshold comparison, or candidate search of its own.
- `_to_per_cue_outcome` only inspects `MatchResult.outcome` and re-packages
  already-computed `MatchResult` fields (`timestamp_seconds`, `score`,
  `configuration.method`) into the Core-shaped `PerCueOutcome` variants; it
  performs no recomputation of the score or the matched position, and no
  threshold comparison of its own (`found` vs. `no_match` remains entirely
  `baseline.py`'s own decision, already made before this function runs).

Conclusion: Application performs no correlation, scoring, or threshold
logic of its own for the multi-cue flow; it is a thin, non-numeric
coordination layer over the unmodified M2 baseline, consistent with
`docs/architecture.md`'s layering.

## 3. Definition-of-Done evidence summary

The following reproducible, versioned artifacts are this issue's evidence,
in place of the manual-inspection-only closure the formal issue's problem
statement warns against:

- `tests/fixtures/multi_cue/manifest.json` -- a synthetic, deterministic
  fixture manifest covering a repeated cue, an absent cue, and a cross-cue
  temporal-overlap case, with every expected outcome value independently
  verified against the real `match_cue` baseline at authoring time (see the
  manifest's own `provenance.generation_method`).
- `tests/test_multi_cue_analysis.py` -- integration tests that drive the
  manifest's fixtures through the real, unmodified `run_multi_cue_analysis`,
  map the resulting Application outcomes into Core's `AnalysisResult` via a
  private, test-local helper (no new production module, per
  `intents/M3/M3-07/implementation-handoff.json` scope_notes G2), and prove
  byte-identical `serialize_analysis_result` output across two independent
  full-flow runs on the same input by value.
- This document (`docs/m3-core-boundary-review.md`), recording the import
  scan above.

Together with the already-existing, unmodified `tests/test_multi_cue_orchestration.py`
(M3-03/M3-05 per-cue outcome and no-match/failure distinction coverage) and
`tests/test_analysis_result_serialization.py` (M3-06's own Core-level
determinism test), these files let `pytest` re-verify all five of the
formal issue's acceptance criteria on demand, rather than relying on a
narrative claim of completeness.

## 4. Scope boundary

This review, and the two files it accompanies, are the only changes this
issue makes. It does not modify
`src/audio_cue_locator/core/analysis_result.py`,
`src/audio_cue_locator/application/multi_cue_orchestration.py`,
`src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
`src/audio_cue_locator/infrastructure/acoustic_matching/acceptance.py`,
`src/audio_cue_locator/infrastructure/media_processing/canonical_audio.py`,
or any predecessor documentation; all remain read-only compatibility
references, per `intents/M3/M3-07/implementation-handoff.json`
`files_guidance.files_must_not_change`.
