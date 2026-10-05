export interface ClientLogsExportResponse {
  accepted: boolean
  path: string
  count: number
  no_physical_command_generated: boolean
}

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

export function exportClientEvents(events: unknown[]): Promise<ClientLogsExportResponse> {
  return request<ClientLogsExportResponse>('/api/logs/export-client-events', {
    method: 'POST',
    body: JSON.stringify({ events, source: 'logs_ui' }),
  })
}
