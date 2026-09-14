# WebUI Timeline Scope Decision

## Purpose

`docs/milestones.md` (M6, Definition of Done) requires that "uma
representação temporal mínima existe ou uma decisão justificada demonstra
que deve ser postergada sem comprometer o fluxo principal." M6-04 scoped a
minimal timeline element representing the source media's duration, with
each occurrence marked at its `temporal_position`. This document records
which of the two admissible outcomes applies, and why.

This document does not implement any timeline component. It does not
change the REST API v1 contract, and it does not modify M6-03's results
view or download flow.

## Decision

**Postponed.** The minimal timeline visualization is not shipped by M6-04.
No repository file other than this document is created or modified by
this issue; `webui/src/components/OccurrenceTimeline.tsx` is not created.

## Evidentiary basis

A minimal timeline that "represents the source media's duration"
(`issues/M6/M6-04/formal-issue.json`, Escopo item 1 and Critério de Aceite
1) requires a numeric value for the source media's total length in
seconds, so every occurrence's `temporal_position` can be plotted
proportionally against it.

That value is not exposed anywhere in the currently documented and
implemented REST API v1 surface:

- **Public Asset schema** (`docs/rest-api-v1-contract.md`, "Public Asset
  schema") fixes exactly seven fields — `identifier`, `logical_type`,
  `sanitized_name`, `media_type`, `size_bytes`, `checksum`,
  `checksum_algorithm` — none of which is a duration or length-in-seconds
  value.
- **Public Analysis schema** (`docs/rest-api-v1-contract.md`, "Public
  Analysis schema") fixes `analysis_id`, `status`, `source_asset_id`,
  `cues`, `lifecycle_timestamps`, `result_reference`, and
  `structured_error` — again, no duration field.
- **The `AnalysisResult` body** (`docs/analysis-result-schema.md`) fixes
  `schema_version`, `final_state`, `method`, `configuration`, `cues`, and
  `structured_error`. Each occurrence carries only `temporal_position`
  and the optional `end` field
  (`docs/occurrence-temporal-semantics-and-policy.md`); `end` is
  documented as always absent for the only currently supported method,
  `normalized_cross_correlation_v1`. Neither field expresses the source
  media's own total duration, only a per-occurrence start (and,
  method-dependently, end) anchor.

## Why this is not worked around

M6-04's own Restrições fix that "no new backend field is requested"
(`issues/M6/M6-04/formal-issue.json`, section 4). Given the confirmed
absence above, satisfying the literal scope ("a minimal timeline element
that represents the source media's duration") would require one of:

1. **Requesting a new backend field or endpoint** to expose the source
   media's duration — explicitly forbidden by this issue's own scope.
2. **Decoding or rendering the raw audio/video file** client-side to
   derive its duration — explicitly forbidden by this issue's own scope
   ("the timeline must not require decoding or rendering raw audio
   waveforms") and by `docs/milestones.md`'s Fora de Escopo ("waveform
   avançada se exigir infraestrutura desproporcional"); the WebUI also has
   no documented endpoint to fetch the raw source-media bytes at all, and
   remains a pure client of `/api/v1`, never the backend filesystem
   (`docs/architecture.md`, "WebUI vs Backend").
3. **Estimating or inferring a duration surrogate** — for example, scaling
   the timeline to the maximum observed `temporal_position` across an
   Analysis's occurrences — which would misrepresent the media's true
   proportional length whenever an occurrence does not fall near the
   media's actual end. This applies to the timeline's own scale exactly
   the kind of estimated/inferred temporal value this issue's own
   constraints forbid for occurrence positions ("no position may be
   recomputed, estimated, or inferred").

None of these three options is available within this issue's scope, so a
duration-proportional timeline cannot currently be built correctly. This
constitutes the justification `docs/milestones.md`'s Definition of Done
requires ("uma decisão justificada demonstra que deve ser postergada") for
choosing postponement instead of treating it as a fallback failure.

## Primary flow is not compromised

M6-03 already delivers the milestone's results/occurrences/download
flow: `webui/src/pages/ResultPage.tsx` lists every occurrence's
`temporal_position`, score, and matching method, distinguishes the
no-match, per-cue-failure, and analysis-level-failed states, and
`webui/src/api/downloadResult.ts` provides a byte-identical JSON download.
Postponing the timeline does not modify, remove, or degrade any part of
that flow: a user can still submit media and cues, track Analysis status,
review every occurrence's exact temporal position in list form, and
download the exact result payload, entirely through the documented
`/api/v1` surface. The primary standalone flow described in
`docs/milestones.md`'s M6 Objetivo remains fully intact without the
timeline.

## Future re-evaluation condition

This postponement should be reconsidered only if a future milestone adds
a documented field or endpoint to the REST API v1 contract (for example,
to the Public Asset schema) that exposes the source media's actual
duration in seconds. Reintroducing the minimal timeline at that point
requires a new, separately authorized implementation handoff; this
document does not itself authorize creating
`webui/src/components/OccurrenceTimeline.tsx` once such a field exists.

## What this decision does not do

- It does not implement any timeline component or visualization.
- It does not request, design, or speculatively document a new backend
  field or endpoint.
- It does not change the REST API v1 contract.
- It does not modify `webui/src/pages/ResultPage.tsx`,
  `webui/src/api/downloadResult.ts`, or any other existing WebUI file.
