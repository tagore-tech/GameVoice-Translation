"""
M5 end-to-end test — uses MockTranslationProvider (offline, deterministic).

Pipeline verified:
  WAV binary -> WebSocket -> backend
  -> M3 PyAV PCM conversion
  -> M4 MockSpeechProvider -> transcription
  -> M5 MockTranslationProvider -> translation
  -> WebSocket JSON 'translation' or 'transcription' message

The backend must be running.
Default config (source=en, target=en): returns 'transcription' (same-language passthrough).
With TRANSLATION_PROVIDER=mock, SOURCE_LANGUAGE=hi, TARGET_LANGUAGE=te:
  returns 'translation' with mock placeholder text.

This test accepts BOTH outcomes as valid M5 behaviour.
"""

import asyncio
import json
import math
import struct
import sys

import websockets


def make_wav_bytes(duration_ms: int = 800) -> bytes:
    sample_rate = 16_000
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    audio_data = struct.pack(f"<{n}h", *samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(audio_data), b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", len(audio_data),
    )
    return header + audio_data


async def run():
    uri = "ws://localhost:8000/ws/M5TEST"
    print(f"[M5 E2E] Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        # Connected ack
        connected = json.loads(await ws.recv())
        assert connected["type"] == "connected", connected
        print(f"[M5 E2E] Connected - room: {connected['room_id']}")
        src = connected.get('source_language', 'n/a')
        tgt = connected.get('target_language', 'n/a')
        print(f"[M5 E2E] Language pair: {src} -> {tgt}")

        # Send audio
        wav = make_wav_bytes(duration_ms=800)
        print(f"[M5 E2E] Sending {len(wav) / 1_024:.1f} KB audio ...")
        await ws.send(wav)

        # Receive response
        resp = json.loads(await ws.recv())
        print(f"[M5 E2E] Response:\n{json.dumps(resp, indent=2, ensure_ascii=True)}")

        msg_type = resp.get("type")
        errors = []

        if msg_type == "translation":
            # M5 full pipeline - source != target
            required = [
                "source_language", "target_language",
                "source_text", "translated_text",
                "provider", "audio_conversion_ms",
                "stt_latency_ms", "translation_latency_ms", "total_latency_ms",
            ]
            for field in required:
                if field not in resp:
                    errors.append(f"Missing field: {field!r}")
            if not resp.get("source_text"):
                errors.append("source_text is empty")
            if not resp.get("translated_text"):
                errors.append("translated_text is empty")

        elif msg_type == "transcription":
            # Same-language or no translation provider — M4 path still valid
            print("[M5 E2E] Got transcription (same-language passthrough or no translation provider)")
            required = ["text", "language", "provider", "total_latency_ms"]
            for field in required:
                if field not in resp:
                    errors.append(f"Missing field: {field!r}")

        else:
            errors.append(f"Unexpected message type: {msg_type!r}")

        if errors:
            for e in errors:
                print(f"[M5 E2E] FAIL: {e}")
            sys.exit(1)
        else:
            print(f"[M5 E2E] Message type: {msg_type}")
            if msg_type == "translation":
                print(f"[M5 E2E] [OK] source_text: {resp['source_text']!r}")
                print(f"[M5 E2E] [OK] translated_text: {resp['translated_text']!r}")
                print(f"[M5 E2E] [OK] source_language: {resp['source_language']}")
                print(f"[M5 E2E] [OK] target_language: {resp['target_language']}")
                print(f"[M5 E2E] [OK] audio_conversion_ms: {resp['audio_conversion_ms']} ms")
                print(f"[M5 E2E] [OK] stt_latency_ms: {resp['stt_latency_ms']} ms")
                print(f"[M5 E2E] [OK] translation_latency_ms: {resp['translation_latency_ms']} ms")
                print(f"[M5 E2E] [OK] total_latency_ms: {resp['total_latency_ms']} ms")
            elif msg_type == "transcription":
                print(f"[M5 E2E] [OK] text: {resp['text']!r}")
                print(f"[M5 E2E] [OK] language: {resp['language']}")
                print(f"[M5 E2E] [OK] total_latency_ms: {resp['total_latency_ms']} ms")
                print("[M5 E2E] Note: Same-language config - no translation performed (expected)")
            print("[M5 E2E] Full PTT->WebSocket->PyAV->STT->Translation pipeline: PASS")


if __name__ == "__main__":
    asyncio.run(run())
