"""
M4 end-to-end test:
  Simulated PTT -> WAV binary -> WebSocket -> backend -> M3 PyAV
  -> M4 MockSpeechProvider -> transcription JSON -> verified

This test connects to the live backend and exercises the complete
M3+M4 pipeline without a real microphone or browser.
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
    uri = "ws://localhost:8000/ws/M4TEST"
    print(f"[M4 E2E] Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        # Expect connected ack
        connected = json.loads(await ws.recv())
        assert connected["type"] == "connected", connected
        print(f"[M4 E2E] Connected — room: {connected['room_id']}")

        # Send binary audio (simulates browser PTT release)
        wav = make_wav_bytes(duration_ms=800)
        print(f"[M4 E2E] Sending {len(wav) / 1_024:.1f} KB audio ...")
        await ws.send(wav)

        # Expect transcription response
        resp = json.loads(await ws.recv())
        print(f"[M4 E2E] Response:\n{json.dumps(resp, indent=2)}")

        errors = []
        if resp.get("type") != "transcription":
            errors.append(f"Expected type='transcription', got {resp.get('type')!r}")
        if not resp.get("text"):
            errors.append("Expected non-empty transcription text")
        if resp.get("language") != "en":
            errors.append(f"Expected language='en', got {resp.get('language')!r}")
        if resp.get("provider") != "mock":
            errors.append(f"Expected provider='mock', got {resp.get('provider')!r}")
        if not isinstance(resp.get("stt_latency_ms"), (int, float)):
            errors.append("Missing stt_latency_ms")
        if not isinstance(resp.get("total_latency_ms"), (int, float)):
            errors.append("Missing total_latency_ms")
        if not isinstance(resp.get("audio_conversion_ms"), (int, float)):
            errors.append("Missing audio_conversion_ms")

        if errors:
            for e in errors:
                print(f"[M4 E2E] FAIL: {e}")
            sys.exit(1)
        else:
            print(f"[M4 E2E] [OK] Text: {resp['text']!r}")
            print(f"[M4 E2E] [OK] Language: {resp['language']}")
            print(f"[M4 E2E] [OK] Provider: {resp['provider']}")
            print(f"[M4 E2E] [OK] Audio conversion: {resp['audio_conversion_ms']} ms")
            print(f"[M4 E2E] [OK] STT latency: {resp['stt_latency_ms']} ms")
            print(f"[M4 E2E] [OK] Total latency: {resp['total_latency_ms']} ms")
            print("[M4 E2E] Full PTT->WebSocket->PyAV->STT->transcription pipeline: PASS")


if __name__ == "__main__":
    asyncio.run(run())
