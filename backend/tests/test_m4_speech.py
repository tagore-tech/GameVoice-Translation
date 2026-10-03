"""
backend/tests/test_m4_speech.py — M4 automated test suite.

Tests validate:
  - SpeechProvider interface contract
  - MockSpeechProvider behaviour (all four languages)
  - TranscriptionResult fields
  - Empty PCM rejection
  - Short PCM rejection
  - Unsupported language handling
  - Provider factory loading
  - Unknown provider raises ValueError
  - Language rotation (different phrases on successive calls)
  - PCM → transcription end-to-end integration (no network)
  - M3 regression: audio.processor still works correctly

All tests are deterministic.  No network calls, no API keys.
"""

from __future__ import annotations

import math
import struct
import time

import pytest

from audio.processor import (
    ProcessedAudio,
    TARGET_CHANNELS,
    TARGET_FORMAT,
    TARGET_SAMPLE_RATE,
    convert,
)
from speech.base import SpeechProvider, TranscriptionResult
from speech.mock import MockSpeechProvider, _MIN_PCM_BYTES
from speech.provider import get_speech_provider


# ============================================================
# Helpers — generate deterministic PCM for tests
# ============================================================

def _make_pcm(duration_ms: int = 500, sample_rate: int = 16_000) -> ProcessedAudio:
    """Return a ProcessedAudio with a synthetic sine-wave PCM payload."""
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    pcm = struct.pack(f"<{n}h", *samples)
    return ProcessedAudio(
        pcm_bytes=pcm,
        sample_rate=sample_rate,
        channels=1,
        sample_format="s16",
        duration_ms=duration_ms,
        conversion_ms=1.0,
    )


def _make_empty_pcm() -> ProcessedAudio:
    return ProcessedAudio(
        pcm_bytes=b"",
        sample_rate=TARGET_SAMPLE_RATE,
        channels=TARGET_CHANNELS,
        sample_format=TARGET_FORMAT,
        duration_ms=0.0,
        conversion_ms=0.0,
    )


def _make_short_pcm(bytes_count: int = 10) -> ProcessedAudio:
    return ProcessedAudio(
        pcm_bytes=b"\x00" * bytes_count,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=TARGET_CHANNELS,
        sample_format=TARGET_FORMAT,
        duration_ms=0.3,
        conversion_ms=0.0,
    )


# ============================================================
# TEST CLASS 1 — Provider factory
# ============================================================

class TestProviderFactory:

    def test_mock_provider_loads(self):
        provider = get_speech_provider("mock")
        assert isinstance(provider, SpeechProvider)

    def test_mock_provider_name(self):
        provider = get_speech_provider("mock")
        assert provider.name == "mock"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown STT_PROVIDER"):
            get_speech_provider("nonexistent_provider")

    def test_mock_is_speech_provider_subclass(self):
        assert issubclass(MockSpeechProvider, SpeechProvider)


# ============================================================
# TEST CLASS 2 — MockSpeechProvider language support
# ============================================================

class TestMockLanguageSupport:

    def setup_method(self):
        self.provider = MockSpeechProvider()

    def test_supports_english(self):
        assert self.provider.supports_language("en")

    def test_supports_hindi(self):
        assert self.provider.supports_language("hi")

    def test_supports_telugu(self):
        assert self.provider.supports_language("te")

    def test_supports_tamil(self):
        assert self.provider.supports_language("ta")

    def test_does_not_support_unknown_language(self):
        assert not self.provider.supports_language("zz")

    def test_unsupported_language_returns_error_status(self):
        audio = _make_pcm(duration_ms=500)
        result = self.provider.transcribe(audio, language="zz")
        assert isinstance(result, TranscriptionResult)
        assert result.status == "unsupported_language"
        assert result.text == ""
        assert "zz" in (result.error_message or "")


# ============================================================
# TEST CLASS 3 — MockSpeechProvider transcription results
# ============================================================

class TestMockTranscription:

    def setup_method(self):
        self.provider = MockSpeechProvider()
        self.audio = _make_pcm(duration_ms=500)

    def test_english_transcription_ok(self):
        result = self.provider.transcribe(self.audio, language="en")
        assert result.status == "ok"
        assert isinstance(result.text, str)
        assert len(result.text) > 0

    def test_hindi_transcription_ok(self):
        result = self.provider.transcribe(self.audio, language="hi")
        assert result.status == "ok"
        assert len(result.text) > 0

    def test_telugu_transcription_ok(self):
        result = self.provider.transcribe(self.audio, language="te")
        assert result.status == "ok"
        assert len(result.text) > 0

    def test_tamil_transcription_ok(self):
        result = self.provider.transcribe(self.audio, language="ta")
        assert result.status == "ok"
        assert len(result.text) > 0

    def test_provider_field_is_mock(self):
        result = self.provider.transcribe(self.audio, language="en")
        assert result.provider == "mock"

    def test_language_field_is_preserved(self):
        result = self.provider.transcribe(self.audio, language="hi")
        assert result.language == "hi"

    def test_confidence_is_set(self):
        result = self.provider.transcribe(self.audio, language="en")
        assert result.confidence is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_stt_latency_is_non_negative(self):
        result = self.provider.transcribe(self.audio, language="en")
        assert result.stt_latency_ms >= 0.0

    def test_phrases_rotate_across_calls(self):
        """Successive calls must not always return the same phrase."""
        results = [
            self.provider.transcribe(self.audio, language="en").text
            for _ in range(6)
        ]
        # With 5 phrases and 6 calls, we must see at least 2 distinct texts
        assert len(set(results)) >= 2

    def test_transcription_never_raises(self):
        """transcribe() must return a value, never raise."""
        for lang in ("en", "hi", "te", "ta", "zz", ""):
            result = self.provider.transcribe(self.audio, language=lang)
            assert isinstance(result, TranscriptionResult)


# ============================================================
# TEST CLASS 4 — Edge cases: empty / short PCM
# ============================================================

class TestEdgeCases:

    def setup_method(self):
        self.provider = MockSpeechProvider()

    def test_empty_pcm_returns_empty_audio_status(self):
        result = self.provider.transcribe(_make_empty_pcm(), language="en")
        assert result.status == "empty_audio"
        assert result.text == ""

    def test_too_short_pcm_returns_empty_audio(self):
        result = self.provider.transcribe(
            _make_short_pcm(_MIN_PCM_BYTES - 1), language="en"
        )
        assert result.status == "empty_audio"

    def test_exactly_min_pcm_succeeds(self):
        audio = _make_short_pcm(_MIN_PCM_BYTES)
        result = self.provider.transcribe(audio, language="en")
        # Exact minimum is accepted by mock
        assert result.status == "ok"

    def test_empty_pcm_does_not_crash(self):
        result = self.provider.transcribe(_make_empty_pcm(), language="en")
        assert isinstance(result, TranscriptionResult)


# ============================================================
# TEST CLASS 5 — PCM→STT integration (no real audio needed)
# ============================================================

class TestPCMToSTTIntegration:
    """
    Tests the full path: ProcessedAudio (from audio.processor) →
    SpeechProvider.transcribe() → TranscriptionResult.
    Verifies M3 output feeds M4 correctly.
    """

    def setup_method(self):
        self.provider = MockSpeechProvider()

    def test_pcm_from_processor_feeds_provider(self):
        """
        Generate a WAV, run it through audio.processor.convert(),
        then pass the resulting ProcessedAudio to the mock provider.
        The full M3→M4 path must succeed.
        """
        import io, math, struct, av

        # Build a minimal WAV in-memory
        sample_rate = 16_000
        n = sample_rate // 2   # 500 ms
        samples = [int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
        audio_data = struct.pack(f"<{n}h", *samples)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(audio_data), b"WAVE",
            b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
            b"data", len(audio_data),
        )
        wav_bytes = header + audio_data

        # M3: convert
        from audio.processor import ProcessedAudio as PA, ProcessingError
        pcm_result = convert(wav_bytes)
        assert isinstance(pcm_result, PA), f"M3 conversion failed: {pcm_result}"

        # M4: transcribe
        stt_result = self.provider.transcribe(pcm_result, language="en")
        assert stt_result.status == "ok"
        assert len(stt_result.text) > 0

    def test_output_has_correct_latency_fields(self):
        audio = _make_pcm(duration_ms=500)
        result = self.provider.transcribe(audio, language="en")
        assert hasattr(result, "stt_latency_ms")
        assert result.stt_latency_ms >= 0

    def test_all_four_languages_end_to_end(self):
        audio = _make_pcm(duration_ms=500)
        for lang in ("en", "hi", "te", "ta"):
            result = self.provider.transcribe(audio, language=lang)
            assert result.status == "ok", f"Failed for language {lang!r}"
            assert result.language == lang


# ============================================================
# TEST CLASS 6 — M3 regression (audio.processor still intact)
# ============================================================

class TestM3Regression:
    """Confirm M3 core functionality is untouched by M4 changes."""

    def test_convert_valid_wav_still_works(self):
        import math, struct
        n = 8_000
        samples = [int(32767 * math.sin(2 * math.pi * 440 * i / 16_000)) for i in range(n)]
        audio_data = struct.pack(f"<{n}h", *samples)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(audio_data), b"WAVE",
            b"fmt ", 16, 1, 1, 16_000, 32_000, 2, 16,
            b"data", len(audio_data),
        )
        result = convert(header + audio_data)
        assert isinstance(result, ProcessedAudio)
        assert result.sample_rate == TARGET_SAMPLE_RATE
        assert result.channels == TARGET_CHANNELS
        assert result.sample_format == TARGET_FORMAT

    def test_convert_empty_returns_error(self):
        from audio.processor import ProcessingError
        result = convert(b"")
        assert isinstance(result, ProcessingError)
        assert result.code == "empty"

    def test_convert_never_raises_on_garbage(self):
        from audio.processor import ProcessingError
        result = convert(b"\xff\xfe" * 200)
        assert isinstance(result, (ProcessedAudio, ProcessingError))
