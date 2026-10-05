import type { SerialCommandResult, SerialLogEntry, SerialStatus } from '../types/serial'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) {
    return configured.replace(/\/$/, '')
  }
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })
  const body = await response.json()
  if (!response.ok) {
    throw new Error(JSON.stringify(body))
  }
  return body as T
}

export function fetchSerialStatus(): Promise<SerialStatus> {
  return request<SerialStatus>('/api/serial/status')
}

export function fetchSerialLogs(): Promise<SerialLogEntry[]> {
  return request<SerialLogEntry[]>('/api/serial/logs')
}

export function sendSerialJson(message: Record<string, unknown>): Promise<SerialCommandResult> {
  return request<SerialCommandResult>('/api/serial/send-json', {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
}

export function simulateSerialRx(message: Record<string, unknown>): Promise<SerialCommandResult> {
  return request<SerialCommandResult>('/api/serial/simulate-rx', {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
}

export function clearSerialLogs(): Promise<SerialCommandResult> {
  return request<SerialCommandResult>('/api/serial/clear-logs', { method: 'POST' })
}

export function resetSerialMagazine(capacity?: number): Promise<SerialCommandResult> {
  return request<SerialCommandResult>('/api/serial/magazine/reset', {
    method: 'POST',
    body: JSON.stringify({ capacity: capacity ?? 8 }),
  })
}
