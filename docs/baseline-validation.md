# M7 Release Validation: Reproducible Baseline

## Purpose and boundary

This document is the M7-05 release-validation procedure and reduced-result
record for `issues/M7/M7-05/formal-issue.json` — "Validate the reproducible
baseline end-to-end and record release regression and benchmark evidence".
It traces every M7 Definition of Done item (`docs/milestones.md`, "M7 —
Baseline Operacional Reproduzível") and every formal-issue acceptance
criterion to a concrete, named scenario, test, or document, so validation
scope is explicit rather than left to ad hoc interpretation.

Formal issue: `issues/M7/M7-05/formal-issue.json`.
Implementation handoff: `intents/M7/M7-05/implementation-handoff.json`.

**Not executed in this phase.** This document, `tests/e2e/test_m7_baseline.py`,
and `benchmarks/main_path_baseline.py` were authored, not run, by the M7-05
implementation phase that created them:
`context-packs/M7/M7-05/implementation-context-pack.json` declares
`safety_boundaries.test_execution_allowed: false` for that phase. Every
result row below reads **"Not executed in this phase"** until a later,
explicitly authorized ASF execution/control phase actually runs the
referenced commands and records the real outcome here, mirroring the
precedent already established twice in this repository — M2-06's
`docs/matching-benchmark.md` section 1.1 and M6-05's
`docs/webui-e2e-validation-evidence.md` "Current status" section — for the
same authored-but-not-yet-executed discipline. No acceptance criterion is
claimed complete by this document.

## 1. Clean-environment procedure

This document does not re-author the clean build/start/health/shutdown
procedure: `docs/local-operation.md` (M7-01) already documents it in full
for the packaged two-service (`api`, `webui`) Docker Compose runtime,
including prerequisites, `docker compose up --build`, health verification
(`GET /api/v1/health`, `docker compose exec api ffmpeg -version`/
`ffprobe -version`), local storage layout, and shutdown. That document's own
"Known limitations and open items" section states plainly that "a
clean-environment build/start/health run of this exact packaging has not
been executed as part of this implementation" and defers it "for that
future validation" — this issue is that future validation. The real
outcome of actually running that procedure must be recorded in section 6
below once an authorized execution phase performs it.

## 2. Preconditions

| Precondition | Status as of this authoring | Where it matters |
|---|---|---|
| `ffmpeg`/`ffprobe` binaries | Installed inside the packaged Docker image via `apt-get` (`docs/local-operation.md`); **not guaranteed on every bare host** — `tests/test_canonicalization_fixtures.py`'s own fixture-provenance note records them as absent during an earlier authoring phase on this project | Real audio/video decode scenarios (`tests/e2e/test_m7_baseline.py`'s video scenario; any WebUI/API flow using `video_with_audio.mp4`) |
| `@playwright/test` runner | Confirmed absent from `webui/package.json`'s `devDependencies` (only `typescript` and `vite` are declared); supplied by the controlled execution environment, per `docs/webui-e2e-validation-evidence.md`'s own already-recorded M6-05 decision — **no `webui/package.json` edit is authorized by this issue** | `webui/tests/e2e/standalone-flow.spec.ts` |
| Five Playwright environment variables (`WEBUI_BASE_URL`, `E2E_SOURCE_MEDIA_PATH`, `E2E_MATCHING_CUE_PATH`, `E2E_SECOND_MATCHING_CUE_PATH`, `E2E_NO_MATCH_CUE_PATH`, optional `E2E_ANALYSIS_TIMEOUT_MS`) | Documented, not provisioned by this issue | `webui/tests/e2e/standalone-flow.spec.ts` |
| Backend test extra (`pytest`, `httpx`) | Declared under `pyproject.toml`'s `[project.optional-dependencies] test` group; install via `pip install -e .[test]` | `tests/test_api_v1_integration.py`, `tests/e2e/test_m7_baseline.py` |

An audio/video parity run must either execute inside the packaged Docker
image (where `ffmpeg`/`ffprobe` are guaranteed present) or on a host
confirmed to have both binaries; `tests/e2e/test_m7_baseline.py`'s video
scenario skips, with an explicit reason, rather than mocking decode, when
they are unavailable.

## 3. Regression-suite scope

The "relevant automated regression suite" this issue's acceptance criterion
4 names is, explicitly:

- `pytest tests/test_api_v1_integration.py` — six existing HTTP-level
  integration tests over the real composition root (M5-04/M6 era), unedited
  by this issue.
- `pytest tests/e2e/test_m7_baseline.py` — this issue's own new integrated
  scenario matrix (video-source decode, multi-cue over HTTP, guardrail
  rejection, `analysis_id`-correlated diagnostics, retention/cleanup after
  an HTTP-completed Analysis); see that module's own docstring for exactly
  which existing coverage it deliberately does not duplicate.
- `webui/tests/e2e/standalone-flow.spec.ts` via an externally-supplied
  `@playwright/test` runner — the existing M6-05 standalone specification
  (multi-cue completion with JSON-download byte-equality, no-match, and
  sanitized-error scenarios), unedited by this issue.

Not in scope for this issue's own regression claim: the full, unbounded
`tests/*.py` suite (unit-level Core/Application/Infrastructure tests are
each milestone's own already-reviewed regression responsibility, not
re-litigated here) and `tests/operational/*` (M7-02/M7-03/M7-04's own
already-existing, already-reviewed operational suites, consulted as
precedent by `tests/e2e/test_m7_baseline.py` but not re-run as this issue's
own deliverable).

## 4. Scenario-to-Definition-of-Done traceability matrix

`docs/milestones.md`'s M7 "Entregáveis Esperados":

| M7 Definition of Done item | Evidence source | Status |
|---|---|---|
| Runtime local reproduzível | `docs/local-operation.md` (M7-01); clean build/start/health/shutdown procedure (section 1 above) | Not executed in this phase |
| Containerização coerente | `compose.yaml`, `Dockerfile` (M7-01), unedited by this issue | Not executed in this phase |
| Dependências e versões controladas | `pyproject.toml`, `webui/package.json`, reviewed not modified | Not executed in this phase |
| FFmpeg disponível de forma previsível | `docs/local-operation.md` health verification; precondition table (section 2) | Not executed in this phase |
| Configuração de limites | M7-02 guardrails; `tests/e2e/test_m7_baseline.py::test_guardrail_rejects_unsupported_media_upload_with_the_shared_error_envelope` | Not executed in this phase |
| Política de cleanup aplicada | M7-03; `tests/e2e/test_m7_baseline.py::test_cleanup_removes_uniquely_owned_assets_after_an_http_completed_analysis` | Not executed in this phase |
| Observabilidade básica | M7-04; `tests/e2e/test_m7_baseline.py::test_successful_http_analysis_emits_an_analysis_id_correlated_diagnostic_event` | Not executed in this phase |
| Documentação de execução local | `docs/local-operation.md` (M7-01), consulted not edited | Present (authored by M7-01) |
| Documentação de formatos e limites | `docs/supported-media-and-limits.md` (M7-02), consulted not edited | Present (authored by M7-02) |
| Testes end-to-end | `webui/tests/e2e/standalone-flow.spec.ts` (M6-05); `tests/e2e/test_m7_baseline.py` (this issue) | Authored; not executed in this phase |
| Regressão automatizada | Section 3 above | Not executed in this phase |
| Benchmark mínimo do fluxo principal | `docs/benchmarks/m7-baseline.md` / `benchmarks/main_path_baseline.py` (this issue); `docs/matching-benchmark.md` / `benchmarks/matching_baseline.py` (M2-06, matcher-only) | Authored; not executed in this phase |
| Demonstração standalone | `webui/tests/e2e/standalone-flow.spec.ts`; README.md "Validated demonstration" section (this issue) | Authored; not executed in this phase |
| Demonstração programática via API | `docs/rest-api-v1-contract.md`; README.md "Validated demonstration" section (this issue) | Authored; not executed in this phase |
| Revisão explícita da estratégia de documentação | Reserved for M7-06 (`issues/M7/M7-05/formal-issue.json`, "Documentação acumulativa a atualizar": `milestones-only`, strategy decision deferred to M7-06) | Out of scope for M7-05 |

`issues/M7/M7-05/formal-issue.json` section 8 acceptance criteria:

| # | Acceptance criterion | Evidence source | Status |
|---|---|---|---|
| 1 | Clean supported environment builds and starts the complete local product | Section 1 | Not executed in this phase |
| 2 | Real WebUI and API flows complete for supported audio/video, multi-cue, match, no-match, failure, and JSON-result scenarios | `tests/test_api_v1_integration.py` (WAV single-cue match/no-match, JSON result); `tests/e2e/test_m7_baseline.py` (video source, multi-cue over HTTP); `webui/tests/e2e/standalone-flow.spec.ts` (standalone multi-cue, no-match, JSON download) | Not executed in this phase |
| 3 | Invalid/excessive inputs rejected; cleanup/retention leave correct artifacts; diagnostics correlate by `analysis_id` | `tests/test_api_v1_integration.py` (upload/cue-count limits); `tests/e2e/test_m7_baseline.py` (guardrail, cleanup, diagnostics scenarios) | Not executed in this phase |
| 4 | Automated regression suite passes or each failure is explicitly triaged | Section 3 and section 6 (defect triage), specifically `tests/test_api_v1_integration.py::test_controlled_matching_failure_is_persisted_and_exposed_safely_over_http` | Not executed in this phase |
| 5 | Benchmark record identifies environment, fixtures, configuration, repetitions, results, variability, limitations | `docs/benchmarks/m7-baseline.md` (main analysis path, new); `docs/matching-benchmark.md` (matcher-only, M2-06) | Authored; results not measured in this phase |
| 6 | Standalone and programmatic demonstrations reproducible without simulated matching | `webui/tests/e2e/standalone-flow.spec.ts`; README.md "Validated demonstration" section | Not executed in this phase |
| 7 | No production/remote-security/multi-OS/SLA/confidence-calibration claim is inferred | This document (section 7); `docs/benchmarks/m7-baseline.md` section 6; `docs/matching-benchmark.md` section 7 | Self-consistent by construction; reviewable once results are filled in |

## 5. Fixture-to-scenario mapping

| Scenario | Fixture(s) used |
|---|---|
| WAV single-cue match / no-match (existing) | `tests/fixtures/matching/manifest.json` cases `found_offset_near_start`, `no_match_absent_cue`, and others |
| Multi-cue over HTTP (new) | `tests/fixtures/matching/manifest.json` case `found_offset_near_start` (same cue submitted as two distinct `cue_id`s) |
| Video-source decode (new) | `tests/fixtures/video_with_audio.mp4` (source), a small synthetic WAV cue (`found_offset_near_start`'s cue array) |
| Guardrail rejection (new, this module) | `tests/fixtures/not_media.txt` |
| Standalone WebUI (existing, M6-05) | Operator-supplied paths via `E2E_SOURCE_MEDIA_PATH`/`E2E_MATCHING_CUE_PATH`/`E2E_SECOND_MATCHING_CUE_PATH`/`E2E_NO_MATCH_CUE_PATH`; `tests/fixtures/video_with_audio.mp4` may be used as `E2E_SOURCE_MEDIA_PATH` to also exercise video through the standalone path, at operator discretion — no code change required, since these are runtime environment variables |
| Main-path benchmark (new) | `tests/fixtures/matching/manifest.json` case `found_offset_near_start` |

No new binary fixture was added; every scenario above reuses
already-committed `tests/fixtures/` content, per the formal issue's own
"Premissas".

## 6. Reduced result summary (template — fill in after an authorized run)

### 6.1. Clean-environment build/start

- Environment description: _not recorded — not executed in this phase_
- `docker compose up --build` outcome: _not recorded_
- Health check (`GET /api/v1/health`, `ffmpeg -version`, `ffprobe -version`): _not recorded_

### 6.2. Regression suite

| Test file | Pass/fail | Notes |
|---|---|---|
| `tests/test_api_v1_integration.py` | _not executed_ | |
| `tests/e2e/test_m7_baseline.py` | _not executed_ | |
| `webui/tests/e2e/standalone-flow.spec.ts` | _not executed_ | |

### 6.3. Defect triage — flagged matching-failure discrepancy

`tests/test_api_v1_integration.py::test_controlled_matching_failure_is_persisted_and_exposed_safely_over_http`
asserts an Analysis-level `FAILED` outcome for a single per-cue
`MatchOutcome.PROCESSING_FAILURE`. Both `states/M7/M7-05/issue-operational-state.json`'s
own static tracing and `intents/M7/M7-05/implementation-handoff.json`'s own
corroborating inspection of `local_analysis_executor.py` (its per-cue
outcome mapping leaves `structured_error` `None` for a per-cue
`CueFailure`) and `core/analysis_result.py`'s `final_state` docstring (a
per-cue `CueFailure` alone does not set `FAILED`) indicate this scenario is
likely to fail against the current, documented implementation — but this
is a static-tracing/inspection finding, **not a confirmed pytest execution
result**.

This document does not silently mark this scenario as failed or passed. A
future, explicitly authorized execution phase must:

1. Actually run `test_controlled_matching_failure_is_persisted_and_exposed_safely_over_http`.
2. Record the real outcome here (pass or fail, with the real assertion
   error if it fails).
3. If it fails, route remediation to separately reviewed scope — per
   `issues/M7/M7-05/formal-issue.json`'s own "Não inclui" ("Silently fixing
   defects discovered during validation without separately reviewed
   scope") — rather than editing the test or the implementation inside this
   validation issue.

### 6.4. Benchmark results

See `docs/benchmarks/m7-baseline.md` section 5 (main analysis path,
not yet measured) and `docs/matching-benchmark.md` section 5 (matcher-only,
not yet measured).

## 7. Non-claims

Consistent with `issues/M7/M7-05/formal-issue.json`'s own "Não inclui" and
`docs/matching-benchmark.md`'s established non-claim discipline: nothing in
this document, `docs/benchmarks/m7-baseline.md`, or their eventual filled-in
results may be read as a production-capacity, SLA, multi-OS-support,
remote-deployment-security, or statistically-calibrated-confidence claim.
All results are local, single-host, and descriptive only.

## References

- `docs/local-operation.md` — clean build/start/health/shutdown procedure (M7-01).
- `docs/observability.md` — structured diagnostic-event schema (M7-04).
- `docs/artifact-retention-and-cleanup-policy.md` — retention/cleanup policy (M7-03).
- `docs/supported-media-and-limits.md` — guardrail limits (M7-02).
- `docs/matching-benchmark.md` — matcher-only benchmark (M2-06).
- `docs/benchmarks/m7-baseline.md` — main analysis path benchmark (this issue).
- `docs/webui-e2e-validation-evidence.md` — standalone WebUI validation evidence (M6-05).
- `tests/test_api_v1_integration.py`, `tests/e2e/test_m7_baseline.py` — regression/integration suites.
- `webui/tests/e2e/standalone-flow.spec.ts` — standalone Playwright specification.
- `issues/M7/M7-05/formal-issue.json`,
  `intents/M7/M7-05/implementation-handoff.json` — formal specification and
  implementation handoff for this document.
