"""Structured value objects returned by the Media Processing adapter.

These are technical metadata/result shapes, not the canonical audio
representation decided by M1-03; they exist so probing and extraction have
a traceable, testable output instead of an ad hoc dict.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProbeResult:
    """Technical metadata produced by probing a supported media file."""

    container_format: str
    duration_seconds: Optional[float]
    has_audio_stream: bool
    audio_stream_index: Optional[int]
    audio_codec_name: Optional[str]
    sample_rate: Optional[int]
    channels: Optional[int]


@dataclass(frozen=True)
class ExtractionResult:
    """Result of decoding/extracting the selected audio stream to a WAV file."""

    output_path: str
    sample_rate: Optional[int]
    channels: Optional[int]
