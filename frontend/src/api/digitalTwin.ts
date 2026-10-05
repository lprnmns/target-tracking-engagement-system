import type {
  DigitalTwinAssetsResponse,
  DigitalTwinReplayGenerateResult,
  DigitalTwinReplaySummary,
  DigitalTwinState,
} from '../types/digitalTwin'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as T
}

export function fetchDigitalTwinState(): Promise<DigitalTwinState> {
  return request<DigitalTwinState>('/api/digital-twin/state')
}

export function fetchDigitalTwinAssets(): Promise<DigitalTwinAssetsResponse> {
  return request<DigitalTwinAssetsResponse>('/api/digital-twin/assets')
}

export function generateDigitalTwinReplay(): Promise<DigitalTwinReplayGenerateResult> {
  return request<DigitalTwinReplayGenerateResult>('/api/digital-twin/replay/generate', { method: 'POST' })
}

export function fetchLatestDigitalTwinReplay(): Promise<DigitalTwinReplaySummary> {
  return request<DigitalTwinReplaySummary>('/api/digital-twin/replay/latest')
}

export function logDigitalTwinPanelRendered(): Promise<{ accepted: boolean, no_physical_command_generated: boolean }> {
  return request<{ accepted: boolean, no_physical_command_generated: boolean }>('/api/digital-twin/panel-rendered', { method: 'POST' })
}
