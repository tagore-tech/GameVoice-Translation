"""
backend/ws/handler.py — M5

WebSocket connection handler.

Routes incoming frames:
  - Text frames  → JSON control messages (echo, future control)
  - Binary frames → full STT+translation pipeline:
        validate → PyAV convert → SpeechProvider → TranslationProvider
        → translation JSON (or transcription JSON if same-language/no provider)

PIPELINE TIMING (all wall-clock ms):
  t_audio_start → websocket.receive() returned
  t_conv_done   → audio.processor.convert() returned
  t_stt_done    → speech_provider.transcribe() returned
  t_trans_done  → translation_provider.translate() returned
  t_end         → JSON sent

Measured fields returned to client:
  audio_conversion_ms   = t_conv_done - t_audio_start
  stt_latency_ms        = from TranscriptionResult
  translation_latency_ms= from TranslationResult  (M5)
  total_latency_ms      = t_end - t_audio_start

PyAV is never imported here — all audio work is delegated to audio.processor.
Provider-specific code is never here — delegated to speech/translation providers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from audio.processor import convert, ProcessedAudio
from speech.base import SpeechProvider, TranscriptionResult
from translation.base import TranslationProvider

logger = logging.getLogger("gamevoice.ws.handler")

# Default languages — overridden by main.py from config
DEFAULT_SOURCE_LANGUAGE = "en"
DEFAULT_TARGET_LANGUAGE = "en"


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------

async def handle_connection(
    websocket: WebSocket,
    room_id: str,
    active_rooms: dict[str, list[WebSocket]],
    speech_provider: Optional[SpeechProvider] = None,
    translation_provider: Optional[TranslationProvider] = None,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> None:
    """
    Manage the full lifecycle of one WebSocket connection.

    Args:
        websocket:            The incoming client socket.
        room_id:              Room identifier (already uppercased by main.py).
        active_rooms:         Shared room registry.
        speech_provider:      M4 STT provider injected from main.py.
                              If None, audio_processed is still sent (M3 mode).
        translation_provider: M5 TranslationProvider injected from main.py.
                              If None, transcription JSON is sent without translation.
        source_language:      BCP-47 code of what this player speaks.
        target_language:      BCP-47 code of what they want to receive.
    """
    await websocket.accept()
    active_rooms.setdefault(room_id, []).append(websocket)
    peer_count = len(active_rooms[room_id])
    logger.info("[%s] Client connected — peers: %d", room_id, peer_count)

    await websocket.send_json({
        "type": "connected",
        "room_id": room_id,
        "peers": peer_count,
        "source_language": source_language,
        "target_language": target_language,
    })

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("bytes"):
                await _on_audio(
                    websocket,
                    msg["bytes"],
                    room_id,
                    speech_provider,
                    translation_provider,
                    source_language,
                    target_language,
                )
            elif msg.get("text"):
                await _on_text(websocket, msg["text"], room_id)

    except WebSocketDisconnect:
        _cleanup(active_rooms, room_id, websocket)
        remaining = len(active_rooms.get(room_id, []))
        logger.info(
            "[%s] Client disconnected — peers remaining: %d",
            room_id,
            remaining,
        )
    except Exception as exc:
        logger.exception("[%s] Unhandled error in handle_connection: %s", room_id, exc)
        _cleanup(active_rooms, room_id, websocket)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Internal server error.",
            })
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Binary audio handler — M3 + M4 + M5 pipeline
# ---------------------------------------------------------------------------

async def _on_audio(
    websocket: WebSocket,
    data: bytes,
    room_id: str,
    speech_provider: Optional[SpeechProvider],
    translation_provider: Optional[TranslationProvider],
    source_language: str,
    target_language: str,
) -> None:
    """
    Full pipeline:

    binary frame
      → M3: validate + PyAV convert → 16kHz mono s16 PCM
      → M4: SpeechProvider.transcribe() → TranscriptionResult
      → M5: TranslationProvider.translate() → TranslationResult
      → send 'translation' JSON frame (or 'transcription' if no translation)
    """
    t_start = time.perf_counter()
    logger.debug("[%s] Binary frame: %.1f KB", room_id, len(data) / 1_024)

    # ── M3: audio conversion ──────────────────────────────────────────────
    audio_result = convert(data)
    t_conv = time.perf_counter()
    audio_conversion_ms = (t_conv - t_start) * 1_000.0

    if not isinstance(audio_result, ProcessedAudio):
        logger.warning(
            "[%s] audio_error [%s]: %s", room_id,
            audio_result.code, audio_result.message,
        )
        await websocket.send_json({
            "type": "audio_error",
            "code": audio_result.code,
            "message": audio_result.message,
        })
        return

    # ── M4: speech recognition ────────────────────────────────────────────
    if speech_provider is None:
        # No provider injected — M3-only response
        total_ms = (time.perf_counter() - t_start) * 1_000.0
        await websocket.send_json({
            "type": "audio_processed",
            "input_bytes": len(data),
            "sample_rate": audio_result.sample_rate,
            "channels": audio_result.channels,
            "sample_format": audio_result.sample_format,
            "pcm_bytes": len(audio_result.pcm_bytes),
            "audio_duration_ms": round(audio_result.duration_ms, 1),
            "audio_conversion_ms": round(audio_conversion_ms, 1),
            "processing_ms": round(total_ms, 1),
            "status": "ok",
        })
        return

    # --- transcribe (always returns, never raises) ---
    # transcribe() is synchronous but calls the Live API (blocking I/O).
    # run_in_executor offloads it to a thread so the event loop stays free
    # for other WebSocket messages (e.g. the 'connected' heartbeat).
    loop = asyncio.get_running_loop()
    stt_result: TranscriptionResult = await loop.run_in_executor(
        None,  # default ThreadPoolExecutor
        lambda: speech_provider.transcribe(audio_result, language=source_language),
    )
    t_stt = time.perf_counter()

    logger.info(
        "[%s] transcription [%s] status=%s  stt=%.1f ms",
        room_id, stt_result.language, stt_result.status, stt_result.stt_latency_ms,
    )

    # --- Handle STT failures ---
    if stt_result.status != "ok":
        total_ms = (time.perf_counter() - t_start) * 1_000.0

        if stt_result.status == "empty_audio":
            await websocket.send_json({
                "type": "transcription_status",
                "status": "empty_audio",
                "message": stt_result.error_message or "Audio too short.",
                "audio_conversion_ms": round(audio_conversion_ms, 1),
                "total_latency_ms": round(total_ms, 1),
            })
        elif stt_result.status == "no_speech":
            await websocket.send_json({
                "type": "transcription_status",
                "status": "no_speech",
                "language": stt_result.language,
                "provider": stt_result.provider,
                "audio_conversion_ms": round(audio_conversion_ms, 1),
                "stt_latency_ms": round(stt_result.stt_latency_ms, 1),
                "total_latency_ms": round(total_ms, 1),
            })
        else:
            await websocket.send_json({
                "type": "transcription_error",
                "code": stt_result.status,
                "message": stt_result.error_message or "Speech recognition failed.",
                "provider": stt_result.provider,
                "audio_conversion_ms": round(audio_conversion_ms, 1),
                "total_latency_ms": round(total_ms, 1),
            })
        return

    # --- STT succeeded — we have transcription text ---
    source_text = stt_result.text

    # ── M5: translation ───────────────────────────────────────────────────
    if translation_provider is None or source_language == target_language:
        # No translation provider, or same-language: send M4-style transcription
        total_ms = (time.perf_counter() - t_start) * 1_000.0
        await websocket.send_json({
            "type": "transcription",
            "text": source_text,
            "language": stt_result.language,
            "provider": stt_result.provider,
            "confidence": stt_result.confidence,
            "audio_duration_ms": round(audio_result.duration_ms, 1),
            "audio_conversion_ms": round(audio_conversion_ms, 1),
            "stt_latency_ms": round(stt_result.stt_latency_ms, 1),
            "total_latency_ms": round(total_ms, 1),
        })
        return

    # --- translate (always returns, never raises) ---
    trans_result = translation_provider.translate(
        text=source_text,
        source_language=source_language,
        target_language=target_language,
    )
    t_trans = time.perf_counter()
    total_latency_ms = (t_trans - t_start) * 1_000.0

    logger.info(
        "[%s] translation [%s->%s] status=%s  trans=%.1f ms  total=%.1f ms",
        room_id,
        source_language, target_language,
        trans_result.status,
        trans_result.translation_latency_ms,
        total_latency_ms,
    )

    if trans_result.succeeded:
        await websocket.send_json({
            "type": "translation",
            "source_language": source_language,
            "target_language": target_language,
            "source_text": source_text,
            "translated_text": trans_result.translated_text,
            "provider": trans_result.provider,
            "confidence": stt_result.confidence,
            "audio_duration_ms": round(audio_result.duration_ms, 1),
            "audio_conversion_ms": round(audio_conversion_ms, 1),
            "stt_latency_ms": round(stt_result.stt_latency_ms, 1),
            "translation_latency_ms": round(trans_result.translation_latency_ms, 1),
            "total_latency_ms": round(total_latency_ms, 1),
        })
    else:
        # Translation failed — send the original transcription + error
        # Frontend must display source_text unchanged (fallback policy)
        await websocket.send_json({
            "type": "translation_error",
            "code": trans_result.status,
            "message": trans_result.error_message or "Translation failed.",
            "provider": trans_result.provider,
            "source_language": source_language,
            "target_language": target_language,
            "source_text": source_text,    # always included for fallback display
            "audio_conversion_ms": round(audio_conversion_ms, 1),
            "stt_latency_ms": round(stt_result.stt_latency_ms, 1),
            "total_latency_ms": round(total_latency_ms, 1),
        })


# ---------------------------------------------------------------------------
# Text (JSON) handler
# ---------------------------------------------------------------------------

async def _on_text(
    websocket: WebSocket,
    raw: str,
    room_id: str,
) -> None:
    """Process a JSON text frame (M1 echo, future control messages)."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json({
            "type": "error",
            "message": "Invalid JSON payload.",
        })
        return

    msg_type = msg.get("type", "unknown")
    logger.debug("[%s] Text message: type=%s", room_id, msg_type)

    # M1 echo — still working for control messages
    await websocket.send_json({"type": "echo", "original": msg})


# ---------------------------------------------------------------------------
# Room registry cleanup
# ---------------------------------------------------------------------------

def _cleanup(
    active_rooms: dict[str, list[WebSocket]],
    room_id: str,
    websocket: WebSocket,
) -> None:
    """Remove a disconnected socket from the room registry."""
    if room_id not in active_rooms:
        return
    try:
        active_rooms[room_id].remove(websocket)
    except ValueError:
        pass
    if not active_rooms[room_id]:
        del active_rooms[room_id]
