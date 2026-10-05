/**
 * Ballistics & Range Calibration API Client
 */

import type {
  BallisticsLiveState,
  BallisticsProfile,
  Stage3SimulationRequest,
  StationUpdateRequest,
} from '../types/ballistics'

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

export function fetchBallisticsProfile(): Promise<BallisticsProfile> {
  return request<BallisticsProfile>('/api/ballistics/profile')
}

export function updateBallisticsProfile(profile: BallisticsProfile): Promise<BallisticsProfile> {
  return request<BallisticsProfile>('/api/ballistics/profile', {
    method: 'PUT',
    body: JSON.stringify(profile),
  })
}

export function saveStation(station: StationUpdateRequest): Promise<BallisticsProfile> {
  return request<BallisticsProfile>('/api/ballistics/station', {
    method: 'POST',
    body: JSON.stringify(station),
  })
}

export function startStage3Simulation(params?: Stage3SimulationRequest): Promise<BallisticsLiveState> {
  return request<BallisticsLiveState>('/api/ballistics/stage3/start', {
    method: 'POST',
    body: JSON.stringify(params || {}),
  })
}

export function stopStage3Simulation(): Promise<BallisticsLiveState> {
  return request<BallisticsLiveState>('/api/ballistics/stage3/stop', {
    method: 'POST',
  })
}

export function fetchBallisticsLive(balloon_w_px?: number, target_vx?: number): Promise<BallisticsLiveState> {
  const params = new URLSearchParams()
  if (balloon_w_px !== undefined) params.set('balloon_w_px', String(balloon_w_px))
  if (target_vx !== undefined) params.set('target_vx_px_s', String(target_vx))
  const qs = params.toString() ? `?${params.toString()}` : ''
  return request<BallisticsLiveState>(`/api/ballistics/live${qs}`)
}
