import type { SelfTestRun, SelfTestStatus } from '../types/selfTest'

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

export function fetchSelfTestStatus(): Promise<SelfTestStatus> {
  return request<SelfTestStatus>('/api/self-test/status')
}

export function runSelfTest(): Promise<SelfTestRun> {
  return request<SelfTestRun>('/api/self-test/run', { method: 'POST', body: JSON.stringify({}) })
}

export function cancelSelfTest(): Promise<{ accepted: boolean; reason: string; run: SelfTestRun | null }> {
  return request<{ accepted: boolean; reason: string; run: SelfTestRun | null }>('/api/self-test/cancel', { method: 'POST' })
}

export function fetchSelfTestRuns(): Promise<SelfTestRun[]> {
  return request<SelfTestRun[]>('/api/self-test/runs')
}

export function selfTestReportUrl(runId: string): string {
  return `${apiBaseUrl()}/api/self-test/runs/${runId}/report`
}
