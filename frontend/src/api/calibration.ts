import type {
  CalibrationPointCreate,
  CalibrationStatus,
  CameraCalibrationConfig,
  FovEstimateRequest,
  FovEstimateResponse,
  DirectionCalibrationProfile,
  DirectionCalibrationStatus,
  DirectionObservationRequest,
  DirectionObservationResult,
  DirectionSimulationRequest,
  DirectionSimulationResult,
} from '../types/calibration'

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

export function fetchCalibrationStatus(): Promise<CalibrationStatus> {
  return request<CalibrationStatus>('/api/calibration/status')
}

export function fetchCalibrationConfig(): Promise<CameraCalibrationConfig> {
  return request<CameraCalibrationConfig>('/api/calibration/config')
}

export function updateCalibrationConfig(config: CameraCalibrationConfig): Promise<CameraCalibrationConfig> {
  return request<CameraCalibrationConfig>('/api/calibration/config', {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export function addCalibrationPoint(point: CalibrationPointCreate): Promise<CalibrationStatus> {
  return request<CalibrationStatus>('/api/calibration/points', {
    method: 'POST',
    body: JSON.stringify(point),
  })
}

export function deleteCalibrationPoint(pointId: string): Promise<CalibrationStatus> {
  return request<CalibrationStatus>(`/api/calibration/points/${pointId}`, { method: 'DELETE' })
}

export function computeCalibration(): Promise<CalibrationStatus> {
  return request<CalibrationStatus>('/api/calibration/compute', { method: 'POST' })
}

export function resetCalibration(): Promise<CalibrationStatus> {
  return request<CalibrationStatus>('/api/calibration/reset', { method: 'POST' })
}

export function estimateFov(requestBody: FovEstimateRequest): Promise<FovEstimateResponse> {
  return request<FovEstimateResponse>('/api/calibration/fov-estimate', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function fetchDirectionStatus(): Promise<DirectionCalibrationStatus> {
  return request<DirectionCalibrationStatus>('/api/calibration/direction/status')
}

export function simulateDirection(requestBody: DirectionSimulationRequest): Promise<DirectionSimulationResult> {
  return request<DirectionSimulationResult>('/api/calibration/direction/simulate', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function recordDirectionObservation(requestBody: DirectionObservationRequest): Promise<DirectionObservationResult> {
  return request<DirectionObservationResult>('/api/calibration/direction/record-observation', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  })
}

export function saveDirectionProfile(): Promise<DirectionCalibrationProfile> {
  return request<DirectionCalibrationProfile>('/api/calibration/direction/save-profile', { method: 'POST' })
}

export function resetDirectionProfile(): Promise<DirectionCalibrationStatus> {
  return request<DirectionCalibrationStatus>('/api/calibration/direction/reset', { method: 'POST' })
}
