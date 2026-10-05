import type { DigitalTwinReplaySummary } from '../types/digitalTwin'
import type { EngagementEvidenceRecordList, EngagementEvidenceStatus } from '../types/engagementEvidence'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

export async function fetchEngagementEvidenceStatus(): Promise<EngagementEvidenceStatus> {
  const response = await fetch(`${apiBaseUrl()}/api/engagement-evidence/status`)
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as EngagementEvidenceStatus
}

export async function fetchEngagementEvidenceRecords(): Promise<EngagementEvidenceRecordList> {
  const response = await fetch(`${apiBaseUrl()}/api/engagement-evidence/records`)
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as EngagementEvidenceRecordList
}

export async function fetchEngagementDigitalTwinReplay(engagementId: string): Promise<DigitalTwinReplaySummary> {
  const response = await fetch(`${apiBaseUrl()}/api/engagement-evidence/records/${encodeURIComponent(engagementId)}/digital-twin-replay`)
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as DigitalTwinReplaySummary
}

export function engagementEvidenceMediaUrl(engagementId: string, filename: string): string {
  return `${apiBaseUrl()}/api/engagement-evidence/records/${encodeURIComponent(engagementId)}/media/${encodeURIComponent(filename)}`
}
