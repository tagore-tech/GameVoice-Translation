/**
 * PushToTalk — PTT button component.
 *
 * Delegates all state management to usePushToTalk.
 * Renders:
 *   - The main PTT button (click, touch, Space key)
 *   - Recording pulse animation while mic is active
 *   - Key binding hint
 *   - Mic error message
 *   - Debug info panel (last capture size, duration, format)
 *
 * The debug panel is intentionally visible in M1–M6 to verify
 * the audio pipeline at each stage. It will be hidden behind a
 * developer toggle in M10 (gaming UI polish).
 */

import { useCallback, useState } from 'react'
import type { AudioChunk } from '../audio/AudioCapture'
import { usePushToTalk } from '../hooks/usePushToTalk'
import { PTT_CONFIG } from '../config/ptt'

interface PushToTalkProps {
  /** Whether PTT interactions are allowed (set to false when not in a room). */
  disabled?: boolean
  /** Optional: called with each captured audio chunk for upstream processing. */
  onChunk?: (chunk: AudioChunk) => void
}

interface DebugInfo {
  sizeKB: string
  durationMs: number
  mimeType: string
}

export function PushToTalk({ disabled = false, onChunk }: PushToTalkProps) {
  const [micError, setMicError] = useState<string | null>(null)
  const [lastDebug, setLastDebug] = useState<DebugInfo | null>(null)

  const handleChunk = useCallback(
    (chunk: AudioChunk) => {
      setLastDebug({
        sizeKB: (chunk.blob.size / 1024).toFixed(1),
        durationMs: chunk.durationMs,
        mimeType: chunk.mimeType.split(';')[0] ?? chunk.mimeType,
      })
      setMicError(null)
      onChunk?.(chunk)
    },
    [onChunk],
  )

  const handleError = useCallback((err: Error) => {
    setMicError(err.message)
  }, [])

  const { state, isRecording, buttonProps } = usePushToTalk({
    onChunk: handleChunk,
    onError: handleError,
    disabled,
  })

  // Human-readable label for current state
  const label =
    state === 'requesting' ? 'REQUESTING MIC…' :
    state === 'recording'  ? 'RECORDING…'       :
    state === 'processing' ? 'PROCESSING…'      :
    state === 'error'      ? 'MIC ERROR'         :
                             'HOLD TO TALK'

  return (
    <div className="ptt-container">

      {/* ── Mic permission error ──────────────────────────────────────── */}
      {micError && (
        <p className="ptt-error" role="alert">
          🎤 {micError}
        </p>
      )}

      {/* ── PTT button ───────────────────────────────────────────────── */}
      <button
        className={[
          'ptt-button',
          isRecording          ? 'ptt-recording'  : '',
          state === 'requesting' ? 'ptt-requesting' : '',
          state === 'error'      ? 'ptt-error-state' : '',
        ].join(' ')}
        aria-label={isRecording ? 'Recording — release to send' : 'Hold to talk'}
        aria-pressed={isRecording}
        {...buttonProps}
      >
        {/* Pulse ring — visible only while recording */}
        {isRecording && <span className="ptt-pulse" aria-hidden="true" />}

        <span className="ptt-icon" aria-hidden="true">
          {isRecording ? '⏺' : '🎤'}
        </span>
        <span className="ptt-label">{label}</span>
      </button>

      {/* ── Key hint ─────────────────────────────────────────────────── */}
      <p className="ptt-hint">
        Hold <kbd>{PTT_CONFIG.defaultKey}</kbd> or press &amp; hold button
      </p>

      {/* ── Debug info (last captured chunk) ─────────────────────────── */}
      {lastDebug && (
        <div className="ptt-debug" aria-label="Last capture info">
          <span className="ptt-debug-item">
            <span className="ptt-debug-label">SIZE</span>
            <span className="ptt-debug-value">{lastDebug.sizeKB} KB</span>
          </span>
          <span className="ptt-debug-item">
            <span className="ptt-debug-label">DURATION</span>
            <span className="ptt-debug-value">{lastDebug.durationMs} ms</span>
          </span>
          <span className="ptt-debug-item">
            <span className="ptt-debug-label">FORMAT</span>
            <span className="ptt-debug-value">{lastDebug.mimeType}</span>
          </span>
        </div>
      )}

    </div>
  )
}
