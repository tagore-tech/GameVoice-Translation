"""
backend/translation/mock.py — PRE-M5

================================================================================
DEVELOPMENT / MOCK TRANSLATION PROVIDER
================================================================================

THIS PROVIDER DOES NOT PERFORM REAL TRANSLATION.

It is used ONLY to:
  1. Validate the TranslationProvider interface.
  2. Allow tests to exercise the M5 data flow contract without network calls.
  3. Demonstrate passthrough behaviour (same source+target language).

REPLACING WITH REAL TRANSLATION
---------------------------------
Set TRANSLATION_PROVIDER=deep_translator (or google) in backend/.env
and provide the corresponding dependencies/credentials.
The mock provider will no longer be loaded.

BEHAVIOUR
---------
For same-language pairs (source == target):
  Returns the input text unchanged, status='ok'.

For different-language pairs (hi→te, en→hi, etc.):
  Returns a clearly labelled placeholder string, status='ok'.
  Example: "[MOCK: hi→te] Dushman left side pe hai"

For empty/whitespace input:
  Returns status='no_input'.

For unsupported pairs (when MockTranslationProvider has a restriction):
  Returns status='unsupported_pair'.
================================================================================
"""

from __future__ import annotations

import logging
import time

from translation.base import TranslationProvider, TranslationResult

logger = logging.getLogger("gamevoice.translation.mock")


class MockTranslationProvider(TranslationProvider):
    """
    DEVELOPMENT ONLY — returns labelled placeholders, NOT real translation.
    Supports all pairs in the supported language set.
    """

    name = "mock"
    # Empty means universal support — the mock handles any pair
    supported_pairs: frozenset[tuple[str, str]] = frozenset()

    def __init__(self) -> None:
        logger.warning(
            "MockTranslationProvider loaded — "
            "returns PLACEHOLDER text, NOT real translation. "
            "Set TRANSLATION_PROVIDER=deep_translator in .env for real translation."
        )

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult:
        t0 = time.perf_counter()

        # --- Empty input ---
        if not text or not text.strip():
            latency_ms = (time.perf_counter() - t0) * 1_000.0
            return TranslationResult(
                status="no_input",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(latency_ms, 1),
                error_message="Input text is empty.",
                source_text=text,
            )

        # --- Same-language passthrough ---
        if source_language == target_language:
            latency_ms = (time.perf_counter() - t0) * 1_000.0
            logger.debug(
                "Mock translation passthrough [%s→%s]: %r",
                source_language, target_language, text,
            )
            return TranslationResult(
                status="ok",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(latency_ms, 1),
                translated_text=text,   # unchanged
                source_text=text,
                metadata={"passthrough": True},
            )

        # --- Cross-language mock ---
        placeholder = f"[MOCK: {source_language}->{target_language}] {text}"
        latency_ms = (time.perf_counter() - t0) * 1_000.0

        logger.info(
            "Mock translation [%s->%s] %.1f ms",
            source_language, target_language, latency_ms,
        )

        return TranslationResult(
            status="ok",
            source_language=source_language,
            target_language=target_language,
            provider=self.name,
            translation_latency_ms=round(latency_ms, 1),
            translated_text=placeholder,
            source_text=text,
            metadata={
                "note": "MOCK provider — not real translation",
            },
        )
