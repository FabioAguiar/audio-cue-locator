# Standalone WebUI end-to-end validation evidence

This document is the durable M6-05 validation record for the standalone
browser flow. The executable specification is
`webui/tests/e2e/standalone-flow.spec.ts`.

## Current status

Implementation status: **ready for the controlled test phase**.

The M6-05 implementation phase created the executable specification and this
evidence record. It did not execute tests because the ASF context pack reserves
test execution for a later route. Consequently, this document does not claim
that the real-backend scenarios have passed yet. The authorized test phase must
record its result separately and may mark the checklist below as observed only
from an actual run.

## Validation boundary

The specification drives the rendered application with Playwright and does not
use request interception, response fulfillment, API stubs, or simulated
matching. The controlled environment must provide a running WebUI connected to
the real REST API v1 backend and real execution/matching adapters.

The test audits every browser `fetch`/XHR operation. It requires the WebUI and
API to have the same origin, requires every operation path to begin with
`/api/v1/`, and permits only these documented method/path pairs:

| Method | Documented public route | Purpose |
|---|---|---|
| `POST` | `/api/v1/assets/source-media` | Upload source media |
| `POST` | `/api/v1/assets/cue` | Upload each cue |
| `POST` | `/api/v1/analyses` | Create the server-owned Analysis |
| `GET` | `/api/v1/analyses/{analysis_id}` | Poll persisted lifecycle state |
| `GET` | `/api/v1/analyses/{analysis_id}/result` | Retrieve a succeeded Result |

No WebUI operation is allowed to access Core, SQLite, the backend filesystem,
or an alternative matching path.

## Controlled environment inputs

The runner is `@playwright/test`, supplied by the controlled execution
environment rather than the repository package manifest. Before an authorized
run, set:

| Variable | Required value |
|---|---|
| `WEBUI_BASE_URL` | Same-origin URL of the running WebUI and proxied real API |
| `E2E_SOURCE_MEDIA_PATH` | Supported source containing both positive cue occurrences |
| `E2E_MATCHING_CUE_PATH` | First WAV cue known to occur in the source |
| `E2E_SECOND_MATCHING_CUE_PATH` | Second WAV cue known to occur in the source |
| `E2E_NO_MATCH_CUE_PATH` | WAV cue curated not to match the source under the active configuration |
| `E2E_WEBM_SOURCE_MEDIA_PATH` | A supported WebM source containing an audio stream (S0002/S0005 WebM regression) |
| `E2E_ANALYSIS_TIMEOUT_MS` | Optional terminal-state timeout; defaults to `180000` |

Fixture paths and media bytes are runtime inputs. They must not be copied into
ASF evidence. The backend configuration and fixture provenance used by the run
must be recorded in reduced form by the test phase, without secrets, raw media,
raw API payloads, raw logs, runtime database contents, or host-specific paths.

## Executable scenarios and assertions

### Multi-cue completion and JSON download

The browser selects one source and two independently matching cues, submits the
form, obtains the server-issued Analysis ID, and waits while the WebUI polls the
real status endpoint until `succeeded`. Results appear automatically on the same
Home screen (S0004, `specs/S0004-responsive-single-screen-home-design-
convergence/spec.md`; no `View results` step or separate status card). The test
checks that both `cue-1` and `cue-2` have occurrence results.

The test captures the successful Result response bytes in memory, activates
**Download result JSON**, and asserts that the downloaded filename contains the
same Analysis ID and that its bytes exactly equal one of the real Result
responses observed by the browser. It does not reserialize the JSON for this
comparison.

### No-match

The browser submits the curated negative cue through the same upload, creation,
polling, and result flow. It asserts both the visible, distinct “No match found
for this cue.” presentation and the API Result outcome `kind: "no_match"` while
the Analysis itself remains successful.

### Per-Cue source-window serialization (S0008)

One real-backend submission carries four Cues and captures the actual
`POST /api/v1/analyses` body. It asserts the complete compatibility matrix:
blank/blank serializes `null`/`null`, End only serializes `null`/value, Start
only serializes value/`null`, and both fields serialize their numeric seconds.
Small, source-valid bounds are used so this scenario reaches `202`; it uses no
route interception or browser-side media decoding.

### WebM source with a blank source search window (S0008)

The browser selects a real WebM source (`E2E_WEBM_SOURCE_MEDIA_PATH`) and one
matching WAV cue, leaving Name/Start/End blank, and submits the form. The test
asserts, from the real captured request/response traffic rather than inferred
UI state:

- the source-media upload response reports `media_type: "video/webm"` (WebM
  is detected/accepted, not rejected as unsupported media);
- the captured `POST /api/v1/analyses` request body serializes the blank
  Start/End as `trim_start_seconds: null` and `trim_end_seconds: null` --
  full-source-search semantics, never `0`/`0`;
- the real `/api/v1/analyses` response status is checked and is explicitly
  `202` before its body is ever treated as an Analysis (a `400
  validation_error` would fail the test at that boundary instead of
  continuing with an undefined Analysis ID);
- the scenario still reaches a visible terminal Results state (the
  **Download result JSON** control becomes visible).

This is the direct regression for the real-user WebM + blank-Start/End path
this spec exists for
(`specs/S0005-cue-trim-validation-clarity-and-analysis-creation-regression/
spec.md`).

### Duration-relative invalid source search window (S0008)

The browser submits a real, valid WAV cue with a deliberately out-of-range
source End bound (`99:59`) and a blank Start against a supported source known
to be shorter than that bound. It asserts, without mocking or
intercepting the Analysis endpoint:

- both the source-media and cue uploads succeed;
- the real `POST /api/v1/analyses` response is `400` with
  `error_code: "validation_error"`;
- the existing public Error notice (`message`/`error_code`/`correlation_id`)
  remains visible unchanged;
- the additional, explicitly conditional trim advisory is visible and does
  not assert a diagnosis (no claimed source duration, no "the window is
  invalid");
- no Analysis ID is adopted and no Results heading/success state appears.

A companion scenario proves the same advisory is *not* shown for a real
`400 validation_error` triggered with every Start/End field left blank (an
over-length Cue label, unrelated to trim bounds), so the advisory is never
shown merely because the ErrorCode is `validation_error`.

### Sanitized error

The browser submits a deliberately unsupported source-media payload to the real
upload endpoint. It asserts a rejected HTTP response with exactly the public
`error_code`, `message`, and `correlation_id` fields, checks the expected
`unsupported_media` code, checks that representative payload, stack, SQL, and
host-path details are absent from the message, and verifies that the UI renders
those public fields without internal detail. It also verifies that no Analysis
creation request follows the rejected upload.

### Uploading and Locating activity feedback (S0006)

The shared real-submission helper synchronizes with the actual source Asset
upload and Analysis-creation request boundaries. It observes the CTA and
semantic activity region transition from **Uploading…** / **Uploading
media…** to **Locating…** / **Locating cues…**, then observes the Locating
indicator in the Results loading area after the server supplies an Analysis
ID. The test does not intercept, fulfill, delay, or mock requests to expose
these transient phases.

Successful Result scenarios assert that no S0006 activity indicator remains
after terminal completion. The real rejected-media scenario asserts that the
Uploading indicator is removed and the idle CTA is restored after the upload
error. Assertions also prove the former `Creating…` and `Analyzing…` wait
copy is absent.

The existing large real Asset-upload scenario emulates
`prefers-reduced-motion: reduce` and verifies during the active Uploading
boundary that semantic phase content remains observable, the decorative bar
artwork remains present, and its computed `animation-name` is `none`. This is
a static fallback check, not measured upload progress.

## M6 minimum-evidence checklist

The statuses below accurately describe this implementation phase. They must not
be changed to “passed” without an authorized real-backend execution.

| Required evidence | Implemented assertion | Observed result |
|---|---|---|
| Standalone end-to-end flow | Browser upload → Analysis creation → polling → result | Not executed in this phase |
| Completed Analysis through WebUI | Server-issued ID and visible `succeeded` state | Not executed in this phase |
| Multi-cue case | Two uploaded cues and two occurrence outcomes | Not executed in this phase |
| No-match case | Successful Analysis with distinct `no_match` outcome | Not executed in this phase |
| Per-Cue source windows (S0008) | Blank, End-only, Start-only, and both-bound values serialize correctly and a source-valid request reaches `202` | Not executed in this phase |
| Error case | Real rejected upload and sanitized Error envelope/UI alert | Not executed in this phase |
| JSON download | Download bytes equal real Result response bytes | Not executed in this phase |
| Public API only | Same-origin `fetch`/XHR allowlist under `/api/v1` | Not executed in this phase |
| Timeline, if introduced | Not applicable; M6-04 recorded justified postponement | Not applicable |
| WebM + blank source window (S0008) | `video/webm` detected, `trim_start_seconds`/`trim_end_seconds` serialize as `null`, `/analyses` explicitly `202` before parsing, terminal Results reached | Not executed in this phase |
| Duration-relative invalid source window (S0008) | Real `400 validation_error`, existing Error notice preserved, safe conditional source-window advisory shown, no fabricated Result | Not executed in this phase |
| Blank-window `validation_error` shows no window advisory (S0008) | Real unrelated `400 validation_error` with blank Start/End does not trigger the source-window advisory | Not executed in this phase |
| Uploading activity (S0006) | CTA/activity observed at the real source Asset-upload boundary | Not executed in this phase |
| Pre-ID Locating activity (S0006) | CTA/Cues activity observed when the real `POST /api/v1/analyses` begins | Not executed in this phase |
| Post-ID Locating activity (S0006) | Results loading activity observed after the real `202` supplies `analysis_id` | Not executed in this phase |
| Terminal/error activity stop (S0006) | No active activity remains after terminal Result or rejected source upload | Not executed in this phase |
| Stale wait copy absent (S0006) | No user-facing `Creating…` or `Analyzing…` during the real flow | Not executed in this phase |
| Reduced-motion fallback (S0006) | Semantic Uploading status and static artwork remain while computed continuous animation is disabled | Not executed in this phase |

## Test-phase recording requirements

The later authorized test record should include only reduced evidence:

- UTC execution timestamp and runner/browser versions;
- a non-sensitive environment description and backend configuration identity;
- fixture identities or checksums suitable for reproducibility, never raw bytes;
- pass/fail for each scenario and each checklist item;
- the Analysis IDs or correlation IDs only if the evidence policy permits them;
- a concise defect reference for any failure, without raw logs or payloads.

The run is invalid for M6-05 if any backend or matching request is mocked, if a
browser operation leaves the documented public route allowlist, if any required
fixture is absent, or if any required scenario is skipped.
