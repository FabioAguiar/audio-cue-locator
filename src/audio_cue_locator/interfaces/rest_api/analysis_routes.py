"""Interfaces (REST API): asynchronous Analysis creation endpoint (M5-04).

Exposes ``POST /analyses`` (mounted at the shared `/api/v1` prefix by
`interfaces.rest_api.app.create_app`, exactly as the M5-03 upload routes
already are). The route accepts the confirmed-existing
`schemas.AnalysisCreateRequest` body, delegates every validation,
canonicalization, and persistence/scheduling decision to
`application.create_analysis.CreateAnalysisUseCase`, and maps the result to
`schemas.AnalysisPublic` with `202 Accepted`
(`app.V1_STATUS_CATALOG["accepted"]`) plus a `Location` header naming the
created Analysis's status location -- Analysis status/Result retrieval
itself remains M5-05's endpoint to define; this route only makes the
identifier discoverable.

Per `docs/architecture.md` ("REST API e quaisquer interfaces futuras devem
invocar operacoes da camada Application"), this module depends on
`application.create_analysis` and this package's own `schemas.py`/
`errors.py`; it never imports `audio_cue_locator.infrastructure`. Like
`asset_routes.py`, it never imports `interfaces.rest_api.app` (a circular
import, since `app.py` imports this module's router-builder to register
it).

`_create_analysis` below is this issue's one narrow translation point
between Application's own `TooManyCuesError`/`AssetContentIncompatibleError`/
`AssetCanonicalizationError` (`application.create_analysis` cannot itself
import anything under `interfaces.rest_api` -- see that module's docstring
for the circular import this would otherwise create) and `errors.py`'s
REST-boundary `ResourceLimitExceededError`/`UnsupportedMediaError`, which
`errors.install_error_handlers` already maps to the safe 413/415 responses.
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
    CreateAnalysisUseCase,
    CueRequest,
    TooManyCuesError,
)
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisPublic,
    analysis_record_to_public,
)

STATUS_LOCATION_HEADER = "Location"
"""Header this route sets to the created Analysis's discoverable status
location. No route currently serves that location (Analysis status/Result
retrieval is M5-05's endpoint to define); the header names where it will
live so a client need not guess the URL shape ahead of time."""


def build_analysis_router(use_case: CreateAnalysisUseCase) -> APIRouter:
    """Return an `APIRouter` exposing the Analysis creation endpoint against
    the given, already-constructed `use_case`.

    A factory rather than a module-level router, mirroring
    `asset_routes.build_asset_router`: `use_case` carries the concrete
    `AnalysisRepositoryPort`/`AssetStoragePort`/`LocalAnalysisExecutor`
    `interfaces.rest_api.app`'s composition root constructs, so this module
    never wires a default itself.
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
        return _create_analysis(payload, response, use_case)

    return router


def _create_analysis(
    payload: AnalysisCreateRequest, response: Response, use_case: CreateAnalysisUseCase
) -> AnalysisPublic:
    """The route's actual logic, factored out so it can be called directly
    in tests without going through FastAPI's request/response machinery
    (mirroring `asset_routes._handle_upload`)."""

    cue_requests = [
        CueRequest(cue_id=cue.cue_id, asset_id=cue.asset_id) for cue in payload.cues
    ]
    try:
        record = use_case.create(source_asset_id=payload.source_asset_id, cues=cue_requests)
    except TooManyCuesError as exc:
        raise ResourceLimitExceededError(str(exc)) from exc
    except (AssetContentIncompatibleError, AssetCanonicalizationError) as exc:
        raise UnsupportedMediaError(str(exc)) from exc

    public = analysis_record_to_public(record)
    response.headers[STATUS_LOCATION_HEADER] = f"/api/v1/analyses/{public.analysis_id}"
    return public
