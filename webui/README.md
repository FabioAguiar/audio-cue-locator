# Audio Cue Locator — WebUI

This is the standalone WebUI application root for Audio Cue Locator. It is
kept separate from `src/audio_cue_locator/` so it can be packaged or served
independently of the backend without changing the backend's logical
boundary (`docs/architecture.md`, "WebUI").

The framework decision and its rationale are recorded in
`docs/webui-stack-decision.md`.

## What this is (M6-01 scope)

This issue establishes only the framework decision and the initial
application scaffold. It does not implement:

- media/cue submission or Analysis lifecycle polling (M6-02);
- result presentation, occurrences, scores, or JSON download (M6-03);
- temporal/timeline visualization (M6-04).

The scaffold currently renders a minimal placeholder view with no upload or
Analysis logic.

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

The intended API base URL is configured as a single constant in
`webui/src/main.tsx` (`API_BASE_URL`). No request is made against it yet;
M6-02 introduces the first actual API call.

This WebUI must never:

- import or depend on `src/audio_cue_locator/core`, `application`, or
  `infrastructure`;
- access SQLite or the backend filesystem directly;
- implement analysis or matching logic locally as an alternative source of
  results (`docs/architecture.md`, "Fluxo standalone": "A WebUI não executa
  matching local como fonte alternativa de resultados.").
