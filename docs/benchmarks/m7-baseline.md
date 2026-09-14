# M7 Main Analysis Path Benchmark

## Purpose and boundary

This document describes the main-analysis-path benchmark M7-05 introduces
to satisfy `issues/M7/M7-05/formal-issue.json` acceptance criterion 5 ("The
benchmark record identifies environment, fixture characteristics,
configuration, repetitions, results, variability, and limitations").

`docs/matching-benchmark.md` (M2-06) already benchmarks the bare `match_cue`
call in isolation, deliberately excluding upload, canonicalization,
persistence, and executor overhead (see that document's own section 1). It
is **not redefined or superseded** by this document: both benchmarks
coexist, each documenting a distinct, explicitly bounded measurement scope.
This document instead covers the scope M2-06's own harness explicitly
leaves out — Asset ingestion, media canonicalization, Analysis persistence,
executor scheduling/matching, and result retrieval — which
`issues/M7/M7-05/formal-issue.json`'s own "main analysis path" language
requires.

Tool: `benchmarks/main_path_baseline.py`.
Formal issue: `issues/M7/M7-05/formal-issue.json`.
Implementation handoff: `intents/M7/M7-05/implementation-handoff.json`.

## 1. Scope and boundaries

- Measures the wall-clock cost of the real Application-layer composition
  root `interfaces.rest_api.app.create_app` itself uses
  (`_build_asset_storage`, `_build_asset_ingestion_use_case`,
  `_build_analysis_use_cases`): Asset ingestion for a fresh source and cue,
  Analysis creation (validation, canonicalization, persistence), executor
  scheduling and matching (the unmodified M2-02 `match_cue`), terminal-state
  polling, and — for a succeeded Analysis — result retrieval.
- Deliberately excludes HTTP/ASGI request-response framing: `httpx` is
  currently declared only under `pyproject.toml`'s
  `[project.optional-dependencies] test` group, not as a base runtime
  dependency, and "main analysis path" is read here as the
  Core/Application/Infrastructure pipeline, not the REST transport layer on
  top of it. `docs/rest-api-v1-contract.md`'s own contract is unaffected;
  this document makes no claim about HTTP-layer latency.
- Does **not** modify `benchmarks/matching_baseline.py`,
  `src/audio_cue_locator/infrastructure/acoustic_matching/baseline.py`,
  `acceptance.py`, or any other `src/audio_cue_locator/` module: it measures
  already-existing, unmodified behavior.
- Reuses the existing, curated `tests/fixtures/matching/manifest.json` case
  `found_offset_near_start` (a real, already-verified matching WAV pair)
  rather than inventing new scenario data, per the formal issue's own
  "Premissas" preferring existing fixtures over new ones.
- `benchmarks/` remains isolated from `src/` (production) and `tests/`
  (automated regression): `benchmarks/main_path_baseline.py` is never
  collected or run by `pytest`, and is not part of the installable package
  defined by `pyproject.toml` (`[tool.setuptools.packages.find] where =
  ["src"]`).

### 1.1. Execution not performed during authoring

Producing the actual timing numbers this issue's acceptance criterion 5
requires is a separate, authorized execution step, outside the scope of
authoring this harness
(`intents/M7/M7-05/implementation-handoff.json`,
`context-packs/M7/M7-05/implementation-context-pack.json`'s
`safety_boundaries.test_execution_allowed: false`): this implementation
phase authors the scenario, methodology, and report structure only; the
actual measured numbers (section 4, "Results") are filled in only after a
later, explicitly authorized ASF execution phase actually runs
`python benchmarks/main_path_baseline.py`. No completion claim is made
before that measured evidence exists, mirroring `docs/matching-benchmark.md`
section 1.1's own precedent for the same discipline.

During authoring, only a static AST syntax check of the script was
performed (no `pytest` run, no benchmark execution, no application
composition root actually invoked) to reduce the risk of an authoring-time
error, exactly as `docs/matching-benchmark.md` section 1.1 records for its
own private, execution-free verification.

## 2. Benchmark scenario

| Parameter | Value |
|---|---|
| Fixture source | `tests/fixtures/matching/manifest.json` |
| Fixture case | `found_offset_near_start` |
| Source encoding | 16-bit mono PCM WAV, 48000 Hz, generated from the case's `source` sample array |
| Cue encoding | 16-bit mono PCM WAV, 48000 Hz, generated from the case's `cue` sample array |
| Cue count per Analysis | 1 |
| Configuration | The Analysis Creation composition root's own default `EffectiveConfiguration` (unmodified `normalized_cross_correlation_v1`, `DEFAULT_CONFIGURATION`'s threshold) |

Each measured repetition ingests a **fresh** source and cue Asset (a new
Asset identifier per repetition, though the same underlying WAV bytes),
mirroring a real client submitting a new request each time rather than
reusing prior Asset state — this exercises Asset ingestion's own per-call
cost on every repetition, not only once.

## 3. Timed window and methodology

The timed window starts immediately before the source and cue Asset
ingestion calls and ends immediately after either the terminal-state poll
observes `FAILED`, or — for `SUCCEEDED` — after the Result body is read
back through `QueryAnalysisUseCase.get_result`. `time.perf_counter_ns()` is
the timer, matching `benchmarks/matching_baseline.py`'s own convention.

One untimed warm-up repetition precedes five sequential timed measured
repetitions (`WARMUP_REPETITIONS = 1`, `MEASURED_REPETITIONS = 5`),
mirroring `benchmarks/matching_baseline.py`'s own established convention.
Polling uses a fixed 10 ms interval with a 30-second per-repetition
timeout; a `--max-total-seconds` budget (default 120 seconds) can abort the
overall run after the repetition in progress completes, reported as
explicitly incomplete rather than silently truncated.

Each Analysis's isolated SQLite database and asset-storage root live under
one process-lifetime `tempfile.TemporaryDirectory`, deleted automatically
when the script exits; no runtime database, volume, or uploaded byte
content is retained as evidence.

## 4. Environment (recorded by the script, per run)

Every run's JSON report records, under its own `environment` key:
UTC start timestamp; repository revision (HEAD commit, branch, working-tree
dirty flag); this script's own SHA-256 (so a future reader can confirm
which exact version of the harness produced a given report); OS
name/release/version; CPU architecture and model (best-effort); logical CPU
count; Python version; and the timer name. No CPU affinity, process
priority, or exclusive-machine control is applied; background load on the
executing host is not controlled or measured.

## 5. Results

**Not executed in this phase.** This implementation authored the harness
and this record only; `context-packs/M7/M7-05/implementation-context-pack.json`
does not authorize running it. A future, explicitly authorized ASF
execution phase must run `python benchmarks/main_path_baseline.py` and
replace this section with the real `environment`, `scenario`, and `timing`
fields from that run's JSON report, following
`docs/matching-benchmark.md` section 1.1's own precedent for recording
measured evidence only after an authorized execution.

## 6. Limitations

- This benchmark measures wall-clock cost on one local, uncontrolled host;
  it is not a production-capacity, SLA, multi-OS, or concurrency-load
  claim, per `issues/M7/M7-05/formal-issue.json`'s own "Não inclui" section
  and this project's own `docs/matching-benchmark.md` non-claim precedent.
- It measures a single-cue, sub-second WAV scenario; it does not generalize
  to multi-cue Analyses, longer source media, or video-source (FFmpeg
  decode) cost — the latter is exercised functionally, but not benchmarked
  for cost, by `tests/e2e/test_m7_baseline.py`'s own video scenario.
  Benchmarking the FFmpeg decode path is explicitly out of scope for this
  record and would require a separately scoped, separately authorized
  benchmark.
- It excludes HTTP/ASGI transport-layer cost (section 1); a future,
  separately scoped benchmark could add that layer if `httpx` (or an
  equivalent) is promoted to a base runtime dependency.
- Host background load, disk cache state, and filesystem type are not
  controlled or measured; reported minimum/median values reflect only the
  repetitions actually executed on the recording machine, not a guarantee
  for any other environment.
- No conclusion about unmeasured scale (many concurrent Analyses, much
  larger media, sustained throughput) may be inferred from this benchmark.

## References

- `docs/matching-benchmark.md` — the narrower, already-existing M2-06
  matcher-only benchmark this document does not redefine or supersede.
- `docs/baseline-validation.md` — the M7-05 release-validation procedure
  this benchmark record is one deliverable of.
- `benchmarks/main_path_baseline.py` — this document's own harness.
- `benchmarks/matching_baseline.py` — the unmodified M2-06 harness.
- `tests/fixtures/matching/manifest.json` — source of the
  `found_offset_near_start` scenario case.
- `issues/M7/M7-05/formal-issue.json`,
  `intents/M7/M7-05/implementation-handoff.json` — formal specification and
  implementation handoff for this document and its harness.
