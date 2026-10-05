import type {
  MotionCommandResponse,
  MotionGoToRequest,
  MotionJogRequest,
  MotionSettings,
  MotionState,
  MotionTrackDryRunRequest,
} from '../types/motion'

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

export function fetchMotionStatus(): Promise<MotionState> {
  return request<MotionState>('/api/motion/status')
}

export function fetchMotionSettings(): Promise<MotionSettings> {
  return request<MotionSettings>('/api/motion/settings')
}

export function updateMotionSettings(settings: MotionSettings): Promise<MotionSettings> {
  return request<MotionSettings>('/api/motion/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  })
}

export function jogMotion(requestBody: MotionJogRequest): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/jog', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function goToMotion(requestBody: MotionGoToRequest): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/go-to', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function homeMotion(): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/home', { method: 'POST' })
}

export function stopMotion(): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/stop', { method: 'POST' })
}

export function startScan(): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/scan/start', { method: 'POST' })
}

export function stopScan(): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/scan/stop', { method: 'POST' })
}

export function trackDryRun(requestBody: MotionTrackDryRunRequest): Promise<MotionCommandResponse> {
  return request<MotionCommandResponse>('/api/motion/track-dry-run', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}
