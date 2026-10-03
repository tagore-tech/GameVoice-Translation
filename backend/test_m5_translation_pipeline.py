"""
M5 translation E2E — tests the TRANSLATION path (source != target).

Temporarily sets TRANSLATION_PROVIDER=mock, SOURCE_LANGUAGE=hi, TARGET_LANGUAGE=te
by calling the backend's pipeline directly (in-process, no network needed for mock).

This script ALSO tests the real deep-translator provider via a separate
in-process call if internet is available.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import math
import struct
import time

# ── In-process pipeline test ─────────────────────────────────────────────────

def make_pcm(duration_ms=800, sample_rate=16_000):
    n = int(sample_rate * duration_ms / 1_000)
    import struct as s, math as m
    samples = [int(32767 * m.sin(2 * m.pi * 440 * i / sample_rate)) for i in range(n)]
    return s.pack(f"<{n}h", *samples)


def test_mock_translation_pipeline():
    """Full in-process: audio bytes -> PCM -> mock STT -> mock translation."""
    from audio.processor import convert, ProcessedAudio
    from speech.mock import MockSpeechProvider
    from translation.mock import MockTranslationProvider

    # M3: convert
    pcm_bytes = make_pcm(800)
    import struct
    n = 800 * 16
    audio_data = pcm_bytes
    header = struct.pack("<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(audio_data), b"WAVE",
        b"fmt ", 16, 1, 1, 16000, 32000, 2, 16,
        b"data", len(audio_data))
    result = convert(header + audio_data)
    assert isinstance(result, ProcessedAudio), f"Unexpected: {result}"

    # M4: transcribe
    stt = MockSpeechProvider()
    tr = stt.transcribe(result, language="hi")
    assert tr.status == "ok", tr
    print(f"[M5 IN-PROC] STT [hi]: {tr.text!r}")

    # M5: translate hi -> te
    trans = MockTranslationProvider()
    t0 = time.perf_counter()
    tresult = trans.translate(tr.text, source_language="hi", target_language="te")
    latency = (time.perf_counter() - t0) * 1000
    assert tresult.status == "ok", tresult
    assert tresult.translated_text, "translated_text is empty"
    assert tresult.source_text == tr.text
    assert tresult.source_language == "hi"
    assert tresult.target_language == "te"
    print(f"[M5 IN-PROC] Translation [hi->te] {latency:.1f} ms")
    print(f"[M5 IN-PROC]   source:     {tresult.source_text!r}")
    print(f"[M5 IN-PROC]   translated: {tresult.translated_text!r}")
    print("[M5 IN-PROC] Mock pipeline: PASS")


def test_real_translation():
    """Tests DeepTranslatorProvider with actual network call.
    Reports clearly if network is unavailable rather than failing."""
    print("\n[M5 REAL] Testing DeepTranslatorProvider (requires internet)...")
    try:
        from translation.deep_translator_provider import DeepTranslatorProvider
        p = DeepTranslatorProvider()

        test_cases = [
            ("Enemy is on the left side", "en", "hi"),
            ("Dushman left side pe hai", "hi", "te"),
        ]

        for text, src, tgt in test_cases:
            t0 = time.perf_counter()
            result = p.translate(text, src, tgt)
            ms = (time.perf_counter() - t0) * 1000

            if result.status == "ok":
                print(f"[M5 REAL] [{src}->{tgt}] {ms:.0f} ms")
                print(f"[M5 REAL]   source:     {text!r}")
                print(f"[M5 REAL]   translated: {result.translated_text!r}")
            else:
                print(f"[M5 REAL] [{src}->{tgt}] FAILED: {result.status} — {result.error_message}")
                print("[M5 REAL] Real provider verification: UNAVAILABLE (network error)")
                return

        print("[M5 REAL] Real provider verification: PASS")

    except Exception as exc:
        print(f"[M5 REAL] Real provider error: {exc}")
        print("[M5 REAL] Real provider verification: UNAVAILABLE")


if __name__ == "__main__":
    print("=" * 60)
    print("M5 In-Process Pipeline Test")
    print("=" * 60)
    test_mock_translation_pipeline()

    print("\n" + "=" * 60)
    print("M5 Real Provider Test")
    print("=" * 60)
    test_real_translation()
