# Local Diagnostic Observability (M7-04)

## Purpose and boundary

This document records the minimum structured, `analysis_id`-correlated
diagnostic baseline this issue applies across the API, application,
executor, media-processing, persistence, and cleanup boundaries, and the
data-minimization rule that bounds every emitted event.

It does not restate `docs/architecture.md`'s own observability
requirements (consult that document, "Observability and Configuration"
and "Logs e metricas", for the architectural rules this baseline
satisfies), and it does not replace `docs/artifact-retention-and-cleanup-policy.md`
or `docs/restart-and-recovery-policy.md`'s own eligibility/recovery rules.

Before this issue, no logging infrastructure existed anywhere in this
project: no `import logging`, no `logging.getLogger`, no structured-
logging dependency in `pyproject.toml`, and no diagnostic `print`
statement anywhere under `src/audio_cue_locator/`. This issue introduces
exactly one new package, `src/audio_cue_locator/observability/`, built on
Python's standard-library `logging` module only.

## Structured-event field schema

Every diagnostic event this project emits carries exactly this fixed set
of fields (`observability/events.py`'s own `emit_diagnostic_event`
signature is the enforcement mechanism -- a caller cannot pass an
arbitrary extra field):

| Field | Type | Meaning |
|---|---|---|
| `event` | `str` | A short, stable event name (for example `"matching_stage_failed"`); never a formatted sentence or exception message. |
| `boundary` | one of `api`, `application`, `executor`, `media_processing`, `matching`, `persistence`, `cleanup`, `result` | Which of this issue's own named boundaries produced the event. |
| `outcome` | `"succeeded"` or `"failed"` | The terminal outcome this event reports. |
| `category` | `str \| None` | An optional short, stable diagnostic label -- an exception class name, or an existing `ErrorCode`/`FailureCategory` value's own string. Never a raw exception message. |
| `analysis_id` | `str \| None` | The persisted Analysis identifier, when one already exists and is known at the point of emission. |
| `correlation_id` | `str \| None` | The REST error envelope's own per-response identifier (`interfaces.rest_api.errors._correlation_id`), when the event is emitted from a request-scoped failure path. |
| `duration_ms` | `float \| None` | An elapsed-time measurement reused from an already-persisted timestamp pair; this package never starts its own timer. |
| `count` | `int \| None` | A non-identifying magnitude (for example how many Assets one cleanup pass deleted) -- never a list of identifiers. |
| `timestamp` | `str` | The event's own emission instant, ISO-8601, UTC, set internally by `emit_diagnostic_event`. |

`analysis_id` and `correlation_id` are the two, previously disconnected,
correlation identifiers this issue's own operational state required
reconciling: the minimum event carries `analysis_id` whenever an Analysis
already exists and `correlation_id` whenever the failure is REST-request-
scoped, rather than choosing one identifier over the other.

## Data-minimization rule

The field schema above is itself this issue's allowlist. No field may ever
carry: a filename or submitted-file path, media bytes, a raw API payload,
a secret, or a raw exception message (`type(exc).__name__` is permitted as
`category`; `str(exc)` is not). A caller wanting to log anything outside
this fixed field set must extend `observability/events.py` explicitly, not
smuggle an extra value through `category` or any other existing field.

## Failure-category vs. diagnostic-category: a deliberate non-extension

`core.analysis_result.FailureCategory` (six members) and
`interfaces.rest_api.schemas.ErrorCode`/`AnalysisFailureCategory` (eight
and six members respectively) remain unchanged by this issue. All three
enums stay closed and additive-only, and `ErrorCode` remains pinned by
`tests/test_api_v1_contracts.py`'s exact-equality OpenAPI assertion.

Where the formal issue's acceptance criteria name `persistence` and
`cleanup` as failure categories that should be "distinguishable where
applicable", this issue satisfies that requirement entirely at the
diagnostic-event `category`/`boundary` fields above -- for example, a
genuine SQLite adapter failure is tagged `boundary="persistence"` with
`category` set to that exception's own class name -- rather than by adding
new members to any of the three closed enums. This keeps every existing
contract test unchanged and avoids re-opening a cross-mirrored enum
surface for a diagnostic-only distinction. A future issue may reopen this
decision if a client-visible persistence/cleanup `ErrorCode` is ever
required.

## Where events are emitted

| Boundary | Site | Event(s) |
|---|---|---|
| `api` | `interfaces/rest_api/errors.py`, `_error_response` | `api_request_failed` for every `/api/v1` failure response, tagged with that response's own `correlation_id` and, when the caught exception carries an `analysis_id` attribute, that identifier too. |
| `executor` | `infrastructure/execution/local_analysis_executor.py`, `_claim_and_run` | `analysis_execution_succeeded` / `matching_stage_failed`, each carrying `analysis_id` and a coarse `duration_ms` reused from `LifecycleTimestamps.duration_seconds`. |
| `application` | `application/create_analysis.py`, `CreateAnalysisUseCase.create` | `cue_validation_failed`, for request validation of a Cue's source search window (`InvalidCueRequestError` after canonical source duration is known -- for example an end beyond source duration or a sample-aligned empty interval) -- distinguished from `media_processing`'s `media_canonicalization_failed` below. No `analysis_id` is available here either, for the same reason as `media_canonicalization_failed`: no Analysis has yet been persisted. |
| `media_processing` | `application/create_analysis.py`, `CreateAnalysisUseCase.create` | `media_canonicalization_failed` for any other pre-persistence Asset resolution/probing/decoding/canonicalization failure; no `analysis_id` is available here, since every rejection happens before an Analysis is ever persisted. |
| `persistence` | `infrastructure/analysis_repository/sqlite_repository.py`, `create`/`add_owned_asset`/`transition` | `analysis_persistence_write_failed`, emitted only for a genuinely unexpected exception -- an already-existing/missing/invalid-record/rejected-transition outcome is excluded, since those are ordinary business-rule rejections, not persistence-layer failures. |
| `cleanup` | `infrastructure/asset_storage/retention_policy.py`, `cleanup_expired_assets` | `asset_retention_cleanup_completed`, once per pass, carrying only `count` (the number of Assets actually deleted). |

`application`'s own pre-persistence Cue-validation event
(`cue_validation_failed`, above) never logs `cue_id`, `label`, either trim
value, the Cue's own decoded duration, an Asset path, a filename, the
raw `InvalidCueRequestError` message, or media bytes -- the fixed field
schema below is itself the allowlist, and this event carries only the
fixed `event`/`boundary`/`outcome`/`category` fields, exactly like
`media_canonicalization_failed`. A request-scoped `InvalidCueRequestError`
raised *before* `CreateAnalysisUseCase.create()` is ever called (for
example, `CueRequest.__post_init__`'s own structural checks, evaluated by
`interfaces.rest_api.analysis_routes` while building the use case's input)
is not covered by this dedicated event; it still surfaces through the
shared `api`-boundary `api_request_failed` event once `interfaces.rest_api.
errors` translates it, exactly as before S0005. `result` is covered
indirectly today: no dedicated `result`-boundary producer exists yet,
since `LocalAnalysisExecutor`'s own `analysis_execution_succeeded` event
already reports the terminal Result outcome. `matching` is not a separate
emission site: `infrastructure/acoustic_matching/` (`acceptance.py`,
`baseline.py`) defines no custom exception types and is not edited by this
issue (see "What this issue does not change" below); a matching-stage
failure is reported as the executor's own `matching_stage_failed` event
instead.

## Remediated leak: matching-stage failure message

Before this issue, `LocalAnalysisExecutor._claim_and_run`'s generic
`except Exception` handler persisted the caught exception's own raw
`f"{type(exc).__name__}: {exc}"` text as `StructuredError.message`, and
`interfaces.rest_api.schemas.analysis_record_to_public` copied that value
unmodified into the public `AnalysisPublic.structured_error` field
returned by both the Analysis-creation and Analysis-status routes, which
`webui/src/pages/NewAnalysisPage.tsx` and `ResultPage.tsx` rendered
directly to the end user -- bypassing `interfaces.rest_api.errors`'s own
`SAFE_MESSAGES` safe-external-message policy entirely, since that policy
only ever governed the separate `ErrorPublic` path.

This issue remediates this at both ends, as defense-in-depth:

1. `local_analysis_executor.py` no longer builds the message from the
   caught exception at all; it persists a fixed, safe constant string
   (`_SAFE_MATCHING_STAGE_FAILURE_MESSAGE`).
2. `schemas.py`'s `analysis_record_to_public` independently redacts
   whatever `structured_error.message` a `StructuredError` producer -- this
   one or any future one -- actually persisted, replacing it with one of
   `SAFE_ANALYSIS_ERROR_MESSAGES`'s small, fixed per-category strings,
   mirroring `errors.SAFE_MESSAGES`'s own established pattern.

No WebUI change is required: once the public field itself is safe, the
existing rendering code is already safe.

## Duration measurement: coarse only

`application/ports/analysis_repository.py`'s `LifecycleTimestamps` gains
one additive method, `duration_seconds(state)`, returning the elapsed time
between `queued_at` and the timestamp recorded for `state`, or `None` if
that state has not been reached. This reuses the already-persisted
`queued_at`/`running_at`/`succeeded_at`/`failed_at` fields; it does not
introduce a second, independent timing mechanism.

This issue only authorizes this coarse, per-Analysis measurement (queued
to terminal). It does not add per-substage timers inside media processing,
matching, or persistence: none exist anywhere in this codebase today, and
inventing them is disproportionate to this issue's diagnostic -- not
performance-profiling -- purpose. A future issue may add finer-grained
instrumentation with a separately authorized handoff.

## Logging configuration

`interfaces/rest_api/app.py`'s `create_app()` calls
`observability.configure_logging` exactly once, before constructing the
`FastAPI` instance, attaching one `StreamHandler` to the
`"audio_cue_locator"` logger hierarchy at the level `AUDIO_CUE_LOCATOR_LOG_LEVEL`
resolves to (default `INFO`), following the same per-file,
environment-overridable configuration convention already used by
`_ffmpeg_timeout_seconds`/`_max_concurrency`. `configure_logging` is
idempotent: calling `create_app()` more than once (as this project's own
test suite already does) never attaches a second duplicate handler.

## Local diagnostic use and known limitations

- **Local-first, diagnostic-only.** Emitted events are written through the
  standard-library `logging` module to whatever handler `configure_logging`
  attaches (a process's own stdout/stderr by default). No corporate
  monitoring, hosted telemetry, remote log sink, or distributed tracing
  infrastructure is introduced (formal issue "Não inclui").
- **Not the authoritative Analysis/result source.** A persisted
  `AnalysisRecord` and its `AnalysisResult`/`StructuredError` remain the
  only authoritative lifecycle and outcome source; diagnostic events are
  evidence for local troubleshooting, never a replacement read path.
- **No per-substage timing.** Only coarse, per-Analysis (queued-to-terminal)
  duration is measured; no decode/matching/persistence sub-stage timer
  exists.
- **No unbounded retention.** This issue does not add log rotation, a
  retention policy, or bounded-size guarantees for whatever destination
  `configure_logging` attaches; an operator relying on this for anything
  beyond local, ephemeral troubleshooting must add that separately.
- **`matching` boundary is reported by the executor, not by
  `infrastructure/acoustic_matching/` itself**, which remains unedited (see
  above).

## What this issue does not change

Per the formal issue's "Não inclui" and this issue's own non-goals, this
issue does not: extend `core.analysis_result.FailureCategory`,
`interfaces.rest_api.schemas.AnalysisFailureCategory`, or `ErrorCode`;
modify `tests/test_api_v1_contracts.py`; modify
`infrastructure/acoustic_matching/` (`acceptance.py`, `baseline.py`) or
`infrastructure/execution/restart_recovery.py`; modify any WebUI file; add
a third-party logging dependency; or introduce any remote/corporate
observability integration.

## Verification performed during implementation

Reduced, non-persisted verification performed while applying this
diagnostic baseline:

- `python -m py_compile` on every edited and newly created Python file
  under `src/audio_cue_locator/` succeeds.
- Direct inspection confirms `pyproject.toml` still declares no logging
  dependency after this issue.
- Direct inspection confirms `tests/test_api_v1_contracts.py`'s `ErrorCode`
  equality assertion set is unchanged by this issue.

No repository test suite was executed as part of this implementation; test
execution is a later ASF phase unless explicitly authorized.
