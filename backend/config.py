"""
GameVoice Translator — Configuration
Reads all settings from environment variables (or a .env file).
Never hard-codes credentials or provider-specific values.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # CORS — comma-separated list of allowed origins
    cors_origins: list[str] = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    # Provider selection — controls which concrete implementation is loaded.
    # Valid values per provider are documented in .env.example.
    stt_provider: str = os.getenv("STT_PROVIDER", "mock")
    translation_provider: str = os.getenv("TRANSLATION_PROVIDER", "mock")
    tts_provider: str = os.getenv("TTS_PROVIDER", "gtts")

    # Language configuration — PRE-M5
    # source_language: what this server's player speaks (their mic input)
    # target_language: what language they want to receive (future M5 translation target)
    # Both must be codes present in languages.SUPPORTED_CODES.
    # Defaults preserve M4 behaviour (English STT, no translation yet).
    source_language: str = os.getenv("SOURCE_LANGUAGE", "en")
    target_language: str = os.getenv("TARGET_LANGUAGE", "en")

    # Gemini model configuration
    # GEMINI_API_KEY is reused for both STT (Gemini Live) and translation.
    gemini_translation_model: str = os.getenv(
        "GEMINI_TRANSLATION_MODEL", "gemini-2.0-flash"
    )


settings = Settings()
