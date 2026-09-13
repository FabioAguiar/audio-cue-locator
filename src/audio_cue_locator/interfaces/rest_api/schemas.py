"""Interfaces (REST API): public v1 transport schemas for Asset and Analysis,
and their explicit mappings to and from Application/Core values (M5-01).

M5-01's objective is the shared external transport and versioning baseline
every later M5 endpoint issue (M5-02 through M5-06) must reuse instead of
each inventing its own Asset/Analysis representation
(`issues/M5/M5-01/formal-issue.json`, sections 3-4;
`intents/M5/M5-01/implementation-handoff.json`). This module defines that
baseline as Pydantic models plus one explicit mapping function per resource
(`asset_to_public`, `analysis_record_to_public`); it does not define or wire
any endpoint (`app.py` owns the one baseline route this issue adds), and it
does not implement upload, asynchronous creation, status/result retrieval,
or centralized error translation, which remain owned by M5-03 through
M5-06 (`docs/rest-api-v1-contract.md`).

Every public model here is declared independently of the Core/Application
types it represents, following the same pattern this repository already
uses at its other layer boundaries (see
`audio_cue_locator.application.multi_cue_orchestration.FailureCategory`,
declared independently of the equal-by-value Core enum in
`audio_cue_locator.core.analysis_result`, "because Application depends on
Core and not the reverse"). A mapping function is the only place a Core or
Application type is imported and read; no public model subclasses,
re-exports, or otherwise reuses a Core entity or persistence record
directly (`docs/architecture.md`, "Tipos Pydantic de transporte nao devem
ser reutilizados automaticamente como entidades do Core";
`docs/asset-identity-and-storage.md`, "Application must depend on
AssetStoragePort").

`Asset.storage_reference` (`core/asset.py`) is deliberately excluded from
`AssetPublic`: it is documented there as "opaque internal reference" that
"Core and Application must not parse... as a filesystem path", and this
issue's own risk register warns against exposing adapter-specific details
publicly (`context-packs/M5/M5-01/issue-analysis.json#/analysis/risks/2`).

`AnalysisResultEnvelope` exists to make acceptance criterion 4 ("API and
Result schema versions are independently represented") a concrete,
OpenAPI-representable schema rather than only a documentation statement:
its `api_version` field is always `"v1"`, while `result_schema_version`
is copied by `analysis_result_to_envelope` from the stored Result body's own
`schema_version` and evolves under that contract's unrelated versioning
policy. M5-05's Result route returns this envelope.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    CueAssetReference,
)
from audio_cue_locator.core.analysis_result import (
    SCHEMA_VERSION as CORE_ANALYSIS_RESULT_SCHEMA_VERSION,
)
from audio_cue_locator.core.asset import Asset

API_VERSION = "v1"
"""The `/api/v1` namespace version this module's schemas belong to. Kept
independent from `CORE_ANALYSIS_RESULT_SCHEMA_VERSION`; see
`AnalysisResultEnvelope`."""


class _ForbidExtraModel(BaseModel):
    """Shared base: reject unknown fields so a typo or an accidentally
    leaked internal field is a validation error, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


def _require_timezone_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


# --- Asset ------------------------------------------------------------------


class AssetLogicalType(str, Enum):
    """Public projection of `core.asset.AssetType`'s closed vocabulary,
    declared independently rather than imported (see module docstring)."""

    SOURCE_MEDIA = "source_media"
    CUE = "cue"
    DERIVED_ARTIFACT = "derived_artifact"
    ANALYSIS_RESULT = "analysis_result"


class AssetCreateRequest(_ForbidExtraModel):
    """Client-supplied Asset creation metadata.

    Deliberately excludes the raw byte payload: the multipart/streaming
    upload transport itself is M5-03's scope
    (`issues/M5/M5-01/formal-issue.json`, section 4, "Nao inclui"). This
    schema fixes only the metadata fields a future upload endpoint will
    accept alongside the bytes, mirroring
    `core.asset.AssetStoragePort.ingest`'s `logical_type` and
    `informative_name`/`detected_media_type` parameters.
    """

    logical_type: AssetLogicalType
    informative_name: str = Field(..., min_length=1, max_length=255)
    media_type: str = Field(..., min_length=3, max_length=255)


class AssetPublic(_ForbidExtraModel):
    """Public v1 representation of a persisted `core.asset.Asset`.

    Every field here has an explicit required/optional designation
    (acceptance criterion 2): all seven fields are required, matching
    `Asset`'s own non-optional fields, with the sole intentional omission
    of `storage_reference` (see module docstring).
    """

    identifier: str
    logical_type: AssetLogicalType
    sanitized_name: str
    media_type: str
    size_bytes: int = Field(..., ge=0)
    checksum: str = Field(..., min_length=64, max_length=64)
    checksum_algorithm: Literal["sha256"]


def asset_to_public(asset: Asset) -> AssetPublic:
    """Map a Core `Asset` to its public v1 representation.

    The only place `Asset` is read by this module. `storage_reference` is
    read from nowhere here; it simply has no corresponding public field.
    """

    return AssetPublic(
        identifier=asset.identifier,
        logical_type=AssetLogicalType(asset.logical_type.value),
        sanitized_name=asset.sanitized_name,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        checksum=asset.checksum,
        checksum_algorithm=asset.checksum_algorithm,  # type: ignore[arg-type]
    )


# --- Analysis -----------------------------------------------------------


class AnalysisStatus(str, Enum):
    """Public projection of `core.analysis_lifecycle.AnalysisLifecycleState`,
    declared independently rather than imported (see module docstring)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisCueReference(_ForbidExtraModel):
    """Public projection of one
    `application.ports.analysis_repository.CueAssetReference`."""

    cue_id: str = Field(..., min_length=1)
    asset_id: str


class AnalysisCreateRequest(_ForbidExtraModel):
    """Client-supplied Analysis creation request.

    Deliberately excludes any effective-configuration override: matching
    configuration is a server-owned concern captured once per Analysis
    (`docs/analysis-effective-configuration.md`), not a client-supplied
    transport field. Asynchronous creation semantics and the resulting
    `202 Accepted` response are M5-04's scope
    (`docs/architecture.md`, "Fluxo programatico"); this schema fixes only
    the request shape.
    """

    source_asset_id: str
    cues: list[AnalysisCueReference] = Field(..., min_length=1)


class AnalysisFailureCategory(str, Enum):
    """Public projection of `core.analysis_result.FailureCategory`,
    declared independently rather than imported (see module docstring)."""

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MEDIA = "unsupported_media"
    DECODE_OR_CANONICALIZATION_FAILURE = "decode_or_canonicalization_failure"
    MATCHING_FAILURE = "matching_failure"
    RESOURCE_LIMIT = "resource_limit"
    INTERNAL_FAILURE = "internal_failure"


class AnalysisStructuredError(_ForbidExtraModel):
    """Public projection of one `core.analysis_result.StructuredError`.
    Present on `AnalysisPublic` if, and only if, `status` is `failed`,
    mirroring `AnalysisRecord`'s own invariant."""

    category: AnalysisFailureCategory
    message: str = Field(..., min_length=1)


class AnalysisLifecycleTimestamps(_ForbidExtraModel):
    """Public projection of one
    `application.ports.analysis_repository.LifecycleTimestamps`. Every
    timestamp is timezone-aware, matching that type's own invariant."""

    queued_at: datetime
    running_at: datetime | None = None
    succeeded_at: datetime | None = None
    failed_at: datetime | None = None

    _validate_queued_at = field_validator("queued_at")(_require_timezone_aware)
    _validate_running_at = field_validator("running_at")(_require_timezone_aware)
    _validate_succeeded_at = field_validator("succeeded_at")(_require_timezone_aware)
    _validate_failed_at = field_validator("failed_at")(_require_timezone_aware)


class AnalysisPublic(_ForbidExtraModel):
    """Public v1 representation of a persisted
    `application.ports.analysis_repository.AnalysisRecord`.

    `result_reference` stays an opaque pointer, exactly as `AnalysisRecord`
    declares it: this schema never embeds the Result body itself (that is
    `AnalysisResultEnvelope`'s narrower, not-yet-routed purpose, reserved
    for M5-05). `owned_asset_ids` is intentionally omitted from the public
    surface: it is bookkeeping for retention/cleanup
    (`docs/asset-identity-and-storage.md`), not part of the resource
    identity a client needs to track an Analysis.
    """

    analysis_id: str
    status: AnalysisStatus
    source_asset_id: str
    cues: list[AnalysisCueReference] = Field(..., min_length=1)
    lifecycle_timestamps: AnalysisLifecycleTimestamps
    result_reference: str | None = None
    structured_error: AnalysisStructuredError | None = None


def analysis_record_to_public(record: AnalysisRecord) -> AnalysisPublic:
    """Map an Application `AnalysisRecord` to its public v1 representation.

    The only place `AnalysisRecord`/`CueAssetReference` are read by this
    module.
    """

    def _cue_to_public(cue: CueAssetReference) -> AnalysisCueReference:
        return AnalysisCueReference(cue_id=cue.cue_id, asset_id=cue.asset_id)

    structured_error = None
    if record.structured_error is not None:
        structured_error = AnalysisStructuredError(
            category=AnalysisFailureCategory(record.structured_error.category.value),
            message=record.structured_error.message,
        )

    return AnalysisPublic(
        analysis_id=record.analysis_id,
        status=AnalysisStatus(record.state.value),
        source_asset_id=record.source_asset_id,
        cues=[_cue_to_public(cue) for cue in record.cues],
        lifecycle_timestamps=AnalysisLifecycleTimestamps(
            queued_at=record.lifecycle_timestamps.queued_at,
            running_at=record.lifecycle_timestamps.running_at,
            succeeded_at=record.lifecycle_timestamps.succeeded_at,
            failed_at=record.lifecycle_timestamps.failed_at,
        ),
        result_reference=record.result_reference,
        structured_error=structured_error,
    )


# --- Result envelope (versioning independence, routed by M5-05) ------------


class AnalysisResultEnvelope(_ForbidExtraModel):
    """Illustrates that a v1 response carrying a Result keeps the Result's
    own `schema_version` fully independent of the `/api/v1` namespace
    version (acceptance criterion 4). `result` stays an untyped mapping
    deliberately, so this module does not duplicate or redefine the M3
    Result schema documented in `docs/analysis-result-schema.md`.
    """

    api_version: Literal["v1"] = API_VERSION
    result_schema_version: str = CORE_ANALYSIS_RESULT_SCHEMA_VERSION
    result: dict = Field(
        ...,
        description=(
            "Opaque canonical Analysis Result body produced by "
            "core.analysis_result.to_canonical_dict; not re-typed here."
        ),
    )


def analysis_result_to_envelope(result: Mapping[str, object]) -> AnalysisResultEnvelope:
    """Wrap one canonical Result mapping without replacing its version.

    ``QueryAnalysisUseCase`` validates the required ``schema_version`` before
    this transport mapping runs.  Reading the value from the Result body,
    rather than substituting the current API or Core default, preserves the
    independent version carried by the stored artifact.
    """

    schema_version = result.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError("Result schema_version must be a non-empty string")
    return AnalysisResultEnvelope(
        result_schema_version=schema_version,
        result=dict(result),
    )


# --- Error envelope (shared v1 external failure contract, M5-02) -----------


class ErrorCode(str, Enum):
    """The stable, closed v1 error-code catalog (`issues/M5/M5-02/formal-
    issue.json`, acceptance criteria 1-2). Every `/api/v1` failure
    response carries exactly one of these codes; the set is additive-only
    -- an existing member is never renamed or repurposed. The mapping from
    a caught exception to one of these codes lives in
    `interfaces.rest_api.errors`, not here."""

    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_MEDIA = "unsupported_media"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    RESOURCE_NOT_FOUND = "resource_not_found"
    LIFECYCLE_CONFLICT = "lifecycle_conflict"
    RESULT_NOT_READY = "result_not_ready"
    ANALYSIS_FAILED = "analysis_failed"
    INTERNAL_ERROR = "internal_error"


class ErrorPublic(_ForbidExtraModel):
    """The one external v1 Error envelope every `/api/v1` failure response
    uses (acceptance criterion 1). Distinct in purpose from
    `AnalysisStructuredError` above: that type is a persisted Analysis's
    own `structured_error` field on a successful 200 `AnalysisPublic`
    response, while this type is the body of a non-2xx HTTP response
    describing the *request's* own outcome. The two are never conflated or
    interchanged (`interfaces.rest_api.errors` module docstring).

    `message` is always one of a small, fixed set of safe per-`error_code`
    strings (`interfaces.rest_api.errors.SAFE_MESSAGES`) -- never a raw
    exception message, stack trace, host path, or echoed request value.
    `correlation_id` is a random, non-guessable identifier a client can
    report back for operator diagnosis without the response itself
    disclosing anything about the underlying cause (acceptance criterion
    5)."""

    error_code: ErrorCode
    message: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
