# GameVoice Translator — PRE-M5 Architecture Document

> **Status:** PRE-M5 architecture preparation complete.
> M5 (translation) is **NOT YET IMPLEMENTED**.
> This document describes what exists and what M5 will build on.

---

## 1. Current Pipeline (M1–M4, verified)

```
Player
  │
  ▼ (microphone, browser MediaRecorder)
WebM/Opus audio blob
  │
  ▼ (WebSocket binary frame)
FastAPI backend  /ws/{room_id}
  │
  ▼  M3 — audio/processor.py (PyAV)
16 kHz / mono / signed 16-bit PCM  [in-memory only, never saved]
  │
  ▼  M4 — speech/base.SpeechProvider
Transcription text
  │
  ▼ (WebSocket JSON)
Frontend transcription panel
```

### M4 Limitation — Mock STT
The current `SpeechProvider` is `MockSpeechProvider`.
It returns pre-written rotating gaming phrases.
**It does NOT perform real speech recognition.**

Real STT requires setting `STT_PROVIDER=google` or `STT_PROVIDER=deepgram`
in `backend/.env` and supplying valid credentials.
M4 is **STT only** — no translation.

---

## 2. Language Model

### Single source of truth: `backend/languages.py`

All language codes across the backend come from this module.
No raw language strings are scattered in other files.

```python
SUPPORTED_LANGUAGES = (
    Language(code="en", name="English",  native_name="English"),
    Language(code="hi", name="Hindi",    native_name="हिन्दी"),
    Language(code="te", name="Telugu",   native_name="తెలుగు"),
    Language(code="ta", name="Tamil",    native_name="தமிழ்"),
)
```

### Frontend mirror: `frontend/src/config/languages.ts`

Keeps the same list in TypeScript.
M5 will import `SUPPORTED_LANGUAGES` to render a language picker.

### Validation

```python
validate_language_code("hi")   # → Language(code="hi", ...)
validate_language_code("zz")   # → raises ValueError (never silent fallback)
```

---

## 3. Source Language vs. Target Language

| Field | Meaning | Where configured now |
|---|---|---|
| `source_language` | What this player speaks (mic input) | `SOURCE_LANGUAGE` env / `settings.source_language` |
| `target_language` | What they want to hear (M5 translation output) | `TARGET_LANGUAGE` env / `settings.target_language` |

Defaults: both `"en"` — preserves M4 English-only behavior.

---

## 4. Participant Language Concept

`ParticipantLanguageConfig` (in `backend/languages.py`) holds the
language pair for one player. It is a **data contract only** — no routing
is implemented.

```python
# Future M7 usage:
player_a = ParticipantLanguageConfig(source_language="hi", target_language="te")
player_b = ParticipantLanguageConfig(source_language="te", target_language="hi")

player_a.needs_translation   # → True
player_a.source.name         # → "Hindi"
player_a.target.native_name  # → "తెలుగు"
```

M7 will attach one `ParticipantLanguageConfig` per WebSocket connection
in the room registry. No routing changes are needed in `ws/handler.py`
until M7.

---

## 5. TranslationProvider Contract

`backend/translation/base.py` defines the full interface.
No real translation is implemented — this is the contract only.

### Interface

```python
class TranslationProvider(ABC):
    name: str
    supported_pairs: frozenset[tuple[str, str]]

    def supports_pair(self, source: str, target: str) -> bool: ...

    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult: ...   # NEVER raises
```

### Result

```python
@dataclass(frozen=True)
class TranslationResult:
    status: str               # 'ok' | 'no_input' | 'unsupported_pair' |
                              # 'provider_error' | 'timeout' | 'auth_error' |
                              # 'quota_exceeded'
    source_language: str
    target_language: str
    provider: str
    translation_latency_ms: float
    translated_text: str      # "" on failure
    error_message: str | None
    source_text: str          # always the original input (for fallback display)
    confidence: float | None
    metadata: dict
    succeeded: bool           # property: status == 'ok'
```

### Provider hierarchy

```
TranslationProvider (abstract)
  ├── MockTranslationProvider   (backend/translation/mock.py)    ← exists
  └── DeepTranslatorProvider    (future)
  └── GoogleTranslateProvider   (future)
```

Factory: `get_translation_provider(name)` in `backend/translation/provider.py`

---

## 6. Error Contract

Translation failures return a `TranslationResult` with `status != 'ok'`.
`translate()` NEVER raises — callers branch on `result.succeeded`.

| `status` | Cause |
|---|---|
| `no_input` | Empty or whitespace-only text |
| `unsupported_pair` | Provider cannot handle this language pair |
| `provider_error` | Internal exception inside provider |
| `timeout` | Provider exceeded time limit |
| `auth_error` | API key/credentials rejected |
| `quota_exceeded` | Rate limit or quota exceeded |

**Fallback policy (M5 responsibility):**
If `result.succeeded` is `False`, M5 must display the original
transcription text unchanged. `result.source_text` always contains
the original so fallback display requires no additional state.

---

## 7. WebSocket Message Contract

### Existing M4 messages (do not break)

```json
// Successful transcription (M4, current)
{
  "type": "transcription",
  "text": "Enemy spotted on the left",
  "language": "en",
  "provider": "mock",
  "confidence": 0.99,
  "audio_duration_ms": 800.0,
  "audio_conversion_ms": 14.5,
  "stt_latency_ms": 0.0,
  "total_latency_ms": 14.6
}

// Non-fatal status (no speech, empty audio)
{ "type": "transcription_status", "status": "no_speech|empty_audio", ... }

// Error
{ "type": "transcription_error", "code": "...", "message": "...", "provider": "..." }

// M3 audio error
{ "type": "audio_error", "code": "...", "message": "..." }
```

### Future M5 messages (NOT yet sent — contract only)

```json
// Successful translation (future M5)
{
  "type": "translation",
  "source_language": "hi",
  "target_language": "te",
  "source_text": "Dushman left side pe hai",
  "translated_text": "ఎడమ వైపు శత్రువు ఉన్నాడు",
  "provider": "deep_translator",
  "stt_latency_ms": 120,
  "translation_latency_ms": 80,
  "total_latency_ms": 215
}

// Translation failure (future M5) — source_text always included
{
  "type": "translation_error",
  "code": "provider_error|unsupported_pair|timeout|...",
  "message": "...",
  "source_text": "Dushman left side pe hai"
}
```

The M4 `transcription` message is preserved unchanged.
M5 adds the new `translation`/`translation_error` types alongside it.

---

## 8. Future M5 Pipeline

```
[M4 output]  Transcription text  (e.g. "Dushman left side pe hai")
                │
                ▼
    ParticipantLanguageConfig
    source_language = "hi"
    target_language = "te"
                │
                ▼  ws/handler.py M5 path
    validate_language_code(source)  ← languages.py
    validate_language_code(target)  ← languages.py
                │
                ▼
    TranslationProvider.translate(text, "hi", "te")   ← FUTURE
                │
                ▼
    TranslationResult
    status = "ok"
    translated_text = "ఎడమ వైపు శత్రువు ఉన్నాడు"
                │
                ▼  WebSocket JSON
    { "type": "translation", ... }
                │
                ▼
    Frontend translation panel   ← FUTURE M5 UI
```

> M5 and M6 are **FUTURE**. The diagram above is the intended design.

---

## 9. What Belongs to M5

- Wiring `TranslationProvider` into `ws/handler.py`
- Sending `{"type": "translation", ...}` WebSocket messages
- Loading `TRANSLATION_PROVIDER` from config at startup
- Real translation (e.g. `deep-translator` library)
- Frontend translation panel UI
- Language picker UI (using `frontend/src/config/languages.ts`)

## 10. What Does NOT Belong to M5

| Feature | Milestone |
|---|---|
| Text-to-Speech | M6 |
| Audio playback of translated speech | M6 |
| Multi-player WebSocket routing | M7 |
| Per-player language configuration over WebSocket | M7 |
| WebRTC | Not planned for prototype |
| VAD / noise suppression | Not planned for prototype |
| Real STT (Google/Deepgram) | M4-credentials (whenever credentials available) |

## 11. How M7 Uses Per-Player Language Configuration

M7 will maintain a `dict[WebSocket, ParticipantLanguageConfig]` inside
`handle_connection()`. When Player A sends audio:

1. Look up `ParticipantLanguageConfig` for Player A's socket.
2. Run M4 STT with `source_language`.
3. If `needs_translation`: run M5 `TranslationProvider.translate(...)` with the pair.
4. Broadcast the translation result to all sockets in the room *except* Player A
   (or to each player with their own target language — M7 design decision).

No changes to `languages.py`, `translation/base.py`, or `speech/base.py`
are needed for M7 — the contracts already support it.

---

## 12. Provider Abstraction Rationale

Both `SpeechProvider` and `TranslationProvider` follow the same pattern:

- Abstract base class in `base.py` defines the interface.
- Concrete providers are isolated in their own files.
- A `provider.py` factory is the only location that knows which provider is active.
- `ws/handler.py` imports only the abstract base and the factory.
- Swapping providers = changing one env variable + optionally adding one file.
- All provider methods return structured value types (never raise, never None).

This means adding Google Cloud STT, Deepgram, or any future provider
never requires changes to the handler, config system, or test infrastructure.

---

## 13. Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `STT_PROVIDER` | `mock` | STT implementation |
| `TRANSLATION_PROVIDER` | `mock` | Translation implementation |
| `SOURCE_LANGUAGE` | `en` | Player's spoken language |
| `TARGET_LANGUAGE` | `en` | Desired output language |
| `TTS_PROVIDER` | `gtts` | TTS implementation (future M6) |

All values read from `backend/.env` via `python-dotenv`.
No credentials are in source control.
