"""
GameVoice Translator — Backend Entry Point

M4: Instantiates the configured SpeechProvider at startup and injects
    it into each WebSocket connection handler.
    Audio → PyAV PCM (M3) → SpeechProvider (M4) → transcription JSON.

M5: Instantiates the configured TranslationProvider at startup and injects
    it alongside the SpeechProvider.
    Transcription text → TranslationProvider (M5) → translation JSON.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from languages import validate_language_code
from speech.base import SpeechProvider
from speech.provider import get_speech_provider
from translation.base import TranslationProvider
from translation.provider import get_translation_provider
from ws.handler import handle_connection

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gamevoice")

# ---------------------------------------------------------------------------
# In-memory room registry
# ---------------------------------------------------------------------------

active_rooms: dict[str, list[WebSocket]] = {}

# ---------------------------------------------------------------------------
# Providers — loaded once at startup
# ---------------------------------------------------------------------------

speech_provider: SpeechProvider | None = None
translation_provider: TranslationProvider | None = None


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    global speech_provider, translation_provider

    # --- Validate language config at startup ---
    try:
        validate_language_code(settings.source_language)
        validate_language_code(settings.target_language)
    except ValueError as exc:
        logger.error("Invalid language configuration: %s", exc)
        logger.warning("Backend will start but language config may be wrong.")

    logger.info(
        "GameVoice Translator starting — "
        f"host={settings.host} port={settings.port} "
        f"debug={settings.debug} stt={settings.stt_provider!r} "
        f"translation={settings.translation_provider!r} "
        f"source={settings.source_language!r} target={settings.target_language!r}"
    )

    # --- STT provider ---
    try:
        speech_provider = get_speech_provider(settings.stt_provider)
        logger.info("SpeechProvider loaded: %s", speech_provider.name)
    except Exception as exc:
        logger.error("Failed to load SpeechProvider %r: %s", settings.stt_provider, exc)
        logger.warning("Backend will start but STT will be unavailable.")
        speech_provider = None

    # --- Translation provider ---
    try:
        translation_provider = get_translation_provider(settings.translation_provider)
        logger.info("TranslationProvider loaded: %s", translation_provider.name)
    except Exception as exc:
        logger.error(
            "Failed to load TranslationProvider %r: %s",
            settings.translation_provider, exc,
        )
        logger.warning("Backend will start but translation will be unavailable.")
        translation_provider = None

    yield

    logger.info("GameVoice Translator shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GameVoice Translator",
    version="0.1.0-m5",
    description="Real-time gaming voice translation backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["infrastructure"])
async def health():
    """Liveness check — includes current provider names."""
    return {
        "status": "ok",
        "version": "0.1.0-m5",
        "stt_provider": speech_provider.name if speech_provider else "none",
        "translation_provider": translation_provider.name if translation_provider else "none",
        "source_language": settings.source_language,
        "target_language": settings.target_language,
        "active_rooms": list(active_rooms.keys()),
        "total_connections": sum(len(v) for v in active_rooms.values()),
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    """
    One WebSocket connection per player.
    Binary frames → M3 PyAV → M4 SpeechProvider → M5 TranslationProvider
                 → 'translation' JSON (or 'transcription' if same-language).
    Text frames  → echo (M1 control path).
    """
    room_id = room_id.upper().strip()
    await handle_connection(
        websocket,
        room_id,
        active_rooms,
        speech_provider=speech_provider,
        translation_provider=translation_provider,
        source_language=settings.source_language,
        target_language=settings.target_language,
    )
