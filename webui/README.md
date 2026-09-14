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
- create an Analysis and watch its lifecycle (`Creating…` / `Analyzing…`)
  without a separate status panel;
- review Results -- occurrences, `no_match`, cue-level failures, and
  Analysis-level failures -- as soon as they are available, and download
  the byte-identical Result JSON.

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
