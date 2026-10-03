"""
backend/speech/mock.py — M4

================================================================================
DEVELOPMENT / MOCK SPEECH PROVIDER
================================================================================

THIS PROVIDER DOES NOT PERFORM REAL SPEECH RECOGNITION.

It returns deterministic, pre-written phrases based on:
  - the configured source language
  - a round-robin rotation (so repeated PTT presses return different text)

PURPOSE
-------
Allows the complete M4 data flow to be tested without any API credentials:

  PTT → WebM/Opus → PyAV → PCM → MockSpeechProvider → transcription text
                                        ↑
                               fake text only, no ASR

It also verifies that:
  - the SpeechProvider interface works end-to-end
  - the WebSocket sends the correct 'transcription' JSON frame
  - the frontend correctly displays the transcription panel

REPLACING WITH REAL STT
-----------------------
Set STT_PROVIDER=google (or deepgram) in backend/.env and provide
the corresponding credentials.  The mock provider will no longer be loaded.
================================================================================
"""

from __future__ import annotations

import logging
import time
from itertools import cycle

from audio.processor import ProcessedAudio
from speech.base import SpeechProvider, TranscriptionResult

logger = logging.getLogger("gamevoice.speech.mock")

# ---------------------------------------------------------------------------
# Supported languages and their rotating example phrases
# ---------------------------------------------------------------------------

_GAMING_PHRASES: dict[str, list[str]] = {
    "en": [
        "Enemy spotted on the left",
        "Cover me, I am reloading",
        "Need backup at the bridge",
        "Grenade incoming, take cover",
        "All clear, move up",
    ],
    "hi": [
        "Dushman left side pe hai",
        "Mujhe cover karo, main reload kar raha hoon",
        "Bridge pe backup chahiye",
        "Grenade aa raha hai, cover lo",
        "Rasta saaf hai, aage badho",
    ],
    "te": [
        "Left vaipu lo enemy unnadu",
        "Naku cover iyyi, reload chestunna",
        "Bridge vad backup kavali",
        "Grenade vasthundi, cover teesuko",
        "Clear ga undi, mundukoyi",
    ],
    "ta": [
        "Enemy idam pakkam irukkiratu",
        "Ennai maraiyungal, main tittu pottukiren",
        "Paalam area-vil backup venumm",
        "Grenade varukiRatu, marainthu kol",
        "Clear, munnadi pogungal",
    ],
}

# Fallback when an unknown language is sent but the provider does not reject it
_FALLBACK_PHRASES = _GAMING_PHRASES["en"]

# Minimum number of non-zero bytes in PCM for the mock to "process" it
_MIN_PCM_BYTES = 320   # 10 ms of s16 at 16kHz = 320 bytes


class MockSpeechProvider(SpeechProvider):
    """
    DEVELOPMENT ONLY — returns rotating pre-written gaming phrases.
    Does NOT perform real speech recognition.
    """

    name = "mock"
    # Advertise support for all four target languages
    supported_languages = frozenset({"en", "hi", "te", "ta"})

    def __init__(self) -> None:
        # Each language gets its own independent rotation
        self._cycles: dict[str, "cycle[str]"] = {
            lang: cycle(phrases)
            for lang, phrases in _GAMING_PHRASES.items()
        }
        logger.warning(
            "MockSpeechProvider loaded — "
            "returns FAKE phrases, NOT real speech recognition. "
            "Set STT_PROVIDER=google or deepgram in .env for real STT."
        )

    def transcribe(
        self,
        audio: ProcessedAudio,
        language: str = "en",
    ) -> TranscriptionResult:
        t0 = time.perf_counter()

        # --- Empty / too-short audio ---
        if len(audio.pcm_bytes) < _MIN_PCM_BYTES:
            stt_ms = (time.perf_counter() - t0) * 1_000.0
            logger.debug("Mock: audio too short (%d bytes), returning empty_audio", len(audio.pcm_bytes))
            return TranscriptionResult(
                status="empty_audio",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=round(stt_ms, 1),
                error_message="Audio too short for recognition.",
            )

        # --- Unsupported language ---
        if not self.supports_language(language):
            stt_ms = (time.perf_counter() - t0) * 1_000.0
            logger.warning("Mock: unsupported language %r", language)
            return TranscriptionResult(
                status="unsupported_language",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=round(stt_ms, 1),
                error_message=f"Language '{language}' is not supported by the mock provider.",
            )

        # --- Simulate a small, fixed processing delay (< 5 ms) ---
        # We do NOT actually recognise anything.
        lang_cycle = self._cycles.get(language, cycle(_FALLBACK_PHRASES))
        phrase = next(lang_cycle)

        stt_ms = (time.perf_counter() - t0) * 1_000.0

        logger.info(
            "Mock STT [%s] -> %r  (%.1f ms, audio %.0f ms)",
            language,
            phrase,
            stt_ms,
            audio.duration_ms,
        )

        return TranscriptionResult(
            status="ok",
            text=phrase,
            language=language,
            provider=self.name,
            stt_latency_ms=round(stt_ms, 1),
            confidence=0.99,   # mock is always "confident" :)
            metadata={
                "note": "MOCK provider — not real speech recognition",
                "audio_duration_ms": round(audio.duration_ms, 1),
            },
        )
