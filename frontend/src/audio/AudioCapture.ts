/**
 * AudioCapture — thin wrapper around the browser MediaRecorder API.
 *
 * Responsibilities:
 * - Request microphone access with sensible constraints for voice/STT.
 * - Record audio while PTT is held.
 * - Return a typed AudioChunk on stop.
 * - Release the microphone track immediately after each recording
 *   (privacy: mic indicator goes off as soon as PTT is released).
 *
 * What this class does NOT do:
 * - Noise reduction (added in M9 via WebAudio nodes)
 * - Sending audio to the backend (handled in M3 by the pipeline layer)
 * - Speech recognition (handled in M4 via SpeechProvider)
 */

/** Resolved audio data from one PTT press-and-release cycle. */
export interface AudioChunk {
  /** Raw audio blob in the browser's native format (WebM/Opus or OGG/Opus). */
  blob: Blob
  /** Wall-clock duration of the recording in milliseconds. */
  durationMs: number
  /** MIME type reported by MediaRecorder (e.g. "audio/webm;codecs=opus"). */
  mimeType: string
}

/** Picks the best supported MIME type for voice recording. */
function pickMimeType(): string {
  const candidates = [
    'audio/webm;codecs=opus', // Chrome, Edge
    'audio/webm',             // Chrome fallback
    'audio/ogg;codecs=opus',  // Firefox
    'audio/ogg',              // Firefox fallback
  ]
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime
  }
  return '' // let the browser decide
}

export class AudioCapture {
  private recorder: MediaRecorder | null = null
  private stream: MediaStream | null = null
  private chunks: Blob[] = []
  private startTime = 0

  /**
   * Start a new recording.
   * Requests microphone access on every call so the OS mic indicator
   * is only active while PTT is actually held.
   *
   * @throws If the user denies microphone permission.
   */
  async start(): Promise<void> {
    // Defensive: stop any in-progress recording first
    if (this.recorder?.state === 'recording') {
      this.recorder.stop()
    }
    this.chunks = []

    // Request mic with voice-optimised constraints.
    // echoCancellation and noiseSuppression are browser built-ins —
    // they provide a baseline that M9 will extend with WebAudio processing.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1, // mono — sufficient for voice, halves bandwidth
      },
      video: false,
    })

    const mimeType = pickMimeType()
    this.recorder = new MediaRecorder(
      this.stream,
      mimeType ? { mimeType } : undefined,
    )

    this.recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) this.chunks.push(e.data)
    }

    this.startTime = Date.now()
    this.recorder.start()
  }

  /**
   * Stop recording and return the captured audio.
   * Releases the microphone track immediately.
   *
   * @throws If no recording is in progress.
   */
  stop(): Promise<AudioChunk> {
    return new Promise((resolve, reject) => {
      if (!this.recorder || this.recorder.state !== 'recording') {
        reject(new Error('AudioCapture: no active recording to stop'))
        return
      }

      const durationMs = Date.now() - this.startTime
      const mimeType = this.recorder.mimeType

      this.recorder.onstop = () => {
        const blob = new Blob(this.chunks, { type: mimeType })
        this.chunks = []

        // Release mic track — OS indicator turns off here
        this.stream?.getTracks().forEach((t) => t.stop())
        this.stream = null

        resolve({ blob, durationMs, mimeType })
      }

      this.recorder.stop()
    })
  }

  get isRecording(): boolean {
    return this.recorder?.state === 'recording'
  }
}
