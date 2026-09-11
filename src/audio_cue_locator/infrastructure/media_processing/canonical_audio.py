"""Internal Canonical Audio contract (M1-03).

Canonical Audio is an Infrastructure-owned, internal representation for
media processing (docs/architecture.md, "Canonical Audio"; "Media
Processing"), distinct from and later consumed by the not-yet-built
Acoustic Matching area. It fixes the representation any decoded audio must
be converted to before matching, since FFmpegMediaAdapter.extract_audio
(M1-02) performs no resampling, downmixing, or normalization of its own —
it writes the decoded audio stream to WAV at the source media's native
sample rate and channel count (see ffmpeg_adapter.py's own module
docstring: "Canonical audio parameters (sample rate, channels,
normalization) remain M1-03 decisions").

This module defines the contract's shape and the recorded parameter
values; it intentionally does not implement the resample/downmix/
normalize transformation itself, which remains a later, separately
validated step (docs/architecture.md, Princípios e Restrições #7 requires
the *decision* to be explicit and evidence-based, not that this issue
implement acoustic-audio transformation code).

The concrete parameter values below were selected via source-code
inspection of the existing FFmpegMediaAdapter and established digital-audio
engineering facts, not via live, non-mocked ffmpeg/ffprobe probing of
representative sample files: ffmpeg/ffprobe were not available in this
implementation environment. CANONICAL_AUDIO_JUSTIFICATIONS records the
technical rationale for each field; PENDING_LIVE_PROBING_VERIFICATION
documents the follow-up required before this decision can be considered
empirically validated, and the decision remains explicitly revisable per
docs/milestones.md's M1 — Notas de Continuidade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class NormalizationPolicy:
    """Amplitude-normalization behavior applied to canonical audio."""

    enabled: bool
    method: str
    target_peak_amplitude: float


@dataclass(frozen=True)
class ParameterJustification:
    """Technical rationale recorded for one canonical-audio parameter."""

    rationale: str
    source: str
    revisable: bool = True


@dataclass(frozen=True)
class CanonicalAudioSpec:
    """Frozen internal contract for M1's canonical audio representation."""

    sample_rate_hz: int
    channels: int
    sample_format: str
    normalization: NormalizationPolicy


CANONICAL_AUDIO_SPEC = CanonicalAudioSpec(
    sample_rate_hz=48000,
    channels=1,
    sample_format="float32",
    normalization=NormalizationPolicy(
        enabled=True,
        method="peak",
        target_peak_amplitude=1.0,
    ),
)


CANONICAL_AUDIO_JUSTIFICATIONS: Mapping[str, ParameterJustification] = {
    "sample_rate_hz": ParameterJustification(
        rationale=(
            "48000 Hz is the native sample rate FFmpegMediaAdapter most commonly "
            "decodes from MP4-with-audio inputs, one of the two media types M1-02 "
            "already supports (per ffmpeg_adapter.py's own module docstring); "
            "choosing it as the canonical target lets that input path avoid "
            "resampling while still satisfying the Nyquist-Shannon criterion for "
            "the full human-audible range (up to 24 kHz), suitable for arbitrary "
            "acoustic cues rather than only speech-band content."
        ),
        source=(
            "src/audio_cue_locator/infrastructure/media_processing/"
            "ffmpeg_adapter.py (module docstring, supported inputs); "
            "Nyquist-Shannon sampling theorem."
        ),
    ),
    "channels": ParameterJustification(
        rationale=(
            "1 (mono) is chosen because docs/architecture.md names normalized "
            "cross-correlation as the first Acoustic Matching algorithm family, "
            "which operates on a single one-dimensional signal; downmixing to "
            "mono once, at canonicalization time, via ffmpeg's deterministic "
            "channel-mix behavior keeps the future matching step from having to "
            "define its own per-channel comparison or reduction policy."
        ),
        source="docs/architecture.md (Acoustic Matching).",
    ),
    "sample_format": ParameterJustification(
        rationale=(
            "float32 samples normalized to [-1.0, 1.0] avoid the integer "
            "overflow and quantization-clipping concerns of correlating "
            "fixed-point (e.g. int16) audio, are the conventional numpy/scipy "
            "representation for signal-processing correlation, and are the "
            "precision required by the peak normalization applied below."
        ),
        source=(
            "pyproject.toml (numpy, scipy dependencies); docs/architecture.md "
            "(Acoustic Matching, NumPy/SciPy)."
        ),
    ),
    "normalization": ParameterJustification(
        rationale=(
            "Amplitude normalization is included in the M1 baseline: peak "
            "normalization (scaling so the maximum absolute sample equals 1.0, "
            "leaving silent/all-zero audio unchanged to avoid division by zero) "
            "gives every canonicalized asset a comparable, bounded dynamic range "
            "for numerically stable float32 correlation, and is complementary to "
            "the normalized-cross-correlation matching approach docs/"
            "architecture.md already names, which is itself amplitude-invariant "
            "in its scoring."
        ),
        source="docs/architecture.md (Acoustic Matching, correlação normalizada).",
    ),
}


PENDING_LIVE_PROBING_VERIFICATION = (
    "The parameter values in CANONICAL_AUDIO_SPEC were selected via source-code "
    "inspection of FFmpegMediaAdapter (M1-02) and established digital-audio "
    "engineering facts, not via live, non-mocked ffmpeg/ffprobe probing of "
    "representative sample files: ffmpeg/ffprobe were not available in this "
    "implementation environment. Before this decision can be considered "
    "empirically validated, implementation must confirm ffmpeg/ffprobe "
    "availability and run FFmpegMediaAdapter.probe/extract_audio against "
    "representative sample audio/video files; any resulting change to "
    "CANONICAL_AUDIO_SPEC must be explicit and accompanied by media-test "
    "regression, per docs/milestones.md's M1 — Notas de Continuidade, not a "
    "silent redefinition."
)
