"""Benchmark harness for the main analysis path, end to end (M7-05).

Measures wall-clock cost across the full path `docs/matching-benchmark.md`'s
own M2-06 harness explicitly excludes ("Only the `match_cue` call itself ...
is timed. Input generation, canonicalization/normalization, configuration
construction, hashing and reporting all happen outside the timed window."):
Asset ingestion (ingestion-policy validation and physical persistence),
media canonicalization (WAV decode/normalize via
`infrastructure.media_processing`), Analysis persistence (SQLite), executor
scheduling and matching (`infrastructure.acoustic_matching.baseline.
match_cue`, unchanged), and terminal-state/result retrieval. This is the
scope `issues/M7/M7-05/formal-issue.json` acceptance criterion 5 names "the
main analysis path"; `benchmarks/matching_baseline.py` (M2-06) is left
unmodified -- its own module docstring scopes it strictly to the bare
`match_cue` call, and this script does not broaden or duplicate that
contract.

This is developer benchmarking tooling under `benchmarks/`, not part of the
installed `audio_cue_locator` package (`pyproject.toml` restricts packaging
to `src/`) and not a test: it is never collected or run by pytest.

Boundaries this script deliberately respects
(`intents/M7/M7-05/implementation-handoff.json` scope):

- It drives the same Application-layer composition-root builders
  `interfaces.rest_api.app.create_app` itself uses
  (`_build_asset_storage`, `_build_asset_ingestion_use_case`,
  `_build_analysis_use_cases`), so the measured path is the real,
  unmodified production wiring -- not a hand-rolled shortcut -- while
  deliberately not adding an HTTP/ASGI dependency this project does not
  already declare as a non-test runtime dependency (`httpx` is currently
  declared only under `pyproject.toml`'s `[project.optional-dependencies]
  test` group, not as a base runtime dependency); HTTP request/response
  framing overhead is therefore outside this script's own timed window,
  consistent with "main analysis path" meaning the Core/Application/
  Infrastructure pipeline, not the REST transport layer atop it.
- It reuses the existing, curated `tests/fixtures/matching/manifest.json`
  case `found_offset_near_start` (a real, already-verified matching WAV
  pair) rather than inventing new scenario data, per this issue's own
  'Premissas' preferring existing fixtures.
- It does not modify `baseline.py`, `acceptance.py`, or any other
  `src/audio_cue_locator/` module, and it does not implement a second
  matching method.
- One untimed warm-up repetition precedes `MEASURED_REPETITIONS` sequential
  timed repetitions; a `--max-total-seconds` budget can abort an
  impractically long run after the repetition in progress completes,
  reported as explicitly incomplete, never silently truncated.
- Running this script to produce the actual timing numbers this issue's
  acceptance criteria require is a separate, authorized execution step,
  outside the scope of authoring this harness
  (`intents/M7/M7-05/implementation-handoff.json`): this file defines the
  scenario and measurement structure; the resulting numbers are recorded
  into `docs/benchmarks/m7-baseline.md`'s own Results section only after
  that authorized run.

Usage (from the repository root, with the project installed, e.g. via
`pip install -e .`; no `[test]` extra is required, unlike running the
pytest suite itself)::

    python benchmarks/main_path_baseline.py [--output PATH] \\
        [--max-total-seconds SECONDS] [--repetitions N]

Prints a JSON report to stdout; `--output` additionally writes it to a file.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import io
import json
import os
import platform
import statistics
import struct
import subprocess
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = Path(__file__).resolve()
_FIXTURE_CASE_ID = "found_offset_near_start"
_FIXTURE_MANIFEST_PATH = _REPO_ROOT / "tests" / "fixtures" / "matching" / "manifest.json"

WARMUP_REPETITIONS = 1
MEASURED_REPETITIONS = 5
"""One untimed warm-up plus five sequential measured repetitions, mirroring
`benchmarks/matching_baseline.py`'s own established convention."""

DEFAULT_MAX_TOTAL_SECONDS = 120.0
"""Default wall-clock budget for warm-up + all measured repetitions
combined. Unlike `benchmarks/matching_baseline.py`'s 60-second synthetic
correlation scenario, this scenario's `found_offset_near_start` fixture is
sub-second audio; the budget here bounds real I/O (SQLite writes, asset
persistence, executor scheduling) rather than CPU-bound correlation cost, so
a much smaller default is used. This is not a timing measurement or a
performance promise -- only an abort ceiling."""

DEFAULT_POLL_INTERVAL_SECONDS = 0.01
DEFAULT_POLL_TIMEOUT_SECONDS = 30.0

TIMER_NAME = "time.perf_counter_ns"


def _wav_bytes(samples: list[float], *, sample_rate: int = 48_000) -> bytes:
    """Encode a plain list of finite floats as 16-bit mono PCM WAV bytes,
    mirroring `tests/test_api_v1_integration.py::_wav_bytes` exactly (this
    script does not import from `tests/`, matching
    `benchmarks/matching_baseline.py`'s own precedent of not depending on
    the test suite)."""

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


def _load_fixture_case() -> dict[str, Any]:
    manifest = json.loads(_FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    return next(
        case for case in manifest["cases"] if case["case_id"] == _FIXTURE_CASE_ID
    )


@dataclasses.dataclass(frozen=True)
class RepetitionMeasurement:
    repetition_index: int
    elapsed_ns: int
    analysis_id: str
    terminal_state: str
    result_outcome_kind: str | None


@dataclasses.dataclass(frozen=True)
class TimingReport:
    status: str  # "complete" or "incomplete"
    warmup_elapsed_ns: int | None
    measured: list[RepetitionMeasurement]
    incomplete_reason: str | None


def _build_use_cases(root: Path):
    """Construct the real composition root's Asset ingestion and Analysis
    use cases against an isolated `root`, exactly as
    `interfaces.rest_api.app.create_app` builds them (see module
    docstring). Imported lazily so `--help` does not require the project to
    already be importable."""

    os.environ["AUDIO_CUE_LOCATOR_ASSET_STORAGE_ROOT"] = str(root / "assets")
    os.environ["AUDIO_CUE_LOCATOR_ANALYSIS_DB_PATH"] = str(root / "analysis.sqlite3")

    app_module = importlib.import_module("audio_cue_locator.interfaces.rest_api.app")
    storage = app_module._build_asset_storage()
    ingestion_use_case = app_module._build_asset_ingestion_use_case(storage)
    create_use_case, query_use_case = app_module._build_analysis_use_cases(storage)
    return ingestion_use_case, create_use_case, query_use_case


def _run_one_repetition(
    *,
    ingestion_use_case,
    create_use_case,
    query_use_case,
    source_bytes: bytes,
    cue_bytes: bytes,
    repetition_index: int,
) -> RepetitionMeasurement:
    """Time exactly one upload-through-result cycle: asset ingestion for a
    fresh source and cue, Analysis creation, polling until a terminal
    state, and (if succeeded) result retrieval. Each repetition uses freshly
    ingested Assets, mirroring a real client submitting a new request each
    time rather than reusing prior state."""

    from audio_cue_locator.application.create_analysis import CueRequest
    from audio_cue_locator.core.analysis_lifecycle import AnalysisLifecycleState
    from audio_cue_locator.core.asset import AssetType

    start = time.perf_counter_ns()

    source_asset = ingestion_use_case.ingest(
        content=source_bytes,
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name=f"main-path-benchmark-source-{repetition_index}.wav",
    )
    cue_asset = ingestion_use_case.ingest(
        content=cue_bytes,
        logical_type=AssetType.CUE,
        informative_name=f"main-path-benchmark-cue-{repetition_index}.wav",
    )
    record = create_use_case.create(
        source_asset_id=source_asset.identifier,
        cues=[CueRequest(cue_id="cue-1", asset_id=cue_asset.identifier)],
    )

    deadline = time.monotonic() + DEFAULT_POLL_TIMEOUT_SECONDS
    status = query_use_case.get_status(record.analysis_id)
    while status.state not in (
        AnalysisLifecycleState.SUCCEEDED,
        AnalysisLifecycleState.FAILED,
    ):
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"analysis {record.analysis_id!r} did not reach a terminal "
                f"state within {DEFAULT_POLL_TIMEOUT_SECONDS}s"
            )
        time.sleep(DEFAULT_POLL_INTERVAL_SECONDS)
        status = query_use_case.get_status(record.analysis_id)

    result_outcome_kind: str | None = None
    if status.state is AnalysisLifecycleState.SUCCEEDED:
        result = query_use_case.get_result(record.analysis_id)
        result_outcome_kind = result["cues"][0]["outcome"]["kind"]

    end = time.perf_counter_ns()
    return RepetitionMeasurement(
        repetition_index=repetition_index,
        elapsed_ns=end - start,
        analysis_id=record.analysis_id,
        terminal_state=status.state.value,
        result_outcome_kind=result_outcome_kind,
    )


def run_timed_benchmark(
    *,
    root: Path,
    source_bytes: bytes,
    cue_bytes: bytes,
    measured_repetitions: int = MEASURED_REPETITIONS,
    max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS,
) -> TimingReport:
    ingestion_use_case, create_use_case, query_use_case = _build_use_cases(root)
    executor = create_use_case._executor
    try:
        total_elapsed_seconds = 0.0

        warmup = _run_one_repetition(
            ingestion_use_case=ingestion_use_case,
            create_use_case=create_use_case,
            query_use_case=query_use_case,
            source_bytes=source_bytes,
            cue_bytes=cue_bytes,
            repetition_index=0,
        )
        total_elapsed_seconds += warmup.elapsed_ns / 1e9
        if total_elapsed_seconds > max_total_seconds:
            return TimingReport(
                status="incomplete",
                warmup_elapsed_ns=warmup.elapsed_ns,
                measured=[],
                incomplete_reason=(
                    f"warm-up repetition alone took {total_elapsed_seconds:.3f}s, "
                    f"exceeding the {max_total_seconds:.3f}s max_total_seconds "
                    "budget; aborted before any measured repetition"
                ),
            )

        measured: list[RepetitionMeasurement] = []
        for index in range(1, measured_repetitions + 1):
            measurement = _run_one_repetition(
                ingestion_use_case=ingestion_use_case,
                create_use_case=create_use_case,
                query_use_case=query_use_case,
                source_bytes=source_bytes,
                cue_bytes=cue_bytes,
                repetition_index=index,
            )
            total_elapsed_seconds += measurement.elapsed_ns / 1e9
            measured.append(measurement)
            if total_elapsed_seconds > max_total_seconds:
                return TimingReport(
                    status="incomplete",
                    warmup_elapsed_ns=warmup.elapsed_ns,
                    measured=measured,
                    incomplete_reason=(
                        f"cumulative elapsed time exceeded the "
                        f"{max_total_seconds:.3f}s max_total_seconds budget "
                        f"after {index} of {measured_repetitions} measured "
                        "repetitions; remaining repetitions were not run"
                    ),
                )

        return TimingReport(
            status="complete",
            warmup_elapsed_ns=warmup.elapsed_ns,
            measured=measured,
            incomplete_reason=None,
        )
    finally:
        executor.shutdown()


def _timing_statistics(measured: list[RepetitionMeasurement]) -> dict[str, Any] | None:
    if not measured:
        return None
    durations_ns = [m.elapsed_ns for m in measured]
    median_ns = statistics.median(durations_ns)
    min_ns = min(durations_ns)
    max_ns = max(durations_ns)
    return {
        "count": len(durations_ns),
        "unit": "nanoseconds",
        "median_ns": median_ns,
        "min_ns": min_ns,
        "max_ns": max_ns,
        "median_ms": median_ns / 1e6,
        "min_ms": min_ns / 1e6,
        "max_ms": max_ns / 1e6,
    }


def _git_command(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _cpu_model() -> str | None:
    processor = platform.processor()
    if processor:
        return processor
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def collect_environment_metadata() -> dict[str, Any]:
    """Record run/environment metadata, mirroring
    `benchmarks/matching_baseline.py::collect_environment_metadata`'s own
    established fields. Unavailable fields are explicitly marked as such,
    never omitted or guessed."""

    is_git_work_tree = _git_command(["rev-parse", "--is-inside-work-tree"]) == "true"

    return {
        "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_revision": {
            "head_commit": _git_command(["rev-parse", "HEAD"]) or "unavailable",
            "branch": _git_command(["rev-parse", "--abbrev-ref", "HEAD"]) or "unavailable",
            "working_tree_dirty": (
                bool(_git_command(["status", "--porcelain"])) if is_git_work_tree else None
            ),
        },
        "script_sha256": hashlib.sha256(_SCRIPT_PATH.read_bytes()).hexdigest(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "architecture": platform.machine(),
        "cpu_model": _cpu_model() or "unavailable",
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "timer": TIMER_NAME,
        "load_limitations": (
            "This measures real filesystem/SQLite I/O and a background "
            "thread-pool executor, not a pure CPU-bound computation; "
            "background load, disk cache state, and filesystem type on the "
            "executing host are not controlled or measured by this script."
        ),
    }


def describe_scenario(source_bytes: bytes, cue_bytes: bytes, case: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture_source": str(_FIXTURE_MANIFEST_PATH.relative_to(_REPO_ROOT)),
        "fixture_case_id": _FIXTURE_CASE_ID,
        "fixture_case_description": case.get("description"),
        "source_wav_bytes": len(source_bytes),
        "cue_wav_bytes": len(cue_bytes),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "cue_sha256": hashlib.sha256(cue_bytes).hexdigest(),
    }


def run_benchmark(
    *,
    measured_repetitions: int = MEASURED_REPETITIONS,
    max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS,
) -> dict[str, Any]:
    case = _load_fixture_case()
    source_bytes = _wav_bytes(case["source"])
    cue_bytes = _wav_bytes(case["cue"])
    environment = collect_environment_metadata()
    scenario = describe_scenario(source_bytes, cue_bytes, case)

    with tempfile.TemporaryDirectory(prefix="acl-main-path-benchmark-") as tmp:
        root = Path(tmp)
        timing = run_timed_benchmark(
            root=root,
            source_bytes=source_bytes,
            cue_bytes=cue_bytes,
            measured_repetitions=measured_repetitions,
            max_total_seconds=max_total_seconds,
        )

    return {
        "report_kind": "main_path_benchmark_report",
        "issue_id": "M7-05",
        "scenario": scenario,
        "methodology": {
            "timed_window": (
                "From immediately before the source/cue Asset ingestion "
                "calls, through Analysis creation, terminal-state polling, "
                "and (for a succeeded Analysis) result retrieval -- the "
                "same Application-layer composition root "
                "interfaces.rest_api.app.create_app uses, excluding only "
                "HTTP/ASGI request-response framing (see module docstring)."
            ),
            "warmup_repetitions": WARMUP_REPETITIONS,
            "measured_repetitions": measured_repetitions,
            "poll_interval_seconds": DEFAULT_POLL_INTERVAL_SECONDS,
            "poll_timeout_seconds": DEFAULT_POLL_TIMEOUT_SECONDS,
            "timer": TIMER_NAME,
            "max_total_seconds_budget": max_total_seconds,
        },
        "environment": environment,
        "timing": {
            "status": timing.status,
            "incomplete_reason": timing.incomplete_reason,
            "warmup_elapsed_ns": timing.warmup_elapsed_ns,
            "measured_repetitions": [
                dataclasses.asdict(m) for m in timing.measured
            ],
            "statistics": _timing_statistics(timing.measured),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark harness for the main analysis path (M7-05): asset "
            "ingestion, canonicalization, Analysis persistence, executor "
            "scheduling/matching, and result retrieval, over the real "
            "Application-layer composition root. Does not modify "
            "benchmarks/matching_baseline.py or any src/audio_cue_locator/ "
            "module."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to also write the JSON report to (in addition to stdout).",
    )
    parser.add_argument(
        "--max-total-seconds",
        type=float,
        default=DEFAULT_MAX_TOTAL_SECONDS,
        help=(
            "Abort remaining repetitions if cumulative measured elapsed time "
            f"exceeds this budget (default: {DEFAULT_MAX_TOTAL_SECONDS} seconds)."
        ),
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=MEASURED_REPETITIONS,
        help=f"Number of measured repetitions (default: {MEASURED_REPETITIONS}).",
    )
    args = parser.parse_args(argv)

    report = run_benchmark(
        measured_repetitions=args.repetitions,
        max_total_seconds=args.max_total_seconds,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["timing"]["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
