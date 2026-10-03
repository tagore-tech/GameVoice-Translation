"""M1 WebSocket handshake verification test."""
import asyncio
import json
import sys
import websockets


async def test_handshake():
    uri = "ws://localhost:8000/ws/TESTROOM"
    print(f"Connecting to {uri} ...")

    async with websockets.connect(uri) as ws:
        raw = await ws.recv()
        msg = json.loads(raw)
        print(f"Received: {json.dumps(msg, indent=2)}")

        errors = []
        if msg.get("type") != "connected":
            errors.append(f"Expected type='connected', got {msg.get('type')!r}")
        if msg.get("room_id") != "TESTROOM":
            errors.append(f"Expected room_id='TESTROOM', got {msg.get('room_id')!r}")
        if msg.get("peers") != 1:
            errors.append(f"Expected peers=1, got {msg.get('peers')!r}")

        if errors:
            for e in errors:
                print(f"FAIL: {e}")
            sys.exit(1)
        else:
            print("WebSocket handshake: PASS")


if __name__ == "__main__":
    asyncio.run(test_handshake())
