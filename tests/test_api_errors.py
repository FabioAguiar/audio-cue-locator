"""Tests for the centralized v1 external Error contract and failure-
translation policy (M5-02):
`src/audio_cue_locator/interfaces/rest_api/errors.py` and the
`ErrorPublic` envelope it returns via `schemas.py`.

These call each registered exception handler directly, rather than
through an ASGI request via FastAPI's `TestClient`, because `TestClient`
requires `httpx`, which is not among this project's declared dependencies
(`pyproject.toml` lists only `fastapi`, `pydantic`, `numpy`, `scipy`, and,
for tests, `pytest`); adding `httpx` is out of this issue's authorized
scope (`intents/M5/M5-02/implementation-handoff.json`, out_of_scope).
Every handler `install_error_handlers` registers ignores its `request`
argument (see `errors.py`), so calling it directly with `None` in that
position exercises exactly the same translation logic a real request
would trigger.
"""

from __future__ import annotations

import json

import pytest
from fastapi.exceptions import RequestValidationError

from audio_cue_locator.application.create_analysis import (
    InvalidCueRequestError,
    InvalidSimilarityScoreError,
)
from audio_cue_locator.application.multi_cue_orchestration import (
    AnalysisSourceInputError,
)
from audio_cue_locator.application.ports.analysis_repository import (
    AnalysisAlreadyExistsError,
    AnalysisNotFoundError,
    InvalidAnalysisRecordError,
)
from audio_cue_locator.core.analysis_lifecycle import (
    AnalysisLifecycleState,
    InvalidLifecycleTransitionError,
)
from audio_cue_locator.core.analysis_result import (
    AnalysisResult,
    CanonicalizationSnapshot,
    CueNoMatch,
    CueResult,
    EffectiveConfigurationSnapshot,
    FailureCategory,
    MatchingSnapshot,
    NormalizationSnapshot,
    StructuredError,
)
from audio_cue_locator.core.asset import (
    AssetNotFoundError,
    AssetStorageCollisionError,
    InvalidAssetIdentifierError,
    InvalidAssetMetadataError,
)
from audio_cue_locator.interfaces.rest_api.app import create_app
from audio_cue_locator.interfaces.rest_api.errors import (
    AnalysisResultUnavailableError,
    ResourceLimitExceededError,
    UnsupportedMediaError,
)
from audio_cue_locator.interfaces.rest_api.schemas import ErrorCode

# Substrings that must never appear in a translated Error response body,
# regardless of what the underlying exception's own message contained.
FORBIDDEN_SUBSTRINGS = (
    "Traceback",
    "/home/",
    "/workspace/",
    "/etc/",
    "site-packages",
    "line ",
    "File \"",
    "SELECT ",
    "sqlite3",
)

MAPPED_EXCEPTIONS = [
    (InvalidAssetIdentifierError("bad id"), ErrorCode.VALIDATION_ERROR, 400),
    (InvalidAssetMetadataError("bad metadata"), ErrorCode.VALIDATION_ERROR, 400),
    (InvalidAnalysisRecordError("bad record"), ErrorCode.VALIDATION_ERROR, 400),
    (AnalysisSourceInputError("bad source"), ErrorCode.VALIDATION_ERROR, 400),
    (
        InvalidCueRequestError(
            "cue label contains the secret value s3cr3t-cue-label"
        ),
        ErrorCode.VALIDATION_ERROR,
        400,
    ),
    (
        InvalidSimilarityScoreError("minimum score included s3cr3t-request-value"),
        ErrorCode.VALIDATION_ERROR,
        400,
    ),
    (UnsupportedMediaError("bad media"), ErrorCode.UNSUPPORTED_MEDIA, 415),
    (
        ResourceLimitExceededError("too many cues"),
        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
        413,
    ),
    (AssetNotFoundError("missing asset"), ErrorCode.RESOURCE_NOT_FOUND, 404),
    (AnalysisNotFoundError("missing analysis"), ErrorCode.RESOURCE_NOT_FOUND, 404),
    (
        InvalidLifecycleTransitionError(
            AnalysisLifecycleState.FAILED, AnalysisLifecycleState.RUNNING
        ),
        ErrorCode.LIFECYCLE_CONFLICT,
        409,
    ),
    (AssetStorageCollisionError("collision"), ErrorCode.LIFECYCLE_CONFLICT, 409),
    (AnalysisAlreadyExistsError("already exists"), ErrorCode.LIFECYCLE_CONFLICT, 409),
    (
        AnalysisResultUnavailableError("analysis failed"),
        ErrorCode.ANALYSIS_FAILED,
        409,
    ),
]


@pytest.fixture()
def app():
    return create_app()


def _handler_for(app, exc_type):
    return app.exception_handlers[exc_type]


def _decode_and_check_sanitized(response) -> dict:
    raw = response.body.decode("utf-8")
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in raw, f"forbidden substring {forbidden!r} leaked into {raw!r}"
    return json.loads(raw)


def test_install_error_handlers_covers_every_mapped_exception_and_the_fallback(app):
    expected_types = {type(exc) for exc, _, _ in MAPPED_EXCEPTIONS}
    expected_types |= {RequestValidationError, Exception}
    for exc_type in expected_types:
        assert exc_type in app.exception_handlers, f"no handler registered for {exc_type}"


@pytest.mark.parametrize(("exc", "expected_error_code", "expected_status"), MAPPED_EXCEPTIONS)
def test_mapped_exception_returns_the_shared_safe_envelope(
    app, exc, expected_error_code, expected_status
):
    handler = _handler_for(app, type(exc))
    response = handler(None, exc)

    assert response.status_code == expected_status
    body = _decode_and_check_sanitized(response)
    assert body["error_code"] == expected_error_code.value
    assert body["message"]
    assert str(exc) not in body["message"]
    assert body["correlation_id"]


def test_request_validation_error_returns_422_without_echoing_input(app):
    exc = RequestValidationError(
        errors=[
            {
                "loc": ("body", "secret_field"),
                "msg": "value is not a valid string",
                "type": "string_type",
                "input": "s3cr3t-request-value",
            }
        ]
    )
    handler = _handler_for(app, RequestValidationError)
    response = handler(None, exc)

    assert response.status_code == 422
    body = _decode_and_check_sanitized(response)
    assert body["error_code"] == ErrorCode.VALIDATION_ERROR.value
    raw = response.body.decode("utf-8")
    assert "s3cr3t-request-value" not in raw
    assert "secret_field" not in raw


def test_unexpected_exception_returns_500_without_leaking_type_or_message(app):
    class _BoomError(RuntimeError):
        """A deliberately unmapped exception type for this test only."""

    exc = _BoomError("/etc/passwd leaked path and a Traceback-looking detail")
    handler = _handler_for(app, Exception)
    response = handler(None, exc)

    assert response.status_code == 500
    body = _decode_and_check_sanitized(response)
    assert body["error_code"] == ErrorCode.INTERNAL_ERROR.value
    raw = response.body.decode("utf-8")
    assert "_BoomError" not in raw
    assert str(exc) not in raw
    assert body["correlation_id"]


def test_correlation_id_is_unique_per_response(app):
    handler = _handler_for(app, AssetNotFoundError)
    first = _decode_and_check_sanitized(handler(None, AssetNotFoundError("a")))
    second = _decode_and_check_sanitized(handler(None, AssetNotFoundError("b")))
    assert first["correlation_id"] != second["correlation_id"]


# --- FAILED-vs-no-match distinction (acceptance criterion 4) ---------------


def _configuration() -> EffectiveConfigurationSnapshot:
    return EffectiveConfigurationSnapshot(
        canonicalization=CanonicalizationSnapshot(
            sample_rate_hz=48000,
            channels=1,
            sample_format="float32",
            normalization=NormalizationSnapshot(
                enabled=True, method="peak", target_peak_amplitude=1.0
            ),
        ),
        matching=MatchingSnapshot(
            method="normalized_cross_correlation_v1", acceptance_threshold=0.7
        ),
        configuration_source_name="tests.test_api_errors",
    )


def test_no_match_result_is_a_plain_value_never_an_exception():
    """A legitimate no-match is Core data (`CueResult`/`CueNoMatch`), never
    an exception; it structurally cannot reach any handler
    `install_error_handlers` registers, because those handlers only ever
    translate raised exceptions, never successful Result values."""

    configuration = _configuration()
    no_match_result = AnalysisResult(
        analysis_id="analysis-no-match",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(CueResult(cue_id="cue-1", outcome=CueNoMatch()),),
    )

    assert no_match_result.final_state == "completed"
    assert not isinstance(no_match_result, BaseException)


def test_analysis_result_unavailable_error_is_distinct_from_the_failed_analysis_data(
    app,
):
    """A persisted FAILED `AnalysisResult` (carrying its own
    `StructuredError`) is Core/Application data describing what happened;
    requesting its absent Result body is a distinct REST-level conflict,
    raised as `AnalysisResultUnavailableError` and mapped to
    `analysis_failed`/409 above -- never the `FailureCategory` value
    itself, and never a 2xx response."""

    configuration = _configuration()
    failed_result = AnalysisResult(
        analysis_id="analysis-failed",
        method=configuration.matching.method,
        configuration=configuration,
        cues=(CueResult(cue_id="cue-1", outcome=CueNoMatch()),),
        structured_error=StructuredError(
            category=FailureCategory.INTERNAL_FAILURE,
            message="shared source could not be processed",
        ),
    )
    assert failed_result.final_state == "failed"

    handler = _handler_for(app, AnalysisResultUnavailableError)
    response = handler(None, AnalysisResultUnavailableError(failed_result.analysis_id))

    assert response.status_code == 409
    body = _decode_and_check_sanitized(response)
    assert body["error_code"] == ErrorCode.ANALYSIS_FAILED.value
    # The safe envelope never echoes the StructuredError's own message.
    assert failed_result.structured_error.message not in body["message"]
