"""Tests for the M1-03 Canonical Audio internal contract.

These validate the M1-03 acceptance criteria that can be checked without
FFmpeg/ffprobe: sample rate, channel count, and sample representation are
defined; the amplitude-normalization decision is explicit; each parameter
carries a recorded technical justification; and the contract is frozen
(immutable) and does not depend on ffmpeg/ffprobe or the FFmpegMediaAdapter,
since it is a pure data contract, not a media-transformation implementation.
"""

import ast
import dataclasses
from pathlib import Path

import pytest

from audio_cue_locator.infrastructure.media_processing import (
    CANONICAL_AUDIO_JUSTIFICATIONS,
    CANONICAL_AUDIO_SPEC,
    PENDING_LIVE_PROBING_VERIFICATION,
    CanonicalAudioSpec,
    NormalizationPolicy,
    ParameterJustification,
)

_CANONICAL_AUDIO_MODULE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "audio_cue_locator"
    / "infrastructure"
    / "media_processing"
    / "canonical_audio.py"
)

_SPEC_FIELDS = ("sample_rate_hz", "channels", "sample_format", "normalization")


def test_canonical_audio_spec_defines_required_characteristics():
    for name in _SPEC_FIELDS:
        assert hasattr(CANONICAL_AUDIO_SPEC, name)

    assert isinstance(CANONICAL_AUDIO_SPEC.sample_rate_hz, int)
    assert CANONICAL_AUDIO_SPEC.sample_rate_hz > 0
    assert isinstance(CANONICAL_AUDIO_SPEC.channels, int)
    assert CANONICAL_AUDIO_SPEC.channels > 0
    assert isinstance(CANONICAL_AUDIO_SPEC.sample_format, str)
    assert CANONICAL_AUDIO_SPEC.sample_format != ""
    assert isinstance(CANONICAL_AUDIO_SPEC.normalization, NormalizationPolicy)


def test_normalization_decision_is_explicit():
    normalization = CANONICAL_AUDIO_SPEC.normalization
    assert isinstance(normalization.enabled, bool)
    assert isinstance(normalization.method, str) and normalization.method != ""
    assert isinstance(normalization.target_peak_amplitude, float)


def test_every_parameter_has_a_recorded_technical_justification():
    for name in _SPEC_FIELDS:
        justification = CANONICAL_AUDIO_JUSTIFICATIONS[name]
        assert isinstance(justification, ParameterJustification)
        assert justification.rationale.strip() != ""
        assert justification.source.strip() != ""
        assert justification.revisable is True


def test_canonical_audio_spec_and_normalization_policy_are_frozen():
    assert dataclasses.is_dataclass(CANONICAL_AUDIO_SPEC)
    with pytest.raises(dataclasses.FrozenInstanceError):
        CANONICAL_AUDIO_SPEC.sample_rate_hz = 44100
    with pytest.raises(dataclasses.FrozenInstanceError):
        CANONICAL_AUDIO_SPEC.normalization.enabled = False


def test_canonical_audio_spec_type_is_frozen_dataclass():
    fields = {f.name for f in dataclasses.fields(CanonicalAudioSpec)}
    assert fields == set(_SPEC_FIELDS)
    assert CanonicalAudioSpec.__dataclass_params__.frozen is True


def test_pending_live_probing_verification_is_documented():
    assert isinstance(PENDING_LIVE_PROBING_VERIFICATION, str)
    assert "ffmpeg" in PENDING_LIVE_PROBING_VERIFICATION.lower()
    assert "ffprobe" in PENDING_LIVE_PROBING_VERIFICATION.lower()


def test_canonical_audio_module_does_not_import_ffmpeg_execution_machinery():
    """Guards the contract/implementation boundary: canonical_audio.py must
    stay a pure data contract and must not couple to ffmpeg_adapter.py or
    subprocess, since actually applying the transformation is out of scope
    for this issue (docs/architecture.md, Canonical Audio)."""
    tree = ast.parse(_CANONICAL_AUDIO_MODULE.read_text(encoding="utf-8"))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden = {"subprocess", "ffmpeg_adapter", ".ffmpeg_adapter"}
    assert not (imported_names & forbidden)
