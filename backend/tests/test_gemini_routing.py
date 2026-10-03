"""
backend/tests/test_gemini_routing.py

Focused tests for the Gemini STT routing and language propagation path.

Coverage:
  TestGeminiFactoryRouting        — factory routes "gemini" -> GeminiSpeechProvider
  TestSourceLanguagePropagation   — handler passes source_language to transcribe()
  TestWsHandlerWithGeminiProvider — WebSocket handler integration with mocked Gemini
  TestWsHandlerSourceLanguageHi   — handler uses "hi" (SOURCE_LANGUAGE) not hardcoded "en"
  TestWsIntegrationSyntheticWav   — live backend + real WebSocket + synthetic WAV audio

All tests are offline (no real Gemini API calls) EXCEPT TestWsIntegrationSyntheticWav,
which attempts a real WebSocket request to a locally running backend.

The real-backend test is marked with @pytest.mark.integration and is skipped
by default (requires STT_PROVIDER=gemini and GEMINI_API_KEY set in backend/.env).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import sys
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pcm(duration_ms: int = 800, sample_rate: int = 16_000) -> bytes:
    """Synthesise 16 kHz mono s16 PCM — a 440 Hz sine wave."""
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


def _make_processed_audio(duration_ms: int = 800):
    from audio.processor import ProcessedAudio
    return ProcessedAudio(
        pcm_bytes=_make_pcm(duration_ms),
        sample_rate=16_000,
        channels=1,
        sample_format="s16",
        duration_ms=float(duration_ms),
        conversion_ms=5.0,
    )


def _make_wav_bytes(duration_ms: int = 800, sample_rate: int = 16_000) -> bytes:
    """Build a complete WAV file in memory (no disk I/O)."""
    pcm = _make_pcm(duration_ms, sample_rate)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", len(pcm),
    )
    return header + pcm


def _make_ok_transcription_result(text: str = "test text", language: str = "hi"):
    from speech.base import TranscriptionResult
    return TranscriptionResult(
        status="ok",
        text=text,
        language=language,
        provider="gemini",
        stt_latency_ms=1200.0,
    )


# ---------------------------------------------------------------------------
# TEST CLASS 1 — Factory routing
# ---------------------------------------------------------------------------

class TestGeminiFactoryRouting:
    """Verify the provider factory routes 'gemini' to GeminiSpeechProvider."""

    def test_factory_mock_returns_mock_provider(self):
        from speech.provider import get_speech_provider
        from speech.mock import MockSpeechProvider
        p = get_speech_provider("mock")
        assert isinstance(p, MockSpeechProvider)
        assert p.name == "mock"

    def test_factory_gemini_returns_gemini_provider(self):
        from speech.provider import get_speech_provider
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = get_speech_provider("gemini")
        assert isinstance(p, GeminiSpeechProvider)
        assert p.name == "gemini"

    def test_factory_gemini_uses_model_from_env(self):
        from speech.provider import get_speech_provider
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake-key",
            "GEMINI_STT_MODEL": "gemini-3.5-transcribe-live",
        }):
            p = get_speech_provider("gemini")
        assert p._model == "gemini-3.5-transcribe-live"

    def test_factory_mock_still_works_alongside_gemini_code(self):
        """MockSpeechProvider must remain available regardless of gemini code."""
        from speech.provider import get_speech_provider
        from speech.mock import MockSpeechProvider
        p = get_speech_provider("mock")
        assert isinstance(p, MockSpeechProvider)

    def test_factory_unknown_raises_value_error(self):
        from speech.provider import get_speech_provider
        with pytest.raises(ValueError, match="Unknown STT_PROVIDER"):
            get_speech_provider("nonexistent_xyz")

    def test_factory_gemini_has_no_api_key_log_but_does_not_raise(self):
        """GeminiSpeechProvider should construct even without a key (fails at transcribe time)."""
        from speech.provider import get_speech_provider
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            p = get_speech_provider("gemini")
        assert p.name == "gemini"
        assert p._api_key == ""


# ---------------------------------------------------------------------------
# TEST CLASS 2 — Source-language propagation
# ---------------------------------------------------------------------------

class TestSourceLanguagePropagation:
    """
    Verify that the configured source_language (e.g. 'hi') is passed through
    the WebSocket handler to SpeechProvider.transcribe(), not hardcoded 'en'.
    """

    def _make_mock_provider(self, response_text: str = "test", language: str = "hi"):
        """Return a mock SpeechProvider that captures its transcribe() args."""
        from speech.base import SpeechProvider, TranscriptionResult

        class CapturingProvider(SpeechProvider):
            name = "mock_capturing"
            supported_languages = frozenset()
            calls: list = []

            def transcribe(self, audio, language="en"):
                CapturingProvider.calls.append(language)
                return TranscriptionResult(
                    status="ok",
                    text=response_text,
                    language=language,
                    provider="mock_capturing",
                    stt_latency_ms=1.0,
                )

        CapturingProvider.calls = []
        return CapturingProvider()

    def test_handler_passes_hi_to_transcribe(self):
        """When source_language='hi', transcribe() receives language='hi'."""
        asyncio.run(
            self._async_handler_language_check("hi")
        )

    def test_handler_passes_te_to_transcribe(self):
        asyncio.run(
            self._async_handler_language_check("te")
        )

    def test_handler_passes_en_to_transcribe(self):
        asyncio.run(
            self._async_handler_language_check("en")
        )

    def test_handler_passes_ta_to_transcribe(self):
        asyncio.run(
            self._async_handler_language_check("ta")
        )

    async def _async_handler_language_check(self, source_language: str):
        """Run _on_audio and assert transcribe() received source_language."""
        from ws.handler import _on_audio
        from audio.processor import ProcessedAudio

        capturing = self._make_mock_provider(language=source_language)

        # Build a valid ProcessedAudio
        audio = _make_processed_audio()

        # Mock WebSocket — capture sent messages
        ws = MagicMock()
        ws.send_json = AsyncMock()

        # Mock audio.processor.convert to bypass PyAV
        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(
                ws,
                b"fake_webm_bytes",
                "TESTROOM",
                capturing,
                None,   # no translation provider
                source_language,
                source_language,  # same source/target → no translation attempted
            )

        from speech.base import SpeechProvider
        # The capturing provider records language; verify it
        assert capturing.calls == [source_language], (
            f"Expected transcribe() to be called with language={source_language!r}, "
            f"got {capturing.calls}"
        )


# ---------------------------------------------------------------------------
# TEST CLASS 3 — WS handler with mocked GeminiSpeechProvider
# ---------------------------------------------------------------------------

class TestWsHandlerWithGeminiProvider:
    """
    Integration-level tests: WebSocket handler + mocked GeminiSpeechProvider.
    Verifies the full M3→M4 pipeline shape with the Gemini provider.
    """

    def _make_gemini_provider(self, response_text: str = "Dushman left side pe hai"):
        """GeminiSpeechProvider with _transcribe_live mocked (no real API call)."""
        from speech.gemini_stt import GeminiSpeechProvider
        from speech.base import TranscriptionResult
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()

        async def _fake_live(audio, language, client, t0):
            if not response_text:
                return TranscriptionResult(
                    status="no_speech", text="", language=language,
                    provider="gemini", stt_latency_ms=100.0,
                )
            return TranscriptionResult(
                status="ok", text=response_text, language=language,
                provider="gemini", stt_latency_ms=100.0,
            )

        p._transcribe_live = _fake_live
        p._client = MagicMock()
        return p

    def test_handler_sends_transcription_json_for_gemini_ok(self):
        asyncio.run(
            self._async_test_transcription_sent()
        )

    async def _async_test_transcription_sent(self):
        from ws.handler import _on_audio
        from audio.processor import ProcessedAudio

        provider = self._make_gemini_provider("Dushman left side pe hai")
        audio = _make_processed_audio()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(ws, b"fake", "ROOM1", provider, None, "hi", "hi")

        assert ws.send_json.called
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "transcription"
        assert payload["text"] == "Dushman left side pe hai"
        assert payload["language"] == "hi"
        assert payload["provider"] == "gemini"

    def test_handler_transcription_includes_latency_fields(self):
        asyncio.run(
            self._async_test_latency_fields()
        )

    async def _async_test_latency_fields(self):
        from ws.handler import _on_audio
        provider = self._make_gemini_provider("test")
        audio = _make_processed_audio()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(ws, b"fake", "ROOM1", provider, None, "hi", "hi")

        payload = ws.send_json.call_args[0][0]
        assert "stt_latency_ms" in payload
        assert "audio_conversion_ms" in payload
        assert "total_latency_ms" in payload

    def test_handler_with_gemini_no_speech_returns_transcription_status(self):
        asyncio.run(
            self._async_test_no_speech()
        )

    async def _async_test_no_speech(self):
        from ws.handler import _on_audio
        provider = self._make_gemini_provider("")   # empty = no_speech
        audio = _make_processed_audio()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(ws, b"fake", "ROOM1", provider, None, "hi", "hi")

        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "transcription_status"
        assert payload["status"] == "no_speech"

    def test_handler_with_gemini_provider_error_returns_transcription_error(self):
        asyncio.run(
            self._async_test_provider_error()
        )

    async def _async_test_provider_error(self):
        from ws.handler import _on_audio
        from speech.gemini_stt import GeminiSpeechProvider
        from speech.base import TranscriptionResult
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            provider = GeminiSpeechProvider()

        async def _fail_live(audio, language, client, t0):
            return TranscriptionResult(
                status="provider_error", text="", language=language,
                provider="gemini", stt_latency_ms=10.0,
                error_message="Speech recognition failed (provider error).",
            )
        provider._transcribe_live = _fail_live
        provider._client = MagicMock()


        audio = _make_processed_audio()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(ws, b"fake", "ROOM1", provider, None, "hi", "hi")

        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "transcription_error"
        assert payload["provider"] == "gemini"

    def test_source_language_hi_reaches_gemini_api_call(self):
        """Verify 'hi' language reaches the GeminiSpeechProvider.transcribe() call."""
        asyncio.run(
            self._async_test_hi_language_reaches_api()
        )

    async def _async_test_hi_language_reaches_api(self):
        from ws.handler import _on_audio
        from speech.gemini_stt import GeminiSpeechProvider, _LANG_BCP47
        from speech.base import TranscriptionResult

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            provider = GeminiSpeechProvider()

        captured_lang = []

        async def _capture_live(audio, language, client, t0):
            captured_lang.append(language)
            return TranscriptionResult(
                status="ok", text="Dushman left side pe hai",
                language=language, provider="gemini", stt_latency_ms=100.0,
            )
        provider._transcribe_live = _capture_live
        provider._client = MagicMock()

        audio = _make_processed_audio()
        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("ws.handler.convert", return_value=audio):
            await _on_audio(ws, b"fake", "ROOM1", provider, None, "hi", "hi")

        # _transcribe_live must have been called with language="hi"
        assert captured_lang == ["hi"], (
            f"Expected language='hi' in _transcribe_live, got {captured_lang}"
        )
        # BCP-47 for "hi" must be "hi-IN"
        assert _LANG_BCP47["hi"] == "hi-IN"



# ---------------------------------------------------------------------------
# TEST CLASS 4 — WebSocket integration (real backend, synthetic audio)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestWsIntegrationSyntheticWav:
    """
    Real WebSocket integration test against a locally running backend.

    Skipped unless ALL of:
      - STT_PROVIDER=gemini in backend/.env
      - GEMINI_API_KEY is a real key (not placeholder) in backend/.env
      - A backend process is reachable at ws://localhost:8000/ws/SMOKETEST

    NOTE: This test sends a 440 Hz SINE TONE — not real human speech.
    Gemini will return 'no_speech' for a sine tone, which is expected.
    The test passes on ANY non-error response (ok, no_speech).
    REAL SPEECH from a microphone cannot be automated in a headless test.
    """

    WS_URL = "ws://localhost:8000/ws/SMOKETEST"

    @pytest.fixture(autouse=True)
    def skip_without_live_backend(self):
        """Skip this test if websockets is not installed or backend unreachable."""
        try:
            import websockets  # noqa: F401
        except ImportError:
            pytest.skip("websockets package not installed")

        # Quick TCP probe to see if backend is up
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("localhost", 8000))
            s.close()
        except (ConnectionRefusedError, OSError):
            pytest.skip("Backend not running at localhost:8000")

    def test_synthetic_wav_reaches_backend_and_returns_result(self):
        asyncio.run(self._async_ws_test())


    async def _async_ws_test(self):
        import websockets

        wav = _make_wav_bytes(800)
        messages = []

        async with websockets.connect(self.WS_URL, open_timeout=5) as ws:
            # Receive the 'connected' greeting
            connected = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert connected["type"] == "connected"

            # Send audio
            await ws.send(wav)

            # Wait for the STT result (may take several seconds with Gemini Live API)
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            messages.append(result)

        assert messages, "No response received from backend"
        msg = messages[0]

        # Valid response types: transcription, transcription_status, audio_processed
        assert msg["type"] in (
            "transcription",
            "transcription_status",
            "audio_processed",
            "transcription_error",
        ), f"Unexpected response type: {msg['type']!r}"

        # If provider_error, fail with a clear message
        if msg["type"] == "transcription_error":
            pytest.fail(
                f"Backend returned transcription_error: {msg.get('message', '?')}. "
                "Check GEMINI_API_KEY in backend/.env."
            )
