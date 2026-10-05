import type { ReportExportRecord, ReportExportRequest, ReportsStatus } from '../types/reports'

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

export function fetchReportsStatus(): Promise<ReportsStatus> {
  return request<ReportsStatus>('/api/reports/status')
}

export function fetchReportExports(): Promise<ReportExportRecord[]> {
  return request<ReportExportRecord[]>('/api/reports/exports')
}

export function generateKtrSummary(payload: ReportExportRequest): Promise<ReportExportRecord> {
  return request<ReportExportRecord>('/api/reports/generate-ktr-summary', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function generateDemoPack(payload: ReportExportRequest): Promise<ReportExportRecord> {
  return request<ReportExportRecord>('/api/reports/generate-demo-pack', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function generateReadinessPack(payload: ReportExportRequest): Promise<ReportExportRecord> {
  return request<ReportExportRecord>('/api/reports/generate-readiness-pack', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
