# WebUI Stack Decision

## Purpose

`docs/architecture.md` ("WebUI runtime") deliberately left the WebUI's
specific framework undecided, stating only that the future choice must
favor:

- manutenção simples;
- upload de arquivos;
- polling ou atualização de status;
- visualização temporal;
- download do JSON;
- bom suporte a execução local.

This document records that decision for M6-01. It does not implement the
media/cue submission, result presentation, or timeline visualization flows
themselves — those remain owned by M6-02 through M6-04.

## Constraints carried from the architecture

- The WebUI must be a pure client of the already-completed REST API v1
  (`docs/rest-api-v1-contract.md`, `/api/v1` namespace). It must not import
  or depend on `src/audio_cue_locator/core`, `application`, or
  `infrastructure`, and must not access SQLite or the backend filesystem
  directly (`docs/architecture.md`, "WebUI" and "Fronteiras e Boundaries →
  WebUI vs Backend").
- No REST API v1 contract change is authorized by this issue.
- The scaffold must live under a new top-level `webui/` root, kept separate
  from `src/audio_cue_locator/`, so it can be packaged or served
  independently without changing the backend's logical boundary.

## Candidates considered

### 1. Plain HTML + vanilla JavaScript (no build step)

- **Simple maintenance:** highest — no toolchain, no dependency graph to
  keep current.
- **Upload / polling:** straightforward with `fetch` and native
  `<input type="file">`.
- **Temporal visualization:** possible with `<canvas>`, but state
  management for an evolving timeline view (multiple cues, multiple
  occurrences, live status) becomes harder to keep organized without any
  component model as the UI grows across M6-02 through M6-04.
- **Local execution:** trivial — any static file server works.
- **Trade-off:** lowest short-term cost, but the least structure for the
  three following WebUI issues that build directly on this scaffold.

### 2. React + TypeScript + Vite

- **Simple maintenance:** a small, conventional component model; Vite's
  dev server and build are minimal-configuration and fast, which keeps
  local execution simple despite adding a build step.
- **Upload / polling:** well-supported through ordinary `fetch` calls and
  component state/effects; no additional library is required for the
  bounded polling this project needs.
- **Temporal visualization:** a component-per-concern structure (upload
  form, status view, timeline view) scales naturally to M6-02 through
  M6-04 without restructuring this scaffold.
- **Local execution:** `npm install && npm run dev` starts a local dev
  server; `npm run build` produces static assets that can be served
  alongside or independently of the backend, matching
  `docs/architecture.md`'s packaging expectation.
- **Trade-off:** introduces Node.js tooling and a `package.json`
  dependency graph that a vanilla-JS scaffold would not need.

### 3. A heavier full-stack meta-framework (e.g., Next.js)

- Rejected outright: it assumes server-side rendering, routing, and a
  runtime shape the architecture does not require. `docs/architecture.md`
  is explicit that the WebUI is a client of a versioned REST API, not a
  server-rendered or full-stack application. Adopting a meta-framework
  here would be a disproportionate, heavyweight choice relative to the
  project's stated need (see `docs/architecture.md`, risk: "An
  unproportional or heavyweight framework choice increases long-term
  maintenance cost without improving the standalone experience").

## Decision

**React 18 with TypeScript, bundled by Vite, using npm as the package
manager.**

### Rationale

- It is proportional: Vite adds a minimal, fast, low-configuration
  toolchain, not a heavyweight framework. It satisfies "bom suporte a
  execução local" as directly as the no-build alternative while adding
  a conventional component model.
- A component model gives M6-02 (submission/polling), M6-03 (result
  presentation/JSON download), and M6-04 (temporal visualization) a
  natural place to grow without restructuring this scaffold, which
  favors "manutenção simples" across the whole M6 milestone rather than
  only this issue.
- TypeScript keeps the REST API v1 contract's shapes (Asset, Analysis,
  Result, Error) explicit at the WebUI boundary, matching this project's
  stated preference for explicit, checkable contracts
  (`docs/vision.md`, "Preferências do Usuário").
- React and Vite are both widely supported, actively maintained, and
  require no server-side runtime beyond serving static files, consistent
  with the local-first, single-deployable direction in
  `docs/architecture.md`.

### What this decision does not do

- It does not implement upload, polling, result rendering, or timeline
  visualization; those remain scoped to M6-02 through M6-04.
- It does not select state-management, routing, or UI-component
  libraries beyond React itself; those remain open for the issues that
  actually need them.
- It does not change the REST API v1 contract or any backend file.

## Initial scaffold

The initial scaffold created alongside this decision is intentionally
minimal:

- `webui/README.md` — entry-point documentation for the new root.
- `webui/package.json` — the React/Vite/TypeScript manifest.
- `webui/index.html` — the Vite application shell.
- `webui/src/main.tsx` — the application bootstrap, rendering a
  placeholder view and documenting the intended `/api/v1` base URL. It
  does not call the API or implement any upload/analysis logic.
