import type { CleanroomVerificationRecord, ReleasePackageRecord, ReleaseStatus } from '../types/release'

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

export const fetchReleaseStatus = () => request<ReleaseStatus>('/api/release/status')
export const fetchReleasePreflight = () => request<ReleaseStatus>('/api/release/preflight')
export const runReleaseCheck = () => request<ReleaseStatus>('/api/release/check', { method: 'POST' })
export const runColdStartCheck = () => request<ReleaseStatus>('/api/release/cold-start-check')
export const fetchLatestReleasePackage = () => request<ReleasePackageRecord | null>('/api/release/package/latest')
export const buildReleasePackage = () => request<ReleasePackageRecord>('/api/release/package/build', { method: 'POST' })
export const fetchLatestCleanroom = () => request<CleanroomVerificationRecord | null>('/api/release/clean-room/latest')
export const runCleanroom = () => request<CleanroomVerificationRecord>('/api/release/clean-room/run', { method: 'POST' })
