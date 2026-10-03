/**
 * WebSocketClient — thin wrapper around the browser WebSocket API.
 *
 * Design principles for GameVoice Translator:
 * - Event-driven: register handlers by message type, not by raw onmessage.
 * - Text messages are JSON objects with a required `type` field.
 * - Binary messages are audio data sent via sendBinary() (M3+).
 * - Callers never touch the raw WebSocket — they only call connect/send/disconnect.
 *
 * M1: basic connect/send/disconnect + typed message routing.
 * M3: adds sendBinary() for WebM/Opus audio transmission.
 */

export type JsonMessage = Record<string, unknown>
type MessageHandler = (msg: JsonMessage) => void
type CloseHandler = (event: CloseEvent) => void
type ErrorHandler = (event: Event) => void

export class WebSocketClient {
  private ws: WebSocket | null = null
  private readonly handlers = new Map<string, MessageHandler[]>()
  private readonly closeHandlers: CloseHandler[] = []
  private readonly errorHandlers: ErrorHandler[] = []

  /**
   * Connect to a room on the backend.
   *
   * @param roomId   The shared room code (e.g. "GAME123"). Will be uppercased.
   * @param baseUrl  WebSocket base URL. Defaults to the local backend.
   * @returns        Resolves when the TCP connection is open (before server
   *                 sends the "connected" confirmation message).
   */
  connect(
    roomId: string,
    baseUrl = 'ws://localhost:8000/ws',
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `${baseUrl}/${encodeURIComponent(roomId.toUpperCase())}`

      this.ws = new WebSocket(url)

      this.ws.onopen = () => resolve()

      this.ws.onerror = (event) => {
        this.errorHandlers.forEach((h) => h(event))
        reject(new Error(`WebSocket connection failed: ${url}`))
      }

      this.ws.onmessage = (event: MessageEvent) => {
        // Backend always responds with JSON text (never binary back to client).
        if (typeof event.data === 'string') {
          try {
            const msg = JSON.parse(event.data) as JsonMessage
            const type = (msg['type'] as string | undefined) ?? 'unknown'
            ;(this.handlers.get(type) ?? []).forEach((h) => h(msg))
          } catch {
            console.warn('[WebSocketClient] Failed to parse message:', event.data)
          }
        }
      }

      this.ws.onclose = (event) => {
        this.closeHandlers.forEach((h) => h(event))
        this.ws = null
      }
    })
  }

  /**
   * Register a handler for a specific message type.
   * Multiple handlers for the same type are all called, in registration order.
   */
  on(type: string, handler: MessageHandler): void {
    if (!this.handlers.has(type)) this.handlers.set(type, [])
    this.handlers.get(type)!.push(handler)
  }

  /** Remove all handlers for a message type. */
  off(type: string): void {
    this.handlers.delete(type)
  }

  /** Called when the connection closes (clean or unclean). */
  onClose(handler: CloseHandler): void {
    this.closeHandlers.push(handler)
  }

  /** Called when a connection error occurs. */
  onError(handler: ErrorHandler): void {
    this.errorHandlers.push(handler)
  }

  /** Send a JSON control message. Silently dropped if socket is not open. */
  send(msg: JsonMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    } else {
      console.warn('[WebSocketClient] send() called but socket is not open')
    }
  }

  /**
   * Send raw binary audio data (WebM/Opus ArrayBuffer) to the backend.
   *
   * The backend recognises binary frames as audio and routes them through
   * the PyAV conversion pipeline.  The response arrives as a JSON
   * "audio_processed" or "audio_error" text frame.
   *
   * Convert Blob → ArrayBuffer before calling:
   *   const buffer = await chunk.blob.arrayBuffer()
   *   client.sendBinary(buffer)
   *
   * Silently dropped if the socket is not open.
   */
  sendBinary(data: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(data)
    } else {
      console.warn('[WebSocketClient] sendBinary() called but socket is not open')
    }
  }

  /** Close the connection cleanly. */
  disconnect(): void {
    this.ws?.close(1000, 'User disconnected')
    this.ws = null
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}
