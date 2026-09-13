# REST API v1 Transport Contract

## Purpose and boundary

M5-01 defines the shared external transport and versioning baseline every
later M5 endpoint issue (M5-02 through M5-06) reuses instead of each
inventing its own Asset/Analysis representation, identifier convention, or
version scheme (`issues/M5/M5-01/formal-issue.json`). It fixes the public
Asset and Analysis schemas, the `/api/v1` namespace, identifier and
timestamp conventions, a baseline route and HTTP status catalog, explicit
transport-to-Application mappings, and OpenAPI representability.

The executable contract lives in two modules under
`src/audio_cue_locator/interfaces/rest_api/`:

- `schemas.py` — the public Pydantic v1 request/response schemas for Asset,
  Analysis, and (M5-02) the shared Error envelope, plus one explicit
  mapping function per resource (`asset_to_public`,
  `analysis_record_to_public`).
- `app.py` — the FastAPI application assembly: the `/api/v1` namespace
  prefix, the baseline HTTP status catalog, one baseline health route, the
  installation of the centralized error-translation policy (M5-02), and
  the registration of every schema below into the generated OpenAPI
  document.
- `errors.py` (M5-02) — the centralized v1 failure-translation policy:
  the closed exception-to-`ErrorCode`/HTTP-status mapping and
  `install_error_handlers`, which every current and future `/api/v1`
  route inherits.

M5-01 does **not** implement Asset upload behavior, Analysis creation,
status/result retrieval, or centralized external error translation. Those
remain owned by M5-03, M5-04, M5-05, and M5-02 respectively. M5-02 (below)
implements the shared Error contract and its translation policy, but no
route other than the baseline health check exists yet, so the policy is
currently exercised only by request-validation failures FastAPI itself
raises and by direct tests, not by any business endpoint.

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
| `/api/v1/health` | GET | 200 | Baseline liveness check for the `/api/v1` namespace. The only route this issue defines. |

The complete baseline status catalog (`app.V1_STATUS_CATALOG`) reserves one
name per HTTP status every later M5 endpoint issue must reuse rather than
reinvent:

| Name | HTTP status | Reserved for |
|---|---:|---|
| `ok` | 200 | Synchronous success (bound to `/api/v1/health` today). |
| `accepted` | 202 | Asynchronous Analysis creation (`docs/architecture.md`, "Fluxo programatico": "202 Accepted + Analysis ID"); M5-04. |
| `no_content` | 204 | A future successful request with no response body. |
| `bad_request` | 400 | Domain-level invalid input caught by Core/Application (`InvalidAssetIdentifierError`, `InvalidAssetMetadataError`, `InvalidAnalysisRecordError`, `AnalysisSourceInputError`); bound by M5-02. |
| `payload_too_large` | 413 | A size/quantity limit exceeded (`errors.ResourceLimitExceededError`); bound by M5-02. |
| `unsupported_media_type` | 415 | Submitted media fails format/codec/audio-stream validation (`errors.UnsupportedMediaError`); bound by M5-02. |
| `not_found` | 404 | An Asset or Analysis identifier with no matching resource (`AssetNotFoundError`, `AnalysisNotFoundError`); bound by M5-02, used by M5-05. |
| `conflict` | 409 | A state conflict: an Analysis lifecycle transition conflict, an already-existing Analysis identifier, an Asset storage collision, or a Result requested for a FAILED Analysis; bound by M5-02, used by M5-04. |
| `unprocessable_entity` | 422 | Request-shape validation caught by FastAPI/Pydantic before Application runs (`RequestValidationError`); bound by M5-02. |
| `internal_error` | 500 | Any unmapped/unexpected exception; bound by M5-02. |

`ok` and every 4xx/5xx entry above are now bound to the centralized
handlers `errors.install_error_handlers` registers on every `/api/v1`
app instance (M5-02); no business route yet raises most of these
exception types, since Asset upload, Analysis creation, and Result
retrieval remain M5-03 through M5-05's endpoints to define, but the
policy already applies to any request-validation failure FastAPI itself
raises today.

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

`AssetCreateRequest` fixes only the client-supplied metadata a future
upload endpoint (M5-03) will accept alongside the raw bytes: `logical_type`,
`informative_name`, and `media_type`. The upload transport itself (how the
bytes travel) is explicitly out of scope for this issue.

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
any code in this package; a future M5-03/M5-04/M5-05 endpoint must raise
the mapped exceptions above, not `HTTPException`, to stay within this
contract.

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
OpenAPI document's `components.schemas`, in addition to the one route
this issue defines. This keeps the contract itself reviewable and
renderable in OpenAPI ahead of the endpoints that will reference these
schemas as their `response_model`/request body/error responses in M5-03
through M5-06.

## Non-goals of this document

This document does not define: Asset upload behavior (M5-03), Analysis
creation orchestration (M5-04), or status/Result retrieval (M5-05). It
does not certify that any of those endpoints exist; it fixes only the
shared transport, versioning, and (as of M5-02) error-translation baseline
they must build on. It also does not change Core matching or Analysis
lifecycle semantics, log raw request bodies or exception payloads, or
define authentication, authorization, quotas, TLS, or production CORS
policy — all explicitly out of M5-02's scope.
