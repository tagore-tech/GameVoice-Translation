"""
backend/speech/gemini_stt.py — M-LIVE-STT

Real speech-to-text via Gemini Live API (google-genai SDK 2.27.0+).

PROVIDER NAME: "gemini"
ENVIRONMENT VARIABLES REQUIRED:
    GEMINI_API_KEY   — your Google AI Studio API key (never in source code)
OPTIONAL:
    GEMINI_STT_MODEL        — Gemini Live STT model (default: gemini-3.5-transcribe-live)
    GEMINI_STT_AUTO_DETECT  — "true" to use language_codes=[] (auto-detect),
                              "false" (default) to pass explicit BCP-47 hint.

HOW IT WORKS
------------
The M3 PyAV pipeline already produces 16 kHz mono signed-16-bit PCM.
The Gemini Live API consumes RAW PCM directly — no WAV header is needed.

Each PTT recording opens a fresh bidirectional Live session:
  1. client.aio.live.connect(model, config)   — WebSocket session
  2. send activity_start (Manual VAD / push-to-talk mode)
  3. stream PCM in 4 KB chunks via send_realtime_input(audio=Blob(...))
  4. send activity_end
  5. collect input_transcription events until turn_complete or error

AUDIO FORMAT (official docs requirement)
-----------------------------------------
  MIME type : "audio/pcm;rate=16000"
  Encoding  : signed 16-bit little-endian PCM
  Sample rate: 16 000 Hz
  Channels  : 1 (mono)
  No WAV / RIFF header required.

API usage:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=True      # Manual VAD / push-to-talk
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=["hi-IN"],   # or [] for auto-detect
        ),
    )
    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_realtime_input(activity_start=types.ActivityStart())
        await session.send_realtime_input(
            audio=types.Blob(data=pcm_chunk, mime_type="audio/pcm;rate=16000")
        )
        await session.send_realtime_input(activity_end=types.ActivityEnd())
        async for response in session.receive():
            if response.server_content and response.server_content.interim_input_transcription:
                interim = response.server_content.interim_input_transcription.text  # partial
            if response.server_content and response.server_content.input_transcription:
                text = response.server_content.input_transcription.text  # finalized
            if response.server_content and response.server_content.turn_complete:
                break

SUPPORTED LANGUAGES
-------------------
  en  English  (BCP-47: en-US)
  hi  Hindi    (BCP-47: hi-IN)
  te  Telugu   (BCP-47: te-IN)
  ta  Tamil    (BCP-47: ta-IN)

ERROR HANDLING
--------------
transcribe() never raises. All failures are returned as TranscriptionResult
with status != 'ok'. Credentials and internal API details are never
exposed in the returned error_message.

WebSocket close code 1008 ("The operation was aborted") is the Live API's
normal close when no speech is found — mapped to status="no_speech".
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import Optional

from audio.processor import ProcessedAudio
from speech.base import SpeechProvider, TranscriptionResult

logger = logging.getLogger("gamevoice.speech.gemini_stt")

# ---------------------------------------------------------------------------
# BCP-47 language code map — required by AudioTranscriptionConfig
# ---------------------------------------------------------------------------

_LANG_BCP47: dict[str, str] = {
    "en": "en-US",
    "hi": "hi-IN",
    "te": "te-IN",
    "ta": "ta-IN",
}

# Minimum audio to bother sending (10 ms at 16kHz mono s16 = 320 bytes)
_MIN_PCM_BYTES = 320

# Default Live STT model
_DEFAULT_MODEL = "gemini-3.5-transcribe-live"

# PCM chunk size for streaming (4 KB ≈ 125 ms at 16kHz s16)
_PCM_CHUNK = 4_000

# MIME type required by the Live API for raw PCM
_PCM_MIME = "audio/pcm;rate=16000"

# Seconds to wait for transcription events after activity_end
_RECEIVE_TIMEOUT = 25.0



class GeminiSpeechProvider(SpeechProvider):
    """
    Real STT via Gemini Live API (google-genai SDK 2.27.0+).

    Uses gemini-3.5-transcribe-live — Google's dedicated low-latency
    streaming speech-to-text model.

    Configuration:
        GEMINI_API_KEY          — required (Google AI Studio key)
        GEMINI_STT_MODEL        — optional (default: gemini-3.5-transcribe-live)
        GEMINI_STT_AUTO_DETECT  — "true" for language auto-detect (default: "false")
    """

    name = "gemini"
    supported_languages: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self._api_key: Optional[str] = os.getenv("GEMINI_API_KEY", "").strip()
        self._model: str = os.getenv("GEMINI_STT_MODEL", _DEFAULT_MODEL).strip()

        if not self._api_key:
            logger.error(
                "GeminiSpeechProvider: GEMINI_API_KEY is not set. "
                "All transcription calls will return provider_error. "
                "Set GEMINI_API_KEY in backend/.env to enable real STT."
            )
        else:
            logger.info(
                "GeminiSpeechProvider loaded - model=%r  (key: ...%s)",
                self._model,
                self._api_key[-4:],  # last 4 chars only — never full key
            )

        # Lazy-import: do not import google.genai at module load time
        # so tests can import this file without the SDK installed.
        self._client = None

    def _get_client(self):
        """Return (and cache) the google.genai Client, or None if unavailable."""
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore[import]
            self._client = genai.Client(api_key=self._api_key)
            return self._client
        except ImportError:
            logger.error(
                "google-genai SDK not installed. "
                "Run: .venv\\Scripts\\python.exe -m pip install google-genai"
            )
            return None

    # ------------------------------------------------------------------
    # Public interface — synchronous wrapper over async Live session
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio: ProcessedAudio,
        language: str = "en",
    ) -> TranscriptionResult:
        """
        Transcribe PCM audio via Gemini Live API.
        Never raises. Returns TranscriptionResult with status != 'ok' on failure.

        Internally runs the async Live session on the current event loop
        (FastAPI's asyncio loop) using asyncio.get_event_loop().run_until_complete()
        when called from a sync context, or via asyncio.run() when no loop exists.
        """
        t0 = time.perf_counter()

        # --- Pre-flight: empty audio ---
        if len(audio.pcm_bytes) < _MIN_PCM_BYTES:
            return TranscriptionResult(
                status="empty_audio",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=round((time.perf_counter() - t0) * 1_000, 1),
                error_message="Audio too short for recognition.",
            )

        # --- Pre-flight: missing credentials ---
        if not self._api_key:
            return TranscriptionResult(
                status="provider_error",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=round((time.perf_counter() - t0) * 1_000, 1),
                error_message=(
                    "GEMINI_API_KEY is not configured. "
                    "Set it in backend/.env to enable real speech recognition."
                ),
            )

        # --- Get SDK client ---
        client = self._get_client()
        if client is None:
            return TranscriptionResult(
                status="provider_error",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=round((time.perf_counter() - t0) * 1_000, 1),
                error_message="google-genai SDK not installed.",
            )

        # --- Amplitude diagnostic ---
        n_samples = len(audio.pcm_bytes) // 2
        if n_samples > 0:
            pcm_vals = struct.unpack(f"<{n_samples}h", audio.pcm_bytes)
            max_amp = max(abs(v) for v in pcm_vals)
            rms = (sum(v * v for v in pcm_vals) / n_samples) ** 0.5
            logger.info(
                "Gemini Live STT audio [%s]: %.0f ms  pcm=%d B  "
                "max_amp=%d (%.1f%%)  rms=%.1f",
                language,
                audio.duration_ms,
                len(audio.pcm_bytes),
                max_amp,
                max_amp / 32767 * 100,
                rms,
            )

        # --- Run async Live session ---
        # Detect whether we're already inside a running event loop.
        # asyncio.get_running_loop() raises RuntimeError when there is none.
        try:
            asyncio.get_running_loop()
            _has_running_loop = True
        except RuntimeError:
            _has_running_loop = False

        try:
            if not _has_running_loop:
                # No running loop (e.g. plain unit test or script) — use asyncio.run()
                result = asyncio.run(
                    self._transcribe_live(audio, language, client, t0)
                )
            else:
                # Already inside a running loop (FastAPI async handler).
                # Run the coroutine on a fresh loop in a worker thread.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._transcribe_live(audio, language, client, t0),
                    )
                    result = future.result(timeout=_RECEIVE_TIMEOUT + 10)

        except Exception as exc:
            stt_ms = round((time.perf_counter() - t0) * 1_000, 1)
            logger.error("Gemini Live STT thread error [%s]: %s", language, exc)
            result = TranscriptionResult(
                status="provider_error",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=stt_ms,
                error_message="Speech recognition failed (provider error).",
            )

        return result

    # ------------------------------------------------------------------
    # Async Live session implementation
    # ------------------------------------------------------------------

    async def _transcribe_live(
        self,
        audio: ProcessedAudio,
        language: str,
        client,
        t0: float,
    ) -> TranscriptionResult:
        """
        Open a Gemini Live session, stream the PCM, collect transcription.
        Returns a TranscriptionResult. Never raises.
        """
        from google.genai import types  # type: ignore[import]

        # --- Resolve BCP-47 language code ---
        bcp47 = _LANG_BCP47.get(language, language)

        # --- Language hint: explicit or auto-detect ---
        _auto = os.getenv("GEMINI_STT_AUTO_DETECT", "false").strip().lower() == "true"
        lang_codes: list[str] = [] if _auto else [bcp47]
        logger.info(
            "Gemini Live STT lang [%s]: bcp47=%r  auto_detect=%s  language_codes=%r",
            language, bcp47, _auto, lang_codes,
        )

        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True,   # Manual VAD — PTT controls the turn
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=lang_codes,
            ),
        )

        transcripts: list[str] = []

        try:
            async with client.aio.live.connect(
                model=self._model, config=config
            ) as session:

                # Signal push-to-talk start
                await session.send_realtime_input(
                    activity_start=types.ActivityStart()
                )

                # Stream raw PCM in chunks (no WAV header — Live API wants raw PCM)
                pcm = audio.pcm_bytes
                for offset in range(0, len(pcm), _PCM_CHUNK):
                    chunk = pcm[offset:offset + _PCM_CHUNK]
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=_PCM_MIME)
                    )

                # Signal push-to-talk end — triggers transcription
                await session.send_realtime_input(
                    activity_end=types.ActivityEnd()
                )

                logger.info(
                    "Gemini Live STT [%s]: streamed %d B in %d chunks, waiting...",
                    language,
                    len(pcm),
                    (len(pcm) + _PCM_CHUNK - 1) // _PCM_CHUNK,
                )

                # Collect transcription events with a hard timeout.
                # asyncio.wait_for() cancels the receive loop if Gemini doesn't
                # respond within _RECEIVE_TIMEOUT seconds.
                #
                # Two transcription fields per the official docs:
                #   interim_input_transcription — low-latency speculative partial
                #   input_transcription         — finalized authoritative transcript
                #
                # Collect BOTH. Use input_transcription as the primary result;
                # fall back to interim_input_transcription if no final arrives.
                interim_transcripts: list[str] = []

                async def _collect() -> None:
                    async for response in session.receive():
                        sc = response.server_content

                        # --- Diagnostic logging (safe: no audio bytes, no keys) ---
                        if sc is not None:
                            has_input   = sc.input_transcription is not None
                            has_interim = sc.interim_input_transcription is not None
                            input_fin   = (
                                getattr(sc.input_transcription, "finished", None)
                                if has_input else None
                            )
                            input_len   = len(
                                (sc.input_transcription.text or "") if has_input else ""
                            )
                            interim_len = len(
                                (sc.interim_input_transcription.text or "") if has_interim else ""
                            )
                            logger.debug(
                                "Gemini Live response [%s]: turn_complete=%s  "
                                "input_transcription=%s(len=%d finished=%s)  "
                                "interim=%s(len=%d)  interrupted=%s  waiting_for_input=%s",
                                language,
                                sc.turn_complete,
                                has_input, input_len, input_fin,
                                has_interim, interim_len,
                                getattr(sc, "interrupted", None),
                                getattr(sc, "waiting_for_input", None),
                            )
                        else:
                            logger.debug(
                                "Gemini Live response [%s]: server_content=None"
                                " (setup_complete or non-content message)",
                                language,
                            )

                        if sc is None:
                            continue

                        # ----------------------------------------------------------------
                        # FINALIZED TRANSCRIPT — primary completion signal
                        # ----------------------------------------------------------------
                        # input_transcription is the FINALIZED result field (distinct
                        # from interim_input_transcription which carries live partials).
                        # It is only populated once Gemini commits the final text for
                        # the current PTT activity window (after activity_end is sent).
                        #
                        # Root cause of previous 25s wait:
                        #   input_transcription.finished defaults to None in the SDK.
                        #   Gemini 3.5-transcribe-live does NOT set finished=True in
                        #   practice — so "if sc.input_transcription.finished:" never
                        #   fired, and the loop waited for the full timeout.
                        #
                        # Correct PTT completion rule:
                        #   Receiving any non-empty input_transcription.text IS the
                        #   terminal event for this PTT turn. Break immediately.
                        #   Do not wait for turn_complete or finished=True.
                        # ----------------------------------------------------------------
                        if sc.input_transcription is not None:
                            text = (sc.input_transcription.text or "").strip()
                            if text:
                                transcripts.append(text)
                                logger.info(
                                    "Gemini Live STT [%s]: finalized transcript: %r"
                                    " — returning promptly",
                                    language, text,
                                )
                                # Non-empty finalized transcript = PTT turn is done.
                                break
                            # Empty input_transcription (Gemini sent the field but
                            # text is blank — no speech or noise only).
                            # Fall through and let turn_complete or timeout exit.
                            logger.debug(
                                "Gemini Live STT [%s]: empty input_transcription"
                                " — continuing to wait",
                                language,
                            )

                        # ----------------------------------------------------------------
                        # INTERIM TRANSCRIPT — live partial, collected as fallback only
                        # ----------------------------------------------------------------
                        # Only used if no finalized input_transcription ever arrives
                        # (e.g. activity_end + no speech detected path).
                        if sc.interim_input_transcription is not None:
                            text = (sc.interim_input_transcription.text or "").strip()
                            if text:
                                interim_transcripts.append(text)
                                logger.debug(
                                    "Gemini Live STT [%s]: interim fragment: %r",
                                    language, text,
                                )

                        # ----------------------------------------------------------------
                        # TURN COMPLETE — secondary exit
                        # ----------------------------------------------------------------
                        # Handles no-speech scenarios (turn_complete arrives without
                        # any input_transcription) and server-initiated turn end.
                        if sc.turn_complete:
                            logger.info(
                                "Gemini Live STT [%s]: turn_complete"
                                " (no finalized transcript yet)",
                                language,
                            )
                            break


                try:
                    await asyncio.wait_for(_collect(), timeout=_RECEIVE_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Gemini Live STT [%s]: %.1f s timeout waiting for transcript",
                        language, _RECEIVE_TIMEOUT,
                    )

                # If no finalized transcript arrived, fall back to interim
                if not transcripts and interim_transcripts:
                    logger.info(
                        "Gemini Live STT [%s]: no finalized transcript — "
                        "using last interim fragment as fallback",
                        language,
                    )
                    # Use only the last (most complete) interim fragment
                    transcripts.append(interim_transcripts[-1])


        except Exception as exc:
            stt_ms = round((time.perf_counter() - t0) * 1_000, 1)
            exc_str = str(exc)

            # WS 1008 ("The operation was aborted") is the Live API's normal
            # close code when the server finds no speech in the audio turn.
            if "1008" in exc_str:
                logger.info(
                    "Gemini Live STT [%s]: no speech detected (WS 1008)  (%.1f ms)",
                    language, stt_ms,
                )
                return TranscriptionResult(
                    status="no_speech",
                    text="",
                    language=language,
                    provider=self.name,
                    stt_latency_ms=stt_ms,
                )

            # Log full error internally — never expose internals to caller
            logger.error("Gemini Live STT error [%s]: %s", language, exc)
            exc_lower = exc_str.lower()
            if any(kw in exc_lower for kw in ("api_key", "authentication", "401", "403")):
                msg = "Authentication failed. Check GEMINI_API_KEY in backend/.env."
            elif any(kw in exc_lower for kw in ("quota", "rate", "429")):
                msg = "Rate limit or quota exceeded. Please wait and try again."
            elif "timeout" in exc_lower:
                msg = "Request timed out."
            else:
                msg = "Speech recognition failed (provider error)."
            return TranscriptionResult(
                status="provider_error",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=stt_ms,
                error_message=msg,
            )

        stt_ms = round((time.perf_counter() - t0) * 1_000, 1)

        # --- Combine all transcript fragments ---
        raw_text = " ".join(transcripts).strip()

        if not raw_text:
            logger.info(
                "Gemini Live STT [%s]: no speech detected  (%.1f ms)", language, stt_ms
            )
            return TranscriptionResult(
                status="no_speech",
                text="",
                language=language,
                provider=self.name,
                stt_latency_ms=stt_ms,
            )

        logger.info(
            "Gemini Live STT [%s] -> %r  (%.1f ms, audio %.0f ms)",
            language,
            raw_text[:80] + ("..." if len(raw_text) > 80 else ""),
            stt_ms,
            audio.duration_ms,
        )

        return TranscriptionResult(
            status="ok",
            text=raw_text,
            language=language,
            provider=self.name,
            stt_latency_ms=stt_ms,
            confidence=None,   # Live API does not expose confidence scores
            metadata={
                "model": self._model,
                "bcp47": bcp47,
                "audio_duration_ms": round(audio.duration_ms, 1),
                "pcm_bytes": len(audio.pcm_bytes),
                "fragments": len(transcripts),
            },
        )
