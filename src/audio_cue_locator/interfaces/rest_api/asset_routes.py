"""Interfaces (REST API): bounded source-media and cue upload endpoints
(M5-03).

Exposes ``POST /assets/source-media`` and ``POST /assets/cue`` (mounted at
the shared `/api/v1` prefix by `interfaces.rest_api.app.create_app`, exactly
as `/api/v1/health` already is), the two v1 upload endpoints M5-03 owns
(`issues/M5/M5-03/formal-issue.json`, section 4). Each endpoint accepts a
multipart request carrying exactly one file part, reads it under an
explicit, configurable byte bound, and delegates all ingestion policy to
`application.asset_ingestion.AssetIngestionUseCase` -- this module contains
no size/media-type policy decision of its own.

Bounded multipart reading (gap G4,
`intents/M5/M5-03/implementation-handoff.json`): FastAPI's usual
``UploadFile = File(...)`` parameter injection lets Starlette's own
multipart parser fully consume the request body -- including an arbitrarily
large file part -- before this module's route function is even called.
Starlette's ``max_part_size`` option (`starlette.formparsers.
MultiPartParser.on_part_data`) only bounds a non-file *field* value held in
memory; it applies no limit at all to a file part's own bytes, which are
written to an `UploadFile`'s spooled temporary file without bound. This
module therefore does not use ``File(...)``/``UploadFile`` parameter
injection at all: each route takes the raw ``fastapi.Request`` and performs
its own bounded read, in two layers --

1. A ``Content-Length`` pre-check (`_reject_if_declared_length_exceeds`):
   a cheap, immediate rejection for a well-behaved client that honestly
   declares an oversized body, before any byte is read from the connection.
2. An authoritative bounded stream wrapper (`_bounded_stream`), which counts
   every byte Starlette's multipart parser will ever see -- across every
   part, including the file payload -- and raises the instant the
   configured limit is exceeded, before any further chunk is read from the
   client or written to a temporary file. This is the actual guarantee: it
   does not depend on the client declaring (or honestly declaring)
   ``Content-Length`` at all, so it also covers chunked transfer encoding.

Once the bounded stream has produced at most one policy's worth of bytes,
those bytes are handed to `starlette.formparsers.MultiPartParser` --
Starlette's own tested multipart parsing implementation, reused rather than
reimplemented -- constructed directly (not via ``Request.form()``, which
would otherwise re-consume ``request.stream()`` without the bounding wrapper
above).

Per `docs/architecture.md` ("REST API e quaisquer interfaces futuras devem
invocar operacoes da camada Application"), this module depends on
`application.asset_ingestion` and on this package's own `schemas.py`; it
never imports `audio_cue_locator.infrastructure`. It also never imports
`interfaces.rest_api.app` (importing `app.V1_STATUS_CATALOG` here would
create a circular import, since `app.py` imports `build_asset_router` from
this module to register it); HTTP status codes below duplicate the relevant
numeric literal directly via `fastapi.status`, exactly as
`interfaces.rest_api.errors` already does for the same reason.

`_handle_upload` below is this issue's one narrow translation point between
Application's own `AssetUploadTooLargeError`/`UnsupportedAssetMediaError`
(`application.asset_ingestion` cannot itself import anything under
`interfaces.rest_api` -- see that module's docstring for the circular-
import this would otherwise create) and `errors.py`'s REST-boundary
`ResourceLimitExceededError`/`UnsupportedMediaError`, which
`errors.install_error_handlers` already maps to the safe 413/415 responses
(`docs/rest-api-v1-contract.md`, "Shared Error contract").
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from audio_cue_locator.application.asset_ingestion import (
    AssetIngestionUseCase,
    AssetUploadTooLargeError,
    UnsupportedAssetMediaError,
)
from audio_cue_locator.core.asset import AssetType
from audio_cue_locator.interfaces.rest_api.errors import (
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import AssetPublic, asset_to_public


def build_asset_router(use_case: AssetIngestionUseCase) -> APIRouter:
    """Return an `APIRouter` exposing both upload endpoints against the
    given, already-constructed `use_case`.

    A factory rather than a module-level router: `use_case` carries the
    concrete `AssetStoragePort` and `UploadLimits`
    `interfaces.rest_api.app`'s composition root constructs, so this module
    never wires a default itself and stays trivially testable against a
    fake or in-memory port without any FastAPI dependency-override
    machinery.
    """

    router = APIRouter()

    @router.post(
        "/assets/source-media",
        response_model=AssetPublic,
        status_code=status.HTTP_201_CREATED,
        summary="Upload source media as a new Asset",
        tags=["v1"],
    )
    async def upload_source_media(request: Request) -> AssetPublic:
        return await _handle_upload(request, use_case, AssetType.SOURCE_MEDIA)

    @router.post(
        "/assets/cue",
        response_model=AssetPublic,
        status_code=status.HTTP_201_CREATED,
        summary="Upload a cue as a new Asset",
        tags=["v1"],
    )
    async def upload_cue(request: Request) -> AssetPublic:
        return await _handle_upload(request, use_case, AssetType.CUE)

    return router


async def _handle_upload(
    request: Request, use_case: AssetIngestionUseCase, logical_type: AssetType
) -> AssetPublic:
    policy = use_case.limits.policy_for(logical_type)
    _reject_if_declared_length_exceeds(request, policy.max_size_bytes)

    form = await _parse_bounded_multipart_form(request, policy.max_size_bytes)
    try:
        upload = _require_single_file_part(form)
        content = await upload.read()
    finally:
        await form.close()

    try:
        asset = use_case.ingest(
            content=content,
            logical_type=logical_type,
            informative_name=upload.filename or "",
        )
    except AssetUploadTooLargeError as exc:
        raise ResourceLimitExceededError(str(exc)) from exc
    except UnsupportedAssetMediaError as exc:
        raise UnsupportedMediaError(str(exc)) from exc
    return asset_to_public(asset)


def _reject_if_declared_length_exceeds(request: Request, max_size_bytes: int) -> None:
    """Cheap fast-path rejection for a client that honestly declares an
    oversized body. Never the sole guarantee: an absent, chunked-encoded,
    or dishonest ``Content-Length`` falls through to `_bounded_stream`."""

    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        declared_bytes = int(declared)
    except ValueError:
        return
    if declared_bytes > max_size_bytes:
        raise ResourceLimitExceededError(
            f"Declared Content-Length {declared_bytes} exceeds the "
            f"configured {max_size_bytes}-byte limit"
        )


async def _bounded_stream(
    stream: AsyncIterator[bytes], max_size_bytes: int
) -> AsyncIterator[bytes]:
    """Wrap a raw request byte stream, raising the instant more than
    ``max_size_bytes`` total bytes have been observed across the whole
    stream -- see module docstring for why this, not
    `MultiPartParser`'s own ``max_part_size``, is the real bound."""

    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > max_size_bytes:
            raise ResourceLimitExceededError(
                f"Upload stream exceeded the configured {max_size_bytes}-byte "
                "limit before its body was fully read"
            )
        yield chunk


def _malformed_request_error(message: str) -> RequestValidationError:
    """A request-shape failure this module detects itself, expressed the
    same way FastAPI/Pydantic's own request parsing would raise it, so it
    reaches the same `errors.install_error_handlers`-registered
    ``RequestValidationError`` handler (422/``validation_error``) as any
    other malformed request (`docs/rest-api-v1-contract.md`, "Shared Error
    contract")."""

    return RequestValidationError(errors=[{"loc": ("body",), "msg": message, "type": "value_error"}])


async def _parse_bounded_multipart_form(request: Request, max_size_bytes: int) -> FormData:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise _malformed_request_error("Request must be multipart/form-data")

    parser = MultiPartParser(request.headers, _bounded_stream(request.stream(), max_size_bytes))
    try:
        return await parser.parse()
    except MultiPartException as exc:
        raise _malformed_request_error(
            "Request body is not valid multipart/form-data"
        ) from exc


def _require_single_file_part(form: FormData) -> StarletteUploadFile:
    uploads: list[Any] = [value for value in form.values() if isinstance(value, StarletteUploadFile)]
    if len(uploads) != 1:
        raise _malformed_request_error("Request must contain exactly one file part")
    return uploads[0]
