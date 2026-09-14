"""Infrastructure/cross-cutting: local diagnostic observability (M7-04).

`docs/architecture.md` names "Observability/Configuration" as a distinct
responsibility ("diagnostico e parametros explicitos") that no module under
`src/audio_cue_locator/` owned before this issue (confirmed by this issue's
own analysis and handoff: no `import logging`, no `logging.getLogger`, and
no equivalent third-party dependency existed anywhere in this project).
This package is that owner.

It exposes exactly one capability: `events.emit_diagnostic_event`, a small,
fixed-shape structured-event helper built on Python's standard-library
`logging` module only (no new dependency, consistent with this project's
dependency-conservative posture confirmed elsewhere in this milestone). It
does not become a second source of truth for Analysis state or results
(`docs/architecture.md`, "Logs e metricas": "evidencia operacional e
diagnostico, nao fonte formal do resultado") and it does not integrate any
remote sink, corporate monitoring, or distributed tracing infrastructure
(formal issue "Nao inclui").

See `docs/observability.md` for the field schema, the allowlist/data-
minimization rule, and this package's documented limitations.
"""

from __future__ import annotations

from audio_cue_locator.observability.events import (
    VALID_BOUNDARIES,
    VALID_OUTCOMES,
    configure_logging,
    emit_diagnostic_event,
)

__all__ = [
    "VALID_BOUNDARIES",
    "VALID_OUTCOMES",
    "configure_logging",
    "emit_diagnostic_event",
]
