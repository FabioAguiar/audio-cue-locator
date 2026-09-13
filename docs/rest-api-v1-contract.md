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
`src/audio_cue_locator/interfaces/rest_api/` (plus one Application module):

- `schemas.py` — the public Pydantic v1 request/response schemas for Asset,
  Analysis, and (M5-02) the shared Error envelope, plus one explicit
  mapping function per resource (`asset_to_public`,
  `analysis_record_to_public`).
- `app.py` — the FastAPI application assembly: the `/api/v1` namespace
  prefix, the baseline HTTP status catalog, the baseline health route, the
  installation of the centralized error-translation policy (M5-02), the
  registration of every schema below into the generated OpenAPI document,
  and (M5-03) the composition root that wires a concrete
  `AssetStoragePort` implementation into the upload routes.
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

M5-01 does **not** implement Analysis creation or status/result retrieval;
those remain owned by M5-04 and M5-05. M5-02 implements the shared Error
contract and its translation policy. M5-03 implements the first two
business routes that actually exercise it: bounded source-media and cue
Asset uploads (see "Bounded source-media and cue uploads (M5-03)" below).

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
before it ever reaches Interfaces.

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
field is the literal `"v1"`, while `result_schema_version` is read from
`core.analysis_result.SCHEMA_VERSION` and `result` stays an untyped,
opaque mapping so this document and module never duplicate or redefine the
M3 Result schema (`docs/analysis-result-schema.md`). No route returns this
envelope yet; Result retrieval is M5-05's endpoint to define.

## Baseline route and HTTP status catalog

| Route | Method | Status | Purpose |
|---|---|---:|---|
| `/api/v1/health` | GET | 200 | Baseline liveness check for the `/api/v1` namespace (M5-01). |
| `/api/v1/assets/source-media` | POST | 201 | Bounded source-media Asset upload (M5-03). |
| `/api/v1/assets/cue` | POST | 201 | Bounded cue Asset upload (M5-03). |

The complete baseline status catalog (`app.V1_STATUS_CATALOG`) reserves one
name per HTTP status every later M5 endpoint issue must reuse rather than
reinvent:

| Name | HTTP status | Reserved for |
|---|---:|---|
| `ok` | 200 | Synchronous success (bound to `/api/v1/health` today). |
| `created` | 201 | A new resource was created (bound to both M5-03 upload routes). |
| `accepted` | 202 | Asynchronous Analysis creation (`docs/architecture.md`, "Fluxo programatico": "202 Accepted + Analysis ID"); M5-04. |
| `no_content` | 204 | A future successful request with no response body. |
| `bad_request` | 400 | Domain-level invalid input caught by Core/Application (`InvalidAssetIdentifierError`, `InvalidAssetMetadataError`, `InvalidAnalysisRecordError`, `AnalysisSourceInputError`); bound by M5-02. |
| `payload_too_large` | 413 | A size/quantity limit exceeded (`errors.ResourceLimitExceededError`); bound by M5-02, first raised by M5-03's upload routes (translated from `asset_ingestion.AssetUploadTooLargeError`). |
| `unsupported_media_type` | 415 | Submitted media fails format/codec/audio-stream validation (`errors.UnsupportedMediaError`); bound by M5-02, first raised by M5-03's upload routes (translated from `asset_ingestion.UnsupportedAssetMediaError`). |
| `not_found` | 404 | An Asset or Analysis identifier with no matching resource (`AssetNotFoundError`, `AnalysisNotFoundError`); bound by M5-02, used by M5-05. |
| `conflict` | 409 | A state conflict: an Analysis lifecycle transition conflict, an already-existing Analysis identifier, an Asset storage collision, or a Result requested for a FAILED Analysis; bound by M5-02, used by M5-04. |
| `unprocessable_entity` | 422 | Request-shape validation caught by FastAPI/Pydantic before Application runs (`RequestValidationError`); bound by M5-02. |
| `internal_error` | 500 | Any unmapped/unexpected exception; bound by M5-02. |

`ok`, `created`, and every 4xx/5xx entry above are bound to the centralized
handlers `errors.install_error_handlers` registers on every `/api/v1`
app instance (M5-02). M5-03's two upload routes are the first business
routes to actually raise `payload_too_large`/`unsupported_media_type`;
Analysis creation and Result retrieval remain M5-04/M5-05's endpoints to
define, and the policy already applies to any request-validation failure
FastAPI itself raises today regardless.

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
transport field. The `202 Accepted` asynchronous-creation response this
request implies is M5-04's endpoint to define.

## Shared Error contract and sanitization policy (M5-02)

Public representation of a `/api/v1` failure. Every non-2xx response
across every endpoint family uses this one schema
(`issues/M5/M5-02/formal-issue.json`, acceptance criterion 1).

| Field | Required | Type | Notes |
|---|---:|---|---|
| `error_code` | yes | `ErrorCode` enum | One of the seven closed, additive-only values below. |
| `message` | yes | string | Always one of a small, fixed set of safe strings (`errors.SAFE_MESSAGES`); never a raw exception message, stack trace, host path, SQL fragment, or echoed request value. |
| `correlation_id` | yes | string | A random, non-guessable `uuid.uuid4().hex` value a client can report back for operator diagnosis, without the response itself disclosing anything about the underlying cause (acceptance criterion 5). |

### Error-code catalog

| `error_code` | HTTP status | Mapped from |
|---|---:|---|
| `validation_error` | 422 (request-shape) or 400 (domain-level) | `RequestValidationError`; `InvalidAssetIdentifierError`; `InvalidAssetMetadataError`; `InvalidAnalysisRecordError`; `AnalysisSourceInputError`. |
| `unsupported_media` | 415 | `errors.UnsupportedMediaError` (a future Application adapter translates an Infrastructure `media_processing.errors.InvalidMediaError`/`NoAudioStreamError` into this type before it reaches Interfaces). |
| `resource_limit_exceeded` | 413 | `errors.ResourceLimitExceededError` (a declared size or quantity limit, for example an oversized upload or too many cues). |
| `resource_not_found` | 404 | `AssetNotFoundError`; `AnalysisNotFoundError`. |
| `lifecycle_conflict` | 409 | `InvalidLifecycleTransitionError`; `AssetStorageCollisionError`; `AnalysisAlreadyExistsError`. |
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
conflict, raised as `AnalysisResultUnavailableError` and mapped to
`analysis_failed`/409 above.

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
"Bounded source-media and cue uploads" below), and a future M5-04/M5-05
endpoint must do the same, not raise `HTTPException`, to stay within this
contract.

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
payload's own leading bytes for the two container signatures
`infrastructure/media_processing/ffmpeg_adapter.py` already documents as
this project's supported inputs (a RIFF/WAVE header for `audio/wav`, an ISO
base media file format `ftyp` box for `video/mp4`); a full FFmpeg
decode/stream probe remains out of this issue's scope and happens later, at
Analysis creation (M5-04) and matching.

### Explicit, configurable limits

| Logical type | Endpoint | Default max size | Supported media types |
|---|---|---:|---|
| `source_media` | `/api/v1/assets/source-media` | 500 MiB | `audio/wav`, `video/mp4` |
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
OpenAPI document's `components.schemas`, alongside the three routes this
project now defines (`/api/v1/health`, and M5-03's two upload routes). This
keeps the contract itself reviewable and renderable in OpenAPI ahead of the
endpoints that will reference these schemas as their `response_model`/
request body/error responses in M5-04 through M5-06.

## Non-goals of this document

This document does not define: Analysis creation orchestration (M5-04) or
status/Result retrieval (M5-05). It does not certify that either endpoint
exists; it fixes only the shared transport, versioning, error-translation
(M5-02), and bounded-upload (M5-03) baseline they must build on. It also
does not change Core matching or Analysis lifecycle semantics, log raw
request bodies or exception payloads, persist raw uploaded media as
evidence, define authentication, authorization, quotas, TLS, or production
CORS policy, or add resumable uploads, streaming analysis, object storage,
or arbitrary/client-controlled destination paths — all explicitly out of
M5-02's and M5-03's scope.
