"""
backend/tests/test_pre_m5.py — PRE-M5 architecture tests.

Tests verify:
  1.  Supported language codes are accepted (validate_language_code).
  2.  Unsupported language codes are rejected (raises ValueError).
  3.  Source and target languages are represented separately (ParticipantLanguageConfig).
  4.  Same-language configuration is handled deterministically.
  5.  needs_translation property is correct.
  6.  Language metadata (name, native_name) is accessible.
  7.  TranslationProvider factory loads the mock provider.
  8.  Unknown provider raises ValueError.
  9.  MockTranslationProvider translates (returns placeholder, status=ok).
 10.  MockTranslationProvider handles same-language passthrough.
 11.  MockTranslationProvider handles empty input (status=no_input).
 12.  TranslationResult structure has all required fields.
 13.  TranslationResult.succeeded is True for ok, False otherwise.
 14.  translate() never raises.
 15.  All four languages translate to each other without error.
 16.  Existing M4 speech tests still pass (import sanity).
 17.  Existing M3 audio tests still pass (import sanity).
 18.  Config has source_language and target_language attributes.
 19.  Settings defaults do not break M4 behaviour.
 20.  Backend package imports succeed (startup smoke test).

All tests are deterministic and offline.  No external API calls.
"""

from __future__ import annotations

import pytest

from languages import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_CODES,
    Language,
    ParticipantLanguageConfig,
    get_language,
    is_supported,
    validate_language_code,
)
from translation.base import TranslationProvider, TranslationResult
from translation.mock import MockTranslationProvider
from translation.provider import get_translation_provider


# ============================================================
# TEST CLASS 1 — Language registry
# ============================================================

class TestLanguageRegistry:

    def test_supported_languages_not_empty(self):
        assert len(SUPPORTED_LANGUAGES) > 0

    def test_all_four_languages_present(self):
        codes = {lang.code for lang in SUPPORTED_LANGUAGES}
        assert {"en", "hi", "te", "ta"}.issubset(codes)

    def test_supported_codes_matches_languages(self):
        expected = {lang.code for lang in SUPPORTED_LANGUAGES}
        assert SUPPORTED_CODES == expected

    def test_language_has_code_name_native_name(self):
        for lang in SUPPORTED_LANGUAGES:
            assert isinstance(lang, Language)
            assert lang.code
            assert lang.name
            assert lang.native_name

    def test_english_metadata(self):
        lang = get_language("en")
        assert lang is not None
        assert lang.name == "English"

    def test_hindi_metadata(self):
        lang = get_language("hi")
        assert lang is not None
        assert lang.name == "Hindi"
        assert lang.native_name == "हिन्दी"

    def test_telugu_metadata(self):
        lang = get_language("te")
        assert lang is not None
        assert lang.name == "Telugu"

    def test_tamil_metadata(self):
        lang = get_language("ta")
        assert lang is not None
        assert lang.name == "Tamil"


# ============================================================
# TEST CLASS 2 — Language validation
# ============================================================

class TestLanguageValidation:

    def test_valid_code_en_accepted(self):
        result = validate_language_code("en")
        assert result.code == "en"

    def test_valid_code_hi_accepted(self):
        result = validate_language_code("hi")
        assert result.code == "hi"

    def test_valid_code_te_accepted(self):
        result = validate_language_code("te")
        assert result.code == "te"

    def test_valid_code_ta_accepted(self):
        result = validate_language_code("ta")
        assert result.code == "ta"

    def test_unknown_code_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported language code"):
            validate_language_code("zz")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_language_code("")

    def test_uppercase_code_raises(self):
        """Language codes are case-sensitive BCP-47 — 'EN' is not valid."""
        with pytest.raises(ValueError):
            validate_language_code("EN")

    def test_is_supported_true_for_valid(self):
        assert is_supported("en") is True
        assert is_supported("hi") is True

    def test_is_supported_false_for_invalid(self):
        assert is_supported("zz") is False
        assert is_supported("") is False

    def test_get_language_returns_none_for_unknown(self):
        assert get_language("xx") is None

    def test_validate_error_message_includes_supported_codes(self):
        with pytest.raises(ValueError) as exc_info:
            validate_language_code("zz")
        error_text = str(exc_info.value)
        assert "en" in error_text
        assert "hi" in error_text


# ============================================================
# TEST CLASS 3 — ParticipantLanguageConfig
# ============================================================

class TestParticipantLanguageConfig:

    def test_valid_config_created(self):
        cfg = ParticipantLanguageConfig(source_language="hi", target_language="te")
        assert cfg.source_language == "hi"
        assert cfg.target_language == "te"

    def test_same_language_config_valid(self):
        """Same source and target is explicitly valid — means no translation needed."""
        cfg = ParticipantLanguageConfig(source_language="en", target_language="en")
        assert cfg.source_language == "en"
        assert cfg.target_language == "en"

    def test_needs_translation_true_when_different(self):
        cfg = ParticipantLanguageConfig(source_language="hi", target_language="te")
        assert cfg.needs_translation is True

    def test_needs_translation_false_when_same(self):
        cfg = ParticipantLanguageConfig(source_language="en", target_language="en")
        assert cfg.needs_translation is False

    def test_source_property_returns_language(self):
        cfg = ParticipantLanguageConfig(source_language="hi", target_language="te")
        assert cfg.source.code == "hi"
        assert cfg.source.name == "Hindi"

    def test_target_property_returns_language(self):
        cfg = ParticipantLanguageConfig(source_language="hi", target_language="te")
        assert cfg.target.code == "te"
        assert cfg.target.name == "Telugu"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError):
            ParticipantLanguageConfig(source_language="zz", target_language="en")

    def test_invalid_target_raises(self):
        with pytest.raises(ValueError):
            ParticipantLanguageConfig(source_language="en", target_language="zz")

    def test_config_is_immutable(self):
        cfg = ParticipantLanguageConfig(source_language="hi", target_language="te")
        with pytest.raises((AttributeError, TypeError)):
            cfg.source_language = "en"  # type: ignore[misc]

    def test_all_valid_language_combinations(self):
        """All source/target combinations from the supported set must be constructable."""
        for src_lang in SUPPORTED_LANGUAGES:
            for tgt_lang in SUPPORTED_LANGUAGES:
                cfg = ParticipantLanguageConfig(
                    source_language=src_lang.code,
                    target_language=tgt_lang.code,
                )
                assert cfg.source_language == src_lang.code
                assert cfg.target_language == tgt_lang.code


# ============================================================
# TEST CLASS 4 — TranslationProvider factory
# ============================================================

class TestTranslationProviderFactory:

    def test_mock_provider_loads(self):
        provider = get_translation_provider("mock")
        assert isinstance(provider, TranslationProvider)

    def test_mock_provider_name(self):
        provider = get_translation_provider("mock")
        assert provider.name == "mock"

    def test_mock_is_translation_provider_subclass(self):
        assert issubclass(MockTranslationProvider, TranslationProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown TRANSLATION_PROVIDER"):
            get_translation_provider("nonexistent")


# ============================================================
# TEST CLASS 5 — MockTranslationProvider behaviour
# ============================================================

class TestMockTranslationProvider:

    def setup_method(self):
        self.provider = MockTranslationProvider()

    def test_basic_translation_returns_ok(self):
        result = self.provider.translate("test input", "en", "hi")
        assert result.status == "ok"

    def test_translated_text_not_empty(self):
        result = self.provider.translate("Enemy spotted", "en", "hi")
        assert len(result.translated_text) > 0

    def test_source_language_preserved(self):
        result = self.provider.translate("text", "hi", "te")
        assert result.source_language == "hi"

    def test_target_language_preserved(self):
        result = self.provider.translate("text", "hi", "te")
        assert result.target_language == "te"

    def test_provider_field_is_mock(self):
        result = self.provider.translate("text", "en", "hi")
        assert result.provider == "mock"

    def test_same_language_passthrough(self):
        result = self.provider.translate("Hello there", "en", "en")
        assert result.status == "ok"
        assert result.translated_text == "Hello there"

    def test_same_language_passthrough_hindi(self):
        text = "Dushman left side pe hai"
        result = self.provider.translate(text, "hi", "hi")
        assert result.translated_text == text

    def test_empty_input_returns_no_input(self):
        result = self.provider.translate("", "en", "hi")
        assert result.status == "no_input"
        assert result.translated_text == ""

    def test_whitespace_only_returns_no_input(self):
        result = self.provider.translate("   ", "en", "hi")
        assert result.status == "no_input"

    def test_latency_is_non_negative(self):
        result = self.provider.translate("text", "en", "hi")
        assert result.translation_latency_ms >= 0.0

    def test_translate_never_raises(self):
        """translate() must return a value for any input, never raise."""
        for src in ("en", "hi", "te", "ta"):
            for tgt in ("en", "hi", "te", "ta"):
                result = self.provider.translate("test", src, tgt)
                assert isinstance(result, TranslationResult)

    def test_result_succeeded_true_on_ok(self):
        result = self.provider.translate("test", "en", "hi")
        assert result.succeeded is True

    def test_result_succeeded_false_on_no_input(self):
        result = self.provider.translate("", "en", "hi")
        assert result.succeeded is False

    def test_all_four_language_pairs(self):
        languages = ["en", "hi", "te", "ta"]
        for src in languages:
            for tgt in languages:
                result = self.provider.translate("Sample text", src, tgt)
                assert result.status == "ok", f"Failed for {src}->{tgt}"


# ============================================================
# TEST CLASS 6 — TranslationResult structure
# ============================================================

class TestTranslationResultStructure:

    def test_has_status_field(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "status")

    def test_has_source_language(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "source_language")

    def test_has_target_language(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "target_language")

    def test_has_translated_text(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "translated_text")

    def test_has_provider_name(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "provider")

    def test_has_latency(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        assert hasattr(result, "translation_latency_ms")

    def test_has_source_text(self):
        provider = MockTranslationProvider()
        result = provider.translate("original text", "en", "hi")
        assert result.source_text == "original text"

    def test_result_is_immutable(self):
        provider = MockTranslationProvider()
        result = provider.translate("text", "en", "hi")
        with pytest.raises((AttributeError, TypeError)):
            result.status = "mutated"  # type: ignore[misc]


# ============================================================
# TEST CLASS 7 — Configuration settings
# ============================================================

class TestSettings:

    def test_settings_has_source_language(self):
        from config import settings
        assert hasattr(settings, "source_language")

    def test_settings_has_target_language(self):
        from config import settings
        assert hasattr(settings, "target_language")

    def test_source_language_is_valid_code(self):
        from config import settings
        # Default must be a supported language code
        assert is_supported(settings.source_language), (
            f"Default source_language {settings.source_language!r} is not supported"
        )

    def test_target_language_is_valid_code(self):
        from config import settings
        assert is_supported(settings.target_language), (
            f"Default target_language {settings.target_language!r} is not supported"
        )

    def test_stt_provider_is_configured(self):
        from config import settings
        # STT provider must be one of the valid options.
        # When backend/.env is present, this reflects the configured value.
        # Default (no .env): "mock". With backend/.env: whatever is set.
        assert settings.stt_provider in ("mock", "gemini", "google", "deepgram"), (
            f"Unexpected stt_provider: {settings.stt_provider!r}"
        )


# ============================================================
# TEST CLASS 8 — Package import smoke test
# ============================================================

class TestPackageImports:

    def test_languages_module_imports(self):
        import languages  # noqa: F401

    def test_translation_base_imports(self):
        from translation import base  # noqa: F401

    def test_translation_mock_imports(self):
        from translation import mock  # noqa: F401

    def test_translation_provider_imports(self):
        from translation import provider  # noqa: F401

    def test_speech_base_still_imports(self):
        from speech import base  # noqa: F401

    def test_speech_mock_still_imports(self):
        from speech import mock  # noqa: F401

    def test_audio_processor_still_imports(self):
        from audio import processor  # noqa: F401
