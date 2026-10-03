"""
M-REAL-STT smoke test — GeminiSpeechProvider

Verifies the Gemini STT pipeline with a real WAV audio file.
Requires GEMINI_API_KEY in environment or backend/.env.

Usage:
    cd backend
    .venv\\Scripts\\python.exe test_gemini_stt_smoke.py

Expected output with a valid key:
    [SMOKE] GeminiSpeechProvider loaded. model=gemini-2.0-flash
    [SMOKE] Sending 25.0 KB WAV (800 ms audio) to Gemini...
    [SMOKE] STATUS: ok
    [SMOKE] PROVIDER: gemini
    [SMOKE] LANGUAGE: en
    [SMOKE] STT_LATENCY: xxx ms
    [SMOKE] TEXT: "..."
    [SMOKE] Gemini STT smoke test: PASS

Without GEMINI_API_KEY:
    [SMOKE] STATUS: provider_error
    [SMOKE] ERROR: GEMINI_API_KEY is not configured...
    [SMOKE] Gemini STT smoke test: CREDENTIAL_MISSING (expected without API key)
"""

import math
import os
import struct
import sys

# Ensure we can import from backend/
sys.path.insert(0, os.path.dirname(__file__))

# Load backend/.env into os.environ before any provider code reads os.getenv().
# This mirrors what config.py does at module level (load_dotenv() with no args
# searches from the process cwd upward). Using an explicit path here makes the
# smoke test work regardless of the working directory it is launched from.
from dotenv import load_dotenv
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)


def make_wav(duration_ms: int = 800, sample_rate: int = 16_000) -> bytes:
    """Synthesise a sine-wave WAV file (440 Hz)."""
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    pcm = struct.pack(f"<{n}h", *samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", len(pcm),
    )
    return header + pcm


def run_smoke_test():
    from audio.processor import ProcessedAudio
    from speech.gemini_stt import GeminiSpeechProvider

    # Construct provider
    provider = GeminiSpeechProvider()
    print(f"[SMOKE] GeminiSpeechProvider loaded. model={provider._model!r}")

    # Construct audio object (simulating what M3 produces)
    duration_ms = 800
    sample_rate = 16_000
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    pcm = struct.pack(f"<{n}h", *samples)

    audio = ProcessedAudio(
        pcm_bytes=pcm,
        sample_rate=sample_rate,
        channels=1,
        sample_format="s16",
        duration_ms=float(duration_ms),
        conversion_ms=5.0,
    )

    print(f"[SMOKE] Sending {len(pcm) / 1024:.1f} KB WAV ({duration_ms} ms audio) to Gemini...")

    result = provider.transcribe(audio, language="en")

    print(f"[SMOKE] STATUS: {result.status}")
    print(f"[SMOKE] PROVIDER: {result.provider}")
    print(f"[SMOKE] LANGUAGE: {result.language}")
    print(f"[SMOKE] STT_LATENCY: {result.stt_latency_ms} ms")

    if result.status == "ok":
        print(f"[SMOKE] TEXT: {result.text!r}")
        print("[SMOKE] Gemini STT smoke test: PASS")
        return True

    elif result.status == "provider_error" and "GEMINI_API_KEY" in (result.error_message or ""):
        print(f"[SMOKE] ERROR: {result.error_message}")
        print("[SMOKE] Gemini STT smoke test: CREDENTIAL_MISSING (expected without API key)")
        print("[SMOKE] To enable real STT: add GEMINI_API_KEY=AIza... to backend/.env")
        return None  # not a failure — credentials just not configured

    elif result.status == "provider_error":
        print(f"[SMOKE] ERROR: {result.error_message}")
        print("[SMOKE] Gemini STT smoke test: PROVIDER_ERROR")
        return False

    elif result.status == "no_speech":
        print("[SMOKE] Gemini returned no_speech for a sine tone — expected (no real speech)")
        print("[SMOKE] Gemini STT smoke test: NO_SPEECH (API call succeeded)")
        return True  # API call worked, just no speech in a sine wave

    else:
        print(f"[SMOKE] Unexpected status: {result.status}")
        return False


if __name__ == "__main__":
    ok = run_smoke_test()
    if ok is False:
        sys.exit(1)
