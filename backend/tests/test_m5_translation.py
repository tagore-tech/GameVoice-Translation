"""
backend/tests/test_m5_translation.py — M5

Tests for the M5 translation pipeline.

ALL TESTS ARE DETERMINISTIC AND OFFLINE.
No external translation API is called.

Test classes:
  TestDeepTranslatorProviderConstruction  — import/init (no network)
  TestMockTranslationInPipeline           — mock provider end-to-end
  TestTranslationProviderFactory          — factory resolves deep_translator
  TestSameLanguageHandling                — passthrough behavior
  TestTranslationResultContract           — structure validation
  TestTranslationErrorHandling            — failure paths
  TestWebSocketMessageSchema              — response message structure
  TestM4Regression                        — M4 speech tests still pass
  TestM3Regression                        — M3 audio tests still pass
"""

from __future__ import annotations

import math
import struct
import time
from unittest.mock import MagicMock

import pytest

from languages import SUPPORTED_LANGUAGES, validate_language_code
from translation.base import TranslationProvider, TranslationResult
from translation.mock import MockTranslationProvider
from translation.provider import get_translation_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pcm(duration_ms: int = 800, sample_rate: int = 16_000) -> bytes:
    """Generate synthetic 16kHz mono s16 PCM bytes."""
    n = int(sample_rate * duration_ms / 1_000)
    samples = [
        int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate))
        for i in range(n)
    ]
    return struct.pack(f"<{n}h", *samples)


def _make_processed_audio(duration_ms: int = 800):
    """Return a ProcessedAudio-like mock for translation tests."""
    from audio.processor import ProcessedAudio
    pcm = _make_pcm(duration_ms)
    return ProcessedAudio(
        pcm_bytes=pcm,
        sample_rate=16_000,
        channels=1,
        sample_format="s16",
        duration_ms=float(duration_ms),
        conversion_ms=5.0,
    )


# ============================================================
# TEST CLASS 1 — DeepTranslatorProvider construction (offline)
# ============================================================

class TestDeepTranslatorProviderConstruction:
    """
    Verify that the module can be imported and instantiated.
    Does NOT call the network.
    """

    def test_deep_translator_module_importable(self):
        """The deep-translator library must be installed."""
        import deep_translator  # noqa: F401

    def test_google_translator_class_importable(self):
        from deep_translator import GoogleTranslator  # noqa: F401

    def test_provider_class_importable(self):
        from translation.deep_translator_provider import DeepTranslatorProvider  # noqa: F401

    def test_provider_instantiates(self):
        from translation.deep_translator_provider import DeepTranslatorProvider
        provider = DeepTranslatorProvider()
        assert provider is not None

    def test_provider_is_translation_provider(self):
        from translation.deep_translator_provider import DeepTranslatorProvider
        assert issubclass(DeepTranslatorProvider, TranslationProvider)

    def test_provider_name(self):
        from translation.deep_translator_provider import DeepTranslatorProvider
        provider = DeepTranslatorProvider()
        assert provider.name == "deep_translator"

    def test_empty_input_no_network(self):
        """Empty input must return no_input without any network call."""
        from translation.deep_translator_provider import DeepTranslatorProvider
        provider = DeepTranslatorProvider()
        result = provider.translate("", "hi", "te")
        assert result.status == "no_input"
        assert result.translated_text == ""

    def test_same_language_no_network(self):
        """Same-language passthrough must not make any network call."""
        from translation.deep_translator_provider import DeepTranslatorProvider
        provider = DeepTranslatorProvider()
        result = provider.translate("Hello there", "en", "en")
        assert result.status == "ok"
        assert result.translated_text == "Hello there"

    def test_whitespace_only_no_network(self):
        from translation.deep_translator_provider import DeepTranslatorProvider
        provider = DeepTranslatorProvider()
        result = provider.translate("   ", "hi", "te")
        assert result.status == "no_input"


# ============================================================
# TEST CLASS 2 — Mock translation in pipeline (fully offline)
# ============================================================

class TestMockTranslationInPipeline:

    def setup_method(self):
        self.provider = MockTranslationProvider()

    def test_hi_to_te_returns_ok(self):
        result = self.provider.translate("Dushman left side pe hai", "hi", "te")
        assert result.status == "ok"

    def test_en_to_hi_returns_ok(self):
        result = self.provider.translate("Enemy spotted", "en", "hi")
        assert result.status == "ok"

    def test_translation_result_has_all_fields(self):
        result = self.provider.translate("test", "hi", "te")
        assert result.source_language == "hi"
        assert result.target_language == "te"
        assert result.provider == "mock"
        assert isinstance(result.translation_latency_ms, float)
        assert result.translation_latency_ms >= 0.0

    def test_source_text_preserved_in_result(self):
        text = "Enemy spotted on the left"
        result = self.provider.translate(text, "en", "hi")
        assert result.source_text == text

    def test_translated_text_not_empty_on_ok(self):
        result = self.provider.translate("Test input text", "en", "hi")
        assert len(result.translated_text) > 0

    def test_all_language_pairs_ok(self):
        codes = [l.code for l in SUPPORTED_LANGUAGES]
        for src in codes:
            for tgt in codes:
                result = self.provider.translate("test", src, tgt)
                assert result.status == "ok", f"Failed: {src}→{tgt}"

    def test_empty_input_status(self):
        result = self.provider.translate("", "hi", "te")
        assert result.status == "no_input"
        assert result.succeeded is False

    def test_same_language_passthrough(self):
        text = "Enemy spotted"
        result = self.provider.translate(text, "en", "en")
        assert result.status == "ok"
        assert result.translated_text == text

    def test_translate_never_raises(self):
        for _ in range(10):
            result = self.provider.translate("test", "hi", "te")
            assert isinstance(result, TranslationResult)


# ============================================================
# TEST CLASS 3 — Provider factory (M5 — both providers)
# ============================================================

class TestTranslationProviderFactoryM5:

    def test_mock_still_loads(self):
        provider = get_translation_provider("mock")
        assert provider.name == "mock"

    def test_deep_translator_loads(self):
        provider = get_translation_provider("deep_translator")
        assert provider.name == "deep_translator"

    def test_deep_translator_is_translation_provider(self):
        provider = get_translation_provider("deep_translator")
        assert isinstance(provider, TranslationProvider)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown TRANSLATION_PROVIDER"):
            get_translation_provider("nonexistent_provider_xyz")

    def test_factory_error_message_lists_valid_values(self):
        with pytest.raises(ValueError) as exc:
            get_translation_provider("bad")
        assert "mock" in str(exc.value)
        assert "deep_translator" in str(exc.value)


# ============================================================
# TEST CLASS 4 — Same-language behavior
# ============================================================

class TestSameLanguageHandling:

    def setup_method(self):
        self.mock = MockTranslationProvider()

    def test_same_language_preserved_en(self):
        text = "Cover me, I am reloading"
        r = self.mock.translate(text, "en", "en")
        assert r.translated_text == text

    def test_same_language_preserved_hi(self):
        text = "Dushman aa raha hai"
        r = self.mock.translate(text, "hi", "hi")
        assert r.translated_text == text

    def test_same_language_preserved_te(self):
        text = "Left vaipu lo enemy unnadu"
        r = self.mock.translate(text, "te", "te")
        assert r.translated_text == text

    def test_same_language_preserved_ta(self):
        text = "Enemy idam pakkam irukkiratu"
        r = self.mock.translate(text, "ta", "ta")
        assert r.translated_text == text

    def test_same_language_deep_translator_no_network(self):
        from translation.deep_translator_provider import DeepTranslatorProvider
        p = DeepTranslatorProvider()
        text = "Hello world"
        r = p.translate(text, "en", "en")
        assert r.status == "ok"
        assert r.translated_text == text


# ============================================================
# TEST CLASS 5 — TranslationResult structure
# ============================================================

class TestTranslationResultContractM5:

    def setup_method(self):
        self.provider = MockTranslationProvider()

    def test_ok_result_structure(self):
        r = self.provider.translate("Enemy spotted", "en", "hi")
        assert r.status == "ok"
        assert r.succeeded is True
        assert r.source_language == "en"
        assert r.target_language == "hi"
        assert r.provider == "mock"
        assert isinstance(r.translation_latency_ms, float)
        assert isinstance(r.translated_text, str)
        assert isinstance(r.source_text, str)

    def test_failure_result_structure(self):
        r = self.provider.translate("", "en", "hi")
        assert r.status == "no_input"
        assert r.succeeded is False
        assert r.translated_text == ""

    def test_result_is_immutable(self):
        r = self.provider.translate("test", "en", "hi")
        with pytest.raises((AttributeError, TypeError)):
            r.status = "mutated"  # type: ignore[misc]

    def test_source_text_always_present(self):
        """source_text must be present even on failure (for fallback display)."""
        text = "Some input text"
        r = self.provider.translate(text, "en", "hi")
        # On success, source_text is set
        assert r.source_text == text

    def test_latency_is_non_negative(self):
        r = self.provider.translate("test", "hi", "te")
        assert r.translation_latency_ms >= 0.0


# ============================================================
# TEST CLASS 6 — Translation error handling
# ============================================================

class TestTranslationErrorHandling:

    def setup_method(self):
        self.provider = MockTranslationProvider()

    def test_empty_text_error(self):
        r = self.provider.translate("", "hi", "te")
        assert r.status == "no_input"
        assert r.error_message is not None

    def test_whitespace_only_error(self):
        r = self.provider.translate("   \t\n", "hi", "te")
        assert r.status == "no_input"

    def test_provider_error_pattern(self):
        """Simulate a provider_error via TranslationResult directly."""
        r = TranslationResult(
            status="provider_error",
            source_language="hi",
            target_language="te",
            provider="mock",
            translation_latency_ms=5.0,
            translated_text="",
            error_message="Simulated provider failure",
            source_text="original text here",
        )
        assert r.succeeded is False
        assert r.source_text == "original text here"   # fallback display works
        assert r.error_message == "Simulated provider failure"

    def test_all_error_statuses_are_not_ok(self):
        for status in ("no_input", "unsupported_pair", "provider_error",
                       "timeout", "auth_error", "quota_exceeded"):
            r = TranslationResult(
                status=status,
                source_language="en",
                target_language="hi",
                provider="mock",
                translation_latency_ms=0.0,
                source_text="original",
            )
            assert r.succeeded is False


# ============================================================
# TEST CLASS 7 — WebSocket message schema (offline simulation)
# ============================================================

class TestWebSocketMessageSchema:
    """
    Simulates the message-building logic in ws/handler.py
    to verify the schema before live tests.
    """

    def _build_translation_msg(self, source_text: str, translated_text: str,
                                src: str = "hi", tgt: str = "te") -> dict:
        return {
            "type": "translation",
            "source_language": src,
            "target_language": tgt,
            "source_text": source_text,
            "translated_text": translated_text,
            "provider": "mock",
            "confidence": 0.99,
            "audio_duration_ms": 800.0,
            "audio_conversion_ms": 14.5,
            "stt_latency_ms": 0.1,
            "translation_latency_ms": 1.2,
            "total_latency_ms": 15.8,
        }

    def _build_translation_error_msg(self, source_text: str) -> dict:
        return {
            "type": "translation_error",
            "code": "provider_error",
            "message": "Connection refused",
            "provider": "mock",
            "source_language": "hi",
            "target_language": "te",
            "source_text": source_text,
            "audio_conversion_ms": 14.5,
            "stt_latency_ms": 0.1,
            "total_latency_ms": 14.6,
        }

    def test_translation_msg_type(self):
        msg = self._build_translation_msg("source", "translated")
        assert msg["type"] == "translation"

    def test_translation_msg_has_source_and_target_language(self):
        msg = self._build_translation_msg("source", "translated")
        assert "source_language" in msg
        assert "target_language" in msg

    def test_translation_msg_has_texts(self):
        msg = self._build_translation_msg("original", "result")
        assert msg["source_text"] == "original"
        assert msg["translated_text"] == "result"

    def test_translation_msg_has_latency_fields(self):
        msg = self._build_translation_msg("s", "t")
        assert "audio_conversion_ms" in msg
        assert "stt_latency_ms" in msg
        assert "translation_latency_ms" in msg
        assert "total_latency_ms" in msg

    def test_translation_error_has_source_text(self):
        """source_text must always be in translation_error for fallback display."""
        msg = self._build_translation_error_msg("Enemy spotted")
        assert msg["source_text"] == "Enemy spotted"
        assert msg["type"] == "translation_error"

    def test_translation_error_has_code(self):
        msg = self._build_translation_error_msg("test")
        assert "code" in msg
        assert "message" in msg


# ============================================================
# TEST CLASS 8 — Configuration (M5)
# ============================================================

class TestConfigM5:

    def test_settings_has_translation_provider(self):
        from config import settings
        assert hasattr(settings, "translation_provider")

    def test_settings_has_source_language(self):
        from config import settings
        assert hasattr(settings, "source_language")

    def test_settings_has_target_language(self):
        from config import settings
        assert hasattr(settings, "target_language")

    def test_translation_provider_is_configured(self):
        from config import settings
        # Translation provider must be one of the valid options.
        # Default (no .env): "mock". With backend/.env: whatever is set.
        assert settings.translation_provider in ("mock", "deep_translator", "gemini", "google"), (
            f"Unexpected translation_provider: {settings.translation_provider!r}"
        )

    def test_language_codes_are_valid(self):
        from config import settings
        validate_language_code(settings.source_language)
        validate_language_code(settings.target_language)


# ============================================================
# TEST CLASS 9 — M4 regression
# ============================================================

class TestM4Regression:

    def test_speech_provider_loads(self):
        from speech.provider import get_speech_provider
        provider = get_speech_provider("mock")
        assert provider is not None

    def test_transcription_result_structure(self):
        from speech.base import TranscriptionResult
        r = TranscriptionResult(
            status="ok",
            text="Enemy spotted",
            language="en",
            provider="mock",
            stt_latency_ms=1.0,
            confidence=0.99,
        )
        assert r.status == "ok"
        assert r.text == "Enemy spotted"

    def test_mock_stt_transcribes_english(self):
        from speech.mock import MockSpeechProvider
        from audio.processor import ProcessedAudio
        provider = MockSpeechProvider()
        audio = ProcessedAudio(
            pcm_bytes=_make_pcm(800),
            sample_rate=16_000,
            channels=1,
            sample_format="s16",
            duration_ms=800.0,
            conversion_ms=5.0,
        )
        result = provider.transcribe(audio, language="en")
        assert result.status == "ok"
        assert len(result.text) > 0


# ============================================================
# TEST CLASS 10 — M3 regression
# ============================================================

class TestM3Regression:

    def test_audio_processor_imports(self):
        from audio import processor  # noqa: F401

    def test_convert_never_raises_on_garbage(self):
        from audio.processor import convert
        result = convert(b"\x00\x01\x02\x03garbage" * 100)
        # Must return without raising
        assert result is not None

    def test_convert_valid_wav(self):
        import math, struct
        from audio.processor import convert, ProcessedAudio

        sample_rate = 16_000
        n = int(sample_rate * 0.5)
        samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
        audio_data = struct.pack(f"<{n}h", *samples)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(audio_data), b"WAVE",
            b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
            b"data", len(audio_data),
        )
        result = convert(header + audio_data)
        assert isinstance(result, ProcessedAudio)
        assert result.sample_rate == 16_000
