"""Interfaces (REST API): the versioned FastAPI application assembly for
`/api/v1` (M5-01), installing the centralized v1 error-translation policy
(M5-02) and, as of M5-03, the bounded source-media/cue upload routes.

This module now owns two things: the one baseline health route proving the
`/api/v1` namespace is live and OpenAPI-representable
(`issues/M5/M5-01/formal-issue.json`, acceptance criteria 1 and 6), and the
*composition root* that wires `application.asset_ingestion.
AssetIngestionUseCase` to a concrete `AssetStoragePort` implementation for
`interfaces.rest_api.asset_routes`'s two upload endpoints (M5-03). It does
not define Analysis creation or status/result retrieval endpoints; those
remain owned by M5-04 and M5-05 (`docs/rest-api-v1-contract.md`). The
Asset/Analysis/Error transport schemas in `schemas.py` are registered into
the generated OpenAPI document as components even where no business route
yet returns them, so the contract itself is reviewable and OpenAPI-
representable ahead of the endpoints that will use it.

`create_app()` registers `errors.install_error_handlers` so every current
and future `/api/v1` route -- not only the ones this module defines --
inherits the same safe failure-translation policy without its own
try/except (`issues/M5/M5-02/formal-issue.json`, acceptance criteria 1-3).

Per `docs/architecture.md` ("O Core nao conhece interfaces externas";
"REST API e quaisquer interfaces futuras devem invocar operacoes da camada
Application"), `interfaces.rest_api.asset_routes` -- the actual route-
handling code -- depends on Application only and never imports
`audio_cue_locator.infrastructure`. This module is the one narrow,
explicit exception: as the composition root (the sole place any concrete
adapter is wired to the `core.asset.AssetStoragePort` protocol it
implements, since no other wiring point exists yet anywhere in this
project's `src/` tree), `create_app()` below imports and constructs
`infrastructure.asset_storage.LocalFilesystemAssetStorage` purely to inject
it into `AssetIngestionUseCase` -- it contains no upload-handling or
business logic of its own. The only other Core/Application imports
anywhere in this package are the mapping functions in `schemas.py` and the
exception-type mapping in `errors.py`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from audio_cue_locator.application.asset_ingestion import (
    AssetIngestionUseCase,
    UploadLimits,
    UploadPolicy,
    default_upload_limits,
)
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.interfaces.rest_api.asset_routes import build_asset_router
from audio_cue_locator.interfaces.rest_api.errors import install_error_handlers
from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisPublic,
    AnalysisResultEnvelope,
    AssetCreateRequest,
    AssetPublic,
    ErrorPublic,
)

API_VERSION = "v1"
API_V1_PREFIX = f"/api/{API_VERSION}"

V1_STATUS_CATALOG: dict[str, int] = {
    "ok": 200,
    "created": 201,
    "accepted": 202,
    "no_content": 204,
    "bad_request": 400,
    "payload_too_large": 413,
    "unsupported_media_type": 415,
    "not_found": 404,
    "conflict": 409,
    "unprocessable_entity": 422,
    "internal_error": 500,
}
"""The baseline HTTP status catalog reserved for the `/api/v1` namespace
(`issues/M5/M5-01/formal-issue.json`, section 3, extended by
`issues/M5/M5-02/formal-issue.json`, section 4, and `issues/M5/M5-03/
formal-issue.json`, section 4). Only `"ok"` (200) is bound to a route
defined directly in this module (`get_health` below); `"created"` (201),
added by M5-03, is bound to `interfaces.rest_api.asset_routes`'s own two
upload routes (`fastapi.status.HTTP_201_CREATED`, duplicated there rather
than imported from here to avoid a circular import -- see that module's
docstring). The remaining entries -- including `"payload_too_large"` and
`"unsupported_media_type"`, added by M5-02 -- are bound to the centralized
handlers `errors.install_error_handlers` registers below, so the status
vocabulary stays coherent across the whole namespace instead of each
endpoint issue picking its own names for the same HTTP status."""

_ASSET_STORAGE_ROOT_ENV = "AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT"
_DEFAULT_ASSET_STORAGE_ROOT = Path("var") / "asset_storage"
_MAX_SOURCE_MEDIA_UPLOAD_BYTES_ENV = "AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES"
_MAX_CUE_UPLOAD_BYTES_ENV = "AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES"


def _asset_storage_root() -> Path:
    """Explicit, non-secret, environment-overridable Asset Storage base
    directory (formal issue restricao: configuration must be explicit).
    Defaults to `var/asset_storage`, relative to the current working
    directory, consistent with this project's local-first scope
    (`docs/architecture.md`, "Local-first nao significa local-only")."""

    configured = os.environ.get(_ASSET_STORAGE_ROOT_ENV)
    return Path(configured) if configured else _DEFAULT_ASSET_STORAGE_ROOT


def _upload_limits() -> UploadLimits:
    """Explicit, environment-overridable upload-size limits layered over
    `asset_ingestion.default_upload_limits`'s own documented defaults (gap
    G1, `intents/M5/M5-03/implementation-handoff.json`). The supported-
    media allowlists are not environment-configurable in this issue: they
    stay fixed to `infrastructure/media_processing/ffmpeg_adapter.py`'s own
    documented supported inputs, not an operator-supplied value."""

    defaults = default_upload_limits()
    source_media_override = os.environ.get(_MAX_SOURCE_MEDIA_UPLOAD_BYTES_ENV)
    cue_override = os.environ.get(_MAX_CUE_UPLOAD_BYTES_ENV)
    return UploadLimits(
        source_media=UploadPolicy(
            max_size_bytes=(
                int(source_media_override)
                if source_media_override
                else defaults.source_media.max_size_bytes
            ),
            supported_media_types=defaults.source_media.supported_media_types,
        ),
        cue=UploadPolicy(
            max_size_bytes=(
                int(cue_override) if cue_override else defaults.cue.max_size_bytes
            ),
            supported_media_types=defaults.cue.supported_media_types,
        ),
    )


def _build_asset_ingestion_use_case() -> AssetIngestionUseCase:
    """Construct this process's one `AssetIngestionUseCase`, injected with a
    concrete `LocalFilesystemAssetStorage` -- the only place in this
    project's `src/` tree a concrete Asset Storage adapter is constructed
    for production use (see module docstring)."""

    storage = LocalFilesystemAssetStorage(_asset_storage_root())
    return AssetIngestionUseCase(storage, _upload_limits())


_TRANSPORT_SCHEMAS_FOR_OPENAPI = (
    AssetCreateRequest,
    AssetPublic,
    AnalysisCreateRequest,
    AnalysisPublic,
    AnalysisResultEnvelope,
    ErrorPublic,
)


def create_app() -> FastAPI:
    """Assemble the `/api/v1` FastAPI application.

    A factory rather than a bare module-level side effect, so a future
    caller (an ASGI server entry point, or a test client in a later,
    separately authorized issue) can construct a fresh instance instead of
    sharing this module's mutable `app.openapi_schema` cache.
    """

    app = FastAPI(
        title="Audio Cue Locator API",
        version=API_VERSION,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.include_router(build_asset_router(_build_asset_ingestion_use_case()), prefix=API_V1_PREFIX)

    @app.get(
        f"{API_V1_PREFIX}/health",
        summary="Baseline v1 namespace health check",
        response_model=dict[str, str],
        status_code=V1_STATUS_CATALOG["ok"],
        tags=["v1"],
    )
    def get_health() -> dict[str, str]:
        """Prove the `/api/v1` namespace and its baseline 200 status are
        live. Carries no Asset/Analysis behavior of its own."""

        return {"status": "ok", "api_version": API_VERSION}

    def _openapi() -> dict[str, Any]:
        return _build_openapi_schema(app)

    app.openapi = _openapi  # type: ignore[method-assign]
    return app


def _build_openapi_schema(app: FastAPI) -> dict[str, Any]:
    """Return `app`'s cached OpenAPI document, generating and extending it
    on first use so the Asset/Analysis transport schemas from
    `schemas.py` are present as `components.schemas` (acceptance
    criterion 6) even though no route currently returns them.
    """

    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        openapi_version=app.openapi_version,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in _TRANSPORT_SCHEMAS_FOR_OPENAPI:
        model_schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        nested_definitions = model_schema.pop("$defs", {})
        components[model.__name__] = model_schema
        for definition_name, definition_schema in nested_definitions.items():
            components.setdefault(definition_name, definition_schema)

    app.openapi_schema = schema
    return app.openapi_schema


app = create_app()
"""Module-level instance for an ASGI server entry point (for example
`uvicorn audio_cue_locator.interfaces.rest_api.app:app`). Constructing it
here only assembles routes and schema metadata; it performs no I/O, opens
no database connection, and calls no Infrastructure adapter."""
