"""
backend/audio/processor.py — M3

Audio processing module.
Receives raw binary audio (WebM/Opus or OGG/Opus) and converts it to
16 kHz mono signed 16-bit PCM using PyAV.

PRIVACY GUARANTEE
-----------------
All processing is performed in memory via io.BytesIO.
No audio data is written to disk at any point.

INTERFACE CONTRACT
------------------
The only public functions are validate() and convert().
No other module imports av directly — all PyAV usage is isolated here.
Replacing PyAV requires only changing this file.

RESULT TYPES
------------
convert() never raises; it returns either ProcessedAudio or
ProcessingError.  Callers must branch on isinstance().
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

import av

logger = logging.getLogger("gamevoice.audio.processor")

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

#: Hard cap on accepted audio payload.  Anything larger is rejected
#: before expensive decoding.  5 MB is well above 30 s of Opus at
#: typical bitrates (~128 kbps) and gives headroom for future codecs.
MAX_PAYLOAD_BYTES: int = 5 * 1024 * 1024   # 5 MB

#: Minimum meaningful payload.  Anything smaller than this is almost
#: certainly garbage, not a real audio frame.
MIN_PAYLOAD_BYTES: int = 16

#: Target output specification — must match what the future STT
#: provider expects.  Change here only; the rest of the codebase
#: reads the values from the ProcessedAudio result.
TARGET_SAMPLE_RATE: int = 16_000    # Hz
TARGET_CHANNELS: int = 1            # mono
TARGET_FORMAT: str = "s16"          # signed 16-bit PCM, little-endian

# ---------------------------------------------------------------------------
# Magic-byte signatures of accepted audio containers.
# This is a fast pre-filter; full structural validation is done by PyAV.
# ---------------------------------------------------------------------------
_VALID_SIGNATURES: tuple[bytes, ...] = (
    b"\x1a\x45\xdf\xa3",  # WebM / EBML (browser MediaRecorder default)
    b"OggS",               # OGG container (Firefox MediaRecorder)
    b"RIFF",               # WAV / RIFF  (used in automated tests)
)

# ---------------------------------------------------------------------------
# Result value types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessedAudio:
    """Successfully decoded and converted audio — ready for the STT provider."""
    pcm_bytes: bytes        # raw s16le PCM samples (in-memory, never saved)
    sample_rate: int        # == TARGET_SAMPLE_RATE
    channels: int           # == TARGET_CHANNELS
    sample_format: str      # == TARGET_FORMAT  ("s16")
    duration_ms: float      # audio duration in milliseconds
    conversion_ms: float    # wall-clock time spent converting (ms)


@dataclass(frozen=True)
class ProcessingError:
    """
    Describes why processing was rejected.
    Returned as a value — never raised as an exception.
    The 'code' field is safe to send to the client.
    The 'message' field should only go to server logs or devs.
    """
    message: str
    code: str  # 'empty' | 'too_small' | 'too_large' | 'invalid_format' | 'decode_failed'

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(data: bytes) -> ProcessingError | None:
    """
    Lightweight pre-flight check before expensive decoding.

    Returns None if the payload should be passed to convert(),
    or a ProcessingError if it must be rejected immediately.
    """
    if not data:
        return ProcessingError("Empty audio payload.", "empty")

    if len(data) < MIN_PAYLOAD_BYTES:
        return ProcessingError(
            f"Payload too small to be valid audio: {len(data)} bytes.",
            "too_small",
        )

    if len(data) > MAX_PAYLOAD_BYTES:
        return ProcessingError(
            f"Payload too large: {len(data) / 1_024:.1f} KB "
            f"(limit: {MAX_PAYLOAD_BYTES // 1_024} KB).",
            "too_large",
        )

    if not any(data.startswith(sig) for sig in _VALID_SIGNATURES):
        return ProcessingError(
            f"Unrecognised audio container "
            f"(first 4 bytes: {data[:4].hex()!r}).",
            "invalid_format",
        )

    return None


def convert(data: bytes) -> ProcessedAudio | ProcessingError:
    """
    Convert raw audio bytes to 16 kHz mono signed 16-bit PCM.

    Args:
        data:  Raw bytes from the browser's MediaRecorder API
               (WebM/Opus or OGG/Opus).

    Returns:
        ProcessedAudio on success.
        ProcessingError on any failure.

    This function NEVER raises.  All exceptions are caught and
    returned as ProcessingError.

    Privacy:  data is processed entirely via io.BytesIO.
              Nothing is written to disk.
    """
    t0 = time.perf_counter()

    # --- fast pre-filter ---
    err = validate(data)
    if err:
        return err

    container = None
    try:
        buf = io.BytesIO(data)
        container = av.open(buf)  # auto-detects WebM, OGG, WAV, etc.

        audio_streams = container.streams.audio
        if not audio_streams:
            return ProcessingError(
                "Container has no audio stream.", "decode_failed"
            )

        stream = audio_streams[0]

        resampler = av.AudioResampler(
            format=TARGET_FORMAT,
            layout="mono",
            rate=TARGET_SAMPLE_RATE,
        )

        chunks: list[bytes] = []

        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(bytes(resampled.planes[0]))

        # flush any buffered samples remaining in the resampler
        for resampled in resampler.resample(None):
            chunks.append(bytes(resampled.planes[0]))

        if not chunks:
            return ProcessingError("Decoded zero audio frames.", "decode_failed")

        pcm_bytes = b"".join(chunks)

        # s16 = 2 bytes per sample, 1 channel
        num_samples = len(pcm_bytes) // 2
        duration_ms = num_samples / TARGET_SAMPLE_RATE * 1_000.0
        conversion_ms = (time.perf_counter() - t0) * 1_000.0

        logger.info(
            "Converted %.1f KB → %.1f KB PCM  "
            "(audio %.0f ms, conversion %.1f ms)",
            len(data) / 1_024,
            len(pcm_bytes) / 1_024,
            duration_ms,
            conversion_ms,
        )

        return ProcessedAudio(
            pcm_bytes=pcm_bytes,
            sample_rate=TARGET_SAMPLE_RATE,
            channels=TARGET_CHANNELS,
            sample_format=TARGET_FORMAT,
            duration_ms=duration_ms,
            conversion_ms=conversion_ms,
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Audio conversion failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return ProcessingError(
            f"Decoding failed: {type(exc).__name__}",
            "decode_failed",
        )

    finally:
        if container is not None:
            try:
                container.close()
            except Exception:  # noqa: BLE001
                pass
