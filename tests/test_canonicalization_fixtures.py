"""Tests for M1-04: reproducible audio/video fixtures and automated tests
for canonicalization determinism and failure handling.

These exercise the real M1-02 FFmpegMediaAdapter and the real M1-03
CanonicalAudioSpec/CANONICAL_AUDIO_SPEC contract directly against small,
synthetic, versioned fixtures committed under tests/fixtures/, per
docs/milestones.md's M1 Definition of Done ("a mesma entrada com a mesma
configuração produza resultado técnico equivalente de forma
determinística"; "mídia sem áudio ou inválida produza erro explícito").

Every test that calls FFmpegMediaAdapter requires real ffmpeg/ffprobe
binaries and is skipped when they are unavailable, mirroring
tests/test_media_processing_ffmpeg_adapter.py's skip-if-unavailable
convention: no mock or artificial substitute may be used here, since doing
so would mask the real canonicalization behavior this issue must
demonstrate (issues/M1/M1-04/formal-issue.json, §6 Regras específicas).

Fixture provenance: tests/fixtures/sine_440hz_mono_1s.wav is a synthetic
440 Hz mono sine wave (48 kHz, 16-bit PCM, ~1s); tests/fixtures/
video_with_audio.mp4 is a synthetic 160x120 H.264 test pattern with an AAC
mono audio track (~1s); tests/fixtures/no_audio_stream.mp4 is the same
video-only test pattern with no audio track; tests/fixtures/not_media.txt
is a small plain-text file that is not parseable as media. None represent
real-world/representative matching material (docs/milestones.md, M1 -
Riscos e Lacunas).
"""

import shutil
from pathlib import Path

import pytest

from audio_cue_locator.infrastructure.media_processing import (
    CANONICAL_AUDIO_SPEC,
    FFmpegMediaAdapter,
    InvalidMediaError,
    NoAudioStreamError,
)

_FFMPEG_UNAVAILABLE = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
_SKIP_REASON = "ffmpeg/ffprobe not available in this environment"

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_AUDIO_FIXTURE = _FIXTURES_DIR / "sine_440hz_mono_1s.wav"
_VIDEO_WITH_AUDIO_FIXTURE = _FIXTURES_DIR / "video_with_audio.mp4"
_NO_AUDIO_STREAM_FIXTURE = _FIXTURES_DIR / "no_audio_stream.mp4"
_NOT_MEDIA_FIXTURE = _FIXTURES_DIR / "not_media.txt"

_MAX_FIXTURE_SIZE_BYTES = 1_000_000


def test_fixtures_are_committed_and_small():
    """Fixtures must exist and stay small enough for versioning alongside
    the code (docs/milestones.md, M1 - Escopo Núcleo), independent of
    whether ffmpeg/ffprobe are installed in this environment."""
    for fixture in (
        _AUDIO_FIXTURE,
        _VIDEO_WITH_AUDIO_FIXTURE,
        _NO_AUDIO_STREAM_FIXTURE,
        _NOT_MEDIA_FIXTURE,
    ):
        assert fixture.is_file(), f"missing fixture: {fixture}"
        assert fixture.stat().st_size < _MAX_FIXTURE_SIZE_BYTES, (
            f"fixture too large for versioning: {fixture}"
        )


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_probe_is_deterministic_for_the_same_wav_fixture_and_configuration():
    adapter = FFmpegMediaAdapter()

    first = adapter.probe(str(_AUDIO_FIXTURE))
    second = adapter.probe(str(_AUDIO_FIXTURE))

    assert first == second
    assert first.has_audio_stream is True
    assert first.channels == CANONICAL_AUDIO_SPEC.channels
    assert first.sample_rate == CANONICAL_AUDIO_SPEC.sample_rate_hz


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_probe_is_deterministic_for_the_same_video_fixture_and_configuration():
    adapter = FFmpegMediaAdapter()

    first = adapter.probe(str(_VIDEO_WITH_AUDIO_FIXTURE))
    second = adapter.probe(str(_VIDEO_WITH_AUDIO_FIXTURE))

    assert first == second
    assert first.has_audio_stream is True


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_extract_audio_is_deterministic_for_the_same_fixture_and_configuration(tmp_path):
    adapter = FFmpegMediaAdapter()

    first = adapter.extract_audio(
        str(_VIDEO_WITH_AUDIO_FIXTURE), str(tmp_path / "first.wav")
    )
    second = adapter.extract_audio(
        str(_VIDEO_WITH_AUDIO_FIXTURE), str(tmp_path / "second.wav")
    )

    assert first.sample_rate == second.sample_rate
    assert first.channels == second.channels
    assert Path(first.output_path).is_file()
    assert Path(second.output_path).is_file()


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_probe_raises_invalid_media_error_for_non_media_fixture():
    adapter = FFmpegMediaAdapter()

    with pytest.raises(InvalidMediaError):
        adapter.probe(str(_NOT_MEDIA_FIXTURE))


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_probe_reports_no_audio_stream_for_audio_less_fixture_without_raising():
    adapter = FFmpegMediaAdapter()

    result = adapter.probe(str(_NO_AUDIO_STREAM_FIXTURE))

    assert result.has_audio_stream is False


@pytest.mark.skipif(_FFMPEG_UNAVAILABLE, reason=_SKIP_REASON)
def test_extract_audio_raises_no_audio_stream_error_for_audio_less_fixture(tmp_path):
    adapter = FFmpegMediaAdapter()

    with pytest.raises(NoAudioStreamError):
        adapter.extract_audio(
            str(_NO_AUDIO_STREAM_FIXTURE), str(tmp_path / "out.wav")
        )
