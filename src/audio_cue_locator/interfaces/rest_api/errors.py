"""Interfaces (REST API): the centralized v1 external Error contract and
failure-translation policy (M5-02).

Every `/api/v1` failure -- request validation, unsupported media, a
size/duration/quantity limit, a missing resource, an Analysis lifecycle
conflict, a persisted FAILED Analysis, or an unexpected exception -- is
translated here into the single `schemas.ErrorPublic` envelope and a safe
HTTP status drawn from `app.V1_STATUS_CATALOG`, so every endpoint family
(present and future) returns one consistent shape instead of each
inventing its own (`issues/M5/M5-02/formal-issue.json`, acceptance
criterion 1).

Per `docs/architecture.md` ("O Core nao conhece interfaces externas";
"REST API e quaisquer interfaces futuras devem invocar operacoes da camada
Application"), this module imports Core/Application exception types only
(`core.asset`, `core.analysis_lifecycle`, `application.ports.
analysis_repository`, `application.multi_cue_orchestration`) and never
`audio_cue_locator.infrastructure`. An Infrastructure failure (for example
`media_processing.errors.InvalidMediaError`) never reaches this module
directly: translating it into one of the REST-boundary exception types
below (or into a persisted `core.analysis_result.StructuredError` on the
Analysis record) is Application's responsibility, exercised by the
endpoint issues that actually call Infrastructure (M5-03/M5-04).
`UnsupportedMediaError`, `ResourceLimitExceededError`, and the compatibility
`AnalysisResultUnavailableError` are this module's own REST-boundary
vocabulary for the failure classes those future endpoints will raise; they
carry no Infrastructure or Core import of their own, and are defined here
-- not in `application/` -- because this issue's authorized edit scope is
limited to `interfaces/rest_api/`. M7-02's `AssetProcessingTimeoutError`
(`application.create_analysis`) is translated to this same module's
`UnsupportedMediaError` by `interfaces.rest_api.analysis_routes`, a
deliberate, documented conflation (`docs/supported-media-and-limits.md`)
that keeps this module's external `ErrorCode`/status contract unchanged.

Every mapped response uses one of a small, fixed set of safe per-
`ErrorCode` messages (`SAFE_MESSAGES`), never a raw exception message, so
a future change to an internal exception's own message text can never
change what a client observes (formal issue section 17, "avoid echoing
untrusted input"). Every response this module returns also carries a
random, non-guessable `correlation_id` (`uuid.uuid4().hex`) so an operator
can be pointed at "the request that produced this id" without the
response itself disclosing anything about the underlying cause
(acceptance criterion 5).

A route must raise one of the exception types mapped below (or let
`RequestValidationError` propagate from FastAPI/Pydantic's own request
parsing) to go through this policy; raising `fastapi.HTTPException`
directly bypasses it and is not used by any code in this package.

M5-05's Application-owned `AnalysisResultNotReadyError` and
`AnalysisFailedError` are mapped here directly to distinct safe codes at the
shared 409 status. The older REST-owned `AnalysisResultUnavailableError`
mapping remains additive compatibility for existing callers.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from audio_cue_locator.application.multi_cue_orchestration import (
    AnalysisSourceInputError,
)
from audio_cue_locator.application.query_analysis import (
    AnalysisFailedError,
    AnalysisResultNotReadyError,
)
from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    InvalidAnalysisRecordError,
)
from audio_cue_locator.core.analysis_lifecycle import InvalidLifecycleTransitionError
from audio_cue_locator.core.asset import (
    AssetNotFoundError,
    AssetStorageCollisionError,
    InvalidAssetIdentifierError,
    InvalidAssetMetadataError,
)
from audio_cue_locator.interfaces.rest_api.schemas import ErrorCode, ErrorPublic
from audio_cue_locator.observability import emit_diagnostic_event


class UnsupportedMediaError(ValueError):
    """Raised at the REST/Application boundary when submitted media fails
    format, codec, or audio-stream validation. Owned by this module, not
    Core or Infrastructure: a future Application-layer adapter translates
    an Infrastructure `media_processing.errors.InvalidMediaError` or
    `NoAudioStreamError` into this type before it reaches Interfaces."""


class ResourceLimitExceededError(ValueError):
    """Raised at the REST/Application boundary when a request exceeds a
    declared size, duration, or quantity limit (for example, an oversized
    upload, an over-duration source/cue Asset, or too many cues in one
    Analysis request)."""


class AnalysisResultUnavailableError(RuntimeError):
    """Raised when a Result is requested for an Analysis whose persisted
    state is FAILED. Distinct from `InvalidLifecycleTransitionError`,
    which guards state *transitions*; this guards Result *retrieval* for
    an Analysis that already reached its terminal FAILED state."""


SAFE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "The request is invalid.",
    ErrorCode.UNSUPPORTED_MEDIA: "The supplied media is not supported.",
    ErrorCode.RESOURCE_LIMIT_EXCEEDED: (
        "The request exceeds an allowed size or quantity limit."
    ),
    ErrorCode.RESOURCE_NOT_FOUND: "The requested resource was not found.",
    ErrorCode.LIFECYCLE_CONFLICT: (
        "The requested operation conflicts with the resource's current state."
    ),
    ErrorCode.RESULT_NOT_READY: "The Analysis Result is not available yet.",
    ErrorCode.ANALYSIS_FAILED: (
        "The Analysis reached a failed state and has no available Result."
    ),
    ErrorCode.INTERNAL_ERROR: "An unexpected error occurred.",
}
"""The only text a client ever sees for each `ErrorCode`. Never derived
from an exception's own message, so an internal message change can never
become a client-visible (or accidentally sensitive) change."""

_EXCEPTION_STATUS_MAP: tuple[tuple[type[Exception], ErrorCode, int], ...] = (
    (RequestValidationError, ErrorCode.VALIDATION_ERROR, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (InvalidAssetIdentifierError, ErrorCode.VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST),
    (InvalidAssetMetadataError, ErrorCode.VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST),
    (InvalidAnalysisRecordError, ErrorCode.VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST),
    (AnalysisSourceInputError, ErrorCode.VALIDATION_ERROR, status.HTTP_400_BAD_REQUEST),
    (UnsupportedMediaError, ErrorCode.UNSUPPORTED_MEDIA, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    (
        ResourceLimitExceededError,
        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    ),
    (AssetNotFoundError, ErrorCode.RESOURCE_NOT_FOUND, status.HTTP_404_NOT_FOUND),
    (AnalysisNotFoundError, ErrorCode.RESOURCE_NOT_FOUND, status.HTTP_404_NOT_FOUND),
    (
        InvalidLifecycleTransitionError,
        ErrorCode.LIFECYCLE_CONFLICT,
        status.HTTP_409_CONFLICT,
    ),
    (AssetStorageCollisionError, ErrorCode.LIFECYCLE_CONFLICT, status.HTTP_409_CONFLICT),
    (AnalysisAlreadyExistsError, ErrorCode.LIFECYCLE_CONFLICT, status.HTTP_409_CONFLICT),
    (
        AnalysisResultNotReadyError,
        ErrorCode.RESULT_NOT_READY,
        status.HTTP_409_CONFLICT,
    ),
    (AnalysisFailedError, ErrorCode.ANALYSIS_FAILED, status.HTTP_409_CONFLICT),
    (
        AnalysisResultUnavailableError,
        ErrorCode.ANALYSIS_FAILED,
        status.HTTP_409_CONFLICT,
    ),
)
"""Closed mapping from one caught exception type to its `ErrorCode` and
safe HTTP status (`issues/M5/M5-02/formal-issue.json`, section 4). Each
entry gets its own `app.add_exception_handler` registration below rather
than one `isinstance` chain, so a subclass is dispatched exactly the way
Starlette's own exception-handler lookup already resolves it."""


def _correlation_id() -> str:
    return uuid.uuid4().hex


def _error_response(
    error_code: ErrorCode, http_status: int, exc: Exception | None = None
) -> JSONResponse:
    correlation_id = _correlation_id()
    payload = ErrorPublic(
        error_code=error_code,
        message=SAFE_MESSAGES[error_code],
        correlation_id=correlation_id,
    )
    # M7-04: reconciles this envelope's own per-response correlation_id
    # with analysis_id, when the caught exception already carries one as
    # an attribute (for example `AnalysisAlreadyClaimedError`) -- the two
    # previously disconnected correlation identifiers this issue's own
    # state required addressing
    # (`states/M7/M7-04/issue-operational-state.json#/risks/3`, gap G4).
    # `category` reuses this response's own closed `ErrorCode` value
    # rather than the caught exception's type or message, so no
    # additional diagnostic taxonomy is introduced at this boundary.
    analysis_id = getattr(exc, "analysis_id", None) if exc is not None else None
    emit_diagnostic_event(
        event="api_request_failed",
        boundary="api",
        outcome="failed",
        category=error_code.value,
        analysis_id=analysis_id if isinstance(analysis_id, str) else None,
        correlation_id=correlation_id,
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump(mode="json"))


def install_error_handlers(app: FastAPI) -> None:
    """Register the centralized v1 failure-translation policy on `app`.

    Every current and future `/api/v1` route inherits these handlers
    automatically; no route needs its own try/except to return a safe
    response (acceptance criteria 1-3). Must be called once per `app`
    instance, before it serves any request.
    """

    for exception_type, error_code, http_status in _EXCEPTION_STATUS_MAP:

        def _mapped_handler(
            request: Request,
            exc: Exception,
            error_code: ErrorCode = error_code,
            http_status: int = http_status,
        ) -> JSONResponse:
            return _error_response(error_code, http_status, exc)

        app.add_exception_handler(exception_type, _mapped_handler)

    def _unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            ErrorCode.INTERNAL_ERROR, status.HTTP_500_INTERNAL_SERVER_ERROR, exc
        )

    app.add_exception_handler(Exception, _unexpected_handler)
