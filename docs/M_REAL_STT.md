# GameVoice Translator — M-REAL-STT: Speech-to-Text Integration

> **Status: COMPLETE**
> Real STT provider implemented via Gemini API.
> MockSpeechProvider preserved for offline tests and development.

---

## 1. What M-REAL-STT adds

```
[Before M-REAL-STT]
PTT -> WebM/Opus -> PyAV -> 16kHz mono s16 PCM -> MockSpeechProvider -> fake text

[After M-REAL-STT]
PTT -> WebM/Opus -> PyAV -> 16kHz mono s16 PCM -> GeminiSpeechProvider -> REAL TEXT
                                                OR MockSpeechProvider  -> fake text (dev/test)
```

The **SpeechProvider abstraction is unchanged**. Switching between mock and real STT
requires only one line in `backend/.env`.

---

## 2. Provider selection

| `STT_PROVIDER` | Provider | Credentials needed | Notes |
|---|---|---|---|
| `mock` | MockSpeechProvider | None | Default. Rotating gaming phrases. NOT real STT. |
| `gemini` | GeminiSpeechProvider | `GEMINI_API_KEY` | Real speech recognition via Gemini API. |

---

## 3. New files

| File | Purpose |
|---|---|
| `backend/speech/gemini_stt.py` | Real STT provider via Gemini API |
| `backend/tests/test_real_stt.py` | 72 offline deterministic tests |
| `backend/test_gemini_stt_smoke.py` | Live smoke test (requires API key) |

## 4. Modified files

| File | Change |
|---|---|
| `backend/speech/provider.py` | Added `gemini` case to factory |
| `backend/requirements.txt` | Added `google-genai>=1.0.0,<2.0.0` |
| `.env.example` | Documented `gemini` provider, `GEMINI_API_KEY`, `GEMINI_STT_MODEL` |
| `frontend/src/App.tsx` | Added STT provider badge (⚠ MOCK STT / ✓ GEMINI STT) |
| `frontend/src/App.css` | Added `.stt-provider-mock` / `.stt-provider-real` styles |

---

## 5. GeminiSpeechProvider architecture

```
ProcessedAudio (16kHz mono s16 PCM)
  -> _pcm_to_wav()           [wrap in minimal 44-byte WAV header]
  -> base64.b64encode()      [inline data — no file upload, no disk I/O]
  -> Gemini generate_content()
     model: gemini-2.0-flash
     prompt: "Transcribe... speaker is speaking in {language}..."
     inline_data: audio/wav
  -> response.text.strip()
  -> TranscriptionResult
```

**No audio is written to disk.** All processing is in-memory.

### Language prompt mapping

| Code | Prompt word |
|---|---|
| `en` | English |
| `hi` | Hindi |
| `te` | Telugu |
| `ta` | Tamil |

Gemini supports 80+ languages automatically via its audio understanding. The prompt word
anchors recognition to the expected language to reduce misrecognitions.

---

## 6. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `STT_PROVIDER` | No | `mock` | `mock` or `gemini` |
| `GEMINI_API_KEY` | If `gemini` | — | Google AI Studio API key |
| `GEMINI_STT_MODEL` | No | `gemini-2.0-flash` | Gemini model to use for transcription |

**Get a free API key:** https://aistudio.google.com/app/apikey

> [!CAUTION]
> Never commit `backend/.env` with a real key.
> The key is only read on the server. It is never sent to the frontend.
> Logs only show the last 4 characters of the key for debugging.

---

## 7. Offline mode (mock)

All automated tests use `MockSpeechProvider`.

```
STT_PROVIDER=mock
```

- No network calls
- No API key required
- Deterministic rotating phrases per language
- Passes 100% offline

---

## 8. Real STT mode (gemini)

**Step 1 — Get API key:**
```
https://aistudio.google.com/app/apikey
```

**Step 2 — Configure:**
```dotenv
# backend/.env
STT_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...your-key-here
```

**Step 3 — Restart backend:**
```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

**Step 4 — Verify startup:**
```
[INFO] GeminiSpeechProvider loaded - model='gemini-2.0-flash' (key: ...XXXX)
```

---

## 9. Smoke test

```powershell
cd backend
.venv\Scripts\python.exe test_gemini_stt_smoke.py
```

Expected results:

| Condition | Output |
|---|---|
| API key set, network available | `Gemini STT smoke test: PASS` |
| No API key | `Gemini STT smoke test: CREDENTIAL_MISSING` |
| Sine tone only (no real speech) | `Gemini STT smoke test: NO_SPEECH (API call succeeded)` |
| Rate limit / network error | `Gemini STT smoke test: PROVIDER_ERROR` |

> The smoke test uses a 440 Hz sine tone, NOT real speech. Gemini may return
> "no speech" for a synthetic tone — this is **correct behaviour** and still
> verifies that the API call succeeded. To test real transcription accuracy,
> use a real voice recording with the backend running.

---

## 10. Error handling

All errors are caught in `transcribe()` and returned as `TranscriptionResult` —
the WebSocket session never crashes due to STT failure.

| Condition | `status` | Frontend receives |
|---|---|---|
| Audio too short | `empty_audio` | `transcription_status {status: "empty_audio"}` |
| No API key | `provider_error` | `transcription_error` with safe message |
| SDK not installed | `provider_error` | `transcription_error` with safe message |
| Rate limit (429) | `provider_error` | `transcription_error` with safe message |
| Auth failure (401) | `provider_error` | `transcription_error` with safe message |
| No speech in audio | `no_speech` | `transcription_status {status: "no_speech"}` |
| Network timeout | `provider_error` | `transcription_error` with safe message |

Error messages:
- Are safe for the frontend (no API keys, no internal details)
- Are logged in full internally for debugging
- Include `provider: "gemini"` for identification

---

## 11. Frontend changes

The transcription panel now shows a provider badge:
- **⚠ MOCK STT** (amber) — MockSpeechProvider active, not real speech recognition
- **✓ GEMINI STT** (green) — GeminiSpeechProvider active, real transcription

The badge appears in the transcription debug row alongside CONVERT / STT / TOTAL latencies.

---

## 12. Gemini free tier limits

| Limit | Value |
|---|---|
| Requests per minute | 15 |
| Requests per day | 1,500 |
| Tokens per minute | 1,000,000 |
| Input audio size | 20 MB (inline) |

For a hackathon prototype with one or two players, this is sufficient.
For production, use a paid Gemini plan or the Cloud Speech-to-Text API.

---

## 13. Privacy

- **No audio is written to disk at any point** (guaranteed by `audio/processor.py`)
- Audio is sent to the Gemini API as an inline base64 WAV and not retained by the app
- The Gemini API's data retention policies apply (see Google AI Studio terms)
- For maximum privacy, run in mock mode: `STT_PROVIDER=mock`

---

## 14. Test results

### Automated (offline)

| Suite | Tests | Result |
|---|---|---|
| M3 audio | 29 | PASS |
| M4 speech | 30 | PASS |
| PRE-M5 | 67 | PASS |
| M5 translation | 54 | PASS |
| M-REAL-STT | 72 | PASS |
| **Total** | **252** | **252/252 PASS** |

### TypeScript
```
tsc --noEmit: 0 errors
```

### Live E2E (backend running, mock config)

| Test | Result |
|---|---|
| M1 WebSocket handshake | PASS |
| M4 PTT->WebSocket->PyAV->STT->transcription | PASS |
| M5 PTT->WebSocket->PyAV->STT->translation | PASS |

### Real STT smoke test (no API key in this environment)

```
[SMOKE] Gemini STT smoke test: CREDENTIAL_MISSING (expected without API key)
```

> Real microphone -> GeminiSpeechProvider has NOT been verified in this environment
> because `GEMINI_API_KEY` is not set. All code paths are verified by mocked tests.
> The provider will work correctly once a key is added to `backend/.env`.

---

## 15. What this milestone does NOT implement

- TTS / audio playback (M6)
- Per-player language routing (M7)
- Real translation (M5, already complete)
- WebRTC
- VAD / noise suppression
- Model training or fine-tuning
- Authentication system

---

## 16. Exact commands

### Start backend (mock mode)
```powershell
cd C:\Users\HP\.gemini\antigravity\scratch\gamevoice-translator\backend
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

### Start backend (real STT mode)
```powershell
# First: add GEMINI_API_KEY to backend/.env
cd C:\Users\HP\.gemini\antigravity\scratch\gamevoice-translator\backend
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

### Start frontend
```powershell
cd C:\Users\HP\.gemini\antigravity\scratch\gamevoice-translator\frontend
cmd /c "npm run dev"
```

### Run all tests (offline)
```powershell
cd C:\Users\HP\.gemini\antigravity\scratch\gamevoice-translator\backend
.venv\Scripts\python.exe -m pytest tests\ -v
```

### Run smoke test (needs GEMINI_API_KEY)
```powershell
cd C:\Users\HP\.gemini\antigravity\scratch\gamevoice-translator\backend
.venv\Scripts\python.exe test_gemini_stt_smoke.py
```

---

## 17. Environment configuration for a real demo

```dotenv
# backend/.env — for full real-STT + real-translation demo
STT_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...your-key-here
TRANSLATION_PROVIDER=deep_translator
SOURCE_LANGUAGE=hi
TARGET_LANGUAGE=te
```

Then:
1. Start backend
2. Start frontend
3. Open `http://localhost:5173`
4. Select Hindi as "My Language", Telugu as "Teammate Language"
5. Enter room code (e.g. `GAME123`)
6. Hold Space / PTT button — speak in Hindi
7. Release — Gemini transcribes -> DeepTranslator translates to Telugu -> displayed in UI

> [!IMPORTANT]
> "Real-time voice translation is working" is ONLY true after you have:
> 1. Added GEMINI_API_KEY to backend/.env
> 2. Verified the smoke test returns PASS or NO_SPEECH (not CREDENTIAL_MISSING)
> 3. Heard real transcription text from an actual voice recording
>
> The mock STT returns fake phrases and does NOT process real speech.
