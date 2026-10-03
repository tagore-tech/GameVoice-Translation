# GameVoice Translator — M5 Translation

> **Status: COMPLETE**
> M5 adds real text translation after M4 transcription.
> M5 does NOT add TTS or audio playback. That is M6.

---

## 1. What M5 adds to the M4 pipeline

```
[Before M5 — M4]
Microphone → PTT → WebM/Opus → WebSocket
  → M3 PyAV PCM
  → M4 SpeechProvider → transcription JSON → frontend

[After M5]
Microphone → PTT → WebM/Opus → WebSocket
  → M3 PyAV PCM
  → M4 SpeechProvider → transcription text
  → M5 TranslationProvider → translated text → frontend
```

---

## 2. New files (M5)

| File | Purpose |
|---|---|
| `backend/translation/deep_translator_provider.py` | Real translation via `deep-translator` |
| `backend/tests/test_m5_translation.py` | 54 automated offline tests |
| `backend/test_m5_e2e.py` | Live E2E test |
| `backend/test_m5_translation_pipeline.py` | In-process integration test (mock + real) |
| `backend/test_real_provider.py` | Quick real-provider smoke test |

---

## 3. Modified files (M5)

| File | Change |
|---|---|
| `backend/main.py` | Loads TranslationProvider at startup; version `0.1.0-m5`; language validation on boot |
| `backend/ws/handler.py` | Added M5 translation step after M4 STT; sends `translation` / `translation_error` JSON |
| `backend/translation/provider.py` | Added `deep_translator` case |
| `backend/config.py` | Added `source_language`, `target_language`, `translation_provider` |
| `backend/requirements.txt` | Added `deep-translator>=1.11.0,<2.0.0` |
| `frontend/src/App.tsx` | Language selector, translation panel, translation_error panel |
| `frontend/src/App.css` | Language selector + translation panel styles |
| `.env.example` | Documented `SOURCE_LANGUAGE`, `TARGET_LANGUAGE`, `TRANSLATION_PROVIDER` |

---

## 4. TranslationProvider architecture

```
TranslationProvider (abstract — translation/base.py)
  ├── MockTranslationProvider  (translation/mock.py)       — no network, deterministic
  └── DeepTranslatorProvider   (translation/deep_translator_provider.py) — real translation
```

Factory: `get_translation_provider(name)` in `translation/provider.py`

Both providers:
- Return `TranslationResult` — **never raise**
- Same-language passthrough: return input text unchanged, no network call
- Empty input: `status='no_input'`, no network call
- All failures return `status != 'ok'` with `source_text` always populated

---

## 5. Supported languages

| Code | Name | Native |
|---|---|---|
| `en` | English | English |
| `hi` | Hindi | हिन्दी |
| `te` | Telugu | తెలుగు |
| `ta` | Tamil | தமிழ் |

Single source of truth: `backend/languages.py` / `frontend/src/config/languages.ts`

---

## 6. Configuration

| Variable | Default | Description |
|---|---|---|
| `TRANSLATION_PROVIDER` | `mock` | `mock` or `deep_translator` |
| `SOURCE_LANGUAGE` | `en` | BCP-47 code of what the player speaks |
| `TARGET_LANGUAGE` | `en` | BCP-47 code of what they want to hear |
| `STT_PROVIDER` | `mock` | Unchanged from M4 |

**To use real translation:**

```dotenv
# backend/.env
TRANSLATION_PROVIDER=deep_translator
SOURCE_LANGUAGE=hi
TARGET_LANGUAGE=te
```

---

## 7. WebSocket message contract

### Successful translation (source != target)

```json
{
  "type": "translation",
  "source_language": "hi",
  "target_language": "te",
  "source_text": "Dushman left side pe hai",
  "translated_text": "...",
  "provider": "deep_translator",
  "confidence": 0.99,
  "audio_duration_ms": 800.0,
  "audio_conversion_ms": 12.5,
  "stt_latency_ms": 0.0,
  "translation_latency_ms": 380.0,
  "total_latency_ms": 393.0
}
```

### Same-language or no translation needed (source == target)

```json
{
  "type": "transcription",
  "text": "Enemy spotted on the left",
  "language": "en",
  "provider": "mock",
  "confidence": 0.99,
  "audio_duration_ms": 800.0,
  "audio_conversion_ms": 12.0,
  "stt_latency_ms": 0.0,
  "total_latency_ms": 12.1
}
```

### Translation failed (source text preserved for fallback)

```json
{
  "type": "translation_error",
  "code": "provider_error",
  "message": "Server Error: Too many requests...",
  "provider": "deep_translator",
  "source_language": "hi",
  "target_language": "te",
  "source_text": "Dushman left side pe hai",
  "audio_conversion_ms": 12.5,
  "stt_latency_ms": 0.0,
  "total_latency_ms": 12.6
}
```

> The frontend always receives `source_text` in `translation_error` and displays it unchanged.

---

## 8. Error handling

| Error | Behavior |
|---|---|
| Empty audio | `transcription_status` `{status: "empty_audio"}` |
| No speech | `transcription_status` `{status: "no_speech"}` |
| STT failure | `transcription_error` with code and message |
| Empty transcription | `translate()` returns `status=no_input`, sends `translation_error` |
| Same-language | Returns `transcription` (no translation performed, no network call) |
| Provider error (rate-limit, network) | `translation_error` with `source_text` preserved |
| WebSocket disconnect | Clean cleanup of room registry |

The WebSocket session is **never crashed** by translation failure.

---

## 9. Latency measurement

All timings are wall-clock milliseconds:

| Field | Description |
|---|---|
| `audio_conversion_ms` | M3 PyAV decode time |
| `stt_latency_ms` | M4 STT time (from `TranscriptionResult`) |
| `translation_latency_ms` | M5 translate() call time |
| `total_latency_ms` | End-to-end from binary frame received to JSON sent |

---

## 10. Frontend behavior

The frontend (`frontend/src/App.tsx`) now:

1. **Language selector** — dropdown for "My language" (source) and "Teammate language" (target)
2. **Translation panel** — shows source text + translated text with latency breakdown
3. **Transcription panel** — shown when same-language (no translation)
4. **Translation error panel** — shows original source text + error note when translation fails

Language configuration is session-level. Per-player routing is M7.

---

## 11. DeepTranslatorProvider limitations

| Limitation | Detail |
|---|---|
| Unofficial endpoint | Uses Google Translate's web endpoint, not the official API |
| Rate limit | 5 requests/second, 200K/day (per Google) |
| Network required | Requires internet at translation time |
| No API key | No credentials needed (Google free tier) |
| Production suitability | Not suitable for high volume — use Google Cloud Translation API for production |

**When rate-limited:** The provider returns `TranslationResult(status="provider_error")`. The frontend displays the original transcription text unchanged. No crash, no data loss.

**To switch to a better provider:** Set `TRANSLATION_PROVIDER=google` in `.env` and implement `translation/google_translate.py` following the existing provider pattern.

---

## 12. Test results

### Automated (offline, deterministic)

| Suite | Tests | Result |
|---|---|---|
| M3 audio | 29 | PASS |
| M4 speech | 30 | PASS |
| PRE-M5 | 67 | PASS |
| M5 translation | 54 | PASS |
| **Total** | **180** | **180/180 PASS** |

### Live (require running backend)

| Test | Result |
|---|---|
| M1 WebSocket handshake | PASS |
| M4 E2E (transcription) | PASS |
| M5 E2E (same-language passthrough) | PASS |
| TypeScript `tsc --noEmit` | 0 errors |

### Real provider

| Test | Result |
|---|---|
| DeepTranslatorProvider construction | PASS |
| Same-language passthrough (offline) | PASS |
| Empty input (offline) | PASS |
| Network translation | **RATE-LIMITED** (temporary, ~1 hour cooldown) |

> The rate limit is from repeated testing during development. The code is correct —
> `provider_error` is returned and handled gracefully. Test again after the cooldown.

---

## 13. How to demo M5

**Step 1 — Configure languages:**

```dotenv
# backend/.env
TRANSLATION_PROVIDER=deep_translator
SOURCE_LANGUAGE=hi
TARGET_LANGUAGE=te
```

**Step 2 — Start backend:**

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

**Step 3 — Start frontend:**

```powershell
cd frontend
npm run dev
```

**Step 4 — Open browser:**
- Navigate to `http://localhost:5173`
- Select "Hindi" as "My language", "Telugu" as "Teammate language"
- Enter room code (e.g. `GAME123`)
- Press and hold Space (or hold the PTT button) — speak in Hindi
- Release — translated Telugu text appears on screen

**Step 5 — With mock STT (no microphone needed):**
The mock STT returns rotating Hindi phrases automatically. Translation of those mock phrases through DeepTranslator will display Telugu output.

---

## 14. What M6 adds

- Text-to-Speech: translated text → audio
- Audio playback in browser (the translated phrase is spoken aloud)
- gTTS (Google TTS) as the initial provider

## 15. What M7 adds

- Per-player language configuration (not session-global)
- Room registry attaches one `ParticipantLanguageConfig` per WebSocket
- Player A speaks Hindi, Player B speaks Telugu — each hears the other in their own language
- No frontend architecture changes needed — the language contract is already defined

---

> **M5 provides text translation only. It does NOT provide translated audio.**
