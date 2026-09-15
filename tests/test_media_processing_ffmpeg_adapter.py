"""Tests for the M1-02 FFmpeg Media Processing adapter.

These validate the M1-02 acceptance criteria: a single adapter is the
project's only caller of ffmpeg/ffprobe, audio-stream detection/selection
works, invalid media and missing-audio-stream conditions raise structured
(non-silent) errors, no shell string is built from untrusted input, and
Core remains independent of media_processing.

ffprobe/ffmpeg subprocess calls are mocked for the unit tests below so
they run deterministically without requiring FFmpeg to be installed;
verifying FFmpeg/ffprobe availability in the target environment is a
separate precondition (see states/M1/M1-02/issue-operational-state.json).
A skip-if-unavailable integration test at the end exercises a real
ffmpeg/ffprobe binary when present, using small inputs synthesized at
test time rather than committed binary fixtures (fixtures are reserved
for M1-04).
"""

import ast
import importlib
import json
import shutil
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from audio_cue_locator.infrastructure.media_processing import (
    FFmpegExecutionError,
    FFmpegMediaAdapter,
    FFmpegTimeoutError,
    InvalidMediaError,
    NoAudioStreamError,
    ProbeResult,
)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["ignored"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _ffprobe_json(has_audio: bool) -> str:
    streams = []
    if has_audio:
        streams.append(
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "pcm_s16le",
                "sample_rate": "44100",
                "channels": 2,
            }
        )
    return json.dumps(
        {
            "format": {"format_name": "wav", "duration": "1.500000"},
            "streams": streams,
        }
    )


def test_probe_detects_and_selects_audio_stream(tmp_path):
    media_file = tmp_path / "sample.wav"
    media_file.write_bytes(b"not-real-audio-bytes")

    adapter = FFmpegMediaAdapter()
    with patch("subprocess.run", return_value=_completed(stdout=_ffprobe_json(True))) as mock_run:
        result = adapter.probe(str(media_file))

    assert isinstance(result, ProbeResult)
    assert result.has_audio_stream is True
    assert result.audio_stream_index == 1
    assert result.audio_codec_name == "pcm_s16le"
    assert result.sample_rate == 44100
    assert result.channels == 2
    assert result.duration_seconds == pytest.approx(1.5)

    called_args, called_kwargs = mock_run.call_args
    command = called_args[0]
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert called_kwargs["shell"] is False


def test_probe_reports_no_audio_stream_without_raising(tmp_path):
    media_file = tmp_path / "video_only.mp4"
    media_file.write_bytes(b"not-real-video-bytes")

    adapter = FFmpegMediaAdapter()
    with patch("subprocess.run", return_value=_completed(stdout=_ffprobe_json(False))):
        result = adapter.probe(str(media_file))

    assert result.has_audio_stream is False
    assert result.audio_stream_index is None


def test_probe_raises_invalid_media_for_missing_file(tmp_path):
    missing_path = tmp_path / "does_not_exist.wav"

    adapter = FFmpegMediaAdapter()
    with pytest.raises(InvalidMediaError):
        adapter.probe(str(missing_path))


def test_probe_raises_invalid_media_when_ffprobe_rejects_input(tmp_path):
    media_file = tmp_path / "garbage.bin"
    media_file.write_bytes(b"\x00\x01\x02")

    adapter = FFmpegMediaAdapter()
    rejection = _completed(
        returncode=1, stderr="Invalid data found when processing input"
    )
    with patch("subprocess.run", return_value=rejection):
        with pytest.raises(InvalidMediaError):
            adapter.probe(str(media_file))


def test_probe_raises_execution_error_for_unexpected_failure(tmp_path):
    media_file = tmp_path / "sample.wav"
    media_file.write_bytes(b"not-real-audio-bytes")

    adapter = FFmpegMediaAdapter()
    unexpected_failure = _completed(returncode=2, stderr="unexpected internal error")
    with patch("subprocess.run", return_value=unexpected_failure):
        with pytest.raises(FFmpegExecutionError):
            adapter.probe(str(media_file))


def test_run_raises_timeout_error_on_subprocess_timeout(tmp_path):
    media_file = tmp_path / "sample.wav"
    media_file.write_bytes(b"not-real-audio-bytes")

    adapter = FFmpegMediaAdapter(timeout_seconds=5.0)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=5.0),
    ):
        with pytest.raises(FFmpegTimeoutError):
            adapter.probe(str(media_file))


def test_extract_audio_raises_no_audio_stream_error(tmp_path):
    media_file = tmp_path / "video_only.mp4"
    media_file.write_bytes(b"not-real-video-bytes")
    output_path = tmp_path / "out.wav"

    adapter = FFmpegMediaAdapter()
    with patch("subprocess.run", return_value=_completed(stdout=_ffprobe_json(False))):
        with pytest.raises(NoAudioStreamError):
            adapter.extract_audio(str(media_file), str(output_path))


def test_extract_audio_invokes_ffmpeg_with_argument_list_only(tmp_path):
    media_file = tmp_path / "with_audio.mp4"
    media_file.write_bytes(b"not-real-video-bytes")
    output_path = tmp_path / "out.wav"

    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))
    extract_response = _completed(returncode=0)

    with patch("subprocess.run", side_effect=[probe_response, extract_response]) as mock_run:
        result = adapter.extract_audio(str(media_file), str(output_path))

    assert result.output_path == str(output_path)
    assert result.sample_rate == 44100
    assert result.channels == 2

    extract_call_args, extract_call_kwargs = mock_run.call_args_list[1]
    command = extract_call_args[0]
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert extract_call_kwargs["shell"] is False
    assert str(media_file) in command
    assert str(output_path) in command


def test_core_does_not_import_media_processing():
    core_package = importlib.import_module("audio_cue_locator.core")
    core_dir = Path(core_package.__file__).parent

    for source_path in core_dir.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module} if node.module else set()
            else:
                continue
            assert not any(
                name and ("media_processing" in name or name in {"ffmpeg", "subprocess"})
                for name in names
            ), f"{source_path} must not import FFmpeg/media_processing: {names}"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available in this environment",
)
def test_probe_and_extract_against_real_ffmpeg(tmp_path):
    wav_path = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
            str(wav_path),
        ],
        shell=False,
        check=True,
        timeout=30,
    )

    adapter = FFmpegMediaAdapter()
    probe_result = adapter.probe(str(wav_path))
    assert probe_result.has_audio_stream is True

    output_path = tmp_path / "extracted.wav"
    extraction_result = adapter.extract_audio(str(wav_path), str(output_path))
    assert Path(extraction_result.output_path).is_file()


# --- S0002: real FFmpeg probe/extract for every newly advertised video ------
# --- container family, synthesized at test time (no committed binary       -
# --- fixtures) -------------------------------------------------------------


_S0002_VIDEO_SYNTHESIS_CASES = [
    pytest.param("mp4", ["-c:a", "aac"], id="mp4"),
    pytest.param("mov", ["-c:a", "aac"], id="mov"),
    pytest.param("webm", ["-c:a", "libvorbis"], id="webm"),
    pytest.param("mkv", ["-c:a", "pcm_s16le"], id="mkv"),
    pytest.param("avi", ["-c:a", "pcm_s16le"], id="avi"),
]


# --- S0013: bounded segment rendering for audio audition -------------------


def _fake_run_writing_output(probe_response):
    """A `subprocess.run` `side_effect` distinguishing the probe call (by
    executable) from the render call, writing deterministic bytes to the
    render call's output-file argument (the real subprocess would produce
    that file; nothing here otherwise touches the filesystem)."""

    def _run(command, **kwargs):
        if command[0].endswith("ffprobe") or "ffprobe" in Path(command[0]).name:
            return probe_response
        Path(command[-1]).write_bytes(b"RIFF-rendered-preview-bytes")
        return _completed(returncode=0)

    return _run


def test_render_wav_segment_probes_first_then_maps_the_selected_audio_stream(tmp_path):
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))

    with patch(
        "subprocess.run", side_effect=_fake_run_writing_output(probe_response)
    ) as mock_run:
        result = adapter.render_wav_segment(
            b"fake-media-bytes",
            start_seconds=1.5,
            duration_seconds=2.5,
            sample_rate_hz=48000,
        )

    assert isinstance(result, bytes)
    assert mock_run.call_count == 2

    probe_args, probe_kwargs = mock_run.call_args_list[0]
    assert probe_args[0][0] == adapter._ffprobe_path
    assert probe_kwargs["shell"] is False

    render_args, render_kwargs = mock_run.call_args_list[1]
    command = render_args[0]
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert render_kwargs["shell"] is False
    assert "-map" in command
    assert command[command.index("-map") + 1] == "0:1"


def test_render_wav_segment_passes_start_and_duration_as_separate_arguments(tmp_path):
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))

    with patch(
        "subprocess.run", side_effect=_fake_run_writing_output(probe_response)
    ) as mock_run:
        adapter.render_wav_segment(
            b"fake-media-bytes",
            start_seconds=3.25,
            duration_seconds=6.0,
            sample_rate_hz=44100,
        )

    command = mock_run.call_args_list[1][0][0]
    assert "-ss" in command
    assert command[command.index("-ss") + 1] == str(float(3.25))
    assert "-t" in command
    assert command[command.index("-t") + 1] == str(float(6.0))
    assert "-vn" in command
    assert "-ac" in command
    assert command[command.index("-ac") + 1] == "1"
    assert "-ar" in command
    assert command[command.index("-ar") + 1] == "44100"
    assert "-c:a" in command
    assert command[command.index("-c:a") + 1] == "pcm_s16le"


def test_render_wav_segment_returns_bytes_from_the_generated_output_file(tmp_path):
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))
    rendered_bytes = b"RIFF-rendered-preview-bytes"

    def _fake_run(command, **kwargs):
        if command[0] == adapter._ffprobe_path:
            return probe_response
        output_path = Path(command[-1])
        output_path.write_bytes(rendered_bytes)
        return _completed(returncode=0)

    with patch("subprocess.run", side_effect=_fake_run):
        result = adapter.render_wav_segment(
            b"fake-media-bytes",
            start_seconds=0.0,
            duration_seconds=1.0,
            sample_rate_hz=48000,
        )

    assert result == rendered_bytes


def test_render_wav_segment_cleans_up_its_temporary_directory(tmp_path):
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))
    captured_dirs: list[Path] = []

    def _fake_run(command, **kwargs):
        if command[0] == adapter._ffprobe_path:
            captured_dirs.append(Path(command[-1]).parent)
            return probe_response
        Path(command[-1]).write_bytes(b"rendered")
        return _completed(returncode=0)

    with patch("subprocess.run", side_effect=_fake_run):
        adapter.render_wav_segment(
            b"fake-media-bytes",
            start_seconds=0.0,
            duration_seconds=1.0,
            sample_rate_hz=48000,
        )

    assert captured_dirs and not captured_dirs[0].exists()


def test_render_wav_segment_raises_no_audio_stream_error_when_missing():
    adapter = FFmpegMediaAdapter()
    with patch("subprocess.run", return_value=_completed(stdout=_ffprobe_json(False))):
        with pytest.raises(NoAudioStreamError):
            adapter.render_wav_segment(
                b"fake-media-bytes",
                start_seconds=0.0,
                duration_seconds=1.0,
                sample_rate_hz=48000,
            )


def test_render_wav_segment_propagates_timeout_as_ffmpeg_timeout_error():
    adapter = FFmpegMediaAdapter(timeout_seconds=5.0)
    probe_response = _completed(stdout=_ffprobe_json(True))

    with patch(
        "subprocess.run",
        side_effect=[probe_response, subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5.0)],
    ):
        with pytest.raises(FFmpegTimeoutError):
            adapter.render_wav_segment(
                b"fake-media-bytes",
                start_seconds=0.0,
                duration_seconds=1.0,
                sample_rate_hz=48000,
            )


def test_render_wav_segment_propagates_invalid_media_error():
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))
    rejection = _completed(
        returncode=1, stderr="Invalid data found when processing input"
    )

    with patch("subprocess.run", side_effect=[probe_response, rejection]):
        with pytest.raises(InvalidMediaError):
            adapter.render_wav_segment(
                b"fake-media-bytes",
                start_seconds=0.0,
                duration_seconds=1.0,
                sample_rate_hz=48000,
            )


def test_render_wav_segment_propagates_execution_error_for_unexpected_failure():
    adapter = FFmpegMediaAdapter()
    probe_response = _completed(stdout=_ffprobe_json(True))
    unexpected_failure = _completed(returncode=2, stderr="unexpected internal error")

    with patch("subprocess.run", side_effect=[probe_response, unexpected_failure]):
        with pytest.raises(FFmpegExecutionError):
            adapter.render_wav_segment(
                b"fake-media-bytes",
                start_seconds=0.0,
                duration_seconds=1.0,
                sample_rate_hz=48000,
            )


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"media_bytes": b""}, id="empty-media-bytes"),
        pytest.param({"media_bytes": "not-bytes"}, id="non-bytes-media"),
        pytest.param({"start_seconds": -1.0}, id="negative-start"),
        pytest.param({"start_seconds": True}, id="bool-start"),
        pytest.param({"start_seconds": float("nan")}, id="nan-start"),
        pytest.param({"duration_seconds": 0.0}, id="zero-duration"),
        pytest.param({"duration_seconds": -1.0}, id="negative-duration"),
        pytest.param({"duration_seconds": float("inf")}, id="infinite-duration"),
        pytest.param({"sample_rate_hz": 0}, id="zero-sample-rate"),
        pytest.param({"sample_rate_hz": -1}, id="negative-sample-rate"),
        pytest.param({"sample_rate_hz": 48000.0}, id="non-integer-sample-rate"),
        pytest.param({"sample_rate_hz": True}, id="bool-sample-rate"),
    ],
)
def test_render_wav_segment_rejects_invalid_inputs_before_any_subprocess_call(kwargs):
    adapter = FFmpegMediaAdapter()
    call_kwargs = {
        "media_bytes": b"fake-media-bytes",
        "start_seconds": 0.0,
        "duration_seconds": 1.0,
        "sample_rate_hz": 48000,
    }
    call_kwargs.update(kwargs)
    media_bytes = call_kwargs.pop("media_bytes")

    with patch("subprocess.run") as mock_run:
        with pytest.raises(InvalidMediaError):
            adapter.render_wav_segment(media_bytes, **call_kwargs)

    mock_run.assert_not_called()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available in this environment",
)
def test_render_wav_segment_against_real_ffmpeg_produces_a_valid_bounded_wav(tmp_path):
    source_path = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
            str(source_path),
        ],
        shell=False,
        check=True,
        timeout=30,
    )

    adapter = FFmpegMediaAdapter()
    rendered = adapter.render_wav_segment(
        source_path.read_bytes(),
        start_seconds=0.5,
        duration_seconds=1.0,
        sample_rate_hz=16000,
    )

    assert rendered[:4] == b"RIFF"
    assert rendered[8:12] == b"WAVE"

    output_path = tmp_path / "rendered.wav"
    output_path.write_bytes(rendered)
    with wave.open(str(output_path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getframerate() == 16000
        assert reader.getsampwidth() == 2
        assert reader.getnframes() > 0


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available in this environment",
)
@pytest.mark.parametrize("extension,audio_codec_args", _S0002_VIDEO_SYNTHESIS_CASES)
def test_probe_and_extract_against_real_ffmpeg_for_every_s0002_video_container(
    tmp_path, extension, audio_codec_args
):
    """S0002 acceptance: real FFmpeg integration coverage synthesizes and
    probe/extracts each newly advertised video-container family, mirroring
    `test_probe_and_extract_against_real_ffmpeg` above's own real-tone
    synthesis convention rather than a committed binary fixture. This is
    the Infrastructure-layer evidence for the S0002 container expansion;
    `application.asset_ingestion.sniff_media_type` byte-signature detection
    for these same families is covered separately (`tests/
    test_api_asset_upload.py`), and does not itself invoke FFmpeg."""

    media_path = tmp_path / f"tone.{extension}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
            *audio_codec_args,
            str(media_path),
        ],
        shell=False,
        check=True,
        timeout=30,
    )

    adapter = FFmpegMediaAdapter()
    probe_result = adapter.probe(str(media_path))
    assert probe_result.has_audio_stream is True

    output_path = tmp_path / f"extracted-{extension}.wav"
    extraction_result = adapter.extract_audio(str(media_path), str(output_path))
    assert Path(extraction_result.output_path).is_file()
    assert Path(extraction_result.output_path).stat().st_size > 0
