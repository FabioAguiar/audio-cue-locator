# Supported Media and Operational Limits

This document is the canonical, evidence-tracked policy for
**M7-02 — Define and verify safe-by-default media, resource, concurrency,
and timeout guardrails**: officially supported media formats, every
enforced numeric limit (size, duration, cue count, concurrency, FFmpeg
timeout), how each is configured, the structured-error behavior a client
observes when a guardrail rejects a request, and two design decisions this
issue makes and records (whether a processing timeout gets its own error
category; the accepted WAV/MP4 probing asymmetry). It does not redefine
the product; see [`docs/architecture.md`](architecture.md) for the
architectural boundaries these guardrails preserve and
[`docs/rest-api-v1-contract.md`](rest-api-v1-contract.md) for the full
external transport contract.

## Officially supported media formats

Support is an explicit product decision, never an accident of whatever an
installed FFmpeg build happens to be able to open
(`docs/architecture.md`, "O conjunto oficial de formatos nao deve ser
derivado automaticamente do que FFmpeg aceita em uma instalacao
especifica"):

| Format | Role | Real probing performed |
|---|---|---|
| `audio/wav` (WAV/PCM) | source media and cue | Python standard-library `wave` module: opens and parses the RIFF/`WAVE` container, channel count, sample width, frame rate, and frame count; rejects anything that does not parse as a well-formed WAV container. |
| `video/mp4` (MP4 with an embedded audio stream) | source media and cue | `ffprobe` (`FFmpegMediaAdapter.probe`) followed by `ffmpeg` audio-stream extraction (`FFmpegMediaAdapter.extract_audio`); rejects a container with no audio stream or that ffmpeg/ffprobe cannot parse. |

Every upload is also magic-byte-sniffed at both upload time
(`application/asset_ingestion.py`) and Analysis-creation time
(`application/create_analysis.py`) before any of the above probing runs; a
filename, file extension, or client-declared `Content-Type` header alone
never establishes support.

### Decision: the WAV/MP4 probing difference is accepted, not a gap

Native WAV content is validated only by the `wave` module plus the
magic-byte sniff, never routed through `ffprobe`/`ffmpeg`; MP4 content is
always routed through a real `ffprobe`/`ffmpeg` decode. This asymmetry was
an open design question
(`states/M7/M7-02/issue-operational-state.json` gap G5): whether WAV
should also gain an `ffprobe`-based structural probe for parity with MP4.

**Decision:** the existing `wave`-module parse is accepted as sufficient
"real media probing" for WAV, and no `ffprobe` call is added for it.
`wave.open` already performs genuine structural validation beyond an
extension/declared-type check -- it parses the actual RIFF/`WAVE` chunk
structure and raises on a malformed container
(`application/create_analysis.py`'s `_wav_bytes_to_canonical_array`) --
and WAV is an uncompressed, fully-specified PCM container with no codec
ambiguity for `ffprobe` to additionally resolve, unlike MP4's compressed,
multi-codec container. Adding a redundant `ffprobe` subprocess invocation
for WAV would add latency and an additional FFmpeg-availability dependency
without a corresponding increase in validation confidence.

This decision is revisited if representative testing (a future,
separately authorized test-execution phase) finds a WAV input the `wave`
module accepts but that is not actually safe to canonicalize.

## Guardrail catalog

Every limit below is explicit, has a documented unit, and is
environment-overridable except where noted. `interfaces/rest_api/app.py`
remains the composition root: every override below follows the same
`os.environ.get(...)` pattern already used for the pre-existing
upload-size and cue-count limits, and no dedicated configuration module
was introduced (`states/M7/M7-02/issue-operational-state.json` gap G6,
decision: keep the existing per-file pattern unless separately justified).

| Guardrail | Default | Unit | Environment override | Enforced at |
|---|---:|---|---|---|
| Max source-media upload size | 500 MiB (`524,288,000` bytes) | bytes | `AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES` | `application/asset_ingestion.py` (upload time) |
| Max cue upload size | 50 MiB (`52,428,800` bytes) | bytes | `AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES` | `application/asset_ingestion.py` (upload time) |
| Max cues per Analysis | 20 | count | `AUDIO_CUE_LOCATOR_MAX_CUE_COUNT` | `application/create_analysis.py` (Analysis-creation time) |
| Max source-media duration | 3600 s (60 min) | seconds | `AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS` | `application/create_analysis.py` (Analysis-creation time, after probing, before/independent of decode) |
| Max cue duration | 600 s (10 min) | seconds | `AUDIO_CUE_LOCATOR_MAX_CUE_MEDIA_DURATION_SECONDS` | `application/create_analysis.py` (Analysis-creation time) |
| Executor concurrency (`max_concurrency`) | 4 workers | count | `AUDIO_CUE_LOCATOR_MAX_CONCURRENCY` | `infrastructure/execution/local_analysis_executor.py`'s `LocalAnalysisExecutor`, constructed by `interfaces/rest_api/app.py` |
| FFmpeg/ffprobe subprocess timeout | 30.0 s | seconds | `AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS` | `infrastructure/media_processing/ffmpeg_adapter.py`'s `FFmpegMediaAdapter`, constructed by `interfaces/rest_api/app.py` |
| CPU/memory resource use | Bounded indirectly by the concurrency and upload-size limits above; no dedicated OS-level mechanism (for example `resource.setrlimit`) is introduced | -- | (none; see below) | -- |

### Evidence status of these defaults

The three pre-existing defaults (source-media size, cue size, cue count)
already shipped in production code before this issue and remain unchanged
here. The three new defaults (source-media duration, cue duration) and the
newly-configurable concurrency/timeout overrides are reasoned starting
points, not yet backed by representative-input measurement:

- The 3600 s / 600 s duration defaults are derived from the already-
  accepted 500 MiB source-media upload-size limit, which already implies
  an approximate 49.5-minute ceiling for uncompressed 16-bit/44.1 kHz
  stereo PCM WAV (`500 MiB / 176,400 bytes-per-second`), rounded to a
  communicable 60 minutes for source media; the cue limit (600 s) is
  independently smaller because a cue is this project's own short
  reference snippet searched for inside the longer source recording
  (`docs/vision.md`), not itself expected to be a long recording. A
  compressed MP4 source is *not* already bounded by the size limit the
  way uncompressed WAV is, which is this guardrail's primary
  justification.
- `tests/operational/test_guardrails.py` (added by this issue) exercises
  every guardrail above with a representative accepted case and a
  representative rejected case, but this issue's own implementation phase
  does not execute that suite: test execution is a separate, explicitly
  authorized ASF phase. The five pre-existing numeric defaults and the two
  new duration defaults must all be revisited against that suite's actual
  results once it runs, per this issue's own acceptance criterion 6
  ("selected defaults include evidence and a documented revision path").

**Revision path:** any change to a value in the table above must (1) be
made in the single composition-root helper function in
`interfaces/rest_api/app.py` (or its default constant in
`application/create_analysis.py` / `infrastructure/media_processing/
ffmpeg_adapter.py` / `infrastructure/execution/local_analysis_executor.py`),
(2) be re-validated against `tests/operational/test_guardrails.py`, and
(3) update the corresponding row in this table.

### CPU/memory resource use

No dedicated OS-level resource-limiting mechanism (for example, `resource.
setrlimit`) is introduced by this issue. For this local, single-process,
unauthenticated baseline, CPU/memory consumption is bounded indirectly by:

- the executor concurrency limit above, which bounds how many Analyses run
  their acoustic-matching step at once;
- the upload-size limits above, which bound the largest single in-memory
  audio buffer any one canonicalization step handles;
- the new duration limits above, which bound how much of that buffer must
  be resampled/normalized per Analysis.

This is a reasoned interpretation of `issues/M7/M7-02/formal-issue.json`'s
own exclusion of "Production capacity promises, SLAs, distributed rate
limiting, or horizontal scaling" from this issue's scope, not a
representative-measurement-backed claim. It should be revisited if a
future, separately authorized test-execution phase measures actual
memory/CPU behavior under the concurrency bound above and finds it
insufficient.

## Structured error behavior

### Decision: a processing timeout is distinguished internally, not by a new public error category

Before this issue, an FFmpeg probe/decode timeout was indistinguishable
from a genuinely malformed file at the Application boundary: both
surfaced as `errors.UnsupportedMediaError` (HTTP 415, `error_code:
"unsupported_media"`) (`states/M7/M7-02/issue-operational-state.json` gap
G3). This issue's own `issues/M7/M7-02/formal-issue.json` (section 6,
"Regras e Limites") requires failures to "distinguish invalid input,
unsupported media, decode/canonicalization, resource limit, **timeout**,
persistence, and internal conditions at the appropriate boundary."

`infrastructure/media_processing/errors.py` already distinguishes a
timeout (`FFmpegTimeoutError`) from other FFmpeg failures
(`InvalidMediaError`, `FFmpegExecutionError`) at the Infrastructure
boundary. This issue extends that distinction one boundary further:
`application/create_analysis.py` now raises its own
`AssetProcessingTimeoutError` whenever `FFmpegTimeoutError` is caught
during probing or decoding, kept separate from
`AssetCanonicalizationError` in this module's own code and exception
handling, rather than silently folding it into that broader class as
before.

**Decision:** this distinction is *not* surfaced as a new public
`ErrorCode`/HTTP status. `interfaces/rest_api/analysis_routes.py`
translates `AssetProcessingTimeoutError` to the same, existing
`errors.UnsupportedMediaError` (HTTP 415, `error_code:
"unsupported_media"`) as `AssetCanonicalizationError`. This conflation is
deliberate and accepted, not an oversight: `tests/
test_api_v1_contracts.py` (outside this issue's authorized edit scope,
`context-packs/M7/M7-02/implementation-context-pack.json`,
`repository_context.allowed_edit_paths`) asserts the OpenAPI-exposed
`ErrorCode` enum value set by exact equality
(`assert set(components["ErrorCode"]["enum"]) == {...}`); adding a ninth
value would fail that existing, unauthorized-to-change test the moment a
future, separately authorized phase runs the suite. Extending the
`ErrorCode`/`V1_STATUS_CATALOG` contract is therefore explicitly deferred
to a future issue authorized to also update that contract test.

A media-duration violation (the new source/cue duration limits above)
reuses the existing `errors.ResourceLimitExceededError` (HTTP 413,
`error_code: "resource_limit_exceeded"`), exactly like the pre-existing
upload-size and cue-count limits: all three are declared-limit violations
that are still valid, well-formed media, not unsupported media and not a
timeout. This does not touch the `ErrorCode` enum's value set, only reuses
an existing member, so it carries no equivalent contract-test risk.

### Updated error-code catalog

| `error_code` | HTTP status | Raised for |
|---|---:|---|
| `resource_limit_exceeded` | 413 | Oversized upload, too many cues, **or an over-duration source/cue Asset (new)**. |
| `unsupported_media` | 415 | Invalid/unsupported/malformed media, **or an FFmpeg probe/decode timeout (new, deliberately conflated -- see decision above)**. |

No new `error_code` value is introduced. See
[`docs/rest-api-v1-contract.md`](rest-api-v1-contract.md) for the complete
error-code catalog, including the six codes this issue leaves entirely
unchanged.

## Known residual limitations

- **A processing timeout is not distinguishable from a malformed file in
  the client-visible response.** Both currently return HTTP 415 /
  `error_code: "unsupported_media"`; see the structured-error decision
  above for why this issue accepts that conflation rather than extending
  the external `ErrorCode` contract. `application/create_analysis.py`'s
  own `AssetProcessingTimeoutError` keeps the two causes distinguishable
  in code even though the REST response does not yet reflect it.
- **No representative-measurement evidence exists yet** for any of the
  seven numeric defaults in the guardrail catalog above; see "Evidence
  status of these defaults."
- **`core.analysis_result.FailureCategory` is not extended.** An FFmpeg
  timeout can only occur synchronously during Analysis-creation
  canonicalization (before an Analysis is ever persisted), never during
  `LocalAnalysisExecutor`'s asynchronous matching run -- the only place
  `FailureCategory` is ever populated on a persisted `FAILED` Analysis
  (`infrastructure/execution/local_analysis_executor.py`'s own docstring:
  matching runs on already-canonicalized arrays and never calls FFmpeg).
  No `FailureCategory` member was added, since no code path could ever
  populate one for this scenario.
