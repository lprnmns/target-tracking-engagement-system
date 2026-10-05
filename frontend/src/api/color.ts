import type {
  ColorClassifierConfig,
  ColorClassifySampleRequest,
  ColorDecisionResult,
  ColorCalibrationStatus,
  MaskPreviewResult,
} from '../types/color'

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

export function fetchColorConfig(): Promise<ColorClassifierConfig> {
  return request<ColorClassifierConfig>('/api/color/config')
}

export function updateColorConfig(config: ColorClassifierConfig): Promise<ColorClassifierConfig> {
  return request<ColorClassifierConfig>('/api/color/config', {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export function classifyColorSample(requestBody: ColorClassifySampleRequest): Promise<ColorDecisionResult> {
  return request<ColorDecisionResult>('/api/color/classify-sample', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function fetchLatestColorDecision(): Promise<ColorDecisionResult | null> {
  return request<ColorDecisionResult | null>('/api/color/latest')
}

export function resetColor(): Promise<{ reset: boolean }> {
  return request<{ reset: boolean }>('/api/color/reset', { method: 'POST' })
}

export function fetchColorCalibration(): Promise<ColorCalibrationStatus> {
  return request('/api/color/calibration')
}

export function addColorCalibrationReference(payload: { expected_team: 'enemy' | 'friend'; capture_id: string }): Promise<ColorCalibrationStatus> {
  return request('/api/color/calibration/references', { method: 'POST', body: JSON.stringify(payload) })
}

export function resetColorCalibration(): Promise<ColorCalibrationStatus> {
  return request('/api/color/calibration/reset', { method: 'POST' })
}

export function previewColorMask(requestBody: ColorClassifySampleRequest): Promise<MaskPreviewResult> {
  return request<MaskPreviewResult>('/api/color/preview-mask', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}
