"""
backend/speech/base.py — M4

Abstract SpeechProvider interface and result value types.

DESIGN RULES
------------
- All concrete providers implement SpeechProvider.
- The caller (ws/handler.py) only imports from this module +
  speech.provider (the factory).  It never imports a concrete
  provider directly.
- Replacing the STT backend means adding/changing one file in
  speech/ and updating STT_PROVIDER in .env.
- transcribe() NEVER raises.  It returns TranscriptionResult
  with status != 'ok' on failure.

INPUT CONTRACT
--------------
Providers receive a ProcessedAudio object (from audio.processor).
This guarantees the audio is already normalised to:
  16 kHz / mono / signed 16-bit PCM
Providers MUST NOT re-convert the audio.

SUPPORTED LANGUAGES
-------------------
Language codes follow BCP-47 / ISO 639-1 conventions:
  en  English
  hi  Hindi
  te  Telugu
  ta  Tamil
More can be added without changing this file.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from audio.processor import ProcessedAudio


# ---------------------------------------------------------------------------
# Result value type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TranscriptionResult:
    """
    Unified result returned by every SpeechProvider implementation.

    status values
    -------------
    'ok'            — text contains a valid transcription
    'no_speech'     — audio was valid but no speech was detected
    'unsupported_language' — provider cannot handle the requested language
    'provider_error' — provider raised an internal exception
    'timeout'       — provider took too long to respond
    'empty_audio'   — PCM input was empty or too short to process
    """
    status: str          # see docstring above
    text: str            # transcription text, or "" on failure
    language: str        # BCP-47 source language used (e.g. "en")
    provider: str        # machine name of the provider (e.g. "mock")
    stt_latency_ms: float     # time inside provider.transcribe()
    confidence: Optional[float] = None     # 0.0–1.0 or None if unavailable
    error_message: Optional[str] = None    # human-readable on failure
    metadata: dict = field(default_factory=dict)  # provider extras


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class SpeechProvider(ABC):
    """
    Base class for all STT providers.

    Subclasses must implement transcribe().
    They must NOT import av or perform audio conversion.
    """

    #: Machine-readable name returned in TranscriptionResult.provider.
    name: str = "base"

    #: BCP-47 language codes this provider supports.
    #: Empty set means the provider claims universal support (check at runtime).
    supported_languages: frozenset[str] = frozenset()

    def supports_language(self, language: str) -> bool:
        """
        Return True if the provider can handle the given language.
        If supported_languages is empty, assume universal support.
        """
        if not self.supported_languages:
            return True
        return language in self.supported_languages

    @abstractmethod
    def transcribe(
        self,
        audio: ProcessedAudio,
        language: str = "en",
    ) -> TranscriptionResult:
        """
        Convert PCM audio to text.

        Args:
            audio:    Normalised PCM from audio.processor.convert().
                      Guaranteed: 16 kHz / mono / s16.
            language: BCP-47 source language code.

        Returns:
            TranscriptionResult — never raises.
        """
        ...
