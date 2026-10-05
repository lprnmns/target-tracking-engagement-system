/**
 * Tracking API Client — Kapalı çevrim hedef takip sistemi REST çağrıları.
 */

import type { TargetPriorityStatus, TargetRegistryStatus, TrackingConfigUpdate, TrackingStatus } from '../types/tracking'

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

export function startTracking(): Promise<TrackingStatus> {
  return request<TrackingStatus>('/api/motion/tracking/start', { method: 'POST' })
}

export function stopTracking(): Promise<TrackingStatus> {
  return request<TrackingStatus>('/api/motion/tracking/stop', { method: 'POST' })
}

export function fetchTrackingStatus(): Promise<TrackingStatus> {
  return request<TrackingStatus>('/api/motion/tracking/status')
}

export function fetchTrackingPriority(): Promise<TargetPriorityStatus> {
  return request<TargetPriorityStatus>('/api/motion/tracking/priority')
}

export function fetchTargetRegistry(): Promise<TargetRegistryStatus> {
  return request<TargetRegistryStatus>('/api/motion/tracking/targets')
}

export function updateTrackingConfig(config: TrackingConfigUpdate): Promise<TrackingStatus> {
  return request<TrackingStatus>('/api/motion/tracking/config', {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export function selectTrackingTarget(payload: { x: number, y: number, detection_id?: number, frame_id?: number }): Promise<TrackingStatus> {
  return request<TrackingStatus>('/api/motion/tracking/select-target', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
