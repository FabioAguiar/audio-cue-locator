"""Interfaces (REST API): the versioned FastAPI application assembly for
`/api/v1` (M5-01), installing the centralized v1 error-translation policy
(M5-02) and, as of M5-03, the bounded source-media/cue upload routes.

This module now owns three things: the one baseline health route proving
the `/api/v1` namespace is live and OpenAPI-representable
(`issues/M5/M5-01/formal-issue.json`, acceptance criteria 1 and 6); the
*composition root* that wires `application.asset_ingestion.
AssetIngestionUseCase` to a concrete `AssetStoragePort` implementation for
`interfaces.rest_api.asset_routes`'s two upload endpoints (M5-03); and, as
of M5-04, the second composition-root responsibility that wires
`application.create_analysis.CreateAnalysisUseCase` to a concrete
`AnalysisRepositoryPort` and a `LocalAnalysisExecutor` for
`interfaces.rest_api.analysis_routes`'s asynchronous creation endpoint. As
of M5-05, that composition root also shares one Result store between the
executor and ``application.query_analysis.QueryAnalysisUseCase`` for status
and Result retrieval. The Asset/Analysis/Error
transport schemas in `schemas.py` are registered into the generated
OpenAPI document as components even where no business route yet returns
them, so the contract itself is reviewable and OpenAPI-representable ahead
of the endpoints that will use it.

`create_app()` registers `errors.install_error_handlers` so every current
and future `/api/v1` route -- not only the ones this module defines --
inherits the same safe failure-translation policy without its own
try/except (`issues/M5/M5-02/formal-issue.json`, acceptance criteria 1-3).

Per `docs/architecture.md` ("O Core nao conhece interfaces externas";
"REST API e quaisquer interfaces futuras devem invocar operacoes da camada
Application"), `interfaces.rest_api.asset_routes`/`analysis_routes` -- the
actual route-handling code -- depend on Application only and never import
`audio_cue_locator.infrastructure`. This module is the one narrow,
explicit exception: as the composition root (the sole place any concrete
adapter is wired to the ports Application depends on, since no other
wiring point exists yet anywhere in this project's `src/` tree),
`create_app()` below imports and constructs `infrastructure.asset_storage.
LocalFilesystemAssetStorage` purely to inject it into
`AssetIngestionUseCase`, and (M5-04) `infrastructure.analysis_repository.
SQLiteAnalysisRepository` and `infrastructure.execution.
LocalAnalysisExecutor` purely to inject them into `CreateAnalysisUseCase`
-- it contains no upload-handling, Analysis-creation, or other business
logic of its own. The only other Core/Application imports anywhere in this
package are the mapping functions in `schemas.py` and the exception-type
mapping in `errors.py`.

`_ThreadLocalAnalysisRepository` below is a composition-root-only wiring
detail, confirmed necessary by direct execution at implementation time:
`SQLiteAnalysisRepository.__init__` opens a `sqlite3.connect(...)`
connection with its default `check_same_thread=True`, so that connection
may only be used by the thread that constructed it -- but
`LocalAnalysisExecutor` calls `AnalysisRepositoryPort.transition` from
whichever thread its own bounded worker pool assigns each claimed
Analysis to, which is never the thread that built the composition root.
Sharing one `SQLiteAnalysisRepository` instance across both raises
`sqlite3.ProgrammingError` from inside the worker thread, silently (no
caller ever inspects the `Future` `LocalAnalysisExecutor.submit` returns),
leaving every created Analysis permanently `queued`. Changing
`infrastructure/analysis_repository/sqlite_repository.py` itself (for
example, to pass `check_same_thread=False`) is outside this issue's
authorized edit scope, so this module instead lazily constructs one
`SQLiteAnalysisRepository` per calling thread, all pointed at the same
database file -- SQLite itself already supports multiple connections to
one file, serialized by its own file locking and the `busy_timeout`
PRAGMA `SQLiteAnalysisRepository.__init__` already sets. This wrapper
embeds no SQL of its own; it only multiplexes the confirmed-existing
constructor per thread.

As of M7-03, this composition root also applies the already-implemented
M4-05/M4-06 startup lifecycle: `_build_analysis_use_cases` calls
`infrastructure.execution.restart_recovery.run_startup_recovery` and
`infrastructure.asset_storage.retention_policy.cleanup_expired_assets`
exactly once each, in that order, after constructing the repository and
strictly before constructing `LocalAnalysisExecutor`, so recovery always
runs before any `QUEUED` Analysis can be claimed. See
`docs/retention-and-cleanup.md` for the applied wiring order and the
startup-only trigger decision.

As of M7-04, `create_app()` also calls `observability.configure_logging`
exactly once, before constructing `FastAPI`, so every diagnostic event the
new `observability` package's other authorized call sites emit already
has a configured destination and level before this process serves its
first request. See `docs/observability.md` for the emitted event schema.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Collection
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
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
from audio_cue_locator.application.create_analysis import (
    DEFAULT_MAX_CUE_COUNT,
    DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS,
    DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS,
    CreateAnalysisUseCase,
)
from audio_cue_locator.application.query_analysis import QueryAnalysisUseCase
from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.infrastructure.asset_storage.retention_policy import (
    cleanup_expired_assets,
    cleanup_orphaned_assets,
)
from audio_cue_locator.infrastructure.execution.local_analysis_executor import (
    DEFAULT_MAX_CONCURRENCY,
    InMemoryResultReferenceStore,
    LocalAnalysisExecutor,
)
from audio_cue_locator.infrastructure.execution.restart_recovery import (
    run_startup_recovery,
)
from audio_cue_locator.infrastructure.media_processing.ffmpeg_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    FFmpegMediaAdapter,
)
from audio_cue_locator.interfaces.rest_api.analysis_routes import build_analysis_router
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
from audio_cue_locator.observability import configure_logging, emit_diagnostic_event

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
upload routes, and `"accepted"` (202), added by M5-04, is bound to
`interfaces.rest_api.analysis_routes`'s Analysis creation route (both use
`fastapi.status.HTTP_2xx_*` directly rather than importing from here, to
avoid a circular import -- see each module's own docstring). The remaining
entries -- including `"payload_too_large"` and `"unsupported_media_type"`,
added by M5-02 -- are bound to the centralized handlers `errors.
install_error_handlers` registers below, so the status vocabulary stays
coherent across the whole namespace instead of each endpoint issue picking
its own names for the same HTTP status. M7-02 deliberately does not add a
new entry here: see `docs/supported-media-and-limits.md` for why an
FFmpeg timeout stays mapped to the existing `"unsupported_media_type"`
entry instead of a new status."""

_ASSET_STORAGE_ROOT_ENV = "AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT"
_DEFAULT_ASSET_STORAGE_ROOT = Path("var") / "asset_storage"
_MAX_SOURCE_MEDIA_UPLOAD_BYTES_ENV = "AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES"
_MAX_CUE_UPLOAD_BYTES_ENV = "AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES"
_ANALYSIS_DB_PATH_ENV = "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH"
_DEFAULT_ANALYSIS_DB_PATH = Path("var") / "analysis_repository.sqlite3"
_MAX_CUE_COUNT_ENV = "AUDIO_CUE_LOCATOR_MAX_CUE_COUNT"
_FFMPEG_TIMEOUT_SECONDS_ENV = "AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS"
_MAX_CONCURRENCY_ENV = "AUDIO_CUE_LOCATOR_MAX_CONCURRENCY"
_MAX_SOURCE_MEDIA_DURATION_SECONDS_ENV = (
    "AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_DURATION_SECONDS"
)
_MAX_CUE_MEDIA_DURATION_SECONDS_ENV = "AUDIO_CUE_LOCATOR_MAX_CUE_MEDIA_DURATION_SECONDS"
_LOG_LEVEL_ENV = "AUDIO_CUE_LOCATOR_LOG_LEVEL"
_DEFAULT_LOG_LEVEL = "INFO"
_ASSET_CLEANUP_INTERVAL_SECONDS_ENV = "AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS"
_DEFAULT_ASSET_CLEANUP_INTERVAL_SECONDS = 86400.0
"""S0011: default periodic Asset-maintenance interval (24 hours)."""
_MINIMUM_ASSET_CLEANUP_INTERVAL_SECONDS = 3600.0
"""S0011: shortest permitted periodic Asset-maintenance interval (one hour).
Never exposed as a smaller production-configurable value; a test seam that
needs a shorter wait constructs `_PeriodicAssetMaintenance` directly instead
of going through this environment-variable convention."""


def _log_level() -> str:
    """Explicit, environment-overridable diagnostic-logging level (M7-04),
    layered over `_DEFAULT_LOG_LEVEL`, following the same per-file
    configuration convention already used by `_ffmpeg_timeout_seconds`/
    `_max_concurrency` above."""

    configured = os.environ.get(_LOG_LEVEL_ENV)
    return configured if configured else _DEFAULT_LOG_LEVEL


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


def _build_asset_storage() -> LocalFilesystemAssetStorage:
    """Construct this process's one concrete `AssetStoragePort`
    implementation, shared by both the Asset ingestion (M5-03) and Analysis
    creation (M5-04) composition-root responsibilities below; both consume
    it as the `AssetStoragePort` Protocol, never as this concrete type."""

    return LocalFilesystemAssetStorage(_asset_storage_root())


def _build_asset_ingestion_use_case(
    storage: LocalFilesystemAssetStorage,
) -> AssetIngestionUseCase:
    """Construct this process's one `AssetIngestionUseCase`, injected with
    the shared concrete `LocalFilesystemAssetStorage` -- the only place in
    this project's `src/` tree a concrete Asset Storage adapter is
    constructed for production use (see module docstring)."""

    return AssetIngestionUseCase(storage, _upload_limits())


class _ThreadLocalAnalysisRepository:
    """Structurally satisfies `AnalysisRepositoryPort` (`create`, `get`,
    `add_owned_asset`, `transition`, `list_by_state`) by lazily
    constructing one `SQLiteAnalysisRepository` per calling thread, all
    bound to the same `database_path`, and delegating every call to that
    thread's own instance (see module docstring for why this is
    necessary). Embeds no SQL of its own."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = database_path
        self._local = threading.local()

    def _repository_for_current_thread(self) -> SQLiteAnalysisRepository:
        repository = getattr(self._local, "repository", None)
        if repository is None:
            repository = SQLiteAnalysisRepository(self._database_path)
            self._local.repository = repository
        return repository

    def create(self, **kwargs: Any) -> Any:
        return self._repository_for_current_thread().create(**kwargs)

    def get(self, analysis_id: str) -> Any:
        return self._repository_for_current_thread().get(analysis_id)

    def add_owned_asset(self, analysis_id: str, asset_id: str) -> Any:
        return self._repository_for_current_thread().add_owned_asset(analysis_id, asset_id)

    def transition(
        self,
        analysis_id: str,
        to_state: Any,
        *,
        at: datetime,
        result_reference: str | None = None,
        structured_error: Any = None,
    ) -> Any:
        return self._repository_for_current_thread().transition(
            analysis_id,
            to_state,
            at=at,
            result_reference=result_reference,
            structured_error=structured_error,
        )

    def list_by_state(self, state: Any) -> Any:
        return self._repository_for_current_thread().list_by_state(state)


def _analysis_db_path() -> Path:
    """Explicit, non-secret, environment-overridable SQLite database path
    for the M4-03 Analysis Repository (formal issue restricao:
    configuration must be explicit). Defaults to
    `var/analysis_repository.sqlite3`, relative to the current working
    directory, mirroring `_asset_storage_root`'s own convention."""

    configured = os.environ.get(_ANALYSIS_DB_PATH_ENV)
    return Path(configured) if configured else _DEFAULT_ANALYSIS_DB_PATH


def _max_cue_count() -> int:
    """Explicit, environment-overridable maximum cue count per Analysis
    creation request, layered over `create_analysis.DEFAULT_MAX_CUE_COUNT`
    (gap G3, `intents/M5/M5-04/implementation-handoff.json`)."""

    configured = os.environ.get(_MAX_CUE_COUNT_ENV)
    return int(configured) if configured else DEFAULT_MAX_CUE_COUNT


def _ffmpeg_timeout_seconds() -> float:
    """Explicit, environment-overridable FFmpeg/ffprobe subprocess timeout,
    layered over `ffmpeg_adapter.DEFAULT_TIMEOUT_SECONDS` (M7-02 gap G1:
    this value previously had no configuration surface even though
    `FFmpegMediaAdapter.__init__` already accepted it)."""

    configured = os.environ.get(_FFMPEG_TIMEOUT_SECONDS_ENV)
    return float(configured) if configured else DEFAULT_TIMEOUT_SECONDS


def _max_concurrency() -> int:
    """Explicit, environment-overridable executor concurrency bound,
    layered over `local_analysis_executor.DEFAULT_MAX_CONCURRENCY` (M7-02
    gap G1: this value previously had no configuration surface even though
    `LocalAnalysisExecutor.__init__` already accepted it)."""

    configured = os.environ.get(_MAX_CONCURRENCY_ENV)
    return int(configured) if configured else DEFAULT_MAX_CONCURRENCY


def _max_source_media_duration_seconds() -> float:
    """Explicit, environment-overridable maximum probed duration for a
    source-media Asset, layered over `create_analysis.
    DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS` (M7-02 gap G2: no media-
    duration limit existed anywhere in the source tree before this
    issue)."""

    configured = os.environ.get(_MAX_SOURCE_MEDIA_DURATION_SECONDS_ENV)
    return float(configured) if configured else DEFAULT_MAX_SOURCE_MEDIA_DURATION_SECONDS


def _max_cue_media_duration_seconds() -> float:
    """Explicit, environment-overridable maximum probed duration for a cue
    Asset, layered over `create_analysis.
    DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS` (M7-02 gap G2), kept
    independently configurable from the source-media duration limit since a
    cue is a short reference snippet, not the long recording being
    searched."""

    configured = os.environ.get(_MAX_CUE_MEDIA_DURATION_SECONDS_ENV)
    return float(configured) if configured else DEFAULT_MAX_CUE_MEDIA_DURATION_SECONDS


def _asset_cleanup_interval_seconds() -> float:
    """Explicit, environment-overridable periodic Asset-maintenance interval
    (S0011), layered over `_DEFAULT_ASSET_CLEANUP_INTERVAL_SECONDS` following
    the same per-file configuration convention as every other `_*_ENV`
    helper above. Unlike those helpers, an invalid value here must fail
    application construction explicitly rather than silently falling back
    (S0011 acceptance criterion "Invalid interval fails explicitly"): a
    non-numeric, non-finite, or sub-one-hour value raises `ValueError`
    instead of being coerced or ignored."""

    configured = os.environ.get(_ASSET_CLEANUP_INTERVAL_SECONDS_ENV)
    if not configured:
        return _DEFAULT_ASSET_CLEANUP_INTERVAL_SECONDS
    try:
        value = float(configured)
    except ValueError as exc:
        raise ValueError(
            f"{_ASSET_CLEANUP_INTERVAL_SECONDS_ENV} must be a numeric value "
            "in seconds"
        ) from exc
    if not math.isfinite(value):
        raise ValueError(
            f"{_ASSET_CLEANUP_INTERVAL_SECONDS_ENV} must be a finite value"
        )
    if value < _MINIMUM_ASSET_CLEANUP_INTERVAL_SECONDS:
        raise ValueError(
            f"{_ASSET_CLEANUP_INTERVAL_SECONDS_ENV} must be at least "
            f"{_MINIMUM_ASSET_CLEANUP_INTERVAL_SECONDS:.0f} seconds (one hour)"
        )
    return value


def _emit_storage_usage_observed(storage: LocalFilesystemAssetStorage) -> None:
    """Emit exactly one `asset_storage_usage_observed` event (S0011):
    aggregate managed-Asset count and total bytes only, from
    `AssetStoragePort.list_entries()` -- never a per-Asset identifier, path,
    or size. Called once after startup maintenance and once after every
    successful periodic maintenance cycle."""

    entries = storage.list_entries()
    emit_diagnostic_event(
        event="asset_storage_usage_observed",
        boundary="cleanup",
        outcome="succeeded",
        count=len(entries),
        size_bytes=sum(entry.size_bytes for entry in entries),
    )


class _AssetMaintenanceCoordinator:
    """S0011: the one process-local coordination boundary between an
    in-flight `CreateAnalysisUseCase.create(...)` request and periodic Asset
    maintenance. Owns a single `threading.Lock` and an Asset-id reservation
    reference-count map; never persisted (`docs/artifact-retention-and-
    cleanup-policy.md`: reservation is not durable ownership).

    `reserve(...)` only briefly holds the lock to update counts, so
    concurrent Analysis-creation requests canonicalize independently.
    `maintenance_snapshot(...)` holds the lock for its entire `with` body,
    so a new reservation registration blocks until an in-progress
    maintenance cycle finishes, and a maintenance cycle always sees every
    Asset id already reserved by that point."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reservation_counts: dict[str, int] = {}

    @contextmanager
    def reserve(self, asset_ids: Collection[str]):
        ids = tuple(dict.fromkeys(asset_ids))
        with self._lock:
            for asset_id in ids:
                self._reservation_counts[asset_id] = (
                    self._reservation_counts.get(asset_id, 0) + 1
                )
        try:
            yield
        finally:
            with self._lock:
                for asset_id in ids:
                    remaining = self._reservation_counts.get(asset_id, 0) - 1
                    if remaining <= 0:
                        self._reservation_counts.pop(asset_id, None)
                    else:
                        self._reservation_counts[asset_id] = remaining

    @contextmanager
    def maintenance_snapshot(self):
        with self._lock:
            yield frozenset(self._reservation_counts)


class _PeriodicAssetMaintenance:
    """S0011: a private, standard-library-only (`threading.Thread` +
    `threading.Event`) periodic worker that repeats the same owned-then-
    orphan cleanup order startup already applies, followed by one storage-
    usage observation, every `interval_seconds`. Waits a full interval
    before its first cycle -- the composition root already performs startup
    maintenance once, so an immediate duplicate cycle is never run."""

    def __init__(
        self,
        *,
        repository: Any,
        storage: LocalFilesystemAssetStorage,
        coordinator: _AssetMaintenanceCoordinator,
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._repository = repository
        self._storage = storage
        self._coordinator = coordinator
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="asset-maintenance", daemon=False
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join()
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self._run_one_cycle()

    def _run_one_cycle(self) -> None:
        try:
            with self._coordinator.maintenance_snapshot() as protected_ids:
                cleanup_expired_assets(
                    self._repository,
                    self._storage,
                    protected_asset_ids=protected_ids,
                )
                cleanup_orphaned_assets(
                    self._repository,
                    self._storage,
                    protected_asset_ids=protected_ids,
                )
                _emit_storage_usage_observed(self._storage)
        except Exception as exc:
            # S0011: a maintenance exception must never terminate the API
            # process or the worker thread -- emit one sanitized diagnostic
            # (category only, never the raw exception text) and return to
            # the wait loop so a later interval may retry.
            emit_diagnostic_event(
                event="asset_maintenance_cycle_failed",
                boundary="cleanup",
                outcome="failed",
                category=type(exc).__name__,
            )


def _build_create_analysis_use_case(
    storage: LocalFilesystemAssetStorage,
) -> CreateAnalysisUseCase:
    """Construct this process's one `CreateAnalysisUseCase`, injected with a
    thread-safe `_ThreadLocalAnalysisRepository` wrapping
    `SQLiteAnalysisRepository`, a `LocalAnalysisExecutor` bound to that same
    repository, and the shared concrete Asset Storage adapter -- this
    project's second composition-root responsibility (see module
    docstring)."""

    create_use_case, _ = _build_analysis_use_cases(storage)
    return create_use_case


def _build_analysis_use_cases(
    storage: LocalFilesystemAssetStorage,
) -> tuple[CreateAnalysisUseCase, QueryAnalysisUseCase]:
    """Construct Analysis write/read use cases with one shared Result store.

    The repository remains the sole lifecycle authority.  The shared
    ``InMemoryResultReferenceStore`` closes the local-process accessibility
    gap: the executor writes a canonical Result body and the query use case
    reads that same body through its Application-owned reader Protocol.

    Before the executor is constructed, this composition root runs the
    M4-05/M4-06/S0012 startup lifecycle exactly once: ``run_startup_recovery``
    resolves any Analysis left ``RUNNING`` by a prior crash to ``FAILED``,
    strictly before any ``QUEUED`` Analysis can be claimed (M7-03,
    preserving the exactly-once-before-any-claim precondition documented in
    `docs/restart-and-recovery-policy.md`); ``cleanup_expired_assets`` then
    deletes uniquely-owned, terminal, retention-eligible Asset bytes using
    its existing default seven-day minimum window; ``cleanup_orphaned_assets``
    (S0012) then deletes physical Assets that remain unreferenced by every
    persisted Analysis after the S0012 24-hour grace window, using the
    physical inventory and every persisted reference left standing once
    owned-retention cleanup has already run. Startup maintenance always runs
    exactly once per process start, never periodically (`docs/retention-
    and-cleanup.md`); S0011 additionally starts a periodic worker that
    repeats the same two cleanup calls on a configured interval, but only
    once the FastAPI application reaches ASGI startup -- never merely
    because this function, or `create_app()`, was called (see
    `_PeriodicAssetMaintenance`, constructed below and started/stopped by
    `create_app()`'s lifespan hook).

    S0011's interval is validated first, before any recovery/cleanup I/O
    runs, so an invalid `AUDIO_CUE_LOCATOR_ASSET_CLEANUP_INTERVAL_SECONDS`
    fails application construction explicitly rather than silently falling
    back or running partial startup work.
    """

    interval_seconds = _asset_cleanup_interval_seconds()

    db_path = _analysis_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    repository = _ThreadLocalAnalysisRepository(db_path)
    run_startup_recovery(repository)
    cleanup_expired_assets(repository, storage)
    cleanup_orphaned_assets(repository, storage)
    _emit_storage_usage_observed(storage)
    result_store = InMemoryResultReferenceStore()
    executor = LocalAnalysisExecutor(
        repository, result_store=result_store, max_concurrency=_max_concurrency()
    )
    media_adapter = FFmpegMediaAdapter(timeout_seconds=_ffmpeg_timeout_seconds())
    coordinator = _AssetMaintenanceCoordinator()
    create_use_case = CreateAnalysisUseCase(
        repository=repository,
        asset_storage=storage,
        executor=executor,
        max_cue_count=_max_cue_count(),
        media_adapter=media_adapter,
        max_source_media_duration_seconds=_max_source_media_duration_seconds(),
        max_cue_media_duration_seconds=_max_cue_media_duration_seconds(),
        reserve_asset_ids=coordinator.reserve,
    )
    # S0013: the audition renderer reuses this same `media_adapter` instance
    # -- never a second Asset Storage root or an independently configured
    # FFmpeg timeout policy -- so audition rendering inherits the exact
    # `AUDIO_CUE_LOCATOR_FFMPEG_TIMEOUT_SECONDS` already applied to Analysis
    # media processing above. S0013 adds no environment configuration
    # surface, so the audition duration/response-byte guardrails are left at
    # `QueryAnalysisUseCase`'s own fixed defaults.
    query_use_case = QueryAnalysisUseCase(
        repository,
        result_store,
        asset_storage=storage,
        audition_renderer=media_adapter,
    )

    # S0011: constructed from the same repository/storage/coordinator this
    # function just built, but not started here -- `_build_analysis_use_cases`
    # is also called directly by several out-of-scope callers/tests that
    # must keep observing its existing 2-tuple return shape, so the runner
    # is handed to `create_app()` by attaching it to `create_use_case`
    # rather than widening this function's return type.
    create_use_case._periodic_maintenance = _PeriodicAssetMaintenance(
        repository=repository,
        storage=storage,
        coordinator=coordinator,
        interval_seconds=interval_seconds,
    )

    return create_use_case, query_use_case


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

    S0011: `create_app()` alone never starts the periodic Asset-maintenance
    worker. It only builds `_PeriodicAssetMaintenance` (inside
    `_build_analysis_use_cases`, below) and registers `_lifespan` as this
    `FastAPI` instance's own lifespan handler; the worker thread starts only
    when a real ASGI server enters that lifespan (`runner.start()`) and
    stops deterministically, via the same handler, when it exits
    (`runner.stop()`, which signals the stop `threading.Event` and joins the
    worker before returning). `periodic_runner` is a plain local variable
    `_lifespan` reads via closure; it is assigned further down, before this
    function returns and therefore strictly before any ASGI server could
    ever invoke `_lifespan`.
    """

    configure_logging(level=_log_level())
    periodic_runner: _PeriodicAssetMaintenance | None = None

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        assert periodic_runner is not None
        periodic_runner.start()
        try:
            yield
        finally:
            periodic_runner.stop()

    app = FastAPI(
        title="Audio Cue Locator API",
        version=API_VERSION,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    install_error_handlers(app)
    asset_storage = _build_asset_storage()
    app.include_router(
        build_asset_router(_build_asset_ingestion_use_case(asset_storage)),
        prefix=API_V1_PREFIX,
    )
    create_analysis_use_case, query_analysis_use_case = _build_analysis_use_cases(
        asset_storage
    )
    periodic_runner = create_analysis_use_case._periodic_maintenance
    app.include_router(
        build_analysis_router(create_analysis_use_case, query_analysis_use_case),
        prefix=API_V1_PREFIX,
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
