import { useCallback, useRef, useState } from 'react'
import { WebSocketClient } from './ws/WebSocketClient'
import { PushToTalk } from './components/PushToTalk'
import { SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE_CONFIG } from './config/languages'
import type { AudioChunk } from './audio/AudioCapture'
import type { JsonMessage } from './ws/WebSocketClient'
import type { ParticipantLanguageConfig } from './config/languages'
import './App.css'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'error'

type PttStatus =
  | 'idle'
  | 'processing'
  | 'transcribing'
  | 'ready'
  | 'no_speech'
  | 'stt_error'
  | 'audio_error'

/** Shape of a received M4 transcription (same-language or no translation). */
interface Transcription {
  text: string
  language: string
  provider: string
  confidence: number | null
  audioDurationMs: number
  audioConversionMs: number
  sttLatencyMs: number
  totalLatencyMs: number
}

/** Shape of a received M5 translation. */
interface Translation {
  sourceLanguage: string
  targetLanguage: string
  sourceText: string
  translatedText: string
  provider: string
  confidence: number | null
  audioDurationMs: number
  audioConversionMs: number
  sttLatencyMs: number
  translationLatencyMs: number
  totalLatencyMs: number
}

/** Translation failed — source text is preserved for fallback display. */
interface TranslationError {
  code: string
  message: string
  sourceText: string
  sourceLanguage: string
  targetLanguage: string
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const clientRef = useRef(new WebSocketClient())

  const [status, setStatus]           = useState<ConnectionStatus>('idle')
  const [roomId, setRoomId]           = useState('GAME123')
  const [peers, setPeers]             = useState(0)
  const [errorMsg, setErrorMsg]       = useState<string | null>(null)
  const [pttStatus, setPttStatus]     = useState<PttStatus>('idle')
  const [statusMsg, setStatusMsg]     = useState<string | null>(null)

  // Language config — session-level for M5 (per-player routing is M7)
  const [langConfig, setLangConfig]   = useState<ParticipantLanguageConfig>(DEFAULT_LANGUAGE_CONFIG)

  // Result state — only one of these is populated at a time
  const [transcription, setTranscription]     = useState<Transcription | null>(null)
  const [translation, setTranslation]         = useState<Translation | null>(null)
  const [translationError, setTranslationError] = useState<TranslationError | null>(null)

  const sendTimeRef = useRef<number>(0)

  const clearResults = () => {
    setTranscription(null)
    setTranslation(null)
    setTranslationError(null)
    setStatusMsg(null)
  }

  // -------------------------------------------------------------------------
  // Connect
  // -------------------------------------------------------------------------

  const connect = async () => {
    const id = roomId.trim().toUpperCase()
    if (!id) return

    setStatus('connecting')
    setErrorMsg(null)
    clearResults()
    setPttStatus('idle')

    const client = clientRef.current

    client.off('connected')
    client.on('connected', (msg) => {
      setStatus('connected')
      setPeers((msg['peers'] as number | undefined) ?? 1)
    })

    // ── M4: successful transcription (same-language or no translation) ───
    client.off('transcription')
    client.on('transcription', (msg: JsonMessage) => {
      setPttStatus('ready')
      clearResults()
      setTranscription({
        text:             (msg['text']               as string)  ?? '',
        language:         (msg['language']           as string)  ?? '',
        provider:         (msg['provider']           as string)  ?? '',
        confidence:       (msg['confidence']         as number | null) ?? null,
        audioDurationMs:  (msg['audio_duration_ms']  as number)  ?? 0,
        audioConversionMs:(msg['audio_conversion_ms'] as number) ?? 0,
        sttLatencyMs:     (msg['stt_latency_ms']     as number)  ?? 0,
        totalLatencyMs:   (msg['total_latency_ms']   as number)  ?? 0,
      })
    })

    // ── M5: successful translation ───────────────────────────────────────
    client.off('translation')
    client.on('translation', (msg: JsonMessage) => {
      setPttStatus('ready')
      clearResults()
      setTranslation({
        sourceLanguage:       (msg['source_language']       as string)  ?? '',
        targetLanguage:       (msg['target_language']       as string)  ?? '',
        sourceText:           (msg['source_text']           as string)  ?? '',
        translatedText:       (msg['translated_text']       as string)  ?? '',
        provider:             (msg['provider']              as string)  ?? '',
        confidence:           (msg['confidence']            as number | null) ?? null,
        audioDurationMs:      (msg['audio_duration_ms']     as number)  ?? 0,
        audioConversionMs:    (msg['audio_conversion_ms']   as number)  ?? 0,
        sttLatencyMs:         (msg['stt_latency_ms']        as number)  ?? 0,
        translationLatencyMs: (msg['translation_latency_ms'] as number) ?? 0,
        totalLatencyMs:       (msg['total_latency_ms']      as number)  ?? 0,
      })
    })

    // ── M5: translation failed (source_text preserved for fallback) ──────
    client.off('translation_error')
    client.on('translation_error', (msg: JsonMessage) => {
      // STT succeeded — only translation failed.
      // Use 'ready' so the label does NOT show 'STT unavailable'.
      // The translation_error panel below shows the actual failure message.
      setPttStatus('ready')
      clearResults()
      setTranslationError({
        code:           (msg['code']            as string) ?? 'unknown',
        message:        (msg['message']         as string) ?? 'Translation failed.',
        sourceText:     (msg['source_text']     as string) ?? '',
        sourceLanguage: (msg['source_language'] as string) ?? '',
        targetLanguage: (msg['target_language'] as string) ?? '',
      })
    })

    // ── M4: non-fatal statuses ───────────────────────────────────────────
    client.off('transcription_status')
    client.on('transcription_status', (msg: JsonMessage) => {
      const s = msg['status'] as string
      if (s === 'no_speech') {
        setPttStatus('no_speech')
        setStatusMsg('No speech detected.')
      } else if (s === 'empty_audio') {
        setPttStatus('audio_error')
        setStatusMsg('Audio too short.')
      } else {
        setPttStatus('idle')
      }
    })

    // ── M4: STT errors ───────────────────────────────────────────────────
    client.off('transcription_error')
    client.on('transcription_error', (msg: JsonMessage) => {
      setPttStatus('stt_error')
      setStatusMsg(
        `STT error [${msg['code'] as string}]: ${msg['message'] as string}`
      )
    })

    // ── M3: audio processing ack ─────────────────────────────────────────
    client.off('audio_processed')
    client.on('audio_processed', (_msg: JsonMessage) => {
      setPttStatus('transcribing')
    })

    // ── M3: audio error ──────────────────────────────────────────────────
    client.off('audio_error')
    client.on('audio_error', (msg: JsonMessage) => {
      setPttStatus('audio_error')
      setStatusMsg(`Audio error [${msg['code'] as string}]: ${msg['message'] as string}`)
    })

    client.onClose(() => {
      setStatus('idle')
      setPeers(0)
    })

    try {
      await client.connect(id)
    } catch {
      setStatus('error')
      setErrorMsg('Could not connect. Is the backend running on port 8000?')
    }
  }

  // -------------------------------------------------------------------------
  // Disconnect
  // -------------------------------------------------------------------------

  const disconnect = () => {
    clientRef.current.disconnect()
    setStatus('idle')
    setPeers(0)
    clearResults()
    setPttStatus('idle')
  }

  // -------------------------------------------------------------------------
  // Audio chunk handler
  // -------------------------------------------------------------------------

  const handleAudioChunk = useCallback(async (chunk: AudioChunk) => {
    const client = clientRef.current
    if (!client.isConnected) {
      console.warn('[App] Audio captured but not connected — discarding')
      return
    }

    setPttStatus('processing')
    clearResults()

    const buffer = await chunk.blob.arrayBuffer()
    sendTimeRef.current = Date.now()
    client.sendBinary(buffer)

    console.debug(
      `[App] Audio sent — ${(buffer.byteLength / 1_024).toFixed(1)} KB, ` +
      `${chunk.durationMs} ms, ${chunk.mimeType}`,
    )
  }, [])

  // -------------------------------------------------------------------------
  // PTT status label
  // -------------------------------------------------------------------------

  const pttStatusLabel = (): string => {
    switch (pttStatus) {
      case 'processing':   return 'Processing...'
      case 'transcribing': return 'Transcribing...'
      case 'ready':        return langConfig.sourceLanguage === langConfig.targetLanguage
                             ? 'Transcription ready'
                             : 'Translation ready'
      case 'no_speech':    return 'No speech detected'
      case 'stt_error':    return 'STT unavailable'
      case 'audio_error':  return 'Audio error'
      default:             return ''
    }
  }

  // -------------------------------------------------------------------------
  // Language selector helpers
  // -------------------------------------------------------------------------

  const setSourceLang = (code: string) =>
    setLangConfig((prev) => ({ ...prev, sourceLanguage: code }))

  const setTargetLang = (code: string) =>
    setLangConfig((prev) => ({ ...prev, targetLanguage: code }))

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const isSameLang = langConfig.sourceLanguage === langConfig.targetLanguage

  return (
    <div className="app">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="app-header">
        <span className="logo" aria-hidden="true">🎮</span>
        <h1>GameVoice</h1>
        <span className={`status-badge status-${status}`}>
          {status === 'idle'       && '⬤ OFFLINE'}
          {status === 'connecting' && '⬤ CONNECTING'}
          {status === 'connected'  && '⬤ ONLINE'}
          {status === 'error'      && '⬤ ERROR'}
        </span>
      </header>

      {/* ── Main ───────────────────────────────────────────────────────── */}
      <main className="app-main">
        {status !== 'connected' ? (

          // ── Join panel ──────────────────────────────────────────────────
          <div className="panel">
            <label className="field-label" htmlFor="room-input">ROOM CODE</label>
            <input
              id="room-input"
              className="room-input"
              value={roomId}
              onChange={(e) => setRoomId(e.target.value.toUpperCase())}
              placeholder="GAME123"
              maxLength={12}
              disabled={status === 'connecting'}
              aria-label="Room code"
              autoComplete="off"
              spellCheck={false}
            />

            {/* ── Language selector (M5) ──────────────────────────────── */}
            <div className="lang-selector-row">
              <div className="lang-selector-item">
                <label className="field-label" htmlFor="src-lang">MY LANGUAGE</label>
                <select
                  id="src-lang"
                  className="lang-select"
                  value={langConfig.sourceLanguage}
                  onChange={(e) => setSourceLang(e.target.value)}
                  disabled={status === 'connecting'}
                >
                  {SUPPORTED_LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.nativeName} ({l.name})
                    </option>
                  ))}
                </select>
              </div>
              <span className="lang-arrow" aria-hidden="true">→</span>
              <div className="lang-selector-item">
                <label className="field-label" htmlFor="tgt-lang">TEAMMATE LANGUAGE</label>
                <select
                  id="tgt-lang"
                  className="lang-select"
                  value={langConfig.targetLanguage}
                  onChange={(e) => setTargetLang(e.target.value)}
                  disabled={status === 'connecting'}
                >
                  {SUPPORTED_LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.nativeName} ({l.name})
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {isSameLang && (
              <p className="lang-same-note">
                Same language selected — transcription only, no translation.
              </p>
            )}

            <button
              className="btn btn-primary"
              onClick={connect}
              disabled={status === 'connecting' || !roomId.trim()}
            >
              {status === 'connecting' ? 'CONNECTING...' : 'JOIN ROOM'}
            </button>
            {errorMsg && <p className="error-msg" role="alert">{errorMsg}</p>}
          </div>

        ) : (

          // ── Connected panel ─────────────────────────────────────────────
          <div className="panel">

            {/* Room + language info */}
            <div className="room-info">
              <div className="info-item">
                <span className="info-label">ROOM</span>
                <span className="info-value accent">{roomId}</span>
              </div>
              <div className="info-item">
                <span className="info-label">PLAYERS</span>
                <span className="info-value accent">{peers}</span>
              </div>
              <div className="info-item">
                <span className="info-label">LANG</span>
                <span className="info-value accent">
                  {langConfig.sourceLanguage.toUpperCase()}
                  {!isSameLang && ` → ${langConfig.targetLanguage.toUpperCase()}`}
                </span>
              </div>
            </div>

            {/* Push-to-talk */}
            <PushToTalk onChunk={handleAudioChunk} />

            {/* PTT status */}
            {pttStatus !== 'idle' && pttStatus !== 'ready' && (
              <p
                className={`ptt-status-label ${
                  pttStatus === 'audio_error' || pttStatus === 'stt_error'
                    ? 'ptt-status-error'
                    : ''
                }`}
                role="status"
              >
                {statusMsg ?? pttStatusLabel()}
              </p>
            )}

            {/* ── M4: transcription (same-language or no translation) ──── */}
            {transcription && (
              <div className="transcription-panel" aria-live="polite">
                <div className="transcription-header">
                  <span className="transcription-label">TRANSCRIPTION</span>
                  <span className="transcription-lang">
                    [{transcription.language.toUpperCase()}]
                  </span>
                </div>
                <blockquote className="transcription-text">
                  &ldquo;{transcription.text}&rdquo;
                </blockquote>
                <div className="ptt-debug">
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">CONVERT</span>
                    <span className="ptt-debug-value">{transcription.audioConversionMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">STT</span>
                    <span className="ptt-debug-value">{transcription.sttLatencyMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">TOTAL</span>
                    <span className="ptt-debug-value">{transcription.totalLatencyMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span
                      className={`ptt-debug-label ${transcription.provider === 'mock' ? 'stt-provider-mock' : 'stt-provider-real'}`}
                      title={transcription.provider === 'mock' ? 'Mock STT — not real speech recognition' : `Real STT: ${transcription.provider}`}
                    >
                      {transcription.provider === 'mock' ? '⚠ MOCK STT' : `✓ ${transcription.provider.toUpperCase()} STT`}
                    </span>
                  </span>
                </div>
              </div>
            )}

            {/* ── M5: translation panel ────────────────────────────────── */}
            {translation && (
              <div className="translation-panel" aria-live="polite">
                <div className="transcription-header">
                  <span className="transcription-label">SOURCE</span>
                  <span className="transcription-lang">
                    [{translation.sourceLanguage.toUpperCase()}]
                  </span>
                </div>
                <blockquote className="transcription-text source-text">
                  &ldquo;{translation.sourceText}&rdquo;
                </blockquote>

                <div className="translation-divider" aria-hidden="true">
                  ↓ translated to [{translation.targetLanguage.toUpperCase()}]
                </div>

                <blockquote className="translation-text">
                  &ldquo;{translation.translatedText}&rdquo;
                </blockquote>

                <div className="ptt-debug">
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">CONVERT</span>
                    <span className="ptt-debug-value">{translation.audioConversionMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">STT</span>
                    <span className="ptt-debug-value">{translation.sttLatencyMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">TRANS</span>
                    <span className="ptt-debug-value">{translation.translationLatencyMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">TOTAL</span>
                    <span className="ptt-debug-value">{translation.totalLatencyMs} ms</span>
                  </span>
                  <span className="ptt-debug-item">
                    <span className="ptt-debug-label">PROVIDER</span>
                    <span className="ptt-debug-value">{translation.provider}</span>
                  </span>
                </div>
              </div>
            )}

            {/* ── M5: translation error (shows source text as fallback) ── */}
            {translationError && (
              <div className="translation-error-panel" aria-live="polite">
                <div className="transcription-header">
                  <span className="transcription-label">TRANSCRIPTION</span>
                  <span className="transcription-lang">
                    [{translationError.sourceLanguage.toUpperCase()}]
                  </span>
                </div>
                <blockquote className="transcription-text">
                  &ldquo;{translationError.sourceText}&rdquo;
                </blockquote>
                <p className="translation-error-note" role="alert">
                  ⚠ Translation unavailable [{translationError.code}] —
                  showing original text
                </p>
              </div>
            )}

            {/* Milestone note */}
            <div className="milestone-note">
              <span className="check">+</span> M1 — WebSocket handshake<br />
              <span className="check">+</span> M2 — Audio capture + PTT<br />
              <span className="check">+</span> M3 — PyAV pipeline (16kHz mono s16)<br />
              <span className="check">+</span> M4 — Speech-to-text (mock STT)<br />
              <span className="check">+</span> M5 — Text translation<br />
              Next: TTS (M6)
            </div>

            <button className="btn btn-secondary" onClick={disconnect}>
              LEAVE ROOM
            </button>
          </div>
        )}
      </main>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <footer className="app-footer">
        <span>GameVoice v0.1-M5</span>
        <span>ws://localhost:8000</span>
      </footer>

    </div>
  )
}
