"""
backend/translation/deep_translator_provider.py — M5

Real translation provider using the `deep-translator` library.

LIBRARY USED
------------
deep-translator >= 1.11, < 2.0
GitHub: https://github.com/nidhaloff/deep-translator
No API key required for GoogleTranslator backend (uses public endpoint).

WARNING: deep-translator's GoogleTranslator backend relies on an
undocumented Google endpoint.  It may break if Google changes the API.
For production use, switch to TRANSLATION_PROVIDER=google and use
the official Google Cloud Translation API with credentials.

LANGUAGE MAPPING
----------------
deep-translator uses BCP-47-like codes but some names differ.
The _LANG_MAP below converts GameVoice codes → deep-translator codes.
All four initial languages are supported.

SAME-LANGUAGE PASSTHROUGH
--------------------------
If source == target, the input text is returned unchanged without
making any network call.  This preserves M4's STT-only behavior
when both languages happen to be the same.

ERRORS
------
translate() never raises.  All exceptions from deep-translator are
caught and returned as TranslationResult with status='provider_error'.
Network failures produce the same safe wrapper.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from translation.base import TranslationProvider, TranslationResult

logger = logging.getLogger("gamevoice.translation.deep_translator")

# ---------------------------------------------------------------------------
# Language code mapping
# deep-translator GoogleTranslator accepts ISO 639-1 codes directly,
# which match our BCP-47 codes (en, hi, te, ta).
# This map is here in case future languages need remapping.
# ---------------------------------------------------------------------------
_LANG_MAP: dict[str, str] = {
    "en": "en",
    "hi": "hi",
    "te": "te",
    "ta": "ta",
}


class DeepTranslatorProvider(TranslationProvider):
    """
    Real translation provider backed by deep-translator (GoogleTranslator).

    No API key is required.

    LIMITATIONS
    -----------
    - Uses an unofficial Google endpoint; may be rate-limited or break.
    - Requires internet connectivity at translation time.
    - Not suitable for high-volume production traffic.

    For reliable production use, set TRANSLATION_PROVIDER=google and
    supply Google Cloud Translation API credentials.
    """

    name = "deep_translator"
    # Empty → universal support (runtime errors surface through TranslationResult)
    supported_pairs: frozenset[tuple[str, str]] = frozenset()

    def __init__(self) -> None:
        # Verify import at startup so failure is caught early
        try:
            from deep_translator import GoogleTranslator  # noqa: F401
            logger.info(
                "DeepTranslatorProvider loaded — real translation active. "
                "Uses GoogleTranslator backend (no API key required). "
                "Internet access required at translation time."
            )
        except ImportError as exc:
            raise ImportError(
                "deep-translator is not installed. "
                "Run: .venv\\Scripts\\python.exe -m pip install deep-translator"
            ) from exc

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult:
        """
        Translate text using deep-translator's GoogleTranslator.

        Never raises — all exceptions are wrapped in TranslationResult.
        """
        t0 = time.perf_counter()

        # --- Empty input ---
        if not text or not text.strip():
            ms = (time.perf_counter() - t0) * 1_000.0
            return TranslationResult(
                status="no_input",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                error_message="Input text is empty.",
                source_text=text,
            )

        # --- Same-language passthrough (no network call) ---
        if source_language == target_language:
            ms = (time.perf_counter() - t0) * 1_000.0
            logger.debug(
                "DeepTranslator passthrough [%s→%s]",
                source_language, target_language,
            )
            return TranslationResult(
                status="ok",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                translated_text=text,
                source_text=text,
                metadata={"passthrough": True},
            )

        # --- Map language codes ---
        src = _LANG_MAP.get(source_language, source_language)
        tgt = _LANG_MAP.get(target_language, target_language)

        # --- Real translation ---
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source=src, target=tgt)
            result_text: Optional[str] = translator.translate(text)
            ms = (time.perf_counter() - t0) * 1_000.0

            if not result_text:
                logger.warning(
                    "DeepTranslator returned empty result for [%s→%s]: %r",
                    src, tgt, text,
                )
                return TranslationResult(
                    status="provider_error",
                    source_language=source_language,
                    target_language=target_language,
                    provider=self.name,
                    translation_latency_ms=round(ms, 1),
                    error_message="Provider returned empty translation.",
                    source_text=text,
                )

            logger.info(
                "DeepTranslator [%s→%s] %.0f ms: %r → %r",
                src, tgt, ms,
                text[:40], result_text[:40],
            )
            return TranslationResult(
                status="ok",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                translated_text=result_text,
                source_text=text,
            )

        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1_000.0
            logger.error(
                "DeepTranslator error [%s→%s]: %s",
                src, tgt, exc,
            )
            return TranslationResult(
                status="provider_error",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                error_message=str(exc),
                source_text=text,
            )
