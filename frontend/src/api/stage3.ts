import type { Stage3RangeCalibrationStatus } from '../types/stage3'

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

export function fetchStage3RangeStatus(): Promise<Stage3RangeCalibrationStatus> {
  return request('/api/stage3/range/status')
}

export function addStage3RangeObservation(payload: {
  class_name: string
  distance_m: number
  bbox_height_px: number
  capture_id: string
  note?: string
}): Promise<Stage3RangeCalibrationStatus> {
  return request('/api/stage3/range/observations', { method: 'POST', body: JSON.stringify(payload) })
}

export function validateStage3Range(): Promise<Stage3RangeCalibrationStatus> {
  return request('/api/stage3/range/validate', { method: 'POST' })
}

export function resetStage3Range(): Promise<Stage3RangeCalibrationStatus> {
  return request('/api/stage3/range/reset', { method: 'POST' })
}

export function removeStage3RangeObservation(observationId: string): Promise<Stage3RangeCalibrationStatus> {
  return request(`/api/stage3/range/observations/${encodeURIComponent(observationId)}`, { method: 'DELETE' })
}
