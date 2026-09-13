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

- `schemas.py` — the public Pydantic v1 request/response schemas for Asset
  and Analysis, plus one explicit mapping function per resource
  (`asset_to_public`, `analysis_record_to_public`).
- `app.py` — the FastAPI application assembly: the `/api/v1` namespace
  prefix, the baseline HTTP status catalog, one baseline health route, and
  the registration of every schema below into the generated OpenAPI
  document.

This issue does **not** implement Asset upload behavior, Analysis creation,
status/result retrieval, or centralized external error translation. Those
remain owned by M5-03, M5-04, M5-05, and M5-02 respectively. No route other
than the baseline health check exists yet.

## Dependency direction

`src/audio_cue_locator/interfaces/rest_api/` may depend on
`src/audio_cue_locator/application/` only. It must not import
`src/audio_cue_locator/infrastructure/` (matcher, SQLite, filesystem, or
execution adapters) and must not reuse a `src/audio_cue_locator/core/`
entity or persistence record as a public schema
(`docs/architecture.md`, Principle 1; "Tipos Pydantic de transporte nao
devem ser reutilizados automaticamente como entidades do Core"). `app.py`
imports only `schemas.py`; the only Core/Application imports in this
package are the two mapping functions in `schemas.py`.

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
| `bad_request` | 400 | Malformed or invalid client input; M5-02. |
| `not_found` | 404 | An Asset or Analysis identifier with no matching resource; M5-02/M5-05. |
| `conflict` | 409 | A state conflict, such as an already-existing Analysis identifier (`AnalysisAlreadyExistsError`); M5-02/M5-04. |
| `unprocessable_entity` | 422 | Schema-valid but semantically invalid input; M5-02. |
| `internal_error` | 500 | An unclassified server-side failure; M5-02. |

Only `ok` is bound to a route today. The remaining names exist so the
status vocabulary stays coherent across the whole `/api/v1` namespace
instead of each endpoint issue picking its own name for the same HTTP
status; centralized error-response shaping for the 4xx/5xx entries above
remains M5-02's scope, not this issue's.

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
`AnalysisCreateRequest`, `AnalysisPublic`, and `AnalysisResultEnvelope`
(plus their nested enums/models) into the generated OpenAPI document's
`components.schemas`, in addition to the one route this issue defines.
This keeps the contract itself reviewable and renderable in OpenAPI ahead
of the endpoints that will reference these schemas as their
`response_model`/request body in M5-02 through M5-06.

## Non-goals of this document

This document does not define: Asset upload behavior (M5-03), Analysis
creation orchestration (M5-04), status/Result retrieval (M5-05), or
centralized external error translation (M5-02). It does not certify that
any of those endpoints exist; it fixes only the shared transport and
versioning baseline they must build on.
