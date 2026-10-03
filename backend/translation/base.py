"""
backend/translation/base.py — PRE-M5

Abstract TranslationProvider interface and result/error value types.

STATUS
------
This module defines the CONTRACT only.  No real translation is
implemented yet.  Real translation begins in M5.

DESIGN RULES
------------
- All concrete providers implement TranslationProvider.
- No provider-specific code lives here.
- translate() NEVER raises.  It returns TranslationResult with
  status != 'ok' on any failure.
- The caller (future ws/handler.py M5 path) imports only from this
  module and translation.provider (the factory).

INPUT CONTRACT
--------------
translate() receives:
  text            — the raw transcription string from M4 STT
  source_language — BCP-47 code of the spoken language
  target_language — BCP-47 code of the desired output language

Both language codes must be valid according to languages.SUPPORTED_CODES.
The provider is NOT responsible for language validation — that is done
before translate() is called.

RESULT FIELDS
-------------
TranslationResult.status values:
  'ok'                    — translated_text contains the result
  'no_input'              — input text was empty or whitespace-only
  'unsupported_pair'      — provider cannot handle source→target combination
  'provider_error'        — provider raised an internal exception
  'timeout'               — provider took too long (future async providers)
  'auth_error'            — API key / credentials rejected
  'quota_exceeded'        — provider rate limit or quota hit

FALLBACK POLICY
---------------
If translation fails (status != 'ok'), the caller (M5 handler) should
display the original transcription text unchanged.  Implementing that
fallback is M5's responsibility — this module only defines the contract.

WEBSOCKET MESSAGE CONTRACT (FUTURE M5)
--------------------------------------
A successful translation will produce a message with shape:

  {
    "type": "translation",
    "source_language": "hi",
    "target_language": "te",
    "source_text": "Enemy left side hai",
    "translated_text": "ఎడమ వైపు శత్రువు ఉన్నాడు",
    "provider": "...",
    "stt_latency_ms": ...,
    "translation_latency_ms": ...,
    "total_latency_ms": ...
  }

A failed translation will produce:

  {
    "type": "translation_error",
    "code": "...",
    "message": "...",
    "source_text": "..."    ← original transcription always included
  }

These messages DO NOT EXIST YET.  They are documented here as the
contract M5 must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Result value type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TranslationResult:
    """
    Unified result returned by every TranslationProvider implementation.

    status values — see module docstring for descriptions.
    """
    status: str                   # 'ok' | 'no_input' | 'unsupported_pair' |
                                  # 'provider_error' | 'timeout' | 'auth_error' |
                                  # 'quota_exceeded'
    source_language: str          # BCP-47 source code used (e.g. "hi")
    target_language: str          # BCP-47 target code used (e.g. "te")
    provider: str                 # machine name (e.g. "mock")
    translation_latency_ms: float # wall-clock ms inside translate()

    # --- Success fields ---
    translated_text: str = ""     # result text; "" on failure

    # --- Failure fields ---
    error_message: Optional[str] = None   # human-readable description
    source_text: str = ""                 # original input (for fallback display)

    # --- Optional extras ---
    confidence: Optional[float] = None    # 0.0–1.0 if provider supplies it
    metadata: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Convenience: True iff status == 'ok'."""
        return self.status == "ok"


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class TranslationProvider(ABC):
    """
    Base class for all translation providers.

    Subclasses must implement translate().
    They must NOT perform audio decoding or STT — receive plain text only.
    """

    #: Machine-readable name returned in TranslationResult.provider.
    name: str = "base"

    #: Set of (source, target) tuples this provider can handle.
    #: Empty set means the provider claims universal support (check at runtime).
    supported_pairs: frozenset[tuple[str, str]] = frozenset()

    def supports_pair(self, source: str, target: str) -> bool:
        """
        Return True if the provider can handle source→target translation.
        If supported_pairs is empty, assume universal support.
        Same-language pairs (source == target) always return True — no
        translation is needed and the input can be returned unchanged.
        """
        if source == target:
            return True
        if not self.supported_pairs:
            return True
        return (source, target) in self.supported_pairs

    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult:
        """
        Translate text from source_language to target_language.

        Args:
            text:             Raw transcription string from M4.
            source_language:  BCP-47 code; validated before this call.
            target_language:  BCP-47 code; validated before this call.

        Returns:
            TranslationResult — NEVER raises.

        Contract:
            If source_language == target_language, return the input text
            unchanged with status='ok' (passthrough).
        """
        ...
