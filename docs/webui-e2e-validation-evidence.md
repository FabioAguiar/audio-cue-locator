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
| `E2E_ANALYSIS_TIMEOUT_MS` | Optional terminal-state timeout; defaults to `180000` |

Fixture paths and media bytes are runtime inputs. They must not be copied into
ASF evidence. The backend configuration and fixture provenance used by the run
must be recorded in reduced form by the test phase, without secrets, raw media,
raw API payloads, raw logs, runtime database contents, or host-specific paths.

## Executable scenarios and assertions

### Multi-cue completion and JSON download

The browser selects one source and two independently matching cues, submits the
form, obtains the server-issued Analysis ID, and waits while the WebUI polls the
real status endpoint until `succeeded`. It then uses **View results** and checks
that both `cue-1` and `cue-2` have occurrence results.

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

### Sanitized error

The browser submits a deliberately unsupported source-media payload to the real
upload endpoint. It asserts a rejected HTTP response with exactly the public
`error_code`, `message`, and `correlation_id` fields, checks the expected
`unsupported_media` code, checks that representative payload, stack, SQL, and
host-path details are absent from the message, and verifies that the UI renders
those public fields without internal detail. It also verifies that no Analysis
creation request follows the rejected upload.

## M6 minimum-evidence checklist

The statuses below accurately describe this implementation phase. They must not
be changed to “passed” without an authorized real-backend execution.

| Required evidence | Implemented assertion | Observed result |
|---|---|---|
| Standalone end-to-end flow | Browser upload → Analysis creation → polling → result | Not executed in this phase |
| Completed Analysis through WebUI | Server-issued ID and visible `succeeded` state | Not executed in this phase |
| Multi-cue case | Two uploaded cues and two occurrence outcomes | Not executed in this phase |
| No-match case | Successful Analysis with distinct `no_match` outcome | Not executed in this phase |
| Error case | Real rejected upload and sanitized Error envelope/UI alert | Not executed in this phase |
| JSON download | Download bytes equal real Result response bytes | Not executed in this phase |
| Public API only | Same-origin `fetch`/XHR allowlist under `/api/v1` | Not executed in this phase |
| Timeline, if introduced | Not applicable; M6-04 recorded justified postponement | Not applicable |

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
