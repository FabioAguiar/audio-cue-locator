"""Benchmark harness for the unchanged M2-02 match_cue baseline (M2-06).

Measures the execution cost of the existing, unmodified single-cue matcher
(`audio_cue_locator.infrastructure.acoustic_matching.baseline.match_cue`) on
a longer, deterministic synthetic scenario than any fixture that currently
exists in this repository (`tests/fixtures/matching/*.json`, all well under
one second): a 60-second canonical source with a 0.5-second cue inserted
once at the 30-second mark, generated from a fixed seed. This represents
duration/cost, not real-media quality or production matching capacity
(docs/matching-benchmark.md, section 2).

This is developer benchmarking tooling under `benchmarks/`, not part of the
installed `audio_cue_locator` package (pyproject.toml restricts packaging to
`src/`) and not a test: it is never collected or run by pytest.

Boundaries this script deliberately respects (docs/matching-benchmark.md,
section 1; formal issue M2-06; intents/M2/M2-06/implementation-handoff.json
scope_notes G1/G2/G6/G7/G8):

- It calls the existing `match_cue` exactly as `baseline.py` defines it, with
  `baseline.py`'s own unmodified `DEFAULT_CONFIGURATION`. It does not modify
  `baseline.py` or `acceptance.py`, does not add a second matching method,
  and does not implement or bundle an FFT-accelerated search of any kind:
  if a future issue decides to compare against FFT-based correlation, that
  implementation belongs there, not here.
- Only the `match_cue` call itself -- including its own internal correlation
  computation -- is timed, with `time.perf_counter_ns()`. Input generation,
  canonicalization/normalization, configuration construction, hashing and
  reporting all happen outside the timed window.
- One untimed warm-up call precedes five sequential timed repetitions on the
  identical arrays; results (outcome, score, timestamp) are asserted
  consistent across repetitions and reported separately from timing, since
  score is method-specific similarity, never a timing signal or a calibrated
  confidence value (docs/matching-contract.md, section 4).
- A `--max-total-seconds` budget can abort an impractically long run after
  the repetition in progress completes; an aborted run is reported as
  explicitly incomplete, never silently truncated or backfilled with
  fabricated or substituted values.
- Running this script to produce the actual timing numbers this issue's
  acceptance criteria require is a separate, authorized execution step, not
  part of authoring this harness (implementation-handoff.json scope_notes,
  gap G8): this file defines the scenario and measurement structure; the
  resulting numbers are recorded into docs/matching-benchmark.md's Results
  section only after that authorized run.

Usage (from the repository root, with the project installed, e.g. via
`pip install -e .`, the same precondition `tests/test_matching_baseline.py`
already relies on)::

    python benchmarks/matching_baseline.py [--output PATH] \\
        [--max-total-seconds SECONDS]

Prints a JSON report to stdout; `--output` additionally writes it to a file.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from audio_cue_locator.infrastructure.acoustic_matching.baseline import (
    DEFAULT_CONFIGURATION,
    EffectiveConfiguration,
    MatchResult,
    match_cue,
)
from audio_cue_locator.infrastructure.media_processing.canonical_audio import (
    CANONICAL_AUDIO_SPEC,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = Path(__file__).resolve()

SEED = 206
"""Fixed generator seed (implementation-handoff.json scope_notes, gap G1)."""

SOURCE_DURATION_SECONDS = 60.0
CUE_DURATION_SECONDS = 0.5
INSERTION_TIME_SECONDS = 30.0
"""Scenario durations and insertion offset, per gap G1: a 60-second source
and 0.5-second cue at 48000 Hz (2,880,000 and 24,000 samples), with one
exact insertion at 30 seconds."""

WARMUP_REPETITIONS = 1
MEASURED_REPETITIONS = 5
"""One untimed warm-up plus five sequential measured repetitions, per gap G6."""

DEFAULT_MAX_TOTAL_SECONDS = 600.0
"""Default wall-clock budget for warm-up + all measured repetitions combined.

The direct ("valid" mode) correlation baseline.py deliberately uses is
O(len(source) * len(cue)); at this scenario's scale that is on the order of
tens of billions of multiply-accumulate operations per call (see
`direct_correlation_operation_count_estimate` in the emitted report), so a
generous default budget is used. This is a static complexity estimate, not
a timing measurement or a promise about actual wall-clock cost on any given
machine."""

TIMER_NAME = "time.perf_counter_ns"

_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


@dataclasses.dataclass(frozen=True)
class BenchmarkArrays:
    """Deterministic canonical (mono, float32) source/cue pair for this benchmark."""

    source: np.ndarray
    cue: np.ndarray
    insertion_sample_index: int
    source_sha256: str
    cue_sha256: str
    source_peak_scale_factor: float
    cue_peak_scale_factor: float


def generate_benchmark_arrays(seed: int = SEED) -> BenchmarkArrays:
    """Generate the deterministic scenario arrays (gap G1).

    Both arrays are bounded, non-silent broadband content: independent draws
    from `numpy.random.default_rng(seed).uniform(-1.0, 1.0, ...)` (a flat,
    broadband spectrum, deliberately distinct from the harmonic-burst cues
    `tests/fixtures/matching/representative-manifest.json` uses for quality
    evaluation -- this scenario represents duration/cost only, not detection
    quality). The cue is generated first and peak-normalized independently
    (`CANONICAL_AUDIO_SPEC.normalization`: method="peak",
    target_peak_amplitude=1.0), exactly as M1 canonicalizes a decoded cue
    asset on its own. It is then embedded verbatim into an independently
    drawn background at the sample index corresponding to
    INSERTION_TIME_SECONDS, and the combined array is itself peak-normalized
    the same way, exactly as M1 canonicalizes a decoded source asset on its
    own. Because the already peak-normalized cue's own peak sample is, with
    overwhelming probability, the combined array's global peak (a continuous
    uniform draw over 2,880,000 background samples essentially never
    reaches exactly 1.0), `source_peak_scale_factor` is expected to be
    exactly 1.0 in practice; it is computed and reported, not assumed, so
    this expectation is falsifiable from the emitted report rather than
    hard-coded.

    All of this happens outside any timed window (gap G6): timing starts
    only once these arrays already exist.
    """

    sample_rate = CANONICAL_AUDIO_SPEC.sample_rate_hz
    target_peak = CANONICAL_AUDIO_SPEC.normalization.target_peak_amplitude

    source_samples = round(SOURCE_DURATION_SECONDS * sample_rate)
    cue_samples = round(CUE_DURATION_SECONDS * sample_rate)
    insertion_index = round(INSERTION_TIME_SECONDS * sample_rate)
    if insertion_index + cue_samples > source_samples:
        raise ValueError(
            "cue insertion would exceed source length; adjust scenario constants"
        )

    rng = np.random.default_rng(seed)

    cue_raw = rng.uniform(-1.0, 1.0, size=cue_samples)
    cue_peak = float(np.max(np.abs(cue_raw)))
    cue_scale = target_peak / cue_peak if cue_peak > 0.0 else 1.0
    cue_canonical = (cue_raw * cue_scale).astype(np.float32)

    background = rng.uniform(-1.0, 1.0, size=source_samples)
    combined = background.copy()
    combined[insertion_index : insertion_index + cue_samples] = cue_canonical.astype(
        np.float64
    )
    source_peak = float(np.max(np.abs(combined)))
    source_scale = target_peak / source_peak if source_peak > 0.0 else 1.0
    source_canonical = (combined * source_scale).astype(np.float32)

    return BenchmarkArrays(
        source=source_canonical,
        cue=cue_canonical,
        insertion_sample_index=insertion_index,
        source_sha256=hashlib.sha256(source_canonical.tobytes()).hexdigest(),
        cue_sha256=hashlib.sha256(cue_canonical.tobytes()).hexdigest(),
        source_peak_scale_factor=source_scale,
        cue_peak_scale_factor=cue_scale,
    )


@dataclasses.dataclass(frozen=True)
class RepetitionMeasurement:
    """One measured `match_cue` call: its cost and its (untimed-relevance) result."""

    repetition_index: int
    elapsed_ns: int
    outcome: str
    score: float | None
    timestamp_seconds: float | None
    diagnostic_best_score: float | None
    diagnostic_best_timestamp_seconds: float | None


@dataclasses.dataclass(frozen=True)
class TimingReport:
    status: str  # "complete" or "incomplete"
    warmup_elapsed_ns: int | None
    measured: list[RepetitionMeasurement]
    result_consistent_across_repetitions: bool | None
    incomplete_reason: str | None


def _invoke_and_measure(
    source: np.ndarray, cue: np.ndarray, configuration: EffectiveConfiguration
) -> tuple[int, MatchResult]:
    """Time exactly one `match_cue` call; nothing else is inside this window."""

    start = time.perf_counter_ns()
    result = match_cue(source, cue, configuration)
    end = time.perf_counter_ns()
    return end - start, result


def _results_consistent(measured: list[RepetitionMeasurement]) -> bool:
    """match_cue is a pure function of its inputs; repeated calls on the same
    arrays must return the same outcome/score/timestamp. This is verified,
    not assumed (gap G6)."""

    if not measured:
        return True
    first = measured[0]
    fields = (
        "outcome",
        "score",
        "timestamp_seconds",
        "diagnostic_best_score",
        "diagnostic_best_timestamp_seconds",
    )
    return all(
        tuple(getattr(m, field) for field in fields)
        == tuple(getattr(first, field) for field in fields)
        for m in measured
    )


def run_timed_benchmark(
    source: np.ndarray,
    cue: np.ndarray,
    configuration: EffectiveConfiguration = DEFAULT_CONFIGURATION,
    *,
    max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS,
) -> TimingReport:
    """Run the warm-up plus measured repetitions, honoring the abort budget.

    The budget is checked only between whole repetitions (a single
    `match_cue` call cannot be interrupted mid-computation); if it is
    exceeded, the run stops immediately and reports `status="incomplete"`
    with an explicit reason and whatever repetitions did complete. No
    remaining repetition is fabricated, estimated, or silently skipped in
    the reported statistics (gap G6).
    """

    total_elapsed_seconds = 0.0

    warmup_elapsed_ns, _warmup_result = _invoke_and_measure(source, cue, configuration)
    total_elapsed_seconds += warmup_elapsed_ns / 1e9
    if total_elapsed_seconds > max_total_seconds:
        return TimingReport(
            status="incomplete",
            warmup_elapsed_ns=warmup_elapsed_ns,
            measured=[],
            result_consistent_across_repetitions=None,
            incomplete_reason=(
                f"warm-up repetition alone took {total_elapsed_seconds:.3f}s, "
                f"exceeding the {max_total_seconds:.3f}s max_total_seconds budget; "
                "aborted before any measured repetition"
            ),
        )

    measured: list[RepetitionMeasurement] = []
    for index in range(1, MEASURED_REPETITIONS + 1):
        elapsed_ns, result = _invoke_and_measure(source, cue, configuration)
        total_elapsed_seconds += elapsed_ns / 1e9
        measured.append(
            RepetitionMeasurement(
                repetition_index=index,
                elapsed_ns=elapsed_ns,
                outcome=result.outcome.value,
                score=result.score,
                timestamp_seconds=result.timestamp_seconds,
                diagnostic_best_score=result.diagnostic_best_score,
                diagnostic_best_timestamp_seconds=result.diagnostic_best_timestamp_seconds,
            )
        )
        if total_elapsed_seconds > max_total_seconds:
            return TimingReport(
                status="incomplete",
                warmup_elapsed_ns=warmup_elapsed_ns,
                measured=measured,
                result_consistent_across_repetitions=_results_consistent(measured),
                incomplete_reason=(
                    f"cumulative elapsed time exceeded the {max_total_seconds:.3f}s "
                    f"max_total_seconds budget after {index} of {MEASURED_REPETITIONS} "
                    "measured repetitions; remaining repetitions were not run"
                ),
            )

    return TimingReport(
        status="complete",
        warmup_elapsed_ns=warmup_elapsed_ns,
        measured=measured,
        result_consistent_across_repetitions=_results_consistent(measured),
        incomplete_reason=None,
    )


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


def _package_version(module_name: str) -> str | None:
    try:
        module = __import__(module_name)
    except ImportError:
        return None
    return getattr(module, "__version__", None)


def collect_environment_metadata() -> dict[str, Any]:
    """Record the run/environment metadata gap G7 requires. Unavailable
    fields are explicitly marked as such, never omitted or guessed."""

    is_git_work_tree = _git_command(["rev-parse", "--is-inside-work-tree"]) == "true"
    thread_env = {name: os.environ.get(name, "unset") for name in _THREAD_ENV_VARS}

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
        "numpy_version": np.__version__,
        "scipy_version": _package_version("scipy") or "unavailable",
        "timer": TIMER_NAME,
        "numeric_library_thread_settings": thread_env,
        "load_limitations": (
            "numpy.correlate's direct ('valid' mode) algorithm used by match_cue is "
            "a scalar C loop, not a BLAS-backed or multi-threaded operation; the "
            "thread environment variables above are recorded for completeness but "
            "are not expected to materially affect this specific call's cost. No "
            "CPU affinity, process priority, or exclusive-machine control was "
            "applied to this run; background load on the executing host is not "
            "controlled or measured by this script."
        ),
    }


def describe_configuration(configuration: EffectiveConfiguration) -> dict[str, Any]:
    return {
        "method": configuration.method,
        "acceptance_threshold": configuration.acceptance_threshold,
        "source": (
            "audio_cue_locator.infrastructure.acoustic_matching.baseline."
            "DEFAULT_CONFIGURATION (unmodified)"
        ),
    }


def describe_canonical_spec() -> dict[str, Any]:
    spec = CANONICAL_AUDIO_SPEC
    return {
        "sample_rate_hz": spec.sample_rate_hz,
        "channels": spec.channels,
        "sample_format": spec.sample_format,
        "normalization": {
            "enabled": spec.normalization.enabled,
            "method": spec.normalization.method,
            "target_peak_amplitude": spec.normalization.target_peak_amplitude,
        },
    }


def describe_scenario(arrays: BenchmarkArrays) -> dict[str, Any]:
    source_samples = int(arrays.source.shape[0])
    cue_samples = int(arrays.cue.shape[0])
    return {
        "seed": SEED,
        "generator": (
            "numpy.random.default_rng(seed).uniform(-1.0, 1.0, size=n) "
            "(independent broadband white-noise draws for cue and background)"
        ),
        "source_duration_seconds": SOURCE_DURATION_SECONDS,
        "source_samples": source_samples,
        "cue_duration_seconds": CUE_DURATION_SECONDS,
        "cue_samples": cue_samples,
        "insertion_time_seconds": INSERTION_TIME_SECONDS,
        "insertion_sample_index": arrays.insertion_sample_index,
        "cue_peak_scale_factor": arrays.cue_peak_scale_factor,
        "source_peak_scale_factor": arrays.source_peak_scale_factor,
        "source_sha256": arrays.source_sha256,
        "cue_sha256": arrays.cue_sha256,
        "direct_correlation_operation_count_estimate": (
            (source_samples - cue_samples + 1) * cue_samples
        ),
        "direct_correlation_operation_count_note": (
            "theoretical multiply-accumulate count for numpy.correlate(..., "
            "mode='valid'), i.e. (len(source) - len(cue) + 1) * len(cue); a static "
            "complexity estimate computed without running the benchmark, not a "
            "timing measurement."
        ),
    }


def run_benchmark(*, max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS) -> dict[str, Any]:
    arrays = generate_benchmark_arrays()
    configuration = DEFAULT_CONFIGURATION
    environment = collect_environment_metadata()
    scenario = describe_scenario(arrays)

    timing = run_timed_benchmark(
        arrays.source, arrays.cue, configuration, max_total_seconds=max_total_seconds
    )

    detection_result = None
    if timing.measured:
        first = timing.measured[0]
        detection_result = {
            "outcome": first.outcome,
            "score": first.score,
            "timestamp_seconds": first.timestamp_seconds,
            "diagnostic_best_score": first.diagnostic_best_score,
            "diagnostic_best_timestamp_seconds": first.diagnostic_best_timestamp_seconds,
            "note": (
                "Reported separately from timing per docs/matching-contract.md "
                "section 4: score is method-specific similarity, not a timing "
                "metric or a calibrated confidence value."
            ),
        }

    return {
        "report_kind": "matching_baseline_benchmark_report",
        "issue_id": "M2-06",
        "scenario": scenario,
        "configuration": describe_configuration(configuration),
        "canonical_audio_spec": describe_canonical_spec(),
        "methodology": {
            "timed_call": (
                "audio_cue_locator.infrastructure.acoustic_matching.baseline."
                "match_cue(source, cue, configuration)"
            ),
            "timed_scope": (
                "Only the match_cue call itself, including its internal "
                "correlation computation, is timed. Array generation, "
                "canonicalization, configuration construction, hashing and "
                "reporting are excluded from the timed window."
            ),
            "warmup_repetitions": WARMUP_REPETITIONS,
            "measured_repetitions": MEASURED_REPETITIONS,
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
            "result_consistent_across_repetitions": timing.result_consistent_across_repetitions,
        },
        "detection_result": detection_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark harness for the unchanged M2-02 match_cue baseline on a "
            "longer, deterministic synthetic scenario (M2-06). Benchmarking-only "
            "tooling: does not modify match_cue/baseline.py/acceptance.py and "
            "authors no FFT-based matching implementation."
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
    args = parser.parse_args(argv)

    report = run_benchmark(max_total_seconds=args.max_total_seconds)
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["timing"]["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
