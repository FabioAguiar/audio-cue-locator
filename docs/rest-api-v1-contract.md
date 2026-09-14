# REST API v1 Transport Contract

## Purpose and boundary

M5-01 defines the shared external transport and versioning baseline every
later M5 endpoint issue (M5-02 through M5-06) reuses instead of each
inventing its own Asset/Analysis representation, identifier convention, or
version scheme (`issues/M5/M5-01/formal-issue.json`). It fixes the public
Asset and Analysis schemas, the `/api/v1` namespace, identifier and
timestamp conventions, a baseline route and HTTP status catalog, explicit
transport-to-Application mappings, and OpenAPI representability.

The executable contract lives in these modules under
`src/audio_cue_locator/interfaces/rest_api/` (plus Application modules):

- `schemas.py` — the public Pydantic v1 request/response schemas for Asset,
  Analysis, and (M5-02) the shared Error envelope, plus one explicit
  mapping function per resource (`asset_to_public`,
  `analysis_record_to_public`).
- `app.py` — the FastAPI application assembly: the `/api/v1` namespace
  prefix, the baseline HTTP status catalog, the baseline health route, the
  installation of the centralized error-translation policy (M5-02), the
  registration of every schema below into the generated OpenAPI document,
  (M5-03) the composition root that wires a concrete `AssetStoragePort`
  implementation into the upload routes, and (M5-04) the second
  composition-root responsibility that wires a concrete
  `AnalysisRepositoryPort` and a `LocalAnalysisExecutor` into the Analysis
  creation route. M5-05 constructs one `InMemoryResultReferenceStore` and
  shares it with both the executor and the Analysis query use case.
- `errors.py` (M5-02) — the centralized v1 failure-translation policy:
  the closed exception-to-`ErrorCode`/HTTP-status mapping and
  `install_error_handlers`, which every current and future `/api/v1`
  route inherits.
- `asset_routes.py` (M5-03) — the two bounded source-media/cue upload
  routes (`build_asset_router`) and their own bounded-multipart-reading
  mechanism; delegates all ingestion policy to Application.
- `application/asset_ingestion.py` (M5-03) — the Application-level Asset
  ingestion use case: explicit, configurable upload-size limits and
  supported-media allowlists per logical Asset type, content-based
  media-type detection, and delegation to the existing M4-02
  `core.asset.AssetStoragePort`.
- `analysis_routes.py` (M5-04/M5-05) — the asynchronous Analysis creation
  route plus status and Result retrieval routes (`build_analysis_router`);
  delegates every decision to Application.
- `application/create_analysis.py` (M5-04) — the Application-level
  asynchronous Analysis creation use case: cue-count validation, Asset
  existence/content-compatibility checks, WAV/video-to-canonical-array
  resolution, a default effective-configuration policy, a documented
  no-idempotency baseline, and delegation to the existing M4-03
  `AnalysisRepositoryPort` and M4-04 `LocalAnalysisExecutor`.
- `application/query_analysis.py` (M5-05) — the read-only Application
  boundary for authoritative persisted status and lifecycle-gated Result
  retrieval through an opaque-reference reader port.

M5-01 does **not** implement Analysis creation or status/result retrieval;
those are owned by M5-04 and M5-05. M5-02 implements the shared Error
contract and its translation policy. M5-03 implements the first two
business routes that actually exercise it: bounded source-media and cue
Asset uploads (see "Bounded source-media and cue uploads (M5-03)" below).
M5-04 implements the third: asynchronous Analysis creation (see
"Asynchronous Analysis creation (M5-04)" below). M5-05 completes the
polling flow with the two GET routes documented below.

## Dependency direction

`src/audio_cue_locator/interfaces/rest_api/` may depend on
`src/audio_cue_locator/application/` only. It must not import
`src/audio_cue_locator/infrastructure/` (matcher, SQLite, filesystem, or
execution adapters) and must not reuse a `src/audio_cue_locator/core/`
entity or persistence record as a public schema
(`docs/architecture.md`, Principle 1; "Tipos Pydantic de transporte nao
devem ser reutilizados automaticamente como entidades do Core"). `app.py`
imports `schemas.py` and `errors.py`; the only Core/Application imports in
this package are the two mapping functions in `schemas.py` and the
exception-type mapping table in `errors.py` (`core.asset`,
`core.analysis_lifecycle`, `application.ports.analysis_repository`,
`application.multi_cue_orchestration`). `errors.py` never imports
`src/audio_cue_locator/infrastructure/`: an Infrastructure failure (for
example `media_processing.errors.InvalidMediaError`) is Application's
responsibility to translate into one of `errors.py`'s own exception types
(`UnsupportedMediaError`, `ResourceLimitExceededError`,
`AnalysisResultUnavailableError`) or into a persisted `StructuredError`
before it ever reaches Interfaces. M5-05's Application query raises its own
storage-independent lifecycle outcomes, which `errors.py` maps centrally.

M5-03's `application/asset_ingestion.py` is one narrow, documented
exception to the "no upward import" rule's naive reading: it cannot import
anything under `interfaces/rest_api/` itself (`interfaces/rest_api/
__init__.py` eagerly imports `app.py`, which must import
`asset_ingestion.py` to build its composition root -- a genuine circular
import, not only a layering concern), so it raises its own
`AssetUploadTooLargeError`/`UnsupportedAssetMediaError` instead.
`asset_routes.py` is the one place that translates those two into
`errors.ResourceLimitExceededError`/`errors.UnsupportedMediaError` before
they reach `errors.install_error_handlers`. Separately, `app.py` -- and
only `app.py` -- also imports `infrastructure/asset_storage/
local_filesystem_storage.py` directly, as this project's one composition
root: the sole place a concrete `AssetStoragePort` implementation is
constructed and injected into `AssetIngestionUseCase`. `asset_routes.py`
itself never imports `infrastructure/`.

M5-04's `application/create_analysis.py` is the same kind of narrow,
documented exception, for the same circular-import reason: it raises its
own `TooManyCuesError`/`AssetContentIncompatibleError`/
`AssetCanonicalizationError` instead of importing `errors.py` directly;
`analysis_routes.py` translates all three to `errors.
ResourceLimitExceededError`/`errors.UnsupportedMediaError`. Unlike
`asset_ingestion.py`, `create_analysis.py` *does* import
`infrastructure.execution.local_analysis_executor.LocalAnalysisExecutor`
and `infrastructure.media_processing`/`infrastructure.acoustic_matching.
acceptance` directly (not only through Core/Application ports): no
Application-owned port wraps the local executor or the
canonicalization/matching-configuration pieces it needs, and introducing
one is not part of this issue's own authorized scope, so this is a second,
equally narrow, documented exception to the general "Application depends
on ports, not concrete Infrastructure" preference -- it never imports
`interfaces/rest_api/` or constructs a SQLite connection or filesystem
path of its own. `app.py` -- and only `app.py` -- constructs the concrete
`SQLiteAnalysisRepository`/`LocalAnalysisExecutor` pair (wrapped in a
thread-safe `_ThreadLocalAnalysisRepository`; see "Asynchronous Analysis
creation (M5-04)" below) and injects them into `CreateAnalysisUseCase`.
`analysis_routes.py` itself never imports `infrastructure/`.

## `/api/v1` namespace and versioning independence

The public boundary is versioned under:

```text
/api/v1
```

matching the namespace `docs/architecture.md` ("API versioning") already
reserves. The `/api/v1` namespace version and the Analysis Result's own
`schema_version` (`core.analysis_result.SCHEMA_VERSION`, currently
`"analysis_result.v1"`) are independently represented and evolve under
unrelated policies: an incompatible `/api/v1` change does not require a new
Result schema version, and vice versa.

`schemas.AnalysisResultEnvelope` makes this concrete: its `api_version`
field is the literal `"v1"`, while `result_schema_version` is copied from
the stored Result body's own `schema_version` and `result` stays an untyped,
opaque mapping so this document and module never duplicate or redefine the
M3 Result schema (`docs/analysis-result-schema.md`).

## Baseline route and HTTP status catalog

| Route | Method | Status | Purpose |
|---|---|---:|---|
| `/api/v1/health` | GET | 200 | Baseline liveness check for the `/api/v1` namespace (M5-01). |
| `/api/v1/assets/source-media` | POST | 201 | Bounded source-media Asset upload (M5-03). |
| `/api/v1/assets/cue` | POST | 201 | Bounded cue Asset upload (M5-03). |
| `/api/v1/analyses` | POST | 202 | Asynchronous Analysis creation from Asset identities (M5-04). |
| `/api/v1/analyses/{analysis_id}` | GET | 200 | Current persisted Analysis status (M5-05). |
| `/api/v1/analyses/{analysis_id}/result` | GET | 200 | Independently versioned Result for a SUCCEEDED Analysis (M5-05). |

The complete baseline status catalog (`app.V1_STATUS_CATALOG`) reserves one
name per HTTP status every later M5 endpoint issue must reuse rather than
reinvent:

| Name | HTTP status | Reserved for |
|---|---:|---|
| `ok` | 200 | Synchronous success (health, Analysis status, and completed Result retrieval). |
| `created` | 201 | A new resource was created (bound to both M5-03 upload routes). |
| `accepted` | 202 | Asynchronous Analysis creation (`docs/architecture.md`, "Fluxo programatico": "202 Accepted + Analysis ID"); bound by M5-04's `POST /api/v1/analyses`. |
| `no_content` | 204 | A future successful request with no response body. |
| `bad_request` | 400 | Domain-level invalid input caught by Core/Application (`InvalidAssetIdentifierError`, `InvalidAssetMetadataError`, `InvalidAnalysisRecordError`, `AnalysisSourceInputError`); bound by M5-02. |
| `payload_too_large` | 413 | A size/duration/quantity limit exceeded (`errors.ResourceLimitExceededError`); bound by M5-02, first raised by M5-03's upload routes (translated from `asset_ingestion.AssetUploadTooLargeError`); extended by M7-02 to also cover an over-duration source/cue Asset. |
| `unsupported_media_type` | 415 | Submitted media fails format/codec/audio-stream validation (`errors.UnsupportedMediaError`); bound by M5-02, first raised by M5-03's upload routes (translated from `asset_ingestion.UnsupportedAssetMediaError`). |
| `not_found` | 404 | An Asset or Analysis identifier with no matching resource (`AssetNotFoundError`, `AnalysisNotFoundError`); bound by M5-02, used by M5-05. |
| `conflict` | 409 | A state conflict: an Analysis lifecycle transition conflict, an already-existing Analysis identifier, an Asset storage collision, or a Result requested while its Analysis is pending/FAILED; used by M5-04/M5-05. |
| `unprocessable_entity` | 422 | Request-shape validation caught by FastAPI/Pydantic before Application runs (`RequestValidationError`); bound by M5-02. |
| `internal_error` | 500 | Any unmapped/unexpected exception; bound by M5-02. |

`ok`, `created`, and every 4xx/5xx entry above are bound to the centralized
handlers `errors.install_error_handlers` registers on every `/api/v1`
app instance (M5-02). M5-03's two upload routes are the first business
routes to actually raise `payload_too_large`/`unsupported_media_type`; the
same policy applies to Analysis creation/status/Result routes and to any
request-validation failure FastAPI itself raises.

## Public Asset schema

Public representation of `core.asset.Asset`
(`docs/asset-identity-and-storage.md`).

| Field | Required | Type | Notes |
|---|---:|---|---|
| `identifier` | yes | string | Canonical lowercase UUID; opaque storage key, never a filename or path. |
| `logical_type` | yes | `AssetLogicalType` enum | `source_media`, `cue`, `derived_artifact`, or `analysis_result`. |
| `sanitized_name` | yes | string | Presentation-only; never used for storage addressing. |
| `media_type` | yes | string | Lowercase detected `type/subtype`. |
| `size_bytes` | yes | integer (>= 0) | Byte count of the ingested payload. |
| `checksum` | yes | string (64 lowercase hex) | SHA-256 digest of the ingested payload. |
| `checksum_algorithm` | yes | literal `"sha256"` | Explicit, currently fixed. |

`storage_reference` is **not** part of the public schema: `core.asset.Asset`
documents it as an opaque internal reference that "Core and Application
must not parse... as a filesystem path"; this contract does not promote it
to a public field.

`AssetCreateRequest` fixes the client-supplied metadata field names a JSON
Asset representation would carry (`logical_type`, `informative_name`,
`media_type`). M5-03's two multipart upload endpoints (below) do not
actually bind a request body to this schema: `logical_type` is implied by
which endpoint is called, `informative_name` comes from the multipart file
part's own filename, and `media_type` is never accepted from the client at
all (see "Bounded source-media and cue uploads"). `AssetCreateRequest`
remains declared and OpenAPI-registered as the general-purpose Asset
metadata shape M5-01 defined it as; M5-03 did not need to extend or repurpose
it.

## Public Analysis schema

Public representation of
`application.ports.analysis_repository.AnalysisRecord`.

| Field | Required | Type | Notes |
|---|---:|---|---|
| `analysis_id` | yes | string | Caller/repository-stable identifier. |
| `status` | yes | `AnalysisStatus` enum | `queued`, `running`, `succeeded`, or `failed`, mirroring `core.analysis_lifecycle.AnalysisLifecycleState`. |
| `source_asset_id` | yes | string | Asset identifier of the shared source media. |
| `cues` | yes | list of `AnalysisCueReference` (>= 1) | Each `{cue_id, asset_id}`. |
| `lifecycle_timestamps` | yes | `AnalysisLifecycleTimestamps` | `queued_at` required; `running_at`/`succeeded_at`/`failed_at` optional and timezone-aware. |
| `result_reference` | no | string or null | Opaque pointer to an externally serialized Result; never the Result body itself. |
| `structured_error` | no | `AnalysisStructuredError` or null | Present if, and only if, `status` is `failed`. |

`owned_asset_ids` (`AnalysisRecord`'s retention/cleanup bookkeeping field,
`docs/asset-identity-and-storage.md`) is intentionally not part of the
public surface; it is not part of the resource identity a client needs to
track an Analysis.

`AnalysisCreateRequest` fixes only `source_asset_id` and `cues`. It
deliberately excludes any effective-configuration override: matching
configuration is a server-owned concern captured once per Analysis
(`docs/analysis-effective-configuration.md`), never a client-supplied
transport field. It also carries no client-supplied Analysis identifier
or idempotency key (see "Asynchronous Analysis creation (M5-04)" below,
no-idempotency baseline). M5-04 implements the `202 Accepted`
asynchronous-creation response this request implies.

## Shared Error contract and sanitization policy (M5-02)

Public representation of a `/api/v1` failure. Every non-2xx response
across every endpoint family uses this one schema
(`issues/M5/M5-02/formal-issue.json`, acceptance criterion 1).

| Field | Required | Type | Notes |
|---|---:|---|---|
| `error_code` | yes | `ErrorCode` enum | One of the eight closed, additive-only values below. |
| `message` | yes | string | Always one of a small, fixed set of safe strings (`errors.SAFE_MESSAGES`); never a raw exception message, stack trace, host path, SQL fragment, or echoed request value. |
| `correlation_id` | yes | string | A random, non-guessable `uuid.uuid4().hex` value a client can report back for operator diagnosis, without the response itself disclosing anything about the underlying cause (acceptance criterion 5). |

### Error-code catalog

| `error_code` | HTTP status | Mapped from |
|---|---:|---|
| `validation_error` | 422 (request-shape) or 400 (domain-level) | `RequestValidationError`; `InvalidAssetIdentifierError`; `InvalidAssetMetadataError`; `InvalidAnalysisRecordError`; `AnalysisSourceInputError`. |
| `unsupported_media` | 415 | `errors.UnsupportedMediaError` (an Application adapter in `application.create_analysis` translates an Infrastructure `media_processing.errors.InvalidMediaError`/`NoAudioStreamError`/`FFmpegExecutionError` into this type before it reaches Interfaces). Also covers an FFmpeg probe/decode subprocess timeout (`media_processing.errors.FFmpegTimeoutError`, via `create_analysis.AssetProcessingTimeoutError`): M7-02 deliberately keeps this conflation rather than adding a new `ErrorCode` (see `docs/supported-media-and-limits.md`). |
| `resource_limit_exceeded` | 413 | `errors.ResourceLimitExceededError` (a declared size, duration, or quantity limit, for example an oversized upload, an over-duration source/cue Asset (M7-02), or too many cues). |
| `resource_not_found` | 404 | `AssetNotFoundError`; `AnalysisNotFoundError`. |
| `lifecycle_conflict` | 409 | `InvalidLifecycleTransitionError`; `AssetStorageCollisionError`; `AnalysisAlreadyExistsError`. |
| `result_not_ready` | 409 | `application.query_analysis.AnalysisResultNotReadyError`, raised for Result retrieval while the persisted state is `queued` or `running`. |
| `analysis_failed` | 409 | `errors.AnalysisResultUnavailableError`, raised when a Result is requested for an Analysis whose persisted state is FAILED. Distinct from `InvalidLifecycleTransitionError`, which guards state *transitions* rather than Result *retrieval*. |
| `internal_error` | 500 | Any other exception (the catch-all `Exception` handler). |

A request-shape failure (missing/mistyped field, caught by FastAPI/Pydantic
before Application runs) and a domain-level failure (semantically invalid
even though structurally well-formed, caught by Core/Application) both use
`validation_error`, at their pre-existing distinct reserved statuses (422
and 400 respectively) — one shared code, two already-reserved statuses,
rather than inventing a third.

### Persisted FAILED vs. no-match (acceptance criterion 4)

A legitimate no-match (`core.analysis_result.CueNoMatch`) is a plain data
value inside a *successful* `AnalysisResult`/`AnalysisPublic` response
(`final_state`/`status` stay `"completed"`/`"succeeded"` or, for a
still-`failed` Analysis in the mixed-outcome case, the no-match cue's own
outcome is unaffected). It is never raised as an exception and therefore
structurally cannot reach any handler `install_error_handlers` registers.
A persisted FAILED Analysis's `structured_error` remains visible on the
successful `AnalysisPublic` response exactly as M5-01 defined it; only
*requesting the Result body* of a FAILED Analysis is a REST-level
conflict, raised as `AnalysisFailedError` (or the compatibility
`AnalysisResultUnavailableError`) and mapped to `analysis_failed`/409 above.

### Sanitization guarantee

No `/api/v1` response ever serializes a stack trace, host filesystem path,
SQL fragment, raw request payload, or media byte content, whether the
failure is mapped or unexpected: every mapped exception's own message is
discarded in favor of one of the fixed `SAFE_MESSAGES` strings, and the
catch-all `Exception` handler never serializes the caught exception's type
name or message. `tests/test_api_errors.py` asserts the absence of a
representative set of these forbidden substrings from every response this
policy produces.

### Raising these exceptions

A route or its Application call must raise one of the exception types in
the catalog above (or let `RequestValidationError` propagate from
FastAPI/Pydantic's own request parsing) to go through this policy. Raising
`fastapi.HTTPException` directly bypasses it entirely and is not used by
any code in this package; M5-03's upload routes already follow this (see
"Bounded source-media and cue uploads" below), and the M5-04/M5-05
endpoints do the same, never raising `HTTPException`.

## Bounded source-media and cue uploads (M5-03)

`POST /api/v1/assets/source-media` and `POST /api/v1/assets/cue` each
accept a `multipart/form-data` request carrying exactly one file part and
return `AssetPublic` (201) on success. `logical_type` is implied by which
endpoint was called, never a client-supplied value; `informative_name` is
the multipart file part's own filename, sanitized by the existing
`core.asset.sanitize_asset_name` exactly as any other Asset creation path;
the internal identifier and storage location remain entirely server-
generated (`docs/asset-identity-and-storage.md`), so two uploads sharing an
identical client filename never collide.

`media_type` is never accepted from a client-declared header or the
filename: `docs/asset-identity-and-storage.md` requires it to be "the
result of trusted media inspection", not "a filename extension alone".
`application/asset_ingestion.py`'s `sniff_media_type` inspects the
payload's own leading bytes for the bounded, explicit set of container
signatures S0002 (`specs/S0002-common-video-container-source-media-
support/spec.md`) defines: a RIFF/WAVE header for `audio/wav`, a RIFF/AVI
header for `video/x-msvideo`, bounded ISO-BMFF `ftyp` major/compatible-
brand evidence distinguishing `video/mp4` from `video/quicktime`, and
bounded EBML `DocType` evidence distinguishing `video/webm` from
`video/x-matroska`. A full FFmpeg decode/stream probe remains out of this
issue's scope and happens later, at Analysis creation (M5-04) and
matching.

### Explicit, configurable limits

| Logical type | Endpoint | Default max size | Supported media types |
|---|---|---:|---|
| `source_media` | `/api/v1/assets/source-media` | 500 MiB | `audio/wav`, `video/mp4`, `video/quicktime`, `video/webm`, `video/x-matroska`, `video/x-msvideo` |
| `cue` | `/api/v1/assets/cue` | 50 MiB | `audio/wav` |

Both defaults (`application/asset_ingestion.py`,
`DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES` /
`DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES`) are conservative, explicitly documented
starting points, not a benchmarked ceiling -- no empirical upload-size
usage evidence exists yet for this project. Each is independently
overridable at process start through a non-secret environment variable read
once by `interfaces/rest_api/app.py`'s composition root:
`AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES` and
`AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES`. The supported-media allowlists
are fixed, not environment-configurable, in this issue.
`AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT` (default `var/asset_storage`,
relative to the process's working directory) configures
`LocalFilesystemAssetStorage`'s base directory the same way.

### Bounded ingestion before unsafe resource consumption

`core.asset.AssetStoragePort.ingest(content: bytes, ...)` accepts only a
complete, already-buffered payload and defines no chunked-read or size-
limit primitive of its own. `asset_routes.py` therefore never uses
FastAPI's `UploadFile = File(...)` parameter injection -- Starlette's own
multipart parser would otherwise fully consume an arbitrarily large file
part into a spooled temporary file before the route function even runs,
since its `max_part_size` option bounds only non-file field values, not a
file part's own bytes. Each route instead reads the raw request stream
itself, enforcing the configured limit in two layers: a `Content-Length`
pre-check (a cheap, immediate rejection for a client that honestly declares
an oversized body) and an authoritative bounded-stream wrapper around
`request.stream()` that raises the instant the limit is exceeded, covering
an absent or dishonest `Content-Length` and chunked transfer encoding
alike. Only once the total observed bytes are already known to be within
the configured bound is the (now safely sized) buffered content handed to
`starlette.formparsers.MultiPartParser` for parsing. A request rejected at
either layer never reaches `AssetStoragePort.ingest`, so no partial or
unowned bytes are ever written to storage.

### Ownership after upload

An Asset created by either endpoint is not yet owned by any Analysis
(`docs/artifact-retention-and-cleanup-policy.md`, "Ownership model"): it
becomes owned only once a future Analysis (M5-04) references it as
`source_asset_id`, a `cues[].asset_id`, or through `owned_asset_ids`. This
issue establishes no ownership or cleanup behavior of its own for an
uploaded-but-never-consumed Asset.

## Asynchronous Analysis creation (M5-04)

`POST /api/v1/analyses` accepts an `AnalysisCreateRequest` body (one
`source_asset_id`, a non-empty `cues` list) and returns `202 Accepted`
with `AnalysisPublic` (`status: "queued"`) and a `Location` header naming
the created Analysis's discoverable status location
(`/api/v1/analyses/{analysis_id}`), without waiting for acoustic matching
to complete. Analysis status/Result retrieval at that location remains
M5-05's endpoint to define; this route only makes the identifier
discoverable.

### Explicit cue-count limit

The maximum cue count per request is explicit and configurable
(`application/create_analysis.py`, `DEFAULT_MAX_CUE_COUNT = 20`),
overridable at process start through the non-secret environment variable
`AUDIO_CUE_LOCATOR_MAX_CUE_COUNT`, mirroring M5-03's own
`AUDIO_CUE_LOCATOR_MAX_*_UPLOAD_BYTES` convention. As with those upload
limits, no empirical usage evidence exists yet for this project; 20 is a
conservative, explicitly documented starting point. A request exceeding
the configured maximum is rejected via `errors.ResourceLimitExceededError`
(413) before any persistence or scheduling call; a request with zero cues
is rejected by `AnalysisCreateRequest`'s own schema validation (422)
before Application is ever invoked.

### No-idempotency baseline (explicit decision)

`AnalysisRepositoryPort.create` requires a caller-supplied `analysis_id`
and defines no deduplication primitive, and `AnalysisCreateRequest` never
accepts one from a client. This issue's explicit, documented baseline:
**`analysis_id` is always freshly generated server-side for every
request; there is no request-level idempotency.** Two structurally
identical creation requests (same `source_asset_id` and `cues`) persist
two independent Analyses, each with its own identity, lifecycle, and
eventual Result. A retried or duplicated submission is indistinguishable
from an unrelated new request. A future issue that needs
duplicate-suppression semantics (for example, a client-supplied
idempotency key) would need to extend `AnalysisCreateRequest` explicitly;
this issue does not introduce one.

### Effective-configuration default (explicit decision)

No function anywhere in `src/` built a default
`EffectiveConfigurationSnapshot` before this issue.
`docs/analysis-effective-configuration.md` section 3 explicitly names the
choice between `acoustic_matching.baseline.DEFAULT_CONFIGURATION` and
`acoustic_matching.acceptance.EVIDENCE_BASED_CONFIGURATION` as "a decision
of a future Application layer". This issue's decision:
`application.create_analysis.default_effective_configuration` copies
`infrastructure.media_processing.canonical_audio.CANONICAL_AUDIO_SPEC`
into `canonicalization` and **`EVIDENCE_BASED_CONFIGURATION`** (not the
provisional `baseline.DEFAULT_CONFIGURATION`) into `matching`, recording
`configuration_source_name = "acoustic_matching.acceptance.
EVIDENCE_BASED_CONFIGURATION"`. Every Analysis created by this endpoint
uses this one policy; there is no per-request override.

### Asset validation and canonicalization happen before persistence

Both of this issue's own State's two most operationally significant open
items are resolved as follows, and — unlike the upload-size/allowlist
decisions above — **both happen synchronously, as part of request
validation, before `AnalysisRepositoryPort.create` ever persists a
`QUEUED` record**:

1. **Existence and content-format compatibility** (in place of a
   true Asset-`logical_type` check, which no confirmed dependency
   supports — see "Residual limitation" below): each referenced Asset
   (`source_asset_id` and every `cues[].asset_id`) is read via the
   existing `core.asset.AssetStoragePort.read` (raising
   `AssetNotFoundError`, already mapped to 404, if it does not exist) and
   its content is inspected with the existing
   `application.asset_ingestion.sniff_media_type` (reused, not
   reimplemented). A source must sniff as `audio/wav` or one of the
   S0002 video media types (`video/mp4`, `video/quicktime`,
   `video/webm`, `video/x-matroska`, `video/x-msvideo`); a cue must
   sniff as `audio/wav`. A mismatch raises `errors.
   UnsupportedMediaError` (415).
2. **Canonicalization**: the same already-read bytes are decoded and
   converted into a `CANONICAL_AUDIO_SPEC`-conformant (mono, float32,
   48000 Hz, peak-normalized) NumPy array — a WAV payload is parsed
   directly with the standard-library `wave` module (downmixed and
   resampled with `numpy`/`scipy` as needed); any of the five S0002
   video payloads is first decoded to WAV via the same existing,
   sole-permitted `infrastructure.media_processing.
   FFmpegMediaAdapter.extract_audio`, through one shared Application
   helper rather than a video-container-specific matcher path. Content
   that sniffs as a supported container but cannot actually be decoded
   (a malformed WAV, or audio `ffmpeg`/`ffprobe` cannot decode) raises
   `errors.UnsupportedMediaError` (415) via `application.
   create_analysis.AssetCanonicalizationError`.

**Why this is synchronous, not deferred to the async executor stage:**
`core.analysis_lifecycle.VALID_TRANSITIONS` defines exactly
`QUEUED`→`RUNNING`, `RUNNING`→`SUCCEEDED`, and `RUNNING`→`FAILED` — there
is no `QUEUED`→`FAILED` transition, and `LocalAnalysisExecutor.submit`
performs its own `QUEUED`→`RUNNING` claim as the very first thing it does
once called. A canonicalization failure discovered *after* a `QUEUED`
record was already persisted, but *before* `submit` is called (and
therefore before anything has claimed the record), could not be recorded
as `FAILED` through this project's existing, unmodified lifecycle
contract — the Analysis would be stuck `QUEUED` forever, with no valid
transition available and no code authorized to add one (`core.
analysis_lifecycle` and `infrastructure/execution/
local_analysis_executor.py` are both outside this issue's edit scope).
Performing existence/content-format/canonicalization validation before
`AnalysisRepositoryPort.create` is ever called avoids this entirely: a
request that fails any of them is rejected the same way a missing or
oversized-cue-count request already is, with no Analysis ever persisted.
**Only the acoustic-matching correlation itself** — `run_multi_cue_
analysis`, invoked from inside `LocalAnalysisExecutor.submit`'s own
bounded worker pool — remains fully asynchronous and is never awaited by
this endpoint; this is the formal issue's own named top risk ("HTTP
handlers execute matching directly and block long requests") and the one
this design keeps off the request path. A large source-media file's own
decode/resample time is a bounded, accepted cost of this synchronous
validation step (the same accepted-latency category as the
existence-check read itself, below), not the risk this issue's own
Restrições target.

### Residual limitation: wrong-type Asset validation

`core.asset.AssetStoragePort` exposes no operation returning a
previously-ingested Asset's `logical_type` by identifier — `read`
returns only raw bytes. This means a validly-ingested `cue` Asset
supplied as `source_asset_id` (or vice versa), where both happen to share
a compatible container format (both `audio/wav`), is **not detectable**
by this endpoint: content-format compatibility checking (above) only
catches a container-type mismatch (for example, an MP4 supplied as a
cue), not a role mislabeling that produces an otherwise-valid container.
This is an explicit, accepted limitation of this issue's authorized
scope, not an oversight; closing it would require either scoping
acceptance further (not currently proposed) or authorizing a new
Asset-metadata-lookup capability on `AssetStoragePort`, which this issue
does not add.

### Existence-check read cost (accepted characteristic)

`AssetStoragePort` exposes no lightweight existence-only primitive; `read`
is the only way to confirm an Asset exists, and it returns the complete
payload. Since the formal issue requires invalid/missing Asset references
to fail before any persistence or scheduling call, this endpoint reads
each referenced Asset's full bytes synchronously as part of request
validation — for a large source-media Asset (up to M5-03's own 500 MiB
upload limit), this is a real, non-trivial I/O cost inside the creation
request. It is accepted as a known characteristic of this endpoint (not a
defect); implementation reuses these same already-read bytes for
canonicalization (above) rather than reading each Asset twice.

### Composition-root thread-safety: `_ThreadLocalAnalysisRepository`

`infrastructure/analysis_repository/sqlite_repository.py`'s
`SQLiteAnalysisRepository` opens a `sqlite3` connection with the default
`check_same_thread=True`, valid only on the thread that created it — but
`LocalAnalysisExecutor` calls `AnalysisRepositoryPort.transition` from
whichever thread its own bounded worker pool assigns each claimed
Analysis to, never the thread that built the composition root. Sharing
one `SQLiteAnalysisRepository` instance across both raises
`sqlite3.ProgrammingError` from inside the worker thread — silently, since
nothing inspects the `Future` `LocalAnalysisExecutor.submit` returns —
leaving every created Analysis permanently `queued` (confirmed by direct
execution during implementation; this is a pre-existing M4-03/M4-04
integration gap this issue is the first to exercise under real concurrent
execution). Changing `sqlite_repository.py` itself is outside this
issue's authorized edit scope. `app.py`'s composition root instead wraps
it in `_ThreadLocalAnalysisRepository`, which lazily constructs one
`SQLiteAnalysisRepository` per calling thread, all pointed at the same
database file (SQLite itself already supports multiple connections to one
file, serialized by its own file locking and the `busy_timeout` PRAGMA
`SQLiteAnalysisRepository.__init__` already sets). The wrapper embeds no
SQL of its own. The database path is explicit and configurable through
`AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH` (default
`var/analysis_repository.sqlite3`, relative to the process's working
directory), mirroring `AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT`'s own
convention.

## Analysis status and Result retrieval (M5-05)

`GET /api/v1/analyses/{analysis_id}` performs one read through
`application.query_analysis.QueryAnalysisUseCase.get_status` and returns
the existing `AnalysisPublic` schema with HTTP 200. The repository record is
the sole lifecycle authority, so all four persisted states (`queued`,
`running`, `succeeded`, and `failed`) are returned as-is and the request does
not cache, infer, or transition state. A missing identifier propagates
`AnalysisNotFoundError` to the shared `resource_not_found`/404 response.

`GET /api/v1/analyses/{analysis_id}/result` first reads the same authoritative
record, then applies this closed lifecycle gate:

| Persisted state / condition | HTTP outcome |
|---|---|
| `queued` or `running` | 409 shared Error envelope with `result_not_ready`. |
| `failed` | 409 shared Error envelope with `analysis_failed`. |
| missing Analysis | 404 shared Error envelope with `resource_not_found`. |
| `succeeded` with an available valid Result body | 200 `AnalysisResultEnvelope`. |
| `succeeded` with a missing reference, unavailable body, malformed JSON, mismatched `analysis_id`, missing version, or non-completed body | Sanitized 500 `internal_error`; no reference or storage detail is exposed. |

The Application query reads the Result through its own
`ResultReferenceReaderPort`; neither REST handler imports Infrastructure.
The composition root creates exactly one `InMemoryResultReferenceStore` and
injects that instance into both `LocalAnalysisExecutor` (writer) and
`QueryAnalysisUseCase` (reader). This is intentionally local-process and
non-durable: restart, multi-process sharing, object storage, or a new result
database remain outside M5-05.

The successful envelope copies `result_schema_version` from the stored
Result body's own `schema_version`; `/api/v1` never substitutes its API
version. The canonical Result body is returned unchanged. In particular, a
`CueNoMatch` remains a successful completed Result whose cue outcome is
`{"kind": "no_match", "occurrences": null, "failure": null}`—never a
FAILED Analysis or an Error response.

## Identifier and timestamp conventions

- Every identifier (`Asset.identifier`, `Analysis.analysis_id`,
  `AnalysisCueReference.asset_id`) is an opaque string. Asset identifiers
  are canonical lowercase UUIDs generated by Asset Storage
  (`core.asset.validate_asset_identifier`); this contract does not weaken
  that format.
- Every timestamp (`AnalysisLifecycleTimestamps.*`) is timezone-aware,
  matching `application.ports.analysis_repository.LifecycleTimestamps`'s
  own invariant; a naive datetime is rejected by schema validation, not
  silently treated as UTC.

## OpenAPI representability

`app.create_app()` registers `AssetCreateRequest`, `AssetPublic`,
`AnalysisCreateRequest`, `AnalysisPublic`, `AnalysisResultEnvelope`, and
(M5-02) `ErrorPublic` (plus their nested enums/models) into the generated
OpenAPI document's `components.schemas`, alongside the six routes this
project now defines (`/api/v1/health`, M5-03's two upload routes, M5-04's
Analysis creation route, and M5-05's status and Result routes). This keeps
the contract reviewable and renderable from the executable route/schema
definitions.

## Non-goals of this document

This document does not define Analysis listing, cancellation, WebSocket or
event-stream updates, optimized polling, direct Result file downloads, or
durable/distributed Result storage. It also does not change Core matching or
Analysis lifecycle semantics, log raw request
bodies or exception payloads, persist raw uploaded media as evidence,
define authentication, authorization, quotas, TLS, or production CORS
policy, or add resumable uploads, streaming analysis, object storage,
distributed queues/brokers/remote workers, Analysis cancellation, bulk
creation, Analysis listing, or arbitrary/client-controlled destination
paths — all explicitly out of M5-02's, M5-03's, and M5-04's scope.
