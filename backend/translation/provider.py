"""
backend/translation/provider.py — M5

Translation provider factory.

HOW TO ADD A NEW PROVIDER
--------------------------
1. Create backend/translation/<name>.py implementing TranslationProvider.
2. Import it here and add a case to the mapping.
3. Set TRANSLATION_PROVIDER=<name> in backend/.env.
4. Restart the backend.

CURRENTLY SUPPORTED
--------------------
mock              MockTranslationProvider
                  Returns clearly labelled placeholders for testing.
                  NOT real translation.  No network calls.

gemini            GeminiTranslationProvider (RECOMMENDED)
                  Real translation via Gemini text generation API.
                  Requires GEMINI_API_KEY (same key as Gemini Live STT).
                  Model: GEMINI_TRANSLATION_MODEL (default: gemini-2.0-flash)

deep_translator   DeepTranslatorProvider (legacy)
                  Real translation via deep-translator (GoogleTranslator).
                  No API key required but rate-limited and unreliable.
"""

from __future__ import annotations

import logging

from translation.base import TranslationProvider

logger = logging.getLogger("gamevoice.translation.provider")


def get_translation_provider(provider_name: str) -> TranslationProvider:
    """
    Return a configured TranslationProvider instance.

    Args:
        provider_name:  Value of TRANSLATION_PROVIDER from config/env.

    Returns:
        An initialised TranslationProvider.

    Raises:
        ValueError   if provider_name is unknown.
        ImportError  if the provider's dependencies are not installed.
    """
    name = provider_name.strip().lower()

    if name == "mock":
        from translation.mock import MockTranslationProvider
        return MockTranslationProvider()

    if name == "gemini":
        from translation.gemini_provider import GeminiTranslationProvider
        return GeminiTranslationProvider()

    if name == "deep_translator":
        from translation.deep_translator_provider import DeepTranslatorProvider
        return DeepTranslatorProvider()

    raise ValueError(
        f"Unknown TRANSLATION_PROVIDER: {provider_name!r}. "
        f"Valid values: mock, gemini, deep_translator. "
        f"See .env.example for configuration options."
    )
