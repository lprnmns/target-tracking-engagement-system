import type { WebSocketEnvelope } from '../types/system'

export interface TelemetrySocketOptions {
  url: string
  onOpen: () => void
  onClose: () => void
  onMessage: (event: WebSocketEnvelope) => void
  onError: () => void
}

export class TelemetrySocket {
  private socket: WebSocket | null = null
  private reconnectTimer: number | null = null
  private manuallyClosed = false
  private readonly options: TelemetrySocketOptions

  constructor(options: TelemetrySocketOptions) {
    this.options = options
  }

  connect(): void {
    this.manuallyClosed = false
    this.clearReconnect()

    try {
      this.socket = new WebSocket(this.options.url)
    } catch {
      this.scheduleReconnect()
      return
    }

    this.socket.onopen = () => {
      this.options.onOpen()
    }

    this.socket.onmessage = (message) => {
      try {
        this.options.onMessage(JSON.parse(message.data) as WebSocketEnvelope)
      } catch {
        this.options.onError()
      }
    }

    this.socket.onerror = () => {
      this.options.onError()
    }

    this.socket.onclose = () => {
      this.options.onClose()
      if (!this.manuallyClosed) {
        this.scheduleReconnect()
      }
    }
  }

  disconnect(): void {
    this.manuallyClosed = true
    this.clearReconnect()
    this.socket?.close()
    this.socket = null
  }

  private scheduleReconnect(): void {
    this.clearReconnect()
    this.reconnectTimer = window.setTimeout(() => this.connect(), 2000)
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }
}
