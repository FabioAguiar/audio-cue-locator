"""Structured error taxonomy for the FFmpeg-backed Media Processing adapter.

Media, decode, and timeout failures must produce explicit, structured state
rather than a silent result or an empty value (docs/architecture.md,
Principios e Restricoes #11, "Falha e um estado explicito").
"""


class MediaProcessingError(Exception):
    """Base class for every error raised by the Media Processing adapter."""


class InvalidMediaError(MediaProcessingError):
    """Raised when the input path does not exist or is not valid media."""


class NoAudioStreamError(MediaProcessingError):
    """Raised when a media file was probed successfully but has no audio stream."""


class FFmpegTimeoutError(MediaProcessingError):
    """Raised when an ffprobe/ffmpeg subprocess exceeds its configured timeout."""


class FFmpegExecutionError(MediaProcessingError):
    """Raised when ffprobe/ffmpeg fails for a reason other than invalid media."""
