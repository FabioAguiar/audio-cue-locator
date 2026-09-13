"""Interfaces (REST API): the versioned FastAPI application assembly for
`/api/v1` (M5-01).

This module owns exactly one route: a baseline health check proving the
`/api/v1` namespace is live and OpenAPI-representable
(`issues/M5/M5-01/formal-issue.json`, acceptance criteria 1 and 6). It does
not define Asset upload, Analysis creation, status/result retrieval, or
error-translation endpoints; those remain owned by M5-02 through M5-06
(`docs/rest-api-v1-contract.md`). The Asset/Analysis transport schemas in
`schemas.py` are registered into the generated OpenAPI document as
components even though no route yet returns them, so the contract itself
is reviewable and OpenAPI-representable ahead of the endpoints that will
use it.

Per `docs/architecture.md` ("O Core nao conhece interfaces externas";
"REST API e quaisquer interfaces futuras devem invocar operacoes da camada
Application"), this module must depend on Application only. It imports
nothing from `audio_cue_locator.infrastructure`; the only Core/Application
imports anywhere in this package are the mapping functions in
`schemas.py`, and this module does not call them (there is no persisted
Asset or Analysis to map yet -- the health route returns a plain literal).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from audio_cue_locator.interfaces.rest_api.schemas import (
    AnalysisCreateRequest,
    AnalysisPublic,
    AnalysisResultEnvelope,
    AssetCreateRequest,
    AssetPublic,
)

API_VERSION = "v1"
API_V1_PREFIX = f"/api/{API_VERSION}"

V1_STATUS_CATALOG: dict[str, int] = {
    "ok": 200,
    "accepted": 202,
    "no_content": 204,
    "bad_request": 400,
    "not_found": 404,
    "conflict": 409,
    "unprocessable_entity": 422,
    "internal_error": 500,
}
"""The baseline HTTP status catalog reserved for the `/api/v1` namespace
(`issues/M5/M5-01/formal-issue.json`, section 3). Only `"ok"` (200) is
bound to an actual route by this issue (`get_health` below); the remaining
entries are reserved names each later M5 endpoint issue (M5-02 through
M5-06) must reuse rather than reinvent -- for example `"accepted"` (202)
for the asynchronous-creation response `docs/architecture.md` documents
under "Fluxo programatico" -- so the status vocabulary stays coherent
across the whole namespace instead of each endpoint issue picking its own
names for the same HTTP status."""

_TRANSPORT_SCHEMAS_FOR_OPENAPI = (
    AssetCreateRequest,
    AssetPublic,
    AnalysisCreateRequest,
    AnalysisPublic,
    AnalysisResultEnvelope,
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
