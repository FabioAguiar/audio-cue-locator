"""M7-05 integrated end-to-end coverage over the real HTTP composition root.

This module deliberately does not duplicate coverage that already exists
elsewhere: `tests/test_api_v1_integration.py` already exercises the WAV
single-cue match and no-match HTTP paths, the shared error envelope, upload
size/cue-count boundaries, async 202-before-completion ordering, and a
controlled matching-failure scenario; `tests/test_multi_cue_analysis.py`
already exercises `run_multi_cue_analysis` directly at the array level;
`tests/operational/test_observability.py` already exercises the structured
diagnostic-event schema and leak remediation directly against
`LocalAnalysisExecutor`; `tests/operational/test_retention_cleanup.py`
already exercises `cleanup_expired_assets`'s composition-root wiring against
directly-constructed repository records. This module's own scope is the
delta `issues/M7/M7-05/formal-issue.json`'s acceptance criteria still
require demonstrating through the real HTTP composition root end-to-end,
which no existing test file covers: a real video-source decode-to-match
flow (M1's FFmpeg adapter, driven for the first time through the public API
rather than directly), a multi-cue Analysis submitted over HTTP, a
guardrail rejection observed at the HTTP boundary, an analysis_id-
correlated diagnostic event observed from an HTTP-driven Analysis, and a
retention/cleanup pass following an HTTP-completed Analysis.

Authored per `intents/M7/M7-05/implementation-handoff.json`'s own scope
decision. Not executed by that handoff or by the implementation phase that
created this file: `context-packs/M7/M7-05/implementation-context-pack.json`
does not authorize test execution. A later, explicitly authorized ASF route
must actually run this module (via `pytest tests/e2e/test_m7_baseline.py`)
and record the real outcome in `docs/baseline-validation.md`, per the
formal issue's own prohibition on treating unexecuted assertions as
sufficient end-to-end evidence.

Every helper below is a local, self-contained reimplementation of the
corresponding helper in `tests/test_api_v1_integration.py`, not an import
from it: no existing test file in this project cross-imports another test
module, and this file preserves that convention rather than introducing a
new one.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import logging
import shutil
import struct
import wave
from collections.abc import Coroutine, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from audio_cue_locator.infrastructure.analysis_repository.sqlite_repository import (
    SQLiteAnalysisRepository,
)
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
)
from audio_cue_locator.infrastructure.asset_storage.retention_policy import (
    cleanup_expired_assets,
)
from audio_cue_locator.observability.events import LOGGER_NAME

app_module = importlib.import_module("audio_cue_locator.interfaces.rest_api.app")
_REAL_BUILD_ANALYSIS_USE_CASES = app_module._build_analysis_use_cases

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures"

_FFMPEG_UNAVAILABLE = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
_FFMPEG_SKIP_REASON = "ffmpeg/ffprobe not available in this environment"


# --- shared helpers (local reimplementation; see module docstring) ---------


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


def _build_test_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, namespace: str
) -> tuple[Path, Any, list[Any]]:
    """Mirrors `tests/test_api_v1_integration.py::_build_test_app`, and
    additionally returns the isolated root directory so callers can reopen
    the same SQLite database and asset storage directly afterward (used by
    the retention/cleanup scenario below)."""

    root = tmp_path / namespace
    monkeypatch.setenv("AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT", str(root / "assets"))
    monkeypatch.setenv(
        "AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH", str(root / "analysis.sqlite3")
    )
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_SOURCE_MEDIA_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CUE_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("AUDIO_CUE_LOCATOR_MAX_CUE_COUNT", raising=False)

    executors: list[Any] = []

    def _capturing_builder(storage):
        create_use_case, query_use_case = _REAL_BUILD_ANALYSIS_USE_CASES(storage)
        executors.append(create_use_case._executor)
        return create_use_case, query_use_case

    monkeypatch.setattr(app_module, "_build_analysis_use_cases", _capturing_builder)
    return root, app_module.create_app(), executors


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
    content_type: str,
    expected_media_type: str,
) -> dict[str, Any]:
    response = await client.post(
        endpoint,
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["media_type"] == expected_media_type
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
    client: httpx.AsyncClient,
    location: str,
    *,
    attempts: int = 250,
    interval_seconds: float = 0.01,
) -> dict[str, Any]:
    """`attempts`/`interval_seconds` are widened for the real video scenario
    below, which involves an actual FFmpeg subprocess decode rather than the
    in-memory WAV path every other scenario in this repository's existing
    suite exercises."""

    last_payload = None
    for _ in range(attempts):
        response = await client.get(location)
        assert response.status_code == 200, response.text
        last_payload = response.json()
        if last_payload["status"] in {"succeeded", "failed"}:
            return last_payload
        await asyncio.sleep(interval_seconds)
    raise AssertionError(f"Analysis did not reach a terminal state: {last_payload}")


def _assert_error(response: httpx.Response, status_code: int, error_code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error_code", "message", "correlation_id"}
    assert payload["error_code"] == error_code
    assert payload["message"]
    assert payload["correlation_id"]


def _acl_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every structured payload `emit_diagnostic_event` attached to a log
    record, mirroring `tests/operational/test_observability.py::_acl_events`
    exactly (same attribute name, same shape)."""

    return [
        record.audio_cue_locator_event
        for record in caplog.records
        if hasattr(record, "audio_cue_locator_event")
    ]


# --- multi-cue: two cue_ids submitted over HTTP against one source ---------


def test_multi_cue_http_flow_resolves_each_submitted_cue_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """One already-matching cue asset (`tests/fixtures/matching/manifest.json`
    case `found_offset_near_start`) is submitted twice, as two distinct
    `cue_id`s, in a single Analysis -- the same two-cue-same-asset shape
    `tests/test_api_v1_integration.py::test_cue_count_accepts_the_configured_limit_and_rejects_the_next_value`
    already uses structurally (there, only to trigger a count rejection).
    `match_cue` is a pure function of `(source, cue, configuration)`,
    independent of `cue_id` labeling, so each of the two cue_ids is expected
    to resolve to its own accepted occurrence, exactly mirroring
    `tests/fixtures/multi_cue/manifest.json`'s own documented repeated-cue
    reasoning (case `repeated_cue_and_absent_cue`)."""

    case = _manifest_case("found_offset_near_start")
    _root, app, executors = _build_test_app(monkeypatch, tmp_path, "multi-cue")

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"], cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            location = created.headers["location"]

            terminal = await _poll_terminal(client, location)
            assert terminal["status"] == "succeeded"

            result_response = await client.get(f"{location}/result")
            assert result_response.status_code == 200, result_response.text
            envelope = result_response.json()
            cues = envelope["result"]["cues"]
            assert len(cues) == 2
            assert {cue_entry["cue_id"] for cue_entry in cues} == {"cue-1", "cue-2"}
            for cue_entry in cues:
                assert cue_entry["outcome"]["kind"] == "occurrences"
                assert cue_entry["outcome"]["occurrences"]

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


# --- video source: the real FFmpeg decode path through the public API ------


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_FFMPEG_SKIP_REASON)
def test_video_source_with_audio_completes_the_real_http_analysis_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Uploads `tests/fixtures/video_with_audio.mp4` (a synthetic MP4 with
    an AAC mono audio track) as the source over the real
    `/api/v1/assets/source-media` endpoint -- exercising M1's FFmpeg
    decode/canonicalization path through the public API for the first time
    in this project's history, rather than directly against
    `FFmpegMediaAdapter` (`tests/test_canonicalization_fixtures.py`,
    `tests/test_media_processing_ffmpeg_adapter.py`). This fixture carries
    no documented cue occurrence or ground-truth position
    (`tests/fixtures/multi_cue/manifest.json`'s own provenance note), so
    the match outcome is intentionally not asserted as either
    `occurrences` or `no_match`: only that the real decode-to-terminal-
    result path completes successfully end-to-end. Skipped, never mocked,
    when ffmpeg/ffprobe are unavailable, mirroring
    `tests/test_canonicalization_fixtures.py`'s own skip-if-unavailable
    convention -- see `intents/M7/M7-05/implementation-handoff.json` risk
    on ffmpeg/ffprobe availability."""

    case = _manifest_case("found_offset_near_start")
    _root, app, executors = _build_test_app(monkeypatch, tmp_path, "video")
    video_bytes = (FIXTURE_ROOT / "video_with_audio.mp4").read_bytes()

    async def _scenario():
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                video_bytes,
                filename="video_with_audio.mp4",
                content_type="video/mp4",
                expected_media_type="video/mp4",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            location = created.headers["location"]

            terminal = await _poll_terminal(
                client, location, attempts=600, interval_seconds=0.25
            )
            assert terminal["status"] == "succeeded"

            result_response = await client.get(f"{location}/result")
            assert result_response.status_code == 200, result_response.text
            envelope = result_response.json()
            outcome = envelope["result"]["cues"][0]["outcome"]
            assert outcome["kind"] in {"occurrences", "no_match"}

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


# --- guardrail: an unsupported upload is rejected at the HTTP boundary -----


def test_guardrail_rejects_unsupported_media_upload_with_the_shared_error_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """`tests/fixtures/not_media.txt` is not a supported container signature
    (`application.asset_ingestion.sniff_media_type` recognizes only RIFF/
    WAVE and ISO-BMFF `ftyp`), so upload is rejected with the shared 415
    envelope -- the M7-02 guardrail boundary observed end-to-end through
    this module's own real composition root, rather than re-asserting
    `tests/test_api_v1_integration.py`'s already-passing equivalent case."""

    _root, app, executors = _build_test_app(monkeypatch, tmp_path, "guardrail")

    async def _scenario():
        async with _client(app) as client:
            response = await client.post(
                "/api/v1/assets/source-media",
                files={
                    "file": (
                        "not_media.txt",
                        (FIXTURE_ROOT / "not_media.txt").read_bytes(),
                        "text/plain",
                    )
                },
            )
            _assert_error(response, 415, "unsupported_media")

    try:
        _run(_scenario())
    finally:
        _shutdown(executors)


# --- diagnostics: an HTTP-driven Analysis emits an analysis_id-correlated event


def test_successful_http_analysis_emits_an_analysis_id_correlated_diagnostic_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """`tests/operational/test_observability.py` already asserts this event
    directly against `LocalAnalysisExecutor`; this scenario confirms the
    same `analysis_execution_succeeded` event is observable end-to-end from
    an Analysis actually created over the real HTTP API, correlated by the
    server-issued `analysis_id`."""

    case = _manifest_case("found_offset_near_start")
    _root, app, executors = _build_test_app(monkeypatch, tmp_path, "diagnostics")

    async def _scenario() -> str:
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            analysis_id = created.json()["analysis_id"]
            terminal = await _poll_terminal(client, created.headers["location"])
            assert terminal["status"] == "succeeded"
            return analysis_id

    try:
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            analysis_id = _run(_scenario())
    finally:
        _shutdown(executors)

    events = _acl_events(caplog)
    succeeded_events = [
        event
        for event in events
        if event["event"] == "analysis_execution_succeeded"
        and event["analysis_id"] == analysis_id
    ]
    assert len(succeeded_events) == 1


# --- retention/cleanup: a pass following an HTTP-completed Analysis --------


def test_cleanup_removes_uniquely_owned_assets_after_an_http_completed_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Completes one Analysis over the real HTTP API, then reopens the same
    SQLite database and asset storage root `cleanup_expired_assets`
    (M4-06, wired at composition-root build time per M7-03) would itself
    read, mirroring `tests/operational/test_retention_cleanup.py`'s own
    direct-reconnection pattern -- but starting from HTTP-created state
    instead of directly-constructed repository records. `now` is pushed
    `MINIMUM_RETENTION_WINDOW` (seven days) plus one day past the terminal
    timestamp so the single, uniquely-owned source and cue Assets become
    eligible; a second pass confirms the cleanup is idempotent."""

    case = _manifest_case("found_offset_near_start")
    root, app, executors = _build_test_app(monkeypatch, tmp_path, "cleanup")

    async def _scenario() -> tuple[str, str]:
        async with _client(app) as client:
            source = await _upload(
                client,
                "/api/v1/assets/source-media",
                _wav_bytes(case["source"]),
                filename="source.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            cue = await _upload(
                client,
                "/api/v1/assets/cue",
                _wav_bytes(case["cue"]),
                filename="cue.wav",
                content_type="audio/wav",
                expected_media_type="audio/wav",
            )
            created = await _create_analysis(
                client, source["identifier"], [cue["identifier"]]
            )
            assert created.status_code == 202, created.text
            terminal = await _poll_terminal(client, created.headers["location"])
            assert terminal["status"] == "succeeded"
            return source["identifier"], cue["identifier"]

    try:
        source_asset_id, cue_asset_id = _run(_scenario())
    finally:
        _shutdown(executors)

    db_path = root / "analysis.sqlite3"
    storage = LocalFilesystemAssetStorage(root / "assets")
    far_future = datetime.now(timezone.utc) + timedelta(days=8)

    with SQLiteAnalysisRepository(db_path) as repository:
        first_pass = cleanup_expired_assets(repository, storage, now=far_future)
    assert set(first_pass.deleted_asset_ids) == {source_asset_id, cue_asset_id}
    assert first_pass.missing_asset_ids == ()

    with SQLiteAnalysisRepository(db_path) as repository:
        second_pass = cleanup_expired_assets(repository, storage, now=far_future)
    assert second_pass.deleted_asset_ids == ()
