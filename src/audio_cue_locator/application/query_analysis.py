"""Application queries for persisted Analysis status and completed Results.

The REST interface consumes this module instead of reading SQLite or result
storage directly.  Persisted ``AnalysisRecord.state`` is authoritative for
every decision: querying never changes lifecycle state and never infers it
from the presence of a stored Result body.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisRecord,
    AnalysisRepositoryPort,
)
from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState


class AnalysisResultNotReadyError(RuntimeError):
    """Raised when a Result is requested while an Analysis is nonterminal."""

    def __init__(self, analysis_id: str, state: AnalysisLifecycleState) -> None:
        self.analysis_id = analysis_id
        self.state = state
        super().__init__(
            f"Analysis {analysis_id!r} has no available Result while {state.value}"
        )


class AnalysisFailedError(RuntimeError):
    """Raised when a FAILED Analysis is asked for a successful Result body."""

    def __init__(self, analysis_id: str) -> None:
        self.analysis_id = analysis_id
        super().__init__(f"Analysis {analysis_id!r} failed and has no Result")


class AnalysisResultIntegrityError(RuntimeError):
    """Raised for an invalid Result reference/body on a SUCCEEDED Analysis.

    This exception is deliberately not part of the public error vocabulary.
    The REST catch-all translates it to the sanitized ``internal_error``
    response without exposing the reference, serialized body, or decoder
    details.
    """


@runtime_checkable
class ResultReferenceReaderPort(Protocol):
    """Read a canonical serialized Result through an opaque reference."""

    def read(self, reference: str) -> str:
        """Return the serialized Result stored under ``reference``."""

        ...


class QueryAnalysisUseCase:
    """Read one persisted Analysis and its lifecycle-gated Result."""

    def __init__(
        self,
        repository: AnalysisRepositoryPort,
        result_reader: ResultReferenceReaderPort,
    ) -> None:
        self._repository = repository
        self._result_reader = result_reader

    def get_status(self, analysis_id: str) -> AnalysisRecord:
        """Return the current authoritative persisted record without mutation."""

        return self._repository.get(analysis_id)

    def get_result(self, analysis_id: str) -> dict:
        """Return a canonical Result object only for a valid SUCCEEDED record.

        Missing identifiers propagate ``AnalysisNotFoundError`` from the
        repository.  QUEUED/RUNNING and FAILED records have distinct named
        outcomes.  A SUCCEEDED record whose opaque reference or stored body
        violates the executor/repository contract is an internal integrity
        failure, never a pending or no-match response.
        """

        record = self.get_status(analysis_id)
        if record.state in (
            AnalysisLifecycleState.QUEUED,
            AnalysisLifecycleState.RUNNING,
        ):
            raise AnalysisResultNotReadyError(analysis_id, record.state)
        if record.state is AnalysisLifecycleState.FAILED:
            raise AnalysisFailedError(analysis_id)

        reference = record.result_reference
        if not reference:
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis has no Result reference"
            )

        try:
            serialized = self._result_reader.read(reference)
        except LookupError as exc:
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis references an unavailable Result"
            ) from exc

        try:
            result = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result is not valid JSON"
            ) from exc

        if not isinstance(result, dict):
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result must be a JSON object"
            )
        schema_version = result.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result has no valid schema version"
            )
        if result.get("analysis_id") != analysis_id:
            raise AnalysisResultIntegrityError(
                "Stored Analysis Result does not belong to the requested Analysis"
            )
        if result.get("final_state") != "completed":
            raise AnalysisResultIntegrityError(
                "SUCCEEDED Analysis references a non-completed Result"
            )
        return result
