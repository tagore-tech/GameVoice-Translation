"""
backend/speech/provider.py — M-REAL-STT

Provider factory — loads the correct SpeechProvider based on the
STT_PROVIDER environment variable.

This is the ONLY place that knows which concrete provider is active.
ws/handler.py only calls get_speech_provider() and uses the returned
SpeechProvider interface.

HOW TO ADD A NEW PROVIDER
--------------------------
1. Create backend/speech/<name>.py implementing SpeechProvider.
2. Import it here and add a case below.
3. Set STT_PROVIDER=<name> in backend/.env.
4. Restart the backend.

No other file needs to change.

CURRENTLY SUPPORTED
--------------------
mock      MockSpeechProvider
          Returns rotating gaming phrases. No credentials needed.
          NOT real speech recognition. Offline / deterministic.

gemini    GeminiSpeechProvider
          Real STT via Gemini API (google-genai SDK).
          Requires: GEMINI_API_KEY in backend/.env
          Supports: en, hi, te, ta (and 80+ other languages automatically)
          Free tier: 15 RPM, 1500 RPD on AI Studio key.
"""

from __future__ import annotations

import logging

from speech.base import SpeechProvider

logger = logging.getLogger("gamevoice.speech.provider")


def get_speech_provider(provider_name: str) -> SpeechProvider:
    """
    Return a configured SpeechProvider instance for the given name.

    Args:
        provider_name:  Value of STT_PROVIDER from config/env.

    Returns:
        An initialised SpeechProvider.

    Raises:
        ValueError  if provider_name is unknown.
                    (A misconfigured backend should fail at startup.)
        ImportError if the provider's dependencies are not installed.
    """
    name = provider_name.strip().lower()

    if name == "mock":
        from speech.mock import MockSpeechProvider
        return MockSpeechProvider()

    if name == "gemini":
        from speech.gemini_stt import GeminiSpeechProvider
        return GeminiSpeechProvider()

    # -----------------------------------------------------------------------
    # FUTURE PROVIDERS
    # -----------------------------------------------------------------------
    # if name == "google":
    #     from speech.google_stt import GoogleSpeechProvider
    #     return GoogleSpeechProvider()
    #
    # if name == "deepgram":
    #     from speech.deepgram_stt import DeepgramSpeechProvider
    #     return DeepgramSpeechProvider()
    # -----------------------------------------------------------------------

    raise ValueError(
        f"Unknown STT_PROVIDER: {provider_name!r}. "
        f"Valid values: mock, gemini. "
        f"See .env.example for configuration options."
    )
