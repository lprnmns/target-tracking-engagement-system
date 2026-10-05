import type { MissionSnapshot, MissionUpdate, Stage1RangeScore, Stage1Target, Stage2EngagementStatus, Stage3EngagementStatus, Stage3TargetClass } from '../types/mission'

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

export function fetchMissionStatus(): Promise<MissionSnapshot> {
  return request<MissionSnapshot>('/api/mission/status')
}

export function updateMissionStatus(update: MissionUpdate): Promise<MissionSnapshot> {
  return request<MissionSnapshot>('/api/mission/status', {
    method: 'PUT',
    body: JSON.stringify(update),
  })
}

export function resetMissionStatus(): Promise<MissionSnapshot> {
  return request<MissionSnapshot>('/api/mission/reset', { method: 'POST' })
}

export function configureStage1Plan(order: Stage1Target[]): Promise<MissionSnapshot> {
  return request('/api/mission/stage1/plan', { method: 'PUT', body: JSON.stringify({ order }) })
}

export function lockStage1Plan(): Promise<MissionSnapshot> {
  return request('/api/mission/stage1/start', { method: 'POST' })
}

export function recordStage1Hit(target: Stage1Target, scoreAwarded: Stage1RangeScore): Promise<MissionSnapshot> {
  return request('/api/mission/stage1/hit', { method: 'POST', body: JSON.stringify({ target, score_awarded: scoreAwarded }) })
}

export function recordStage1WrongTarget(target: Stage1Target): Promise<MissionSnapshot> {
  return request('/api/mission/stage1/wrong-target', { method: 'POST', body: JSON.stringify({ target }) })
}

export function sendStage1ManualMotion(payload: { speed_x: number; speed_y: number; duration_ms: number }): Promise<{ accepted: boolean; reason_codes: string[]; detail: string }> {
  return request('/api/mission/manual-motion', { method: 'POST', body: JSON.stringify(payload) })
}

export function completeStage2Round(confirmedHits: number): Promise<MissionSnapshot> {
  return request('/api/mission/stage2/round/complete', { method: 'POST', body: JSON.stringify({ confirmed_hits: confirmedHits }) })
}

export function fetchStage2Engagement(): Promise<Stage2EngagementStatus> {
  return request('/api/mission/stage2/engagement')
}

export function closeStage2Round(): Promise<MissionSnapshot> {
  return request('/api/mission/stage2/round/close', { method: 'POST' })
}

export function fetchStage3Engagement(): Promise<Stage3EngagementStatus> {
  return request('/api/mission/stage3/engagement')
}

export function closeStage3Round(): Promise<MissionSnapshot> {
  return request('/api/mission/stage3/round/close', { method: 'POST' })
}

export function completeStage3Round(payload: { enemy_class: Stage3TargetClass; enemy_hit: boolean; friend_hit: boolean }): Promise<MissionSnapshot> {
  return request('/api/mission/stage3/round/complete', { method: 'POST', body: JSON.stringify(payload) })
}
