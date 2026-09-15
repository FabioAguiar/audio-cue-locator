"""Interfaces (REST API): Analysis creation and read endpoints (M5-04/M5-05).

Exposes ``POST /analyses`` (mounted at the shared `/api/v1` prefix by
`interfaces.rest_api.app.create_app`, exactly as the M5-03 upload routes
already are). The route accepts the confirmed-existing
`schemas.AnalysisCreateRequest` body, delegates every validation,
canonicalization, and persistence/scheduling decision to
`application.create_analysis.CreateAnalysisUseCase`, and maps the result to
`schemas.AnalysisPublic` with `202 Accepted`
(`app.V1_STATUS_CATALOG["accepted"]`) plus a `Location` header naming the
created Analysis's status location.  M5-05 adds ``GET /analyses/{id}`` and
``GET /analyses/{id}/result`` through ``application.query_analysis``;
neither handler reads SQLite, result storage, or the filesystem directly.

Per `docs/architecture.md` ("REST API e quaisquer interfaces futuras devem
invocar operacoes da camada Application"), this module depends on
`application.create_analysis` and this package's own `schemas.py`/
`errors.py`; it never imports `audio_cue_locator.infrastructure`. Like
`asset_routes.py`, it never imports `interfaces.rest_api.app` (a circular
import, since `app.py` imports this module's router-builder to register
it).

`_create_analysis` below is this issue's one narrow translation point
between Application's own `TooManyCuesError`/`AssetContentIncompatibleError`/
`AssetCanonicalizationError`/`MediaDurationExceededError`/
`AssetProcessingTimeoutError` (`application.create_analysis` cannot itself
import anything under `interfaces.rest_api` -- see that module's docstring
for the circular import this would otherwise create) and `errors.py`'s
REST-boundary `ResourceLimitExceededError`/`UnsupportedMediaError`, which
`errors.install_error_handlers` already maps to the safe 413/415
responses. M7-02 adds `MediaDurationExceededError` alongside
`TooManyCuesError` (both a declared-limit violation, 413) and
`AssetProcessingTimeoutError` alongside `AssetContentIncompatibleError`/
`AssetCanonicalizationError` (all three map to the existing 415
`UnsupportedMediaError`, a deliberate, documented conflation rather than a
new `ErrorCode`/status -- see `docs/supported-media-and-limits.md`).
`core.asset.AssetNotFoundError`/`InvalidAssetIdentifierError` and
`application.ports.analysis_repository.InvalidAnalysisRecordError`/
`AnalysisAlreadyExistsError` are already mapped by `errors.py` and are left
to propagate unchanged; this module does not catch them.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from audio_cue_locator.application.create_analysis import (
    AssetCanonicalizationError,
    AssetContentIncompatibleError,
    AssetProcessingTimeoutError,
    CreateAnalysisUseCase,
    CueRequest,
    MediaDurationExceededError,
    TooManyCuesError,
)
from audio_cue_locator.application.query_analysis import QueryAnalysisUseCase
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisPublic,
    AnalysisResultEnvelope,
    ErrorPublic,
    analysis_record_to_public,
    analysis_result_to_envelope,
)

STATUS_LOCATION_HEADER = "Location"
"""Header this route sets to the created Analysis's status endpoint."""

_STATUS_ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorPublic},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorPublic},
}
_RESULT_ERROR_RESPONSES = {
    **_STATUS_ERROR_RESPONSES,
    status.HTTP_409_CONFLICT: {"model": ErrorPublic},
}


def build_analysis_router(
    create_use_case: CreateAnalysisUseCase,
    query_use_case: QueryAnalysisUseCase | None = None,
) -> APIRouter:
    """Return an ``APIRouter`` for Analysis creation, status, and Result.

    A factory rather than a module-level router, mirroring
    ``asset_routes.build_asset_router``. Both use cases are constructed by
    ``interfaces.rest_api.app``; this module depends on Application contracts
    only and never wires or imports a concrete persistence/result-storage
    adapter. ``query_use_case`` remains optional only so the pre-M5-05
    creation-only builder call stays compatible; the production composition
    root always supplies it.
    """

    router = APIRouter()

    @router.post(
        "/analyses",
        response_model=AnalysisPublic,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Create an Analysis asynchronously from Asset identities",
        tags=["v1"],
    )
    def create_analysis(payload: AnalysisCreateRequest, response: Response) -> AnalysisPublic:
        return _create_analysis(payload, response, create_use_case)

    if query_use_case is not None:

        @router.get(
            "/analyses/{analysis_id}",
            response_model=AnalysisPublic,
            status_code=status.HTTP_200_OK,
            summary="Get the current persisted Analysis status",
            responses=_STATUS_ERROR_RESPONSES,
            tags=["v1"],
        )
        def get_analysis_status(analysis_id: str) -> AnalysisPublic:
            return _get_analysis_status(analysis_id, query_use_case)

        @router.get(
            "/analyses/{analysis_id}/result",
            response_model=AnalysisResultEnvelope,
            status_code=status.HTTP_200_OK,
            summary="Get a completed Analysis Result",
            responses=_RESULT_ERROR_RESPONSES,
            tags=["v1"],
        )
        def get_analysis_result(analysis_id: str) -> AnalysisResultEnvelope:
            return _get_analysis_result(analysis_id, query_use_case)

    return router


def _create_analysis(
    payload: AnalysisCreateRequest, response: Response, use_case: CreateAnalysisUseCase
) -> AnalysisPublic:
    """The route's actual logic, factored out so it can be called directly
    in tests without going through FastAPI's request/response machinery
    (mirroring `asset_routes._handle_upload`)."""

    cue_requests = [
        CueRequest(
            cue_id=cue.cue_id,
            asset_id=cue.asset_id,
            label=cue.label,
            trim_start_seconds=cue.trim_start_seconds,
            trim_end_seconds=cue.trim_end_seconds,
        )
        for cue in payload.cues
    ]
    try:
        record = use_case.create(
            source_asset_id=payload.source_asset_id,
            cues=cue_requests,
            minimum_similarity_score=payload.minimum_similarity_score,
        )
    except (TooManyCuesError, MediaDurationExceededError) as exc:
        raise ResourceLimitExceededError(str(exc)) from exc
    except (
        AssetContentIncompatibleError,
        AssetCanonicalizationError,
        AssetProcessingTimeoutError,
    ) as exc:
        raise UnsupportedMediaError(str(exc)) from exc

    public = analysis_record_to_public(record)
    response.headers[STATUS_LOCATION_HEADER] = f"/api/v1/analyses/{public.analysis_id}"
    return public


def _get_analysis_status(
    analysis_id: str, use_case: QueryAnalysisUseCase
) -> AnalysisPublic:
    """Map the Application's current persisted record to its public shape."""

    return analysis_record_to_public(use_case.get_status(analysis_id))


def _get_analysis_result(
    analysis_id: str, use_case: QueryAnalysisUseCase
) -> AnalysisResultEnvelope:
    """Map one lifecycle-gated canonical Result to the versioned envelope."""

    return analysis_result_to_envelope(use_case.get_result(analysis_id))
