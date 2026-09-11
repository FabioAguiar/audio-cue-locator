"""Media Processing: the project's single point of access to FFmpeg/ffprobe.

Exposes FFmpegMediaAdapter and its structured result/error types, plus the
CanonicalAudioSpec contract (M1-03) for canonical audio representation. No
other module in this project may invoke ffmpeg or ffprobe directly
(docs/architecture.md, section "FFmpeg").
"""

from .canonical_audio import (
    CANONICAL_AUDIO_JUSTIFICATIONS,
    CANONICAL_AUDIO_SPEC,
    PENDING_LIVE_PROBING_VERIFICATION,
    CanonicalAudioSpec,
    NormalizationPolicy,
    ParameterJustification,
)
from .errors import (
    FFmpegExecutionError,
    FFmpegTimeoutError,
    InvalidMediaError,
    MediaProcessingError,
    NoAudioStreamError,
)
from .ffmpeg_adapter import FFmpegMediaAdapter
from .models import ExtractionResult, ProbeResult

__all__ = [
    "CANONICAL_AUDIO_JUSTIFICATIONS",
    "CANONICAL_AUDIO_SPEC",
    "PENDING_LIVE_PROBING_VERIFICATION",
    "CanonicalAudioSpec",
    "ExtractionResult",
    "FFmpegExecutionError",
    "FFmpegMediaAdapter",
    "FFmpegTimeoutError",
    "InvalidMediaError",
    "MediaProcessingError",
    "NoAudioStreamError",
    "NormalizationPolicy",
    "ParameterJustification",
    "ProbeResult",
]
