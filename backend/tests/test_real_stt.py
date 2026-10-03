"""
backend/tests/test_real_stt.py — M-LIVE-STT

Deterministic tests for the Gemini Live STT provider.

ALL TESTS ARE OFFLINE — no real Gemini API calls are made.
The async Live session is mocked via unittest.mock.AsyncMock.

Test classes:
  TestGeminiProviderInit        — constructor / credential detection
  TestGeminiMissingCredentials  — missing GEMINI_API_KEY path
  TestGeminiSdkNotInstalled     — ImportError path
  TestGeminiEmptyAudio          — audio too short path
  TestGeminiLiveConfig          — LiveConnectConfig construction
  TestGeminiLivePcmMime         — raw PCM MIME type (audio/pcm;rate=16000)
  TestGeminiLiveTranscribeSuccess — mocked successful Live transcription
  TestGeminiLiveNoSpeech        — WS 1008 / empty transcript -> no_speech
  TestGeminiLiveErrors          — API error -> provider_error mapping
  TestGeminiLanguageMapping     — BCP-47 language code forwarding
  TestGeminiProviderFactory     — factory returns GeminiSpeechProvider
  TestGeminiFactoryUnknown      — factory raises on bad name
  TestGeminiResultContract      — TranscriptionResult structure
  TestGeminiLatencyMetadata     — latency always present
  TestMockRegressionFromRealSTT — MockSpeechProvider still works
  TestPcmAmplitudeLog           — amplitude diagnostic log behaviour
  TestGeminiAutoDetect          — GEMINI_STT_AUTO_DETECT=true sends language_codes=[]
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from audio.processor import ProcessedAudio
from speech.base import SpeechProvider, TranscriptionResult
from speech.mock import MockSpeechProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pcm(duration_ms: int = 800, sample_rate: int = 16_000) -> bytes:
    """Synthesise 16kHz mono s16 PCM."""
    n = int(sample_rate * duration_ms / 1_000)
    samples = [int(32_767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


def _make_audio(duration_ms: int = 800) -> ProcessedAudio:
    return ProcessedAudio(
        pcm_bytes=_make_pcm(duration_ms),
        sample_rate=16_000,
        channels=1,
        sample_format="s16",
        duration_ms=float(duration_ms),
        conversion_ms=5.0,
    )


def _tiny_audio() -> ProcessedAudio:
    """Audio too short to process (< 320 bytes)."""
    return ProcessedAudio(
        pcm_bytes=b"\x00" * 64,
        sample_rate=16_000,
        channels=1,
        sample_format="s16",
        duration_ms=2.0,
        conversion_ms=0.0,
    )


# ---------------------------------------------------------------------------
# Mock Live session builder
# ---------------------------------------------------------------------------

def _make_live_session_mock(transcripts: list[str] = None, ws_close_code: int = None):
    """
    Build a mock that resembles a client.aio.live.connect() async context manager.

    transcripts:     list of text strings to emit as input_transcription events.
    ws_close_code:   if 1008, raise an APIError mid-receive (simulates no_speech).
    """
    responses = []

    if ws_close_code == 1008:
        # Server closes with 1008 (no speech) before any transcript
        async def _receive_1008():
            raise Exception(f"APIError: {ws_close_code} None. The operation was aborted.")
            # unreachable — just need the type
            yield  # make this an async generator

    else:
        # Build fake LiveServerContent responses
        for text in (transcripts or []):
            sc = MagicMock()
            sc.input_transcription = MagicMock()
            sc.input_transcription.text = text
            sc.interim_input_transcription = None   # avoid truthy MagicMock fallback
            sc.turn_complete = False
            resp = MagicMock()
            resp.server_content = sc
            responses.append(resp)

        # Final turn_complete response
        sc_done = MagicMock()
        sc_done.input_transcription = None
        sc_done.interim_input_transcription = None  # avoid truthy MagicMock fallback
        sc_done.turn_complete = True
        resp_done = MagicMock()
        resp_done.server_content = sc_done
        responses.append(resp_done)


    # Async generator for session.receive()
    async def _receive_ok():
        for r in responses:
            yield r

    session = AsyncMock()
    session.send_realtime_input = AsyncMock()

    if ws_close_code == 1008:
        async def _gen_1008():
            raise Exception(f"APIError: {ws_close_code} None. The operation was aborted.")
            yield  # make async generator

        session.receive = _gen_1008
    else:
        session.receive = _receive_ok

    # async context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


def _patch_provider_live(provider, transcripts=None, ws_close_code=None):
    """
    Replace _transcribe_live on the provider with a coroutine that returns
    the expected TranscriptionResult, bypassing real asyncio.run() /
    ThreadPoolExecutor machinery.
    """
    from speech.gemini_stt import GeminiSpeechProvider

    if ws_close_code == 1008 or (not transcripts):
        expected = TranscriptionResult(
            status="no_speech",
            text="",
            language="en",
            provider="gemini",
            stt_latency_ms=0.0,
        )
    else:
        expected = TranscriptionResult(
            status="ok",
            text=" ".join(transcripts).strip(),
            language="en",
            provider="gemini",
            stt_latency_ms=0.0,
        )
    return expected


# ---------------------------------------------------------------------------
# TEST CLASS 1 — GeminiSpeechProvider init
# ---------------------------------------------------------------------------

class TestGeminiProviderInit:

    def test_provider_importable(self):
        from speech.gemini_stt import GeminiSpeechProvider  # noqa: F401

    def test_provider_is_speech_provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        assert issubclass(GeminiSpeechProvider, SpeechProvider)

    def test_provider_name_is_gemini(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key-for-test"}):
            from speech.gemini_stt import GeminiSpeechProvider
            p = GeminiSpeechProvider()
            assert p.name == "gemini"

    def test_provider_init_without_key_does_not_raise(self):
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            from speech.gemini_stt import GeminiSpeechProvider
            p = GeminiSpeechProvider()
            assert p is not None

    def test_provider_stores_model_from_env(self):
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake-key",
            "GEMINI_STT_MODEL": "gemini-custom-model",
        }):
            from speech.gemini_stt import GeminiSpeechProvider
            p = GeminiSpeechProvider()
            assert p._model == "gemini-custom-model"

    def test_provider_default_model_is_live(self):
        """Default model must be gemini-3.5-transcribe-live."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_STT_MODEL",)}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
                from speech.gemini_stt import GeminiSpeechProvider, _DEFAULT_MODEL
                p = GeminiSpeechProvider()
                assert p._model == _DEFAULT_MODEL
                assert "live" in p._model.lower(), (
                    f"Default model should be the Live API model, got {p._model!r}"
                )


# ---------------------------------------------------------------------------
# TEST CLASS 2 — Missing credentials
# ---------------------------------------------------------------------------

class TestGeminiMissingCredentials:

    def _make_provider_no_key(self):
        from speech.gemini_stt import GeminiSpeechProvider
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            p = GeminiSpeechProvider()
            p._api_key = ""
            return p

    def test_missing_key_returns_provider_error(self):
        p = self._make_provider_no_key()
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "provider_error"

    def test_missing_key_error_message_mentions_key_name(self):
        p = self._make_provider_no_key()
        result = p.transcribe(_make_audio(800), language="en")
        assert "GEMINI_API_KEY" in (result.error_message or "")

    def test_missing_key_does_not_call_api(self):
        p = self._make_provider_no_key()
        with patch("speech.gemini_stt.GeminiSpeechProvider._get_client") as mock_client:
            p.transcribe(_make_audio(800), language="en")
            mock_client.assert_not_called()

    def test_missing_key_result_has_latency(self):
        p = self._make_provider_no_key()
        result = p.transcribe(_make_audio(800), language="en")
        assert result.stt_latency_ms >= 0.0

    def test_missing_key_result_has_provider_name(self):
        p = self._make_provider_no_key()
        result = p.transcribe(_make_audio(800), language="en")
        assert result.provider == "gemini"

    def test_missing_key_result_never_exposes_secrets(self):
        p = self._make_provider_no_key()
        result = p.transcribe(_make_audio(800), language="en")
        msg = result.error_message or ""
        assert "AIza" not in msg
        assert "Bearer" not in msg


# ---------------------------------------------------------------------------
# TEST CLASS 3 — SDK not installed
# ---------------------------------------------------------------------------

class TestGeminiSdkNotInstalled:

    def test_returns_provider_error_when_sdk_missing(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()
            p._get_client = lambda: None
            result = p.transcribe(_make_audio(800), language="en")
            assert result.status == "provider_error"

    def test_error_message_mentions_sdk(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()
            p._get_client = lambda: None
            result = p.transcribe(_make_audio(800), language="en")
            msg = (result.error_message or "").lower()
            assert "google-genai" in msg or "sdk" in msg


# ---------------------------------------------------------------------------
# TEST CLASS 4 — Empty / too-short audio
# ---------------------------------------------------------------------------

class TestGeminiEmptyAudio:

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            return GeminiSpeechProvider()

    def test_tiny_audio_returns_empty_audio(self):
        p = self._provider()
        result = p.transcribe(_tiny_audio(), language="en")
        assert result.status == "empty_audio"

    def test_tiny_audio_no_api_call(self):
        p = self._provider()
        with patch.object(p, "_get_client") as mc:
            p.transcribe(_tiny_audio(), language="en")
            mc.assert_not_called()

    def test_tiny_audio_has_error_message(self):
        p = self._provider()
        result = p.transcribe(_tiny_audio(), language="en")
        assert result.error_message

    def test_tiny_audio_text_is_empty(self):
        p = self._provider()
        result = p.transcribe(_tiny_audio(), language="en")
        assert result.text == ""


# ---------------------------------------------------------------------------
# TEST CLASS 5 — Live session configuration
# ---------------------------------------------------------------------------

class TestGeminiLiveConfig:
    """Verify the LiveConnectConfig passed to client.aio.live.connect()."""

    def _run_transcribe_and_capture_config(self, language="hi", auto_detect=False):
        """
        Run _transcribe_live with a mocked session and capture the config
        that was passed to client.aio.live.connect().
        """
        from speech.gemini_stt import GeminiSpeechProvider
        from google.genai import types

        captured_config = {}

        def _mock_connect(model, config):
            captured_config["model"] = model
            captured_config["config"] = config
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()

            async def _receive():
                sc = MagicMock()
                sc.input_transcription = MagicMock()
                sc.input_transcription.text = "test"
                sc.interim_input_transcription = None  # avoid truthy MagicMock fallback
                sc.turn_complete = False
                yield MagicMock(server_content=sc)

                sc2 = MagicMock()
                sc2.input_transcription = None
                sc2.interim_input_transcription = None  # avoid truthy MagicMock fallback
                sc2.turn_complete = True
                yield MagicMock(server_content=sc2)

            session.receive = _receive
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        # Keep patch active for the entire call — _transcribe_live reads
        # GEMINI_STT_AUTO_DETECT at runtime, not at construction time.
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake-key",
            "GEMINI_STT_AUTO_DETECT": "true" if auto_detect else "false",
        }):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            asyncio.run(p._transcribe_live(_make_audio(800), language, mock_client, time.perf_counter()))

        return captured_config

    def test_response_modalities_is_text(self):
        cfg = self._run_transcribe_and_capture_config()
        assert cfg["config"].response_modalities == ["TEXT"]

    def test_automatic_vad_is_disabled(self):
        """Manual VAD (PTT): automatic_activity_detection.disabled must be True."""
        cfg = self._run_transcribe_and_capture_config()
        ric = cfg["config"].realtime_input_config
        assert ric.automatic_activity_detection.disabled is True

    def test_input_audio_transcription_present(self):
        cfg = self._run_transcribe_and_capture_config()
        assert cfg["config"].input_audio_transcription is not None

    def test_explicit_bcp47_sent_when_auto_detect_false(self):
        cfg = self._run_transcribe_and_capture_config(language="hi", auto_detect=False)
        lang_codes = cfg["config"].input_audio_transcription.language_codes
        assert lang_codes == ["hi-IN"]

    def test_empty_lang_codes_sent_when_auto_detect_true(self):
        cfg = self._run_transcribe_and_capture_config(language="hi", auto_detect=True)
        lang_codes = cfg["config"].input_audio_transcription.language_codes
        assert lang_codes == []

    def test_model_passed_to_connect(self):
        cfg = self._run_transcribe_and_capture_config()
        assert "transcribe" in cfg["model"]
        assert "live" in cfg["model"]


# ---------------------------------------------------------------------------
# TEST CLASS 6 — Raw PCM MIME type
# ---------------------------------------------------------------------------

class TestGeminiLivePcmMime:
    """Verify that raw PCM is sent with the correct MIME type (no WAV header)."""

    def _capture_audio_blobs(self, language="hi"):
        from speech.gemini_stt import GeminiSpeechProvider, _PCM_MIME
        from google.genai import types

        sent_blobs = []
        sent_calls = []

        async def _send_realtime_input(**kwargs):
            sent_calls.append(kwargs)
            if "audio" in kwargs:
                sent_blobs.append(kwargs["audio"])

        def _mock_connect(model, config):
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input.side_effect = _send_realtime_input

            async def _receive():
                sc = MagicMock()
                sc.input_transcription = MagicMock()
                sc.input_transcription.text = "namaste"
                sc.interim_input_transcription = None  # avoid truthy MagicMock fallback
                sc.turn_complete = False
                yield MagicMock(server_content=sc)
                sc2 = MagicMock()
                sc2.input_transcription = None
                sc2.interim_input_transcription = None  # avoid truthy MagicMock fallback
                sc2.turn_complete = True
                yield MagicMock(server_content=sc2)

            session.receive = _receive
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        # Keep patch active during asyncio.run() — _transcribe_live reads
        # GEMINI_STT_AUTO_DETECT at runtime, not at construction time.
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key",
                                     "GEMINI_STT_AUTO_DETECT": "false"}):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            asyncio.run(p._transcribe_live(_make_audio(800), language, mock_client, time.perf_counter()))
        return sent_blobs, sent_calls, _PCM_MIME

    def test_audio_blobs_use_pcm_mime_type(self):
        blobs, _, PCM_MIME = self._capture_audio_blobs()
        assert len(blobs) > 0
        for blob in blobs:
            assert blob.mime_type == PCM_MIME, (
                f"Expected mime_type={PCM_MIME!r}, got {blob.mime_type!r}. "
                "Live API requires raw PCM, not WAV."
            )

    def test_pcm_mime_does_not_start_with_wav(self):
        _, _, PCM_MIME = self._capture_audio_blobs()
        assert not PCM_MIME.startswith("audio/wav"), (
            "Live API requires audio/pcm;rate=16000, not audio/wav"
        )

    def test_pcm_mime_contains_rate(self):
        _, _, PCM_MIME = self._capture_audio_blobs()
        assert "16000" in PCM_MIME

    def test_activity_start_sent(self):
        _, calls, _ = self._capture_audio_blobs()
        start_calls = [c for c in calls if "activity_start" in c]
        assert len(start_calls) >= 1, "activity_start must be sent"

    def test_activity_end_sent(self):
        _, calls, _ = self._capture_audio_blobs()
        end_calls = [c for c in calls if "activity_end" in c]
        assert len(end_calls) >= 1, "activity_end must be sent"

    def test_no_wav_header_in_pcm_blob(self):
        """PCM blob data must NOT start with RIFF (no WAV header)."""
        blobs, _, _ = self._capture_audio_blobs()
        for blob in blobs:
            assert blob.data[:4] != b"RIFF", (
                "Blob data must be raw PCM, not WAV (no RIFF header)"
            )


# ---------------------------------------------------------------------------
# TEST CLASS 7 — Successful Live transcription
# ---------------------------------------------------------------------------

class TestGeminiLiveTranscribeSuccess:
    """Mock the _transcribe_live coroutine to return a known result."""

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()
        p._client = MagicMock()
        return p

    def _mock_live_result(self, provider, result):
        """Patch _transcribe_live to return a fixed result."""
        async def _fake_live(audio, language, client, t0):
            return result
        provider._transcribe_live = _fake_live

    def test_success_status_is_ok(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="ok", text="Enemy spotted on the left",
            language="en", provider="gemini", stt_latency_ms=500.0,
        ))
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "ok"

    def test_success_text_returned(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="ok", text="Dushman left side pe hai",
            language="hi", provider="gemini", stt_latency_ms=500.0,
        ))
        result = p.transcribe(_make_audio(800), language="hi")
        assert result.text == "Dushman left side pe hai"

    def test_success_provider_is_gemini(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="ok", text="test", language="te",
            provider="gemini", stt_latency_ms=100.0,
        ))
        result = p.transcribe(_make_audio(800), language="te")
        assert result.provider == "gemini"

    def test_success_language_preserved(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="ok", text="test", language="te",
            provider="gemini", stt_latency_ms=100.0,
        ))
        result = p.transcribe(_make_audio(800), language="te")
        assert result.language == "te"

    def test_success_latency_non_negative(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="ok", text="all clear", language="en",
            provider="gemini", stt_latency_ms=250.0,
        ))
        result = p.transcribe(_make_audio(800), language="en")
        assert result.stt_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# TEST CLASS 8 — No-speech (WS 1008 or empty transcript)
# ---------------------------------------------------------------------------

class TestGeminiLiveNoSpeech:

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()
        p._client = MagicMock()
        return p

    def _mock_live_result(self, provider, result):
        async def _fake_live(audio, language, client, t0):
            return result
        provider._transcribe_live = _fake_live

    def test_no_speech_status(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="no_speech", text="", language="hi",
            provider="gemini", stt_latency_ms=1200.0,
        ))
        result = p.transcribe(_make_audio(800), language="hi")
        assert result.status == "no_speech"

    def test_no_speech_text_is_empty(self):
        p = self._provider()
        self._mock_live_result(p, TranscriptionResult(
            status="no_speech", text="", language="hi",
            provider="gemini", stt_latency_ms=1200.0,
        ))
        result = p.transcribe(_make_audio(800), language="hi")
        assert result.text == ""

    def test_ws_1008_mapped_to_no_speech_via_transcribe_live(self):
        """
        _transcribe_live must return no_speech when it catches a 1008 exception.
        """
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()

        def _mock_connect(model, config):
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()

            async def _receive_1008():
                raise Exception("APIError: 1008 None. The operation was aborted.")
                yield  # make async generator

            session.receive = _receive_1008
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_client = MagicMock()
        mock_client.aio.live.connect.side_effect = _mock_connect
        p._client = mock_client

        result = asyncio.run(
            p._transcribe_live(_make_audio(800), "hi", mock_client, time.perf_counter())
        )
        assert result.status == "no_speech"
        assert result.text == ""


# ---------------------------------------------------------------------------
# TEST CLASS 9 — Error handling
# ---------------------------------------------------------------------------

class TestGeminiLiveErrors:

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()
        return p

    def _mock_live_error_result(self, provider, exc_message):
        """Simulate _transcribe_live raising exc_message as Exception."""
        async def _fake_live(audio, language, client, t0):
            stt_ms = round((time.perf_counter() - t0) * 1_000, 1)
            exc_lower = exc_message.lower()
            if any(kw in exc_lower for kw in ("api_key", "authentication", "401", "403")):
                msg = "Authentication failed. Check GEMINI_API_KEY in backend/.env."
            elif any(kw in exc_lower for kw in ("quota", "rate", "429")):
                msg = "Rate limit or quota exceeded. Please wait and try again."
            elif "timeout" in exc_lower:
                msg = "Request timed out."
            elif "1008" in exc_lower:
                return TranscriptionResult(
                    status="no_speech", text="", language=language,
                    provider="gemini", stt_latency_ms=stt_ms,
                )
            else:
                msg = "Speech recognition failed (provider error)."
            return TranscriptionResult(
                status="provider_error", text="", language=language,
                provider="gemini", stt_latency_ms=stt_ms,
                error_message=msg,
            )
        provider._transcribe_live = _fake_live
        provider._client = MagicMock()

    def test_generic_exception_returns_provider_error(self):
        p = self._provider()
        self._mock_live_error_result(p, "Something went wrong")
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "provider_error"

    def test_generic_exception_does_not_raise(self):
        p = self._provider()
        self._mock_live_error_result(p, "Something went wrong")
        result = p.transcribe(_make_audio(800), language="en")
        assert isinstance(result, TranscriptionResult)

    def test_generic_exception_has_latency(self):
        p = self._provider()
        self._mock_live_error_result(p, "fail")
        result = p.transcribe(_make_audio(800), language="en")
        assert result.stt_latency_ms >= 0.0

    def test_auth_error_returns_provider_error(self):
        p = self._provider()
        self._mock_live_error_result(p, "authentication 401 failed")
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "provider_error"
        assert "Authentication" in (result.error_message or "")

    def test_quota_error_returns_provider_error(self):
        p = self._provider()
        self._mock_live_error_result(p, "quota exceeded 429")
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "provider_error"
        assert "quota" in (result.error_message or "").lower() \
            or "limit" in (result.error_message or "").lower()

    def test_1008_error_returns_no_speech(self):
        """WS 1008 is explicitly mapped to no_speech (not provider_error)."""
        p = self._provider()
        self._mock_live_error_result(p, "APIError: 1008 None")
        result = p.transcribe(_make_audio(800), language="hi")
        assert result.status == "no_speech"

    def test_error_message_safe(self):
        """Error message must not expose api key or internal stack trace."""
        p = self._provider()
        self._mock_live_error_result(p, "Something went wrong")
        result = p.transcribe(_make_audio(800), language="en")
        msg = result.error_message or ""
        assert "fake-key" not in msg
        assert "AIza" not in msg
        assert "Traceback" not in msg


# ---------------------------------------------------------------------------
# TEST CLASS 10 — Language mapping
# ---------------------------------------------------------------------------

class TestGeminiLanguageMapping:
    """BCP-47 codes forwarded correctly in LiveConnectConfig."""

    def test_en_maps_to_en_us(self):
        from speech.gemini_stt import _LANG_BCP47
        assert _LANG_BCP47["en"] == "en-US"

    def test_hi_maps_to_hi_in(self):
        from speech.gemini_stt import _LANG_BCP47
        assert _LANG_BCP47["hi"] == "hi-IN"

    def test_te_maps_to_te_in(self):
        from speech.gemini_stt import _LANG_BCP47
        assert _LANG_BCP47["te"] == "te-IN"

    def test_ta_maps_to_ta_in(self):
        from speech.gemini_stt import _LANG_BCP47
        assert _LANG_BCP47["ta"] == "ta-IN"

    def test_unknown_code_passes_through(self):
        from speech.gemini_stt import _LANG_BCP47
        assert _LANG_BCP47.get("xx", "xx") == "xx"

    def test_live_config_receives_hi_in(self):
        """hi -> hi-IN in input_audio_transcription.language_codes."""
        from speech.gemini_stt import GeminiSpeechProvider

        captured = {}

        def _mock_connect(model, config):
            captured["config"] = config
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()

            async def _recv():
                sc = MagicMock()
                sc.input_transcription = None
                sc.interim_input_transcription = None  # prevent truthy MagicMock fallback
                sc.turn_complete = True
                yield MagicMock(server_content=sc)

            session.receive = _recv
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake",
            "GEMINI_STT_AUTO_DETECT": "false",
        }):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            asyncio.run(p._transcribe_live(_make_audio(800), "hi", mock_client, time.perf_counter()))
        assert captured["config"].input_audio_transcription.language_codes == ["hi-IN"]

    def test_live_config_auto_detect_sends_empty(self):
        """GEMINI_STT_AUTO_DETECT=true -> language_codes=[]."""
        from speech.gemini_stt import GeminiSpeechProvider

        captured = {}

        def _mock_connect(model, config):
            captured["config"] = config
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()

            async def _recv():
                sc = MagicMock()
                sc.input_transcription = None
                sc.interim_input_transcription = None  # prevent truthy MagicMock fallback
                sc.turn_complete = True
                yield MagicMock(server_content=sc)

            session.receive = _recv
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake",
            "GEMINI_STT_AUTO_DETECT": "true",
        }):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            asyncio.run(p._transcribe_live(_make_audio(800), "hi", mock_client, time.perf_counter()))
        assert captured["config"].input_audio_transcription.language_codes == []


# ---------------------------------------------------------------------------
# TEST CLASS 11 — Provider factory
# ---------------------------------------------------------------------------

class TestGeminiProviderFactory:

    def test_factory_returns_gemini_provider(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            from speech.provider import get_speech_provider
            p = get_speech_provider("gemini")
            assert p.name == "gemini"

    def test_factory_gemini_instance_type(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            from speech.gemini_stt import GeminiSpeechProvider
            from speech.provider import get_speech_provider
            p = get_speech_provider("gemini")
            assert isinstance(p, GeminiSpeechProvider)

    def test_factory_model_override_via_env(self):
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake-key",
            "GEMINI_STT_MODEL": "gemini-3.5-transcribe-live",
        }):
            from speech.provider import get_speech_provider
            p = get_speech_provider("gemini")
            assert p._model == "gemini-3.5-transcribe-live"


# ---------------------------------------------------------------------------
# TEST CLASS 12 — Factory unknown provider
# ---------------------------------------------------------------------------

class TestGeminiFactoryUnknown:

    def test_unknown_provider_raises(self):
        from speech.provider import get_speech_provider
        with pytest.raises((ValueError, KeyError)):
            get_speech_provider("not_a_real_provider_xyz")


# ---------------------------------------------------------------------------
# TEST CLASS 13 — TranscriptionResult contract
# ---------------------------------------------------------------------------

class TestGeminiResultContract:

    def _ok_result(self) -> TranscriptionResult:
        return TranscriptionResult(
            status="ok",
            text="enemy spotted",
            language="en",
            provider="gemini",
            stt_latency_ms=300.0,
        )

    def test_result_is_frozen(self):
        r = self._ok_result()
        with pytest.raises((AttributeError, TypeError)):
            r.text = "modified"  # type: ignore

    def test_result_has_all_required_fields(self):
        r = self._ok_result()
        assert r.status == "ok"
        assert r.text == "enemy spotted"
        assert r.language == "en"
        assert r.provider == "gemini"
        assert r.stt_latency_ms == 300.0

    def test_optional_fields_default_none(self):
        r = self._ok_result()
        assert r.confidence is None
        assert r.error_message is None

    def test_metadata_defaults_to_empty_dict(self):
        r = self._ok_result()
        assert isinstance(r.metadata, dict)

    def test_metadata_in_ok_result(self):
        from speech.gemini_stt import GeminiSpeechProvider

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            p = GeminiSpeechProvider()

        async def _fake_live(audio, language, client, t0):
            return TranscriptionResult(
                status="ok",
                text="test",
                language=language,
                provider="gemini",
                stt_latency_ms=100.0,
                metadata={"model": p._model, "bcp47": "en-US",
                           "audio_duration_ms": 800.0, "pcm_bytes": len(audio.pcm_bytes),
                           "fragments": 1},
            )

        p._transcribe_live = _fake_live
        p._client = MagicMock()

        result = p.transcribe(_make_audio(800), language="en")
        assert "model" in result.metadata
        assert "bcp47" in result.metadata
        assert "pcm_bytes" in result.metadata
        assert "fragments" in result.metadata


# ---------------------------------------------------------------------------
# TEST CLASS 14 — Latency always present
# ---------------------------------------------------------------------------

class TestGeminiLatencyMetadata:

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
            return GeminiSpeechProvider()

    def test_empty_audio_has_latency(self):
        p = self._provider()
        result = p.transcribe(_tiny_audio(), language="en")
        assert result.stt_latency_ms >= 0.0

    def test_missing_key_has_latency(self):
        p = self._provider()
        p._api_key = ""
        result = p.transcribe(_make_audio(800), language="en")
        assert result.stt_latency_ms >= 0.0

    def test_sdk_missing_has_latency(self):
        p = self._provider()
        p._get_client = lambda: None
        result = p.transcribe(_make_audio(800), language="en")
        assert result.stt_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# TEST CLASS 15 — MockSpeechProvider regression
# ---------------------------------------------------------------------------

class TestMockRegressionFromRealSTT:

    def test_mock_provider_still_works(self):
        p = MockSpeechProvider()
        result = p.transcribe(_make_audio(800), language="en")
        assert result.status == "ok"
        assert len(result.text) > 0

    def test_mock_transcribes_hindi(self):
        p = MockSpeechProvider()
        result = p.transcribe(_make_audio(800), language="hi")
        assert result.status == "ok"

    def test_mock_transcribes_telugu(self):
        p = MockSpeechProvider()
        result = p.transcribe(_make_audio(800), language="te")
        assert result.status == "ok"

    def test_mock_transcribes_tamil(self):
        p = MockSpeechProvider()
        result = p.transcribe(_make_audio(800), language="ta")
        assert result.status == "ok"

    def test_mock_empty_audio_returns_empty_audio(self):
        p = MockSpeechProvider()
        result = p.transcribe(_tiny_audio(), language="en")
        assert result.status == "empty_audio"

    def test_mock_factory_still_works(self):
        from speech.provider import get_speech_provider
        p = get_speech_provider("mock")
        assert p.name == "mock"


# ---------------------------------------------------------------------------
# TEST CLASS 16 — PCM amplitude log
# ---------------------------------------------------------------------------

class TestPcmAmplitudeLog:

    def _provider(self):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-amplitude"}):
            return GeminiSpeechProvider()

    def test_amplitude_log_fires_for_normal_audio(self, caplog):
        import logging
        p = self._provider()

        async def _fake_live(audio, language, client, t0):
            return TranscriptionResult(
                status="ok", text="test", language=language,
                provider="gemini", stt_latency_ms=100.0,
            )
        p._transcribe_live = _fake_live
        p._client = MagicMock()

        with caplog.at_level(logging.INFO, logger="gamevoice.speech.gemini_stt"):
            p.transcribe(_make_audio(800), language="en")

        amp_logs = [r for r in caplog.records if "max_amp" in r.getMessage()]
        assert len(amp_logs) >= 1, "Expected amplitude log to fire"

    def test_amplitude_log_does_not_fire_for_tiny_audio(self, caplog):
        import logging
        p = self._provider()

        with caplog.at_level(logging.INFO, logger="gamevoice.speech.gemini_stt"):
            p.transcribe(_tiny_audio(), language="en")

        amp_logs = [r for r in caplog.records if "max_amp" in r.getMessage()]
        assert len(amp_logs) == 0

    def test_wav_header_not_in_module(self):
        """_pcm_to_wav is no longer needed — should not be used by the Live provider."""
        import speech.gemini_stt as m
        # _pcm_to_wav is removed; Live API uses raw PCM
        assert not hasattr(m, "_pcm_to_wav"), (
            "_pcm_to_wav was used for the batch API; "
            "Live API uses raw PCM and should not have this function."
        )


# ---------------------------------------------------------------------------
# TEST CLASS 17 — GEMINI_STT_AUTO_DETECT flag
# ---------------------------------------------------------------------------

class TestGeminiAutoDetect:
    """
    GEMINI_STT_AUTO_DETECT=true  -> language_codes=[] (auto-detect)
    GEMINI_STT_AUTO_DETECT=false -> language_codes=[bcp47] (explicit)
    Verified via _transcribe_live async coroutine directly.
    """

    def _provider(self, auto_detect="false"):
        from speech.gemini_stt import GeminiSpeechProvider
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key-autodetect",
            "GEMINI_STT_AUTO_DETECT": auto_detect,
        }):
            return GeminiSpeechProvider()

    def _run_and_capture(self, language, auto_detect="false"):
        from speech.gemini_stt import GeminiSpeechProvider

        captured = {}

        def _mock_connect(model, config):
            captured["config"] = config
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()

            async def _recv():
                sc = MagicMock()
                sc.input_transcription = None
                sc.interim_input_transcription = None  # prevent truthy MagicMock fallback
                sc.turn_complete = True
                yield MagicMock(server_content=sc)

            session.receive = _recv
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "fake",
            "GEMINI_STT_AUTO_DETECT": auto_detect,
        }):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            asyncio.run(p._transcribe_live(_make_audio(800), language, mock_client, time.perf_counter()))
        return captured.get("config")

    def test_auto_detect_true_sends_empty_language_codes(self):
        cfg = self._run_and_capture("hi", auto_detect="true")
        assert cfg.input_audio_transcription.language_codes == []

    def test_auto_detect_false_sends_bcp47_hint(self):
        cfg = self._run_and_capture("hi", auto_detect="false")
        assert cfg.input_audio_transcription.language_codes == ["hi-IN"]

    def test_auto_detect_true_for_te_still_sends_empty(self):
        cfg = self._run_and_capture("te", auto_detect="true")
        assert cfg.input_audio_transcription.language_codes == []

    def test_auto_detect_false_for_te_sends_te_in(self):
        cfg = self._run_and_capture("te", auto_detect="false")
        assert cfg.input_audio_transcription.language_codes == ["te-IN"]


# ---------------------------------------------------------------------------
# TEST CLASS 18 — _collect() early-exit on non-empty input_transcription
# ---------------------------------------------------------------------------

class TestGeminiEarlyExit:
    """
    Verify that _collect() exits promptly as soon as it receives a non-empty
    input_transcription.text — without requiring finished=True or turn_complete.

    Root cause reproduced here:
        gemini-3.5-transcribe-live sends input_transcription with finished=None.
        The old code checked 'if sc.input_transcription.finished:' which evaluates
        False for None, so the loop waited 25s for the timeout.

    The fix: break on any non-empty input_transcription.text immediately.

    All tests are fully offline — no real Gemini API calls.
    """

    def _make_sc(
        self,
        *,
        input_text: str | None = None,
        finished: bool | None = None,
        interim_text: str | None = None,
        turn_complete: bool = False,
    ) -> MagicMock:
        """Build a mock LiveServerContent object."""
        sc = MagicMock()
        sc.turn_complete = turn_complete
        sc.interim_input_transcription = None
        sc.waiting_for_input = None

        if input_text is not None:
            sc.input_transcription = MagicMock()
            sc.input_transcription.text = input_text
            sc.input_transcription.finished = finished
        else:
            sc.input_transcription = None

        if interim_text is not None:
            sc.interim_input_transcription = MagicMock()
            sc.interim_input_transcription.text = interim_text

        return sc

    def _make_response(self, sc: MagicMock) -> MagicMock:
        r = MagicMock()
        r.server_content = sc
        return r

    def _run_live(self, responses: list) -> TranscriptionResult:
        """Run _transcribe_live with a mocked session yielding `responses`."""
        from speech.gemini_stt import GeminiSpeechProvider

        async def _receive():
            for r in responses:
                yield r

        def _mock_connect(model, config):
            cm = AsyncMock()
            session = AsyncMock()
            session.send_realtime_input = AsyncMock()
            session.receive = _receive
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-early-exit"}):
            p = GeminiSpeechProvider()
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = _mock_connect
            p._client = mock_client
            return asyncio.run(
                p._transcribe_live(_make_audio(800), "hi", mock_client, time.perf_counter())
            )

    # --- CASE 1: The real observed failure ---
    # activity_end -> final input_transcription with finished=None (no turn_complete)
    # -> provider MUST return promptly, not wait 25s
    def test_final_transcript_finished_none_returns_promptly(self):
        """
        Reproduces the real observed behavior:
          Gemini sends input_transcription with text but finished=None.
          No turn_complete follows.
          Provider must break on the non-empty text, not wait for timeout.
        """
        sc = self._make_sc(input_text="पीछे देख, पीछे दुश्मन है।", finished=None)
        # No turn_complete message follows — mirrors the real PTT session
        result = self._run_live([self._make_response(sc)])
        assert result.status == "ok"
        assert "पीछे" in result.text

    # --- CASE 2: finished=True also exits promptly (bonus guard) ---
    def test_final_transcript_finished_true_returns_promptly(self):
        """finished=True on a non-empty transcript -> status ok, correct text."""
        sc = self._make_sc(input_text="hello world", finished=True)
        result = self._run_live([self._make_response(sc)])
        assert result.status == "ok"
        assert result.text == "hello world"

    # --- CASE 3: interim only, no final -> must NOT trigger early-exit break ---
    def test_interim_only_does_not_trigger_early_exit(self):
        """
        Interim transcripts alone must NOT trigger the early-exit break.
        The loop must wait for turn_complete before finishing.
        After turn_complete, the last interim is promoted as fallback -> status ok.
        (If interim caused early exit, 'partial two' would not appear.)
        """
        sc_interim1 = self._make_sc(interim_text="partial one")
        sc_interim2 = self._make_sc(interim_text="partial two")
        sc_done = self._make_sc(turn_complete=True)
        result = self._run_live([
            self._make_response(sc_interim1),
            self._make_response(sc_interim2),
            self._make_response(sc_done),
        ])
        # Interim fallback path: last interim promoted -> ok with text
        assert result.status == "ok"
        assert result.text == "partial two"  # last (most complete) interim fragment

    # --- CASE 4: final transcript + immediate turn_complete -> returns promptly ---
    def test_final_transcript_with_turn_complete_returns_ok(self):
        """Both input_transcription and turn_complete in sequence -> ok."""
        sc_text = self._make_sc(input_text="Namaste!", finished=None, turn_complete=False)
        sc_done = self._make_sc(turn_complete=True)
        result = self._run_live([
            self._make_response(sc_text),
            self._make_response(sc_done),
        ])
        assert result.status == "ok"
        assert result.text == "Namaste!"

    # --- CASE 5: multiple interim + final -> only finalized text returned ---
    def test_multiple_interim_then_final_returns_final_text(self):
        """Several interim updates, then non-empty input_transcription -> final text only."""
        sc1 = self._make_sc(interim_text="na")
        sc2 = self._make_sc(interim_text="namas")
        sc3 = self._make_sc(interim_text="namaste")
        sc4 = self._make_sc(input_text="Namaste!", finished=None)
        result = self._run_live([
            self._make_response(sc1),
            self._make_response(sc2),
            self._make_response(sc3),
            self._make_response(sc4),
        ])
        assert result.status == "ok"
        assert result.text == "Namaste!"

    # --- CASE 6: no transcript at all -> turn_complete -> no_speech ---
    def test_no_transcript_turn_complete_returns_no_speech(self):
        """No input_transcription, only turn_complete -> no_speech."""
        sc = self._make_sc(turn_complete=True)
        result = self._run_live([self._make_response(sc)])
        assert result.status == "no_speech"
        assert result.text == ""

    # --- CASE 7: empty input_transcription text -> fall through to turn_complete ---
    def test_empty_input_transcription_falls_through_to_turn_complete(self):
        """input_transcription with empty/None text -> not a completion signal."""
        sc_empty = self._make_sc(input_text="", finished=None)
        sc_done = self._make_sc(turn_complete=True)
        result = self._run_live([
            self._make_response(sc_empty),
            self._make_response(sc_done),
        ])
        assert result.status == "no_speech"

    # --- CASE 8: interim fallback used when no finalized transcript arrives ---
    def test_interim_used_as_fallback_when_no_final(self):
        """
        If only interim transcripts arrive (and turn_complete exits the loop),
        the last interim fragment is used as fallback.
        """
        sc_interim = self._make_sc(interim_text="fallback text")
        sc_done = self._make_sc(turn_complete=True)
        result = self._run_live([
            self._make_response(sc_interim),
            self._make_response(sc_done),
        ])
        # Interim fallback -> status ok with the last interim fragment
        assert result.status == "ok"
        assert result.text == "fallback text"
