/**
 * frontend/src/config/languages.ts — PRE-M5
 *
 * Centralized language definitions for the frontend.
 *
 * SINGLE SOURCE OF TRUTH
 * ----------------------
 * All frontend code that refers to a supported language MUST use this file.
 * Do not scatter raw language code strings across components.
 *
 * CURRENT STATUS (M4)
 * -------------------
 * Language selection UI does NOT exist yet — that is M5.
 * This file establishes the contract so M5 can import SUPPORTED_LANGUAGES
 * and render a picker without any backend contract changes.
 *
 * FUTURE M5 USAGE
 * ---------------
 * A minimal language-selection UI will let each player configure:
 *   sourceLanguage — "My language" (what I speak)
 *   targetLanguage — "Teammate language" (what I want to hear)
 *
 * Both values come from SUPPORTED_LANGUAGES below.
 * The frontend sends them to the backend (details TBD in M5).
 *
 * WEBSOCKET CONTRACT NOTE
 * -----------------------
 * M4 'transcription' messages include a `language` field (source language).
 * Future M5 'translation' messages will include both `source_language` and
 * `target_language`.  All codes are from this list.
 */

/** One supported language entry. */
export interface Language {
  /** BCP-47 / ISO 639-1 code — matches backend languages.py exactly. */
  code: string
  /** Display name in English. */
  name: string
  /** Display name in the language's own script. */
  nativeName: string
}

/**
 * Master list of supported languages.
 * Kept in sync with backend/languages.py SUPPORTED_LANGUAGES.
 * Add new languages here only — no other frontend file needs to change.
 */
export const SUPPORTED_LANGUAGES: readonly Language[] = [
  { code: 'en', name: 'English', nativeName: 'English' },
  { code: 'hi', name: 'Hindi',   nativeName: 'हिन्दी'  },
  { code: 'te', name: 'Telugu',  nativeName: 'తెలుగు'  },
  { code: 'ta', name: 'Tamil',   nativeName: 'தமிழ்'   },
] as const

/** Set of valid language codes — use for validation. */
export const SUPPORTED_CODES: ReadonlySet<string> = new Set(
  SUPPORTED_LANGUAGES.map((l) => l.code),
)

/**
 * Look up a Language by code.
 * Returns undefined if not found — caller must handle gracefully.
 */
export function getLanguage(code: string): Language | undefined {
  return SUPPORTED_LANGUAGES.find((l) => l.code === code)
}

/**
 * Return true if code is in the supported language set.
 */
export function isSupportedLanguage(code: string): boolean {
  return SUPPORTED_CODES.has(code)
}

/**
 * M5 intent: participant language configuration.
 *
 * This type represents what the frontend will send to the backend
 * when a player configures their language pair.  It is NOT yet used
 * in M4 — defined here so M5 can import it without renaming.
 *
 * sourceLanguage:  "My language" — what I speak (mic input)
 * targetLanguage:  "Teammate language" — what I want to hear
 */
export interface ParticipantLanguageConfig {
  sourceLanguage: string  // BCP-47 code from SUPPORTED_CODES
  targetLanguage: string  // BCP-47 code from SUPPORTED_CODES
}

/** Default configuration — preserves M4 English-only behavior. */
export const DEFAULT_LANGUAGE_CONFIG: ParticipantLanguageConfig = {
  sourceLanguage: 'en',
  targetLanguage: 'en',
}
