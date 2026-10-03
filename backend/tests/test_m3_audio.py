"""
backend/tests/test_m3_audio.py — M3 automated test suite.

Tests validate:
  - payload validation (empty, too_small, too_large, invalid_format)
  - successful WAV → PCM conversion
  - successful WebM/Opus → PCM conversion
  - error handling (corrupted WebM, random bytes)
  - PCM output properties (sample rate, channels, format, bit width)
  - conversion latency is measured and reasonable

Audio generation:
  - WAV files are synthesised in pure Python (no external tools).
  - WebM/Opus files are synthesised with PyAV (same library we test against).
  - No real microphone recordings are used or stored.
"""

from __future__ import annotations

import io
import math
import struct

import pytest
import av

from audio.processor import (
    MAX_PAYLOAD_BYTES,
    MIN_PAYLOAD_BYTES,
    TARGET_CHANNELS,
    TARGET_FORMAT,
    TARGET_SAMPLE_RATE,
    ProcessedAudio,
    ProcessingError,
    convert,
    validate,
)


# ============================================================
# Audio generation helpers (no external tools needed)
# ============================================================

def _make_wav(
    duration_ms: int = 500,
    sample_rate: int = 16_000,
    frequency: float = 440.0,
) -> bytes:
    """
    Generate a valid mono PCM WAV file entirely in Python.
    No external libraries or binaries required.
    """
    num_samples = int(sample_rate * duration_ms / 1_000)
    samples = [
        int(32_767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for i in range(num_samples)
    ]
    audio_data = struct.pack(f"<{num_samples}h", *samples)
    data_size = len(audio_data)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,         # file size − 8
        b"WAVE",
        b"fmt ",
        16,                     # fmt chunk size
        1,                      # PCM format
        1,                      # mono
        sample_rate,
        sample_rate * 2,        # byte rate = sample_rate * channels * bits/8
        2,                      # block align = channels * bits/8
        16,                     # bits per sample
        b"data",
        data_size,
    )
    return header + audio_data


def _make_webm_opus(duration_ms: int = 500) -> bytes:
    """
    Generate a minimal WebM/Opus file using PyAV (in-memory only).
    The output starts with the EBML WebM magic bytes.
    """
    sample_rate = 16_000
    num_samples = int(sample_rate * duration_ms / 1_000)

    # Build a sine wave as float32 (fltp = float planar)
    sine_bytes = struct.pack(
        f"<{num_samples}f",
        *(
            math.sin(2 * math.pi * 440 * i / sample_rate) * 0.5
            for i in range(num_samples)
        ),
    )

    buf = io.BytesIO()
    container = av.open(buf, mode="w", format="webm")
    stream = container.add_stream("libopus", rate=sample_rate)
    stream.layout = "mono"

    frame = av.AudioFrame(format="fltp", layout="mono", samples=num_samples)
    frame.sample_rate = sample_rate
    frame.pts = 0
    frame.planes[0].update(sine_bytes)

    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode(None):   # flush encoder
        container.mux(packet)

    container.close()
    webm_bytes = buf.getvalue()
    assert webm_bytes[:4] == b"\x1a\x45\xdf\xa3", "Helper must produce a WebM file"
    return webm_bytes


# ============================================================
# TEST CLASS 1 — Payload validation
# ============================================================

class TestValidation:
    """validate() is the fast pre-flight check before expensive decoding."""

    def test_empty_payload_rejected(self):
        err = validate(b"")
        assert isinstance(err, ProcessingError)
        assert err.code == "empty"

    def test_too_small_payload_rejected(self):
        err = validate(b"\x00" * (MIN_PAYLOAD_BYTES - 1))
        assert isinstance(err, ProcessingError)
        assert err.code == "too_small"

    def test_oversized_payload_rejected_before_decoding(self):
        # Use a valid EBML header so the format check passes —
        # the size check must still reject it.
        oversized = b"\x1a\x45\xdf\xa3" + b"\x00" * MAX_PAYLOAD_BYTES
        err = validate(oversized)
        assert isinstance(err, ProcessingError)
        assert err.code == "too_large"

    def test_random_bytes_invalid_format(self):
        err = validate(b"\xde\xad\xbe\xef" * 100)
        assert isinstance(err, ProcessingError)
        assert err.code == "invalid_format"

    def test_mp3_like_bytes_invalid_format(self):
        # MP3 sync word — not a supported container
        err = validate(b"\xff\xfb\x90\x00" + b"\x00" * 200)
        assert isinstance(err, ProcessingError)
        assert err.code == "invalid_format"

    def test_valid_wav_header_passes(self):
        wav = _make_wav()
        assert validate(wav) is None

    def test_valid_webm_header_passes(self):
        # We only need valid magic bytes at this stage
        webm_prefix = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
        assert validate(webm_prefix) is None

    def test_valid_ogg_header_passes(self):
        ogg_prefix = b"OggS" + b"\x00" * 32
        assert validate(ogg_prefix) is None


# ============================================================
# TEST CLASS 2 — WAV conversion (no codec dependency)
# ============================================================

class TestConversionWAV:
    """
    WAV conversion uses only the RIFF decoder built into PyAV —
    no Opus codec required.  These tests verify the full pipeline
    without codec-specific concerns.
    """

    def test_valid_wav_returns_processed_audio(self):
        wav = _make_wav(duration_ms=500)
        result = convert(wav)
        assert isinstance(result, ProcessedAudio), (
            f"Expected ProcessedAudio, got {result!r}"
        )

    def test_output_sample_rate_is_16k(self):
        result = convert(_make_wav(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.sample_rate == TARGET_SAMPLE_RATE

    def test_output_is_mono(self):
        result = convert(_make_wav(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.channels == TARGET_CHANNELS

    def test_output_format_is_s16(self):
        result = convert(_make_wav(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.sample_format == TARGET_FORMAT

    def test_pcm_bytes_are_even_width(self):
        """s16 = 2 bytes per sample; total byte count must be even."""
        result = convert(_make_wav(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert len(result.pcm_bytes) % 2 == 0

    def test_pcm_non_empty(self):
        result = convert(_make_wav(duration_ms=200))
        assert isinstance(result, ProcessedAudio)
        assert len(result.pcm_bytes) > 0

    def test_duration_approximately_correct(self):
        """500 ms of audio must produce ~500 ms of PCM (±50 ms tolerance)."""
        result = convert(_make_wav(duration_ms=500, sample_rate=16_000))
        assert isinstance(result, ProcessedAudio)
        assert abs(result.duration_ms - 500.0) < 50.0

    def test_pcm_byte_count_approximately_correct(self):
        """500 ms @ 16 kHz s16 mono = 8000 samples × 2 bytes = 16 000 bytes."""
        result = convert(_make_wav(duration_ms=500, sample_rate=16_000))
        assert isinstance(result, ProcessedAudio)
        expected = int(0.5 * TARGET_SAMPLE_RATE * 2)   # 16 000
        assert abs(len(result.pcm_bytes) - expected) < 2_000


# ============================================================
# TEST CLASS 3 — WebM/Opus conversion
# ============================================================

class TestConversionWebM:
    """Verify the primary browser audio format (WebM/Opus) round-trips."""

    def test_valid_webm_returns_processed_audio(self):
        webm = _make_webm_opus(duration_ms=500)
        result = convert(webm)
        assert isinstance(result, ProcessedAudio), (
            f"Expected ProcessedAudio, got {result!r}"
        )

    def test_webm_output_sample_rate(self):
        result = convert(_make_webm_opus(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.sample_rate == TARGET_SAMPLE_RATE

    def test_webm_output_is_mono(self):
        result = convert(_make_webm_opus(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.channels == TARGET_CHANNELS

    def test_webm_output_format_is_s16(self):
        result = convert(_make_webm_opus(duration_ms=300))
        assert isinstance(result, ProcessedAudio)
        assert result.sample_format == TARGET_FORMAT

    def test_webm_duration_approximately_correct(self):
        """
        Opus encodes at 48 kHz internally and adds pre/post-roll padding
        (pre-skip + encoder lookahead).  Decoded duration routinely exceeds
        the source duration by 300–500 ms — this is normal, not a bug.
        We verify that decoded audio covers at least the intended content
        and is not unreasonably long (< 2× input duration).
        """
        result = convert(_make_webm_opus(duration_ms=1_000))
        assert isinstance(result, ProcessedAudio)
        # Must cover at least 90% of the intended content
        assert result.duration_ms >= 900.0, (
            f"Too short: {result.duration_ms:.0f} ms (expected >= 900 ms)"
        )
        # Must not exceed 2× the input duration
        assert result.duration_ms < 2_000.0, (
            f"Too long: {result.duration_ms:.0f} ms (expected < 2000 ms)"
        )


# ============================================================
# TEST CLASS 4 — Error / failure paths
# ============================================================

class TestErrorHandling:
    """Backend must never crash; all errors return ProcessingError."""

    def test_empty_bytes_returns_error(self):
        result = convert(b"")
        assert isinstance(result, ProcessingError)
        assert result.code == "empty"

    def test_random_bytes_returns_error(self):
        result = convert(b"\x00\x01\x02\x03" * 100)
        assert isinstance(result, ProcessingError)

    def test_corrupted_webm_returns_decode_error(self):
        """Valid EBML magic + garbage body → graceful failure."""
        corrupted = b"\x1a\x45\xdf\xa3" + b"\xff\xee\xdd\xcc" * 64
        result = convert(corrupted)
        assert isinstance(result, ProcessingError)
        assert result.code == "decode_failed"

    def test_oversized_payload_returns_error(self):
        oversized = b"\x1a\x45\xdf\xa3" + b"\x00" * MAX_PAYLOAD_BYTES
        result = convert(oversized)
        assert isinstance(result, ProcessingError)
        assert result.code == "too_large"

    def test_convert_never_raises(self):
        """convert() must return — never propagate an exception."""
        inputs = [
            b"",
            b"\xff" * 10,
            b"\x1a\x45\xdf\xa3" + b"\xba\xd0" * 50,
        ]
        for payload in inputs:
            result = convert(payload)
            # If this raises, the test itself fails — which is the correct signal
            assert isinstance(result, (ProcessedAudio, ProcessingError))


# ============================================================
# TEST CLASS 5 — Latency measurement
# ============================================================

class TestLatency:
    """Verify conversion timing is measured and within acceptable bounds."""

    def test_conversion_ms_is_positive(self):
        result = convert(_make_wav(duration_ms=500))
        assert isinstance(result, ProcessedAudio)
        assert result.conversion_ms >= 0

    def test_conversion_ms_is_reasonable(self):
        """
        Converting 1 s of WAV on this machine should complete
        in under 5 seconds — well within real-time requirements.
        """
        result = convert(_make_wav(duration_ms=1_000))
        assert isinstance(result, ProcessedAudio)
        assert result.conversion_ms < 5_000

    def test_webm_conversion_ms_is_reasonable(self):
        result = convert(_make_webm_opus(duration_ms=1_000))
        assert isinstance(result, ProcessedAudio)
        assert result.conversion_ms < 5_000
