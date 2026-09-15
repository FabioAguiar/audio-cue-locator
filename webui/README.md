# Audio Cue Locator — WebUI

This is the standalone WebUI application root for Audio Cue Locator. It is
kept separate from `src/audio_cue_locator/` so it can be packaged or served
independently of the backend without changing the backend's logical
boundary (`docs/architecture.md`, "WebUI").

The framework decision and its rationale are recorded in
`docs/webui-stack-decision.md`.

## What this is

This is the standalone Home screen for Audio Cue Locator: a single
responsive workflow (`webui/src/main.tsx` + `webui/src/pages/
NewAnalysisPage.tsx` + `webui/src/pages/ResultPage.tsx`) rather than a
placeholder scaffold. From `/`, a user can:

- select a supported source-media file (by click or drag/drop);
- add one or more WAV cues, each with optional Name/Start time/End time
  (S0003 `label`/`trim_start_seconds`/`trim_end_seconds`);
- create an Analysis with truthful presentation phases (`Uploading…` while
  source/Cue Assets are sent, then `Locating…` while Analysis creation and
  queued/running processing are active) without a separate status panel;
- review Results -- occurrences, `no_match`, cue-level failures, and
  Analysis-level failures -- as soon as they are available, and download
  the byte-identical Result JSON.

The Uploading and Locating phases are presentation-only mappings to real
network and persisted lifecycle boundaries; they are not backend Analysis
statuses and do not measure progress. Their compact audio-bar artwork is
indeterminate. With `prefers-reduced-motion: reduce`, continuous bar and
magnifier movement is disabled while the static themed artwork and semantic
phase text remain visible.

### Cue Start/End: per-Cue source search windows (S0008)

A cue's optional `Start time`/`End time` (`trim_start_seconds`/
`trim_end_seconds`) constrain where that complete Cue is searched on the
source-media timeline. The names are retained for API compatibility. Neither
field is pre-populated: blank/blank searches the full source, End only searches
`[0, End)`, Start only searches `[Start, source end)`, and both search
`[Start, End)`.

Leaving both fields blank searches the full source and is not an error.
`Start`/`End` accept only `MM:SS`, `MM:SS.fraction`,
`HH:MM:SS`, or `HH:MM:SS.fraction` (S0004); this presentation-layer parsing
and the existing start-before-end cross-field check are local convenience
only and do not replace backend validation. The backend remains the sole
authoritative check for whether a bound actually falls within the canonical
source duration -- the WebUI does not decode media to duplicate that check.
When the server rejects a request with
`error_code: validation_error` and at least one Start/End field was not
left blank, the WebUI shows an additional, explicitly conditional advisory
suggesting the user verify the source search interval against the source
duration; it never claims certainty about the server's exact cause, and it
is never shown when every Start/End field was blank.

Temporal/timeline visualization remains out of scope (M6-04's postponement
still applies; see `docs/vision.md`). S0014 instead keeps completed Results
compact and source-grounded: each Cue has one initially expanded group whose
header shows the user Name when present (otherwise the current-session upload
filename, then `cue_id`), supplied Start/End source-search bounds, that Cue's
match count or failure status, and the effective matching method. The `−`/`+`
control collapses or expands only that Cue's details.

Matched details remain in canonical chronological order. **#** is the
one-based canonical Index, while **Rank** is a separate one-based similarity
ordinal derived from raw score descending, earlier position, then canonical
index. Position uses `MM:SS` below one hour and `HH:MM:SS` from one hour,
fractional seconds are floored for display only, and Similarity remains a
two-decimal raw score rather than a percentage or confidence value.

Compact Cue and occurrence play/stop controls use the S0013 Analysis-scoped
WAV audition endpoints. Audio is fetched only after activation; one shared
controller permits at most one playback at a time, and stopping, switching
targets, collapsing the active group, natural completion, or leaving the
Analysis releases the audio and Blob URL. The WebUI does not extract media,
construct source segments, or perform matching locally.

### Settings: Minimum similarity score (S0010)

The existing Settings dialog owns one global preference for each newly
created Analysis. Its native range is `0.00..1.00` in `0.01` steps and shows
the recommended/default position as approximately `0.71`. This presentation
value does not replace the backend's exact default: until the user moves the
range, Analysis creation omits `minimum_similarity_score` and the server owns
the exact threshold.

An explicit custom value is stored alone under the versioned browser-local key
`audio-cue-locator.minimum-similarity-score.v1`, restored across reloads, and
captured in the next Analysis request for all its Cues. Invalid stored values
are removed and fall back to server-default behavior. **Reset to recommended
default** clears both the custom state and key so later requests omit the
override. Changing or resetting Settings never mutates an Analysis already
created or running. The value is a decimal similarity filter, not a percentage
or a statistically calibrated measure.

## Stack

- React 18 + TypeScript
- Vite (dev server and build)
- npm as the package manager

See `docs/webui-stack-decision.md` for the comparison against alternatives
and the rationale.

## Running locally

```bash
cd webui
npm install
npm run dev
```

`npm run build` produces static assets under `webui/dist/` that can be
served alongside or independently of the backend.

## API consumption boundary

This WebUI is a pure client of the already-completed REST API v1
documented in `docs/rest-api-v1-contract.md`. All backend communication
must go through the `/api/v1` namespace, for example:

- `GET /api/v1/health`
- `POST /api/v1/assets/source-media`
- `POST /api/v1/assets/cue`
- `POST /api/v1/analyses`
- `GET /api/v1/analyses/{analysis_id}`
- `GET /api/v1/analyses/{analysis_id}/result`
- `GET /api/v1/analyses/{analysis_id}/cues/{cue_id}/audio`
- `GET /api/v1/analyses/{analysis_id}/cues/{cue_id}/occurrences/{occurrence_index}/audio`

`webui/src/api/client.ts` owns construction and Error-envelope handling for
the upload, Analysis status, and S0013 binary audition operations, including
both bounded audio paths. The deliberately separate
`webui/src/api/downloadResult.ts` continues to own Result retrieval so its
captured raw response bytes remain the exact download source.

This WebUI must never:

- import or depend on `src/audio_cue_locator/core`, `application`, or
  `infrastructure`;
- access SQLite or the backend filesystem directly;
- implement analysis or matching logic locally as an alternative source of
  results (`docs/architecture.md`, "Fluxo standalone": "A WebUI não executa
  matching local como fonte alternativa de resultados.").
