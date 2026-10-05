import type { DemoReadiness, DemoTimeline, JuryRehearsal } from '../types/demo'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
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
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as T
}

export function fetchDemoTimeline(): Promise<DemoTimeline> {
  return request<DemoTimeline>('/api/demo/timeline')
}

export function runDemoTimeline(): Promise<DemoTimeline> {
  return request<DemoTimeline>('/api/demo/run', { method: 'POST' })
}

export function fetchLatestDemoTimeline(): Promise<DemoTimeline> {
  return request<DemoTimeline>('/api/demo/latest')
}

export function fetchDemoReadiness(): Promise<DemoReadiness> {
  return request<DemoReadiness>('/api/demo/readiness')
}

export function runJuryRehearsal(): Promise<JuryRehearsal> {
  return request<JuryRehearsal>('/api/demo/jury-rehearsal/run', { method: 'POST' })
}

export function fetchLatestJuryRehearsal(): Promise<JuryRehearsal> {
  return request<JuryRehearsal>('/api/demo/jury-rehearsal/latest')
}
