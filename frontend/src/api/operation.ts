import type { OperationState, OperationTargetSelection, OperationUpdate } from '../types/operation'

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

export function fetchOperationState(): Promise<OperationState> {
  return request<OperationState>('/api/operation/state')
}

export function updateOperationState(update: OperationUpdate): Promise<OperationState> {
  return request<OperationState>('/api/operation/state', {
    method: 'PUT',
    body: JSON.stringify(update),
  })
}

export function selectOperationTarget(selection: OperationTargetSelection): Promise<OperationState> {
  return request<OperationState>('/api/operation/target', {
    method: 'POST',
    body: JSON.stringify(selection),
  })
}

export function clearOperationTarget(): Promise<OperationState> {
  return request<OperationState>('/api/operation/target/clear', { method: 'POST' })
}

export function startStage2Mission(): Promise<OperationState> {
  return request<OperationState>('/api/operation/stage2/start', { method: 'POST' })
}

export function stopStage2Mission(): Promise<OperationState> {
  return request<OperationState>('/api/operation/stage2/stop', { method: 'POST' })
}

export function startStage3Round(roundNumber?: number): Promise<any> {
  const query = roundNumber ? `?round_number=${roundNumber}` : ''
  return request<any>(`/api/operation/stage3/start-round${query}`, { method: 'POST' })
}

export function nextStage3Round(): Promise<any> {
  return request<any>('/api/operation/stage3/next-round', { method: 'POST' })
}

export function resetStage3Rounds(): Promise<any> {
  return request<any>('/api/operation/stage3/reset-rounds', { method: 'POST' })
}

export function fetchPatrolStatus(): Promise<any> {
  return request<any>('/api/operation/patrol/status')
}

export function capturePatrolAngle(pathNumber: number): Promise<any> {
  return request<any>(`/api/operation/patrol/capture-angle?path_number=${pathNumber}`, { method: 'POST' })
}

export function gotoPatrolPath(pathNumber: number): Promise<any> {
  return request<any>(`/api/operation/patrol/goto-path?path_number=${pathNumber}`, { method: 'POST' })
}

export function updatePatrolAngles(config: {
  path1_deg?: number
  path2_deg?: number
  path3_deg?: number
  dwell_time_s?: number
  strategy?: string
  sweep_speed_dps?: number
  sweep_sector_deg?: number
}): Promise<any> {
  return request<any>('/api/operation/patrol/angles', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

export function setPatrolAxisLock(axis: string = 'tilt', locked: boolean = true): Promise<any> {
  return request<any>('/api/operation/patrol/axis-lock', {
    method: 'POST',
    body: JSON.stringify({ axis, locked }),
  })
}

