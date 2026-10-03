/**
 * usePushToTalk — React hook managing the full PTT lifecycle.
 *
 * Handles three interaction modalities:
 *   1. Mouse: onMouseDown / onMouseUp / onMouseLeave (button escapes)
 *   2. Touch: onTouchStart / onTouchEnd (mobile / Android PWA)
 *   3. Keyboard: keydown / keyup on the configured key (default: Space)
 *
 * The keyboard listener is attached to `window` so it works even when
 * the PTT button is not focused — the way push-to-talk works in games.
 * Typing in inputs is explicitly excluded so the user can still type.
 *
 * State machine:
 *
 *   idle ──press──► requesting ──mic granted──► recording ──release──► processing ──► idle
 *                       │                                                     │
 *                    mic denied                                           chunk emitted
 *                       │                                                 via onChunk
 *                       ▼
 *                     error ──(auto-reset after 3s)──► idle
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { TouchEvent as ReactTouchEvent } from 'react'
import { AudioCapture, type AudioChunk } from '../audio/AudioCapture'
import { PTT_CONFIG } from '../config/ptt'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PTTState = 'idle' | 'requesting' | 'recording' | 'processing' | 'error'

export interface UsePushToTalkOptions {
  /** Called with the audio blob after each valid PTT release. */
  onChunk?: (chunk: AudioChunk) => void
  /** Called when mic access fails or another error occurs. */
  onError?: (error: Error) => void
  /**
   * KeyboardEvent.code for the keyboard shortcut.
   * Defaults to PTT_CONFIG.defaultKey ('Space').
   * Pass a different code to override (e.g. 'KeyV', 'CapsLock').
   */
  keyBinding?: string
  /** Disable all PTT interactions (e.g. when not connected to a room). */
  disabled?: boolean
}

export interface PTTButtonProps {
  onMouseDown: () => void
  onMouseUp: () => void
  onMouseLeave: () => void
  onTouchStart: (e: ReactTouchEvent) => void
  onTouchEnd: (e: ReactTouchEvent) => void
  disabled: boolean
}

export interface UsePushToTalkResult {
  state: PTTState
  isRecording: boolean
  /** Spread these directly onto the PTT <button> element. */
  buttonProps: PTTButtonProps
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePushToTalk({
  onChunk,
  onError,
  keyBinding = PTT_CONFIG.defaultKey,
  disabled = false,
}: UsePushToTalkOptions = {}): UsePushToTalkResult {
  const [state, setState] = useState<PTTState>('idle')

  // Stable refs — values updated every render but never cause re-renders
  const captureRef = useRef(new AudioCapture())
  const isHeldRef = useRef(false)           // prevents double-start on repeated keydown
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onChunkRef = useRef(onChunk)
  const onErrorRef = useRef(onError)

  // Keep callback refs fresh without requiring them in useEffect deps
  useEffect(() => { onChunkRef.current = onChunk }, [onChunk])
  useEffect(() => { onErrorRef.current = onError }, [onError])

  // -------------------------------------------------------------------------
  // Core actions
  // -------------------------------------------------------------------------

  const stopRecording = useCallback(async () => {
    if (!isHeldRef.current) return
    isHeldRef.current = false

    // Clear auto-stop timer if it was set
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current)
      autoStopTimerRef.current = null
    }

    if (!captureRef.current.isRecording) {
      // start() hasn't finished yet (very quick tap) — just reset
      setState('idle')
      return
    }

    setState('processing')
    try {
      const chunk = await captureRef.current.stop()

      if (chunk.durationMs < PTT_CONFIG.minDurationMs) {
        // Too short — likely an accidental tap; discard silently
        setState('idle')
        return
      }

      onChunkRef.current?.(chunk)
    } catch (err) {
      onErrorRef.current?.(
        err instanceof Error ? err : new Error('Failed to capture audio'),
      )
    } finally {
      setState('idle')
    }
  }, []) // stable: only uses refs

  const startRecording = useCallback(async () => {
    if (isHeldRef.current || disabled) return
    isHeldRef.current = true
    setState('requesting')

    try {
      await captureRef.current.start()
      setState('recording')

      // Auto-stop safety net — frees the mic if PTT key/button is forgotten
      autoStopTimerRef.current = setTimeout(() => {
        void stopRecording()
      }, PTT_CONFIG.maxDurationMs)
    } catch (err) {
      isHeldRef.current = false
      setState('error')
      onErrorRef.current?.(
        err instanceof Error ? err : new Error('Microphone access denied'),
      )
      // Auto-reset error state after 3 s so the button becomes usable again
      setTimeout(() => setState('idle'), 3000)
    }
  }, [disabled, stopRecording])

  // -------------------------------------------------------------------------
  // Keyboard PTT (window-level listener)
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (disabled) return

    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't hijack Space when the user is typing in a form field
      const target = e.target as HTMLElement
      const tag = target.tagName.toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return

      if (e.code === keyBinding && !e.repeat) {
        e.preventDefault() // prevent page scroll on Space
        void startRecording()
      }
    }

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.code === keyBinding) {
        e.preventDefault()
        void stopRecording()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [keyBinding, disabled, startRecording, stopRecording])

  // -------------------------------------------------------------------------
  // Result
  // -------------------------------------------------------------------------

  return {
    state,
    isRecording: state === 'recording',
    buttonProps: {
      onMouseDown: () => void startRecording(),
      onMouseUp: () => void stopRecording(),
      // Safety release: if the cursor escapes the button while held, stop recording
      onMouseLeave: () => void stopRecording(),
      onTouchStart: (e: ReactTouchEvent) => {
        e.preventDefault() // prevent ghost mouse events on mobile
        void startRecording()
      },
      onTouchEnd: (e: ReactTouchEvent) => {
        e.preventDefault()
        void stopRecording()
      },
      disabled,
    },
  }
}
