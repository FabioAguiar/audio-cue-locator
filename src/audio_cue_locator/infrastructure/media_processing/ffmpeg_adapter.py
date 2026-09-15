"""Single Infrastructure adapter encapsulating every ffprobe/ffmpeg
invocation for media probing and audio decode/extraction.

docs/architecture.md requires FFmpeg to remain the sole boundary for media
probing, decoding, and audio-stream extraction ("Media Processing"), fully
encapsulated behind one adapter ("FFmpeg"), and never invoked by
interpolating untrusted input into a shell command ("Execucao de FFmpeg").
No other module in this project may invoke ffmpeg or ffprobe directly.

Supported inputs: WAV as the native audio input format, plus the explicit
set of video containers `application.asset_ingestion.
SOURCE_MEDIA_SUPPORTED_MEDIA_TYPES` recognizes and routes here for audio-
stream extraction (S0002, `specs/S0002-common-video-container-source-
media-support/spec.md`) -- MP4/M4V, MOV (QuickTime), WebM, Matroska/MKV,
and AVI. This adapter performs no container-specific branching of its own:
`ffprobe`/`ffmpeg` already handle each of these container formats
generically through the same `probe`/`extract_audio` calls below. The
authoritative supported-format allowlist remains Application's own,
explicit, versioned decision (`application/asset_ingestion.py`), never
derived from whatever formats an installed FFmpeg build happens to report
as readable (`ffmpeg -formats`). Canonical audio parameters (sample rate,
channels, normalization) remain M1-03 decisions; extraction here only
writes the decoded audio stream to a WAV file.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from .errors import (
    FFmpegExecutionError,
    FFmpegTimeoutError,
    InvalidMediaError,
    NoAudioStreamError,
)
from .models import ExtractionResult, ProbeResult

DEFAULT_TIMEOUT_SECONDS = 30.0

_INVALID_MEDIA_MARKERS = (
    "invalid data found when processing input",
    "moov atom not found",
    "could not find codec parameters",
    "invalid argument",
    "no such file or directory",
)


class FFmpegMediaAdapter:
    """The project's single point of access to ffprobe/ffmpeg.

    Every command is built as an argument list and executed with
    shell=False; a media path is never interpolated into a shell string.
    """

    def __init__(
        self,
        ffprobe_path: Optional[str] = None,
        ffmpeg_path: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._ffprobe_path = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self._ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self._timeout_seconds = timeout_seconds

    def probe(self, media_path: str) -> ProbeResult:
        """Probe a supported media file and return structured technical metadata.

        Raises InvalidMediaError when the path does not exist or ffprobe
        cannot parse it as media. Does not raise for a missing audio
        stream; inspect the returned ProbeResult.has_audio_stream instead.
        """
        path = Path(media_path)
        if not path.is_file():
            raise InvalidMediaError(f"media file does not exist: {media_path}")

        command = [
            self._ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        completed = self._run(command)

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise InvalidMediaError(
                f"ffprobe did not return valid JSON for: {media_path}"
            ) from exc

        return self._parse_probe_payload(payload)

    def extract_audio(self, media_path: str, output_path: str) -> ExtractionResult:
        """Decode/extract the selected audio stream to a WAV file.

        Raises InvalidMediaError for invalid media, NoAudioStreamError when
        the media has no audio stream, FFmpegTimeoutError on subprocess
        timeout, and FFmpegExecutionError for any other ffmpeg failure.
        """
        probe_result = self.probe(media_path)
        if not probe_result.has_audio_stream:
            raise NoAudioStreamError(f"media has no audio stream: {media_path}")

        command = [
            self._ffmpeg_path,
            "-y",
            "-v", "error",
            "-i", str(Path(media_path)),
            "-vn",
            "-map", f"0:{probe_result.audio_stream_index}",
            str(Path(output_path)),
        ]
        self._run(command)

        return ExtractionResult(
            output_path=str(output_path),
            sample_rate=probe_result.sample_rate,
            channels=probe_result.channels,
        )

    def render_wav_segment(
        self,
        media_bytes: bytes,
        *,
        start_seconds: float,
        duration_seconds: float,
        sample_rate_hz: int,
    ) -> bytes:
        """Render one bounded mono PCM16 WAV segment from ``media_bytes``
        (S0013): structurally satisfies ``application.query_analysis.
        AudioAuditionRendererPort`` without that module importing this
        concrete adapter.

        Reuses ``self.probe`` to select the existing audio stream exactly
        like `extract_audio`, then requests accurate output seeking (`-ss`/
        `-t` as ffmpeg *output* options, after `-i`) rather than
        keyframe-only approximate input seeking, so the rendered segment
        starts at the exact requested sample rather than the nearest
        keyframe. Every temporary input/output file lives inside one
        `tempfile.TemporaryDirectory()` for the duration of this call only;
        no path is logged or returned. Applies no amplitude normalization --
        only the channel/sample-rate conversion needed for stable browser
        playback.
        """

        self._validate_segment_render_inputs(
            media_bytes, start_seconds, duration_seconds, sample_rate_hz
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "input.media"
            output_path = Path(tmp_dir) / "preview.wav"
            input_path.write_bytes(bytes(media_bytes))

            probe_result = self.probe(str(input_path))
            if not probe_result.has_audio_stream:
                raise NoAudioStreamError(
                    "media has no audio stream for audition rendering"
                )

            command = [
                self._ffmpeg_path,
                "-y",
                "-v", "error",
                "-i", str(input_path),
                "-ss", str(float(start_seconds)),
                "-t", str(float(duration_seconds)),
                "-vn",
                "-map", f"0:{probe_result.audio_stream_index}",
                "-ac", "1",
                "-ar", str(int(sample_rate_hz)),
                "-c:a", "pcm_s16le",
                str(output_path),
            ]
            self._run(command)

            return output_path.read_bytes()

    @staticmethod
    def _validate_segment_render_inputs(
        media_bytes: Any,
        start_seconds: Any,
        duration_seconds: Any,
        sample_rate_hz: Any,
    ) -> None:
        """Reject invalid `render_wav_segment` inputs before any subprocess
        runs, using this adapter's existing error vocabulary."""

        if (
            not isinstance(media_bytes, (bytes, bytearray, memoryview))
            or len(media_bytes) == 0
        ):
            raise InvalidMediaError(
                "render_wav_segment requires non-empty bytes-like media"
            )
        if isinstance(start_seconds, bool) or not isinstance(
            start_seconds, (int, float)
        ):
            raise InvalidMediaError("render_wav_segment start_seconds must be a number")
        if not math.isfinite(start_seconds) or start_seconds < 0:
            raise InvalidMediaError(
                "render_wav_segment start_seconds must be finite and >= 0"
            )
        if isinstance(duration_seconds, bool) or not isinstance(
            duration_seconds, (int, float)
        ):
            raise InvalidMediaError(
                "render_wav_segment duration_seconds must be a number"
            )
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise InvalidMediaError(
                "render_wav_segment duration_seconds must be finite and > 0"
            )
        if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
            raise InvalidMediaError(
                "render_wav_segment sample_rate_hz must be an integer"
            )
        if sample_rate_hz <= 0:
            raise InvalidMediaError(
                "render_wav_segment sample_rate_hz must be positive"
            )

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess:
        try:
            completed = subprocess.run(
                list(command),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegTimeoutError(
                f"ffmpeg/ffprobe timed out after {self._timeout_seconds} seconds"
            ) from exc
        except FileNotFoundError as exc:
            raise FFmpegExecutionError(
                f"ffmpeg/ffprobe executable not found: {exc.filename}"
            ) from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            if self._looks_like_invalid_media(stderr):
                raise InvalidMediaError(
                    f"ffmpeg/ffprobe reported invalid media: {stderr or 'unknown error'}"
                )
            raise FFmpegExecutionError(
                f"ffmpeg/ffprobe exited with status {completed.returncode}: "
                f"{stderr or 'no error output'}"
            )
        return completed

    @staticmethod
    def _looks_like_invalid_media(stderr: str) -> bool:
        lowered = stderr.lower()
        return any(marker in lowered for marker in _INVALID_MEDIA_MARKERS)

    @staticmethod
    def _parse_probe_payload(payload: dict[str, Any]) -> ProbeResult:
        format_info = payload.get("format") or {}
        streams = payload.get("streams") or []

        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        selected_audio = audio_streams[0] if audio_streams else None

        duration_raw = format_info.get("duration")
        duration_seconds = float(duration_raw) if duration_raw is not None else None

        sample_rate_raw = selected_audio.get("sample_rate") if selected_audio else None
        sample_rate = int(sample_rate_raw) if sample_rate_raw is not None else None

        return ProbeResult(
            container_format=format_info.get("format_name", ""),
            duration_seconds=duration_seconds,
            has_audio_stream=selected_audio is not None,
            audio_stream_index=selected_audio.get("index") if selected_audio else None,
            audio_codec_name=selected_audio.get("codec_name") if selected_audio else None,
            sample_rate=sample_rate,
            channels=selected_audio.get("channels") if selected_audio else None,
        )
