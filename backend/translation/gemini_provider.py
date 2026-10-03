"""
backend/translation/gemini_provider.py

GeminiTranslationProvider — text-to-text translation via the Gemini API.

Uses the same google-genai SDK (2.27.0+) already installed for Gemini Live STT.
Reuses GEMINI_API_KEY from the existing environment configuration.

Model: configurable via GEMINI_TRANSLATION_MODEL (default: gemini-3.8-flash)

DESIGN
------
- Implements TranslationProvider (same ABC as DeepTranslatorProvider/MockProvider).
- translate() is synchronous and NEVER raises — returns TranslationResult.
- Uses generate_content() (simple text, not Live/streaming) — minimal latency.
- Prompt is strict: translate only, no commentary, no explanation.
- Same-language passthrough returns input unchanged without any API call.
- API key sourced exclusively from environment variable; never hard-coded.

LANGUAGE NAMES
--------------
Full language names are embedded in the prompt so Gemini does not need
to guess from BCP-47 codes.
"""

from __future__ import annotations

import logging
import os
import time

from translation.base import TranslationProvider, TranslationResult

logger = logging.getLogger("gamevoice.translation.gemini")

# ---------------------------------------------------------------------------
# Language code → full name mapping (for prompt clarity)
# ---------------------------------------------------------------------------
_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
}

_DEFAULT_MODEL = "gemini-3.8-flash"


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get(code, code)


class GeminiTranslationProvider(TranslationProvider):
    """
    Translation provider backed by Gemini text generation (generate_content).

    Configuration (all from environment, via config.py):
        GEMINI_API_KEY             — required; same key used for Gemini Live STT.
        GEMINI_TRANSLATION_MODEL   — optional; default: gemini-3.8-flash.

    Never raises from translate(). All failures are wrapped in TranslationResult.
    """

    name = "gemini"
    # Empty = claims universal support; errors surface through TranslationResult
    supported_pairs: frozenset[tuple[str, str]] = frozenset()

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Add it to backend/.env. "
                "Get a free key at https://aistudio.google.com/app/apikey"
            )

        self._model = os.getenv("GEMINI_TRANSLATION_MODEL", _DEFAULT_MODEL).strip()
        if not self._model:
            self._model = _DEFAULT_MODEL

        try:
            from google import genai
            self._client = genai.Client(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "google-genai is not installed. "
                "Run: .venv\\Scripts\\python.exe -m pip install google-genai"
            ) from exc

        logger.info(
            "GeminiTranslationProvider ready — model=%s", self._model
        )

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult:
        """
        Translate text from source_language to target_language using Gemini.

        Never raises — all exceptions are caught and returned as TranslationResult.
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

        # --- Same-language passthrough (no API call) ---
        if source_language == target_language:
            ms = (time.perf_counter() - t0) * 1_000.0
            logger.debug(
                "Gemini translation passthrough [%s->%s]",
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

        src_name = _lang_name(source_language)
        tgt_name = _lang_name(target_language)

        # --- Build strict translation prompt ---
        prompt = (
            f"Translate the following {src_name} text into {tgt_name}.\n"
            f"Rules:\n"
            f"- Return ONLY the translated text. Nothing else.\n"
            f"- Do not explain, answer, comment, or add any extra text.\n"
            f"- Preserve the meaning exactly.\n"
            f"- Preserve gaming callouts and short phrases naturally.\n"
            f"- Do not translate proper names or game-specific terms unless"
            f" there is a clear natural equivalent.\n"
            f"- Do not hallucinate or add information not present in the input.\n"
            f"- Do not translate into English unless {tgt_name} IS English.\n"
            f"\n"
            f"Text to translate:\n"
            f"{text.strip()}"
        )

        # --- Call Gemini generate_content ---
        try:
            from google.genai import types as genai_types

            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    # Gemini 3.8 Flash rejects legacy sampling controls such
                    # as temperature. Keep this request model-compatible.
                    max_output_tokens=512,
                ),
            )
            ms = (time.perf_counter() - t0) * 1_000.0

            raw = (response.text or "").strip()

            if not raw:
                logger.warning(
                    "Gemini translation [%s->%s] returned empty response for: %r",
                    source_language, target_language, text[:60],
                )
                return TranslationResult(
                    status="provider_error",
                    source_language=source_language,
                    target_language=target_language,
                    provider=self.name,
                    translation_latency_ms=round(ms, 1),
                    error_message="Gemini returned empty translation.",
                    source_text=text,
                )

            logger.info(
                "Gemini translation [%s->%s] %.0f ms: %r -> %r",
                source_language, target_language, ms,
                text[:40], raw[:40],
            )
            return TranslationResult(
                status="ok",
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                translated_text=raw,
                source_text=text,
                metadata={"model": self._model},
            )

        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1_000.0
            exc_str = str(exc)
            logger.exception(
                "Gemini translation error [%s->%s] (%s): %s",
                source_language,
                target_language,
                type(exc).__name__,
                exc_str,
            )
            exc_lower = exc_str.lower()
            if any(kw in exc_lower for kw in ("api_key", "authentication", "401", "403")):
                status = "auth_error"
                msg = "Authentication failed. Check GEMINI_API_KEY in backend/.env."
            elif any(kw in exc_lower for kw in ("quota", "rate", "429")):
                status = "quota_exceeded"
                msg = "Gemini quota exceeded. Please wait and try again."
            elif "timeout" in exc_lower:
                status = "timeout"
                msg = "Translation request timed out."
            else:
                status = "provider_error"
                msg = f"Translation failed: {exc_str[:120]}"

            return TranslationResult(
                status=status,
                source_language=source_language,
                target_language=target_language,
                provider=self.name,
                translation_latency_ms=round(ms, 1),
                error_message=msg,
                source_text=text,
            )
