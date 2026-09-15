"""Observability: the minimum structured diagnostic-event schema (M7-04).

This module is the sole place a structured diagnostic event is built and
emitted anywhere in this project. It resolves this issue's own logging-
mechanism gap (`states/M7/M7-04/issue-operational-state.json#/gaps/0`, G1)
by using Python's standard-library `logging` module exclusively -- no new
dependency is declared in `pyproject.toml`.

Every event carries a fixed, small set of fields, enforced by
`emit_diagnostic_event`'s own keyword-only signature rather than an
open-ended mapping a caller could add arbitrary keys to:

- ``event``: a short, stable event name (for example
  ``"matching_stage_failed"``); never a formatted sentence, an exception
  message, or any other free-text content.
- ``boundary``: which of this issue's own named boundaries produced the
  event (see `VALID_BOUNDARIES`).
- ``outcome``: ``"succeeded"`` or ``"failed"`` (see `VALID_OUTCOMES`).
- ``category``: an optional short, stable diagnostic label (for example an
  exception class name, or an existing `ErrorCode`/`FailureCategory`
  value's own string). Never a raw exception message.
- ``analysis_id``: the persisted Analysis identifier, when one exists and
  is already known at the point of emission (`docs/architecture.md`,
  "Logs devem referenciar analysis_id").
- ``correlation_id``: the REST error envelope's own random per-response
  identifier (`interfaces.rest_api.errors._correlation_id`), when the event
  is emitted from a request-scoped failure path. `analysis_id` and
  `correlation_id` are the two, previously disconnected, correlation
  identifiers this issue's own state explicitly required reconciling
  (gap G4); a caller supplies either, both, or neither, and this module
  performs no inference between them.
- ``duration_ms``: an optional elapsed-time measurement in milliseconds,
  reused from an already-persisted timestamp pair (for example
  `application.ports.analysis_repository.LifecycleTimestamps.
  duration_seconds`) -- this module never starts its own timer and never
  introduces a second, independent timing mechanism.
- ``count``: an optional non-identifying magnitude (for example how many
  Assets one cleanup pass deleted) -- never a list of identifiers.
- ``size_bytes``: an optional non-negative integer magnitude (S0011; for
  example the total bytes of every currently managed Asset). Like ``count``,
  this is an aggregate: never a per-Asset size, and never paired with any
  Asset identifier or path.

No field here ever carries a filename, a submitted-file path, media bytes,
a raw payload, a secret, or a raw exception message: this fixed field set
is itself this issue's data-minimization allowlist
(`states/M7/M7-04/issue-operational-state.json#/constraints/2`; see
`docs/observability.md` for the full policy). A caller wanting to log
anything outside this set must extend this module explicitly, not smuggle
an extra field through `category` or any other field.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final

LOGGER_NAME: Final[str] = "audio_cue_locator.observability"
"""The one logger every emitted event is written through. A caller
configuring destination/level/format configures this logger (or its
ancestor `"audio_cue_locator"` logger), never a per-module logger of its
own, so every diagnostic event in this project stays discoverable in one
place."""

_logger = logging.getLogger(LOGGER_NAME)

VALID_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {
        "api",
        "application",
        "executor",
        "media_processing",
        "matching",
        "persistence",
        "cleanup",
        "result",
    }
)
"""This issue's own named cross-boundary coverage
(`issues/M7/M7-04/formal-issue.json`, section 4, "Cover API, application,
local executor, media processing, matching, persistence, cleanup, and
result boundaries"). Fixed and closed: a caller supplying any other value
raises `ValueError` rather than silently emitting an inconsistent label."""

VALID_OUTCOMES: Final[frozenset[str]] = frozenset({"succeeded", "failed"})
"""The only two terminal outcomes a diagnostic event may report. This is
independent of, and deliberately does not extend or duplicate, the six-
member `core.analysis_result.FailureCategory` enum or the eight-member
`interfaces.rest_api.schemas.ErrorCode` enum (both remain closed and
unchanged by this issue; see `docs/observability.md`)."""

_FAILED_LOG_LEVEL: Final[int] = logging.WARNING
_SUCCEEDED_LOG_LEVEL: Final[int] = logging.INFO


def configure_logging(*, level: int | str = logging.INFO) -> None:
    """Attach exactly one `StreamHandler` to the `"audio_cue_locator"`
    logger hierarchy, idempotently.

    Intended to be called once by `interfaces.rest_api.app.create_app`'s
    composition root, following the same per-file, environment-overridable
    configuration convention already used there (`_ffmpeg_timeout_seconds`,
    `_max_concurrency`). Safe to call more than once (`create_app` is
    already called repeatedly across this project's own test suite): a
    second call only adjusts the level, it never attaches a second
    duplicate handler.
    """

    root = logging.getLogger("audio_cue_locator")
    root.setLevel(level)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)


def emit_diagnostic_event(
    *,
    event: str,
    boundary: str,
    outcome: str,
    category: str | None = None,
    analysis_id: str | None = None,
    correlation_id: str | None = None,
    duration_ms: float | None = None,
    count: int | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit exactly one structured diagnostic event through `LOGGER_NAME`.

    Raises `ValueError` if `boundary` is not one of `VALID_BOUNDARIES`,
    `outcome` is not one of `VALID_OUTCOMES`, or `size_bytes` is not a
    non-negative integer (bools rejected; S0011), rather than silently
    logging an inconsistent event. The human-readable log message is
    `event` itself; every other field is attached to the emitted
    `LogRecord` as `record.audio_cue_locator_event` (a single structured
    payload, not individual free-standing attributes), so a caller with
    `caplog` or an equivalent capture mechanism can assert on the full
    structured payload without parsing a formatted string.
    """

    if boundary not in VALID_BOUNDARIES:
        raise ValueError(f"unknown observability boundary: {boundary!r}")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"unknown observability outcome: {outcome!r}")
    if size_bytes is not None:
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
            raise ValueError("size_bytes must be a non-negative integer or None")
        if size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")

    payload = {
        "event": event,
        "boundary": boundary,
        "outcome": outcome,
        "category": category,
        "analysis_id": analysis_id,
        "correlation_id": correlation_id,
        "duration_ms": duration_ms,
        "count": count,
        "size_bytes": size_bytes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    level = _SUCCEEDED_LOG_LEVEL if outcome == "succeeded" else _FAILED_LOG_LEVEL
    _logger.log(level, event, extra={"audio_cue_locator_event": payload})
