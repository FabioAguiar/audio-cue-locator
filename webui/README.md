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

### Cue Start/End: Cue-local bounds, not source-media bounds (S0005)

A cue's optional `Start time`/`End time` (`trim_start_seconds`/
`trim_end_seconds`) are positions **inside that cue's own audio file**, not
positions inside the source media being searched -- they never constrain
where in the source media a match may be found
(`specs/S0005-cue-trim-validation-clarity-and-analysis-creation-regression/
spec.md`). The WebUI states this explicitly next to the Start/End fields,
and their placeholder examples (`e.g. 00:00:01` / `e.g. 00:00:03`) are
short, Cue-scale values rather than source-media-scale ones -- they are
examples, never defaults, and neither field is ever pre-populated.

Leaving both fields blank uses the cue's full duration; this is the normal
case and is not an error. `Start`/`End` accept only `MM:SS`, `MM:SS.fraction`,
`HH:MM:SS`, or `HH:MM:SS.fraction` (S0004); this presentation-layer parsing
and the existing start-before-end cross-field check are local convenience
only and do not replace backend validation. The backend remains the sole
authoritative check for whether a bound actually falls within the cue's real
decoded duration -- the WebUI never decodes cue audio itself (no
`AudioContext`/`HTMLAudioElement` probing, no manual WAV/RIFF duration
parsing) to duplicate that check. When the server rejects a request with
`error_code: validation_error` and at least one Start/End field was not
left blank, the WebUI shows an additional, explicitly conditional advisory
suggesting the user verify the Cue-local bounds against that cue's own
duration; it never claims certainty about the server's exact cause, and it
is never shown when every Start/End field was blank.

Temporal/timeline visualization remains out of scope (M6-04's postponement
still applies; see `docs/vision.md`), and no media preview/play control is
implemented (S0004, `specs/S0004-responsive-single-screen-home-design-
convergence/spec.md`).

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

The API base URL is a single constant (`API_BASE_URL`) in
`webui/src/api/client.ts`, the only module that constructs a `fetch`
request or knows the request/response JSON shapes.

This WebUI must never:

- import or depend on `src/audio_cue_locator/core`, `application`, or
  `infrastructure`;
- access SQLite or the backend filesystem directly;
- implement analysis or matching logic locally as an alternative source of
  results (`docs/architecture.md`, "Fluxo standalone": "A WebUI não executa
  matching local como fonte alternativa de resultados.").
