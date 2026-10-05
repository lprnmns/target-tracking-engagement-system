import type { FirstRunActionResult, FirstRunReport, FirstRunStatus } from '../types/firstRun'

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

export function fetchFirstRunStatus(): Promise<FirstRunStatus> {
  return request<FirstRunStatus>('/api/first-run/status')
}

export function runFirstRunCheck(): Promise<FirstRunReport> {
  return request<FirstRunReport>('/api/first-run/check', { method: 'POST' })
}

export function markFirstRunComplete(): Promise<FirstRunActionResult> {
  return request<FirstRunActionResult>('/api/first-run/mark-complete', { method: 'POST' })
}

export function resetFirstRun(): Promise<FirstRunActionResult> {
  return request<FirstRunActionResult>('/api/first-run/reset', { method: 'POST' })
}

export function fetchFirstRunReport(): Promise<FirstRunReport> {
  return request<FirstRunReport>('/api/first-run/report')
}
