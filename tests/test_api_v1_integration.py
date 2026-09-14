"""Complete in-process HTTP verification for the assembled REST API v1.

All mutable state is rooted below pytest's temporary directory.  The normal
success and no-match paths use the real application composition, SQLite
repository, local executor, Result store, canonicalization, and matcher.
Only the deliberately delayed and FAILED scenarios replace the matcher at
its existing test seam so their lifecycle ordering/outcome is deterministic.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import struct
import threading
import wave
from collections.abc import Coroutine, Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest

from audio_cue_locator.infrastructure.acoustic_matching import MatchOutcome, MatchResult


app_module = importlib.import_module("audio_cue_locator.interfaces.rest_api.app")
orchestration = importlib.import_module(
    "audio_cue_locator.application.multi_cue_orchestration"
)
_REAL_BUILD_ANALYSIS_USE_CASES = app_module._build_analysis_use_cases
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _manifest_case(case_id: str) -> dict[str, Any]:
    manifest = json.loads(
        (FIXTURE_ROOT / "matching" / "manifest.json").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["case_id"] == case_id)


def _wav_bytes(samples: Iterable[float], *, sample_rate: int = 48_000) -> bytes:
    values = tuple(float(value) for value in samples)
    peak = max((abs(value) for value in values), default=1.0) or 1.0
    pcm = [
        max(-32_768, min(32_767, round((value / peak) * 24_000)))
        for value in values
    ]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"".join(struct.pack("<h", value) for value in pcm))
    return buffer.getvalue()


def _multipart_body(
    content: bytes, *, filename: str = "fixture.wav", boundary: str = "m5boundary"
) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")


def _build_test_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    namespace: str,
    *,
    source_upload_limit: int | None = None,
    cue_upload_limit: int | None = None,
    max_cue_count: int | None = None,
):
    root = tmp_path / namespace
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(root / "assets")
    )
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(root / "analysis.sqlite3")
    )
    if source_upload_limit is None:
        monkeypatch.delenv(
            "AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES", raising=False
        )
    else:
        monkeypatch.setenv(
            "AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES",
            str(source_upload_limit),
        )
    if cue_upload_limit is None:
        monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES", raising=False)
    else:
        monkeypatch.setenv(
            "AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES", str(cue_upload_limit)
        )
    if max_cue_count is None:
        monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CUE_COUNT", raising=False)
    else:
        monkeypatch.setenv("AUDIO_CUE_LOCATOR_MAX_CUE_COUNT", str(max_cue_count))

    executors = []

    def _capturing_builder(storage):
        create_use_case, query_use_case = _REAL_BUILD_ANALYSIS_USE_CASES(storage)
        executors.append(create_use_case._executor)
        return create_use_case, query_use_case

    monkeypatch.setattr(app_module, "_build_analysis_use_cases", _capturing_builder)
    return app_module.create_app(), executors


def _shutdown(executors: list[Any]) -> None:
    for executor in executors:
        executor.shutdown()


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )


async def _upload(
    client: httpx.AsyncClient,
    endpoint: str,
    content: bytes,
    *,
    filename: str,
) -> dict[str, Any]:
    response = await client.post(
        endpoint,
        files={"file": (filename, content, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["media_type"] == "audio/wav"
    assert payload["checksum_algorithm"] == "sha256"
    return payload


async def _create_analysis(
    client: httpx.AsyncClient,
    source_asset_id: str,
    cue_asset_ids: list[str],
) -> httpx.Response:
    return await client.post(
        "/api/v1/analyses",
        json={
            "source_asset_id": source_asset_id,
            "cues": [
                {"cue_id": f"cue-{index}", "asset_id": asset_id}
                for index, asset_id in enumerate(cue_asset_ids, start=1)
            ],
        },
    )


async def _poll_terminal(
    client: httpx.AsyncClient, location: str, *, attempts: int = 250
) -> dict[str, Any]:
    last_payload = None
    for _ in range(attempts):
        response = await client.get(location)
        assert response.status_code == 200, response.text
        last_payload = response.json()
        if last_payload["status"] in {"succeeded", "failed"}:
            return last_payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"Analysis did not reach a terminal state: {last_payload}")


def _assert_error(response: httpx.Response, status_code: int, error_code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error_code", "message", "correlation_id"}
    assert payload["error_code"] == error_code
    assert payload["message"]
    assert payload["correlation_id"]


@pytest.mark.parametrize(
    ("case_id", "expected_kind"),
    [
        ("found_offset_near_start", "occurrences"),
        ("no_match_absent_cue", "no_match"),
    ],
)
def test_complete_upload_to_versioned_result_http_flow(
    case_id: str,
    expected_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    case = _manifest_case(case_id)
    app, executors = _build_test_app(monkeypatch, tmp_path, case_id)

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename=f"{case_id}-source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename=f"{case_id}-cue.wav",
            )

            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            created_payload = created.json()
            assert created_payload["status"] == "queued"
            location = created.headers["location"]
            assert location == (
                f"/api/v1/analyses/{created_payload['analysis_id']}"
            )

            terminal = await _poll_terminal(client, location)
            assert terminal["status"] == "succeeded"

            result_response = await client.get(f"{location}/result")
            assert result_response.status_code == 200, result_response.text
            envelope = result_response.json()
            assert envelope["api_version"] == "v1"
            assert envelope["result_schema_version"] == envelope["result"][
                "schema_version"
            ]
            assert envelope["result_schema_version"] != envelope["api_version"]
            outcome = envelope["result"]["cues"][0]["outcome"]
            assert outcome["kind"] == expected_kind
            if expected_kind == "occurrences":
                assert outcome["occurrences"]
            else:
                assert outcome["occurrences"] is None

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


def test_invalid_and_unsupported_requests_use_the_shared_error_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    app, executors = _build_test_app(monkeypatch, tmp_path, "invalid-input")

    async def _scenario():
        async with _client(app) as client:
            invalid = await client.post(
                "/api/v1/analyses",
                json={
                    "source_asset_id": "00000000-0000-4000-8000-000000000001",
                    "cues": [],
                },
            )
            _assert_error(invalid, 422, "validation_error")

            missing = await client.post(
                "/api/v1/analyses",
                json={
                    "source_asset_id": "00000000-0000-4000-8000-000000000001",
                    "cues": [
                        {
                            "cue_id": "missing-cue",
                            "asset_id": "00000000-0000-4000-8000-000000000002",
                        }
                    ],
                },
            )
            _assert_error(missing, 404, "resource_not_found")

            unsupported = await client.post(
                "/api/v1/assets/source-media",
                files={
                    "file": (
                        "not_media.txt",
                        (FIXTURE_ROOT / "not_media.txt").read_bytes(),
                        "text/plain",
                    )
                },
            )
            _assert_error(unsupported, 415, "unsupported_media")

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


@pytest.mark.parametrize(
    ("endpoint", "limit_name"),
    [
        ("/api/v1/assets/source-media", "source"),
        ("/api/v1/assets/cue", "cue"),
    ],
)
def test_upload_limits_accept_the_exact_boundary_and_reject_one_byte_over(
    endpoint: str,
    limit_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    boundary = "m5boundary"
    body = _multipart_body(_wav_bytes([0.8, -0.6, 0.4, -0.2] * 10), boundary=boundary)
    limits = {
        "source_upload_limit": len(body) if limit_name == "source" else None,
        "cue_upload_limit": len(body) if limit_name == "cue" else None,
    }
    exact_app, exact_executors = _build_test_app(
        monkeypatch, tmp_path, f"{limit_name}-exact", **limits
    )
    limits = {
        "source_upload_limit": len(body) - 1 if limit_name == "source" else None,
        "cue_upload_limit": len(body) - 1 if limit_name == "cue" else None,
    }
    over_app, over_executors = _build_test_app(
        monkeypatch, tmp_path, f"{limit_name}-over", **limits
    )

    async def _scenario():
        headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
        async with _client(exact_app) as client:
            accepted = await client.post(endpoint, content=body, headers=headers)
            assert accepted.status_code == 201, accepted.text
        async with _client(over_app) as client:
            rejected = await client.post(endpoint, content=body, headers=headers)
            _assert_error(rejected, 413, "resource_limit_exceeded")

    try:
        _run(_scenario())
    finally:
        _shutdown(exact_executors + over_executors)


def test_cue_count_accepts_the_configured_limit_and_rejects_the_next_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    case = _manifest_case("found_offset_near_start")
    app, executors = _build_test_app(
        monkeypatch, tmp_path, "cue-count", max_cue_count=1
    )

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
            )
            accepted = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert accepted.status_code == 202, accepted.text
            await _poll_terminal(client, accepted.headers["location"])

            rejected = await _create_analysis(
                client,
                source["identifier"],
                [cue["identifier"], cue["identifier"]],
            )
            _assert_error(rejected, 413, "resource_limit_exceeded")

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


def test_http_202_returns_before_gate_controlled_processing_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    entered = threading.Event()
    release = threading.Event()
    real_match_cue = orchestration.match_cue

    def _gated_match(source, cue, configuration):
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test gate was not released")
        return real_match_cue(source, cue, configuration)

    monkeypatch.setattr(orchestration, "match_cue", _gated_match)
    case = _manifest_case("found_offset_near_start")
    app, executors = _build_test_app(monkeypatch, tmp_path, "delayed")

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )

            assert created.status_code == 202, created.text
            assert entered.wait(timeout=2)
            assert not release.is_set()
            location = created.headers["location"]
            running = await client.get(location)
            assert running.status_code == 200
            assert running.json()["status"] == "running"
            pending_result = await client.get(f"{location}/result")
            _assert_error(pending_result, 409, "result_not_ready")

            release.set()
            terminal = await _poll_terminal(client, location)
            assert terminal["status"] == "succeeded"

    try:
        _run(_scenario())
    finally:
        release.set()
        _shutdown(executors)


def test_controlled_matching_failure_is_persisted_and_exposed_safely_over_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    def _failed_match(source, cue, configuration):
        return MatchResult(
            outcome=MatchOutcome.PROCESSING_FAILURE,
            configuration=configuration,
            reason="controlled internal failure detail",
        )

    monkeypatch.setattr(orchestration, "match_cue", _failed_match)
    case = _manifest_case("found_offset_near_start")
    app, executors = _build_test_app(monkeypatch, tmp_path, "failed")

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            location = created.headers["location"]

            terminal = await _poll_terminal(client, location)
            assert terminal["status"] == "failed"
            assert terminal["structured_error"]["category"] == "matching_failure"
            assert terminal["result_reference"] is None

            result_response = await client.get(f"{location}/result")
            _assert_error(result_response, 409, "analysis_failed")
            assert "controlled internal failure detail" not in result_response.text

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


# --- S0003: Cue labels and optional cue-local trim bounds --------------------


def test_cue_local_trim_selects_interior_target_and_preserves_source_absolute_timeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A real end-to-end HTTP flow: the source WAV contains a known target
    at a known absolute sample offset; the Cue WAV carries leading and
    trailing material (values the target does not share) around an
    interior copy of that same target. The request's
    `trim_start_seconds`/`trim_end_seconds` select exactly the interior
    target segment. The Analysis must still succeed, the occurrence must
    remain on the source-media absolute timeline (unaffected by the Cue's
    own trim), and the create/status response must echo the requested
    optional Cue fields back."""

    case = _manifest_case("found_offset_near_start")
    target = case["cue"]  # [0.7, -0.5, 0.2, -0.8, 0.4, 0.1]
    filler = [0.9, -0.9, 0.9, -0.9]
    cue_samples = filler + target + filler
    trim_start_seconds = len(filler) / 48_000
    trim_end_seconds = (len(filler) + len(target)) / 48_000

    app, executors = _build_test_app(monkeypatch, tmp_path, "cue-trim")

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(cue_samples),
                filename="cue.wav",
            )

            created = await client.post(
                "/api/v1/analyses",
                json={
                    "source_asset_id": source["identifier"],
                    "cues": [
                        {
                            "cue_id": "cue-1",
                            "asset_id": cue["identifier"],
                            "label": "  Target hit  ",
                            "trim_start_seconds": trim_start_seconds,
                            "trim_end_seconds": trim_end_seconds,
                        }
                    ],
                },
            )
            assert created.status_code == 202, created.text
            created_payload = created.json()
            assert created_payload["cues"][0]["label"] == "Target hit"
            assert created_payload["cues"][0]["trim_start_seconds"] == pytest.approx(
                trim_start_seconds
            )
            assert created_payload["cues"][0]["trim_end_seconds"] == pytest.approx(
                trim_end_seconds
            )
            location = created.headers["location"]

            terminal = await _poll_terminal(client, location)
            assert terminal["status"] == "succeeded"
            assert terminal["cues"][0]["label"] == "Target hit"
            assert terminal["cues"][0]["trim_start_seconds"] == pytest.approx(
                trim_start_seconds
            )
            assert terminal["cues"][0]["trim_end_seconds"] == pytest.approx(
                trim_end_seconds
            )

            result_response = await client.get(f"{location}/result")
            assert result_response.status_code == 200, result_response.text
            outcome = result_response.json()["result"]["cues"][0]["outcome"]
            assert outcome["kind"] == "occurrences"
            occurrence = outcome["occurrences"][0]
            # cue_start_sample=12 at the fixture's 48 kHz canonical rate:
            # the occurrence stays on the source-media absolute timeline,
            # measured from source origin 0, exactly as before S0003 --
            # never shifted or reinterpreted by the Cue's own trim bounds.
            assert occurrence["temporal_position"] == pytest.approx(
                case["cue_start_sample"] / 48_000, abs=1e-4
            )

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


def test_duration_aware_invalid_trim_interval_returns_sanitized_error_and_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    case = _manifest_case("found_offset_near_start")
    app, executors = _build_test_app(monkeypatch, tmp_path, "cue-trim-invalid")

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
            )

            # The cue is `len(case["cue"]) / 48000` seconds long;
            # requesting a start at-or-after that duration is a
            # duration-aware semantic violation only Application can
            # detect (it requires decoding the Cue), not a request-shape
            # one Pydantic could catch.
            cue_duration_seconds = len(case["cue"]) / 48_000
            rejected = await client.post(
                "/api/v1/analyses",
                json={
                    "source_asset_id": source["identifier"],
                    "cues": [
                        {
                            "cue_id": "cue-1",
                            "asset_id": cue["identifier"],
                            "trim_start_seconds": cue_duration_seconds,
                        }
                    ],
                },
            )
            _assert_error(rejected, 400, "validation_error")

    try:
        _run(_scenario())
    finally:
        for executor in executors:
            assert executor._repository.list_by_state(
                importlib.import_module(
                    "audio_cue_locator.core.analysis_lifecycle"
                ).AnalysisLifecycleState.QUEUED
            ) == ()
        _shutdown(executors)
