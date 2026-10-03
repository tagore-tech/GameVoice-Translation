"""
Quick end-to-end pipeline test:
  browser (simulated) → binary WebSocket frame → backend → PyAV → PCM ack

Sends a real WAV file as binary over WebSocket and verifies the backend
returns an audio_processed message with the correct PCM format.

This is the M3 success criteria test: PTT→WebSocket→backend→PyAV→PCM.
"""

import asyncio
import io
import json
import math
import struct
import sys
import websockets


def make_wav_bytes(duration_ms: int = 800) -> bytes:
    """Pure-Python WAV generator — no external tools."""
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


async def test_binary_pipeline():
    uri = "ws://localhost:8000/ws/M3TEST"
    print(f"[M3 E2E] Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        # Wait for the 'connected' ack
        connected_raw = await ws.recv()
        connected = json.loads(connected_raw)
        assert connected["type"] == "connected", f"Expected 'connected', got {connected}"
        print(f"[M3 E2E] Connected — room: {connected['room_id']}, peers: {connected['peers']}")

        # Send a WAV file as binary (simulates browser sendBinary())
        wav_bytes = make_wav_bytes(duration_ms=800)
        print(f"[M3 E2E] Sending {len(wav_bytes) / 1_024:.1f} KB as binary frame ...")
        await ws.send(wav_bytes)

        # Receive the backend's audio_processed response
        response_raw = await ws.recv()
        response = json.loads(response_raw)
        print(f"[M3 E2E] Response: {json.dumps(response, indent=2)}")

        # Validate the response
        errors = []

        if response.get("type") != "audio_processed":
            errors.append(f"Expected type='audio_processed', got {response.get('type')!r}")
        if response.get("sample_rate") != 16_000:
            errors.append(f"Expected sample_rate=16000, got {response.get('sample_rate')!r}")
        if response.get("channels") != 1:
            errors.append(f"Expected channels=1, got {response.get('channels')!r}")
        if response.get("sample_format") != "s16":
            errors.append(f"Expected sample_format='s16', got {response.get('sample_format')!r}")
        if not response.get("pcm_bytes", 0) > 0:
            errors.append("Expected pcm_bytes > 0")
        if response.get("status") != "ok":
            errors.append(f"Expected status='ok', got {response.get('status')!r}")

        if errors:
            for e in errors:
                print(f"[M3 E2E] FAIL: {e}")
            sys.exit(1)
        else:
            print(f"[M3 E2E] [OK] PCM format: {response['sample_rate']} Hz, "
                  f"ch={response['channels']}, fmt={response['sample_format']}")
            print(f"[M3 E2E] [OK] PCM size: {response['pcm_bytes']} bytes")
            print(f"[M3 E2E] [OK] Audio duration: {response['audio_duration_ms']} ms")
            print(f"[M3 E2E] [OK] Conversion time: {response['conversion_ms']} ms")
            print(f"[M3 E2E] [OK] Total processing: {response['processing_ms']} ms")
            print("[M3 E2E] Binary audio pipeline: PASS")


if __name__ == "__main__":
    asyncio.run(test_binary_pipeline())
