"""
backend/tests/test_gemini_translation.py

Focused unit tests for GeminiTranslationProvider.

ALL TESTS ARE FULLY OFFLINE — no real Gemini API calls.
The Gemini client/SDK is mocked via unittest.mock.

Test classes:
  TestGeminiTranslationProviderInit   — constructor / credential detection
  TestGeminiTranslationProviderLogic  — translate() method logic (mocked client)
  TestGeminiTranslationFactory        — factory selects GeminiTranslationProvider
  TestGeminiTranslationResultContract — TranslationResult structure
"""

from __future__ import annotations

import os
import logging
from unittest.mock import MagicMock, patch

import pytest

from translation.base import TranslationProvider, TranslationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(model: str = "gemini-3.8-flash"):
    """Instantiate GeminiTranslationProvider with a fake key and mocked client."""
    from translation.gemini_provider import GeminiTranslationProvider

    mock_client = MagicMock()
    with patch.dict(os.environ, {
        "GEMINI_API_KEY": "test-fake-key",
        "GEMINI_TRANSLATION_MODEL": model,
    }):
        with patch("google.genai.Client", return_value=mock_client):
            p = GeminiTranslationProvider()
    p._client = mock_client
    return p


def _mock_response(text: str) -> MagicMock:
    """Build a mock Gemini generate_content response."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# TEST CLASS 1 — init / credentials
# ---------------------------------------------------------------------------

class TestGeminiTranslationProviderInit:

    def test_missing_api_key_raises_value_error(self):
        """No GEMINI_API_KEY -> ValueError at construction time."""
        from translation.gemini_provider import GeminiTranslationProvider
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the key is absent
            os.environ.pop("GEMINI_API_KEY", None)
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                GeminiTranslationProvider()

    def test_provider_is_translation_provider(self):
        p = _make_provider()
        assert isinstance(p, TranslationProvider)

    def test_provider_name_is_gemini(self):
        p = _make_provider()
        assert p.name == "gemini"

    def test_model_from_env(self):
        p = _make_provider(model="gemini-3.8-flash")
        assert p._model == "gemini-3.8-flash"

    def test_default_model_used_when_env_empty(self):
        from translation.gemini_provider import GeminiTranslationProvider
        mock_client = MagicMock()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            os.environ.pop("GEMINI_TRANSLATION_MODEL", None)
            with patch("google.genai.Client", return_value=mock_client):
                p = GeminiTranslationProvider()
        assert p._model  # default was set (non-empty)


# ---------------------------------------------------------------------------
# TEST CLASS 2 — translate() logic
# ---------------------------------------------------------------------------

class TestGeminiTranslationProviderLogic:

    def setup_method(self):
        self.provider = _make_provider()

    def _set_response(self, text: str):
        self.provider._client.models.generate_content.return_value = _mock_response(text)

    # --- empty input ---
    def test_empty_input_returns_no_input(self):
        result = self.provider.translate("", "hi", "te")
        assert result.status == "no_input"
        assert result.translated_text == ""
        # No Gemini API call
        self.provider._client.models.generate_content.assert_not_called()

    def test_whitespace_only_returns_no_input(self):
        result = self.provider.translate("   ", "hi", "te")
        assert result.status == "no_input"

    # --- same-language passthrough ---
    def test_same_language_passthrough_no_api_call(self):
        result = self.provider.translate("Hello world", "en", "en")
        assert result.status == "ok"
        assert result.translated_text == "Hello world"
        self.provider._client.models.generate_content.assert_not_called()

    def test_same_language_hi_hi_passthrough(self):
        result = self.provider.translate("पीछे देख।", "hi", "hi")
        assert result.status == "ok"
        assert result.translated_text == "पीछे देख।"

    # --- CASE 1: hi -> te ---
    def test_hi_to_te_translation(self):
        self._set_response("వెనక్కి చూడు.")
        result = self.provider.translate("पीछे देख।", "hi", "te")
        assert result.status == "ok"
        assert result.translated_text == "వెనక్కి చూడు."
        assert result.source_language == "hi"
        assert result.target_language == "te"
        assert result.provider == "gemini"

    def test_generate_content_uses_configured_model(self):
        provider = _make_provider(model="gemini-3.8-flash")
        provider._client.models.generate_content.return_value = _mock_response(
            "వెనక్కి చూడు."
        )

        result = provider.translate("पीछे देख।", "hi", "te")

        assert result.status == "ok"
        assert (
            provider._client.models.generate_content.call_args.kwargs["model"]
            == "gemini-3.8-flash"
        )

    def test_gemini_38_request_omits_unsupported_temperature(self):
        """Gemini 3.8 Flash rejects legacy sampling parameters."""
        provider = _make_provider(model="gemini-3.8-flash")
        provider._client.models.generate_content.return_value = _mock_response(
            "వెనక్కి చూడు."
        )

        result = provider.translate("पीछे देख।", "hi", "te")

        assert result.status == "ok"
        config = provider._client.models.generate_content.call_args.kwargs["config"]
        assert config.max_output_tokens == 512
        assert config.temperature is None

    # --- CASE 2: en -> hi ---
    def test_en_to_hi_translation(self):
        self._set_response("दुश्मन पीछे है।")
        result = self.provider.translate("Enemy behind.", "en", "hi")
        assert result.status == "ok"
        assert result.translated_text == "दुश्मन पीछे है।"
        assert result.source_language == "en"
        assert result.target_language == "hi"

    # --- CASE 3: te -> en ---
    def test_te_to_en_translation(self):
        self._set_response("Enemy is behind.")
        result = self.provider.translate("వెనక్కి శత్రువు ఉన్నాడు.", "te", "en")
        assert result.status == "ok"
        assert result.translated_text == "Enemy is behind."

    # --- CASE 4: ta -> en ---
    def test_ta_to_en_translation(self):
        self._set_response("Watch out left side!")
        result = self.provider.translate("இடது புறம் பார்!", "ta", "en")
        assert result.status == "ok"
        assert result.translated_text == "Watch out left side!"

    # --- result contract ---
    def test_ok_result_has_source_text(self):
        self._set_response("translated")
        result = self.provider.translate("original", "en", "hi")
        assert result.source_text == "original"

    def test_ok_result_latency_non_negative(self):
        self._set_response("translated")
        result = self.provider.translate("test", "en", "hi")
        assert result.translation_latency_ms >= 0.0

    def test_ok_result_metadata_has_model(self):
        self._set_response("translated")
        result = self.provider.translate("test", "en", "hi")
        assert "model" in result.metadata

    # --- provider error ---
    def test_api_exception_returns_provider_error(self):
        self.provider._client.models.generate_content.side_effect = RuntimeError("Network failure")
        result = self.provider.translate("Enemy spotted", "en", "hi")
        assert result.status == "provider_error"
        assert result.translated_text == ""
        assert result.source_text == "Enemy spotted"
        assert result.error_message is not None

    def test_api_exception_logs_its_type_and_message(self, caplog):
        self.provider._client.models.generate_content.side_effect = RuntimeError("Network failure")

        with caplog.at_level(logging.ERROR, logger="gamevoice.translation.gemini"):
            result = self.provider.translate("Enemy spotted", "en", "hi")

        assert result.status == "provider_error"
        assert "RuntimeError" in caplog.text
        assert "Network failure" in caplog.text

    def test_auth_error_returns_auth_error_status(self):
        self.provider._client.models.generate_content.side_effect = Exception("401 authentication failed")
        result = self.provider.translate("test", "en", "hi")
        assert result.status == "auth_error"

    def test_quota_error_returns_quota_exceeded_status(self):
        self.provider._client.models.generate_content.side_effect = Exception("429 quota exceeded")
        result = self.provider.translate("test", "en", "hi")
        assert result.status == "quota_exceeded"

    def test_timeout_error_returns_timeout_status(self):
        self.provider._client.models.generate_content.side_effect = Exception("Request timeout")
        result = self.provider.translate("test", "en", "hi")
        assert result.status == "timeout"

    def test_empty_api_response_returns_provider_error(self):
        self.provider._client.models.generate_content.return_value = _mock_response("")
        result = self.provider.translate("test", "en", "hi")
        assert result.status == "provider_error"

    def test_none_api_response_text_returns_provider_error(self):
        resp = MagicMock()
        resp.text = None
        self.provider._client.models.generate_content.return_value = resp
        result = self.provider.translate("test", "en", "hi")
        assert result.status == "provider_error"

    # --- never raises ---
    def test_translate_never_raises(self):
        self.provider._client.models.generate_content.side_effect = Exception("Unexpected crash")
        result = self.provider.translate("test", "en", "hi")
        assert isinstance(result, TranslationResult)


# ---------------------------------------------------------------------------
# TEST CLASS 3 — factory
# ---------------------------------------------------------------------------

class TestGeminiTranslationFactory:

    def test_factory_returns_gemini_provider(self):
        from translation.provider import get_translation_provider
        from translation.gemini_provider import GeminiTranslationProvider

        mock_client = MagicMock()
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key",
            "TRANSLATION_PROVIDER": "gemini",
        }):
            with patch("google.genai.Client", return_value=mock_client):
                p = get_translation_provider("gemini")
        assert isinstance(p, GeminiTranslationProvider)
        assert p.name == "gemini"

    def test_factory_mock_still_works(self):
        from translation.provider import get_translation_provider
        p = get_translation_provider("mock")
        assert p.name == "mock"

    def test_factory_deep_translator_still_works(self):
        from translation.provider import get_translation_provider
        from translation.base import TranslationProvider
        p = get_translation_provider("deep_translator")
        assert isinstance(p, TranslationProvider)

    def test_factory_unknown_raises(self):
        from translation.provider import get_translation_provider
        with pytest.raises(ValueError, match="Unknown TRANSLATION_PROVIDER"):
            get_translation_provider("nonexistent_xyz")

    def test_factory_error_message_includes_gemini(self):
        from translation.provider import get_translation_provider
        with pytest.raises(ValueError) as exc:
            get_translation_provider("bad_provider")
        assert "gemini" in str(exc.value)


# ---------------------------------------------------------------------------
# TEST CLASS 4 — TranslationResult contract with gemini provider
# ---------------------------------------------------------------------------

class TestGeminiTranslationResultContract:

    def setup_method(self):
        self.provider = _make_provider()
        self.provider._client.models.generate_content.return_value = _mock_response(
            "Translated output"
        )

    def test_ok_result_structure(self):
        r = self.provider.translate("Enemy spotted", "en", "hi")
        assert r.status == "ok"
        assert r.succeeded is True
        assert r.source_language == "en"
        assert r.target_language == "hi"
        assert r.provider == "gemini"
        assert isinstance(r.translation_latency_ms, float)
        assert isinstance(r.translated_text, str)
        assert isinstance(r.source_text, str)

    def test_failure_succeeded_is_false(self):
        self.provider._client.models.generate_content.side_effect = Exception("fail")
        r = self.provider.translate("test", "en", "hi")
        assert r.succeeded is False
        assert r.translated_text == ""

    def test_result_is_immutable(self):
        r = self.provider.translate("test", "en", "hi")
        with pytest.raises((AttributeError, TypeError)):
            r.status = "mutated"  # type: ignore[misc]

    def test_source_text_always_present_on_error(self):
        self.provider._client.models.generate_content.side_effect = Exception("fail")
        r = self.provider.translate("original input", "en", "hi")
        assert r.source_text == "original input"
