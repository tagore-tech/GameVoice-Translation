"""
backend/languages.py — PRE-M5

Centralized language registry and session/participant language model.

SINGLE SOURCE OF TRUTH FOR LANGUAGE CODES
------------------------------------------
All backend modules that need to reference a language code MUST import
from here.  No raw language strings should be scattered across the codebase.

SUPPORTED LANGUAGES
-------------------
Initial set covers all four target gaming languages.
Adding a new language requires only:
  1. Adding an entry to SUPPORTED_LANGUAGES below.
  2. Verifying the STT/translation provider supports it.
  No other code changes are required.

LANGUAGE VALIDATION
--------------------
validate_language_code() raises ValueError for unsupported codes.
It never silently falls back to English.  Callers must handle invalid
codes explicitly.

PARTICIPANT LANGUAGE CONFIG
----------------------------
ParticipantLanguageConfig holds the source+target language pair for one
player session.  It does NOT implement routing — that is M7.
It only establishes the contract shape so M7 can attach it to individual
room participants without refactoring.

WEBSOCKET CONTRACT NOTE
------------------------
The existing M4 'transcription' message includes a 'language' field
representing the source language.  Future M5 'translation' messages
will add 'source_language' and 'target_language' fields.  Both use
the BCP-47 codes defined here.

See docs/PRE_M5_ARCHITECTURE.md for the full message contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

class Language(NamedTuple):
    """A supported BCP-47 language entry."""
    code: str        # BCP-47 / ISO 639-1 code (e.g. "hi")
    name: str        # Display name in English (e.g. "Hindi")
    native_name: str # Display name in the language itself (e.g. "हिन्दी")


#: Master list of supported languages.
#: Add new languages here only — no other files need to change for discovery.
SUPPORTED_LANGUAGES: tuple[Language, ...] = (
    Language(code="en", name="English",  native_name="English"),
    Language(code="hi", name="Hindi",    native_name="हिन्दी"),
    Language(code="te", name="Telugu",   native_name="తెలుగు"),
    Language(code="ta", name="Tamil",    native_name="தமிழ்"),
)

#: Fast lookup: code → Language
_LANGUAGE_MAP: dict[str, Language] = {lang.code: lang for lang in SUPPORTED_LANGUAGES}

#: Frozenset of valid codes — use for membership checks
SUPPORTED_CODES: frozenset[str] = frozenset(_LANGUAGE_MAP)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_language_code(code: str) -> Language:
    """
    Return the Language entry for the given BCP-47 code.

    Raises:
        ValueError  — if code is not in SUPPORTED_CODES.
                      Never silently falls back to a default.
    """
    lang = _LANGUAGE_MAP.get(code)
    if lang is None:
        supported = ", ".join(sorted(SUPPORTED_CODES))
        raise ValueError(
            f"Unsupported language code: {code!r}. "
            f"Supported codes: {supported}"
        )
    return lang


def is_supported(code: str) -> bool:
    """Return True if code is in the supported language set."""
    return code in SUPPORTED_CODES


def get_language(code: str) -> Language | None:
    """Return Language for code, or None if unsupported (non-raising)."""
    return _LANGUAGE_MAP.get(code)


# ---------------------------------------------------------------------------
# Participant language configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParticipantLanguageConfig:
    """
    Language configuration for one player session.

    source_language:  What this player speaks (their microphone input).
    target_language:  What language they want to hear translated to.

    DESIGN NOTE
    -----------
    This is a pure data container — it does NOT implement routing or
    translation.  It establishes the contract for M7 multi-player routing,
    where each room participant will have their own ParticipantLanguageConfig.

    Same-language sessions (source == target) are valid and mean no
    translation is needed — the transcription is forwarded as-is.

    Example future usage in M7:
        player_a = ParticipantLanguageConfig(source_language="hi", target_language="te")
        player_b = ParticipantLanguageConfig(source_language="te", target_language="hi")

    Validation is performed at construction time.
    """
    source_language: str  # BCP-47 code — what the player speaks
    target_language: str  # BCP-47 code — what the player wants to receive

    def __post_init__(self) -> None:
        # Validate both codes — raises ValueError on unknown codes
        validate_language_code(self.source_language)
        validate_language_code(self.target_language)

    @property
    def needs_translation(self) -> bool:
        """Return True if source != target (translation is needed for M5)."""
        return self.source_language != self.target_language

    @property
    def source(self) -> Language:
        """Return the full Language entry for the source language."""
        return _LANGUAGE_MAP[self.source_language]

    @property
    def target(self) -> Language:
        """Return the full Language entry for the target language."""
        return _LANGUAGE_MAP[self.target_language]
