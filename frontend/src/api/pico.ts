import type {
  PicoConnectionEvent,
  PicoDiscoveryPortsResponse,
  PicoPort,
  PicoPermissionDiagnosis,
  PicoReadOnlyEvidence,
  PicoReadOnlyStatus,
  PicoReadOnlyTelemetry,
  PicoProtocolStatus,
  PicoProtocolTelemetry,
  PicoStatus,
  PinProfile,
  PinValidationResult,
} from '../types/pico'

export class ApiRequestError extends Error {
  endpoint: string
  method: string
  status: number | null
  suggestion: string

  constructor(endpoint: string, method: string, status: number | null, message: string) {
    super(message)
    this.endpoint = endpoint
    this.method = method
    this.status = status
    this.suggestion = status === null
      ? 'Backend URL ve dev server durumunu kontrol et.'
      : status >= 500
        ? 'Backend loglarini kontrol et ve endpoint servis durumunu dogrula.'
        : 'Istek payload ve sistem modunu kontrol et.'
  }
}

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) {
    return configured.replace(/\/$/, '')
  }
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers,
      },
      ...init,
    })
  } catch (error) {
    throw new ApiRequestError(path, method, null, error instanceof Error ? error.message : 'Network request failed')
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiRequestError(path, method, response.status, JSON.stringify(body))
  }
  return body as T
}

export function fetchPicoStatus(): Promise<PicoStatus> {
  return request<PicoStatus>('/api/pico/status')
}

export function fetchPicoPorts(): Promise<PicoPort[]> {
  return request<PicoPort[]>('/api/pico/ports')
}

export function fetchPicoDiscoveryPorts(): Promise<PicoDiscoveryPortsResponse> {
  return request<PicoDiscoveryPortsResponse>('/api/pico/discovery/ports')
}

export function connectPicoReadOnly(port: string, baudrate: number): Promise<PicoReadOnlyStatus> {
  return request<PicoReadOnlyStatus>('/api/pico/read-only/connect', {
    method: 'POST',
    body: JSON.stringify({ port, baudrate, read_only: true }),
  })
}

export function disconnectPicoReadOnly(): Promise<PicoReadOnlyStatus> {
  return request<PicoReadOnlyStatus>('/api/pico/read-only/disconnect', { method: 'POST' })
}

export function fetchPicoReadOnlyStatus(): Promise<PicoReadOnlyStatus> {
  return request<PicoReadOnlyStatus>('/api/pico/read-only/status')
}

export function fetchPicoReadOnlyPermissionStatus(): Promise<PicoPermissionDiagnosis> {
  return request<PicoPermissionDiagnosis>('/api/pico/read-only/permission-status')
}

export function fetchPicoReadOnlyTelemetry(): Promise<PicoReadOnlyTelemetry> {
  return request<PicoReadOnlyTelemetry>('/api/pico/read-only/latest-telemetry')
}

export function capturePicoReadOnlyEvidence(): Promise<PicoReadOnlyEvidence> {
  return request<PicoReadOnlyEvidence>('/api/pico/read-only/capture-evidence', { method: 'POST' })
}

export function fetchPicoReadOnlyEvidence(): Promise<PicoReadOnlyEvidence> {
  return request<PicoReadOnlyEvidence>('/api/pico/read-only/latest-evidence')
}

export function fetchPicoProtocolStatus(): Promise<PicoProtocolStatus> {
  return request<PicoProtocolStatus>('/api/pico/protocol/status')
}

export function fetchPicoProtocolTelemetry(): Promise<PicoProtocolTelemetry> {
  return request<PicoProtocolTelemetry>('/api/pico/protocol/latest-telemetry')
}

export function fetchPicoProtocolContract(): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>('/api/pico/protocol/contract')
}

export function connectPico(port: string, baudrate: number): Promise<PicoConnectionEvent> {
  return request<PicoConnectionEvent>('/api/pico/connect', {
    method: 'POST',
    body: JSON.stringify({ port, baudrate }),
  })
}

export function disconnectPico(): Promise<PicoConnectionEvent> {
  return request<PicoConnectionEvent>('/api/pico/disconnect', { method: 'POST' })
}

export function fetchPicoPins(): Promise<PinProfile> {
  return request<PinProfile>('/api/pico/pins')
}

export function validatePicoPins(profile: PinProfile): Promise<PinValidationResult> {
  return request<PinValidationResult>('/api/pico/pins/validate', {
    method: 'POST',
    body: JSON.stringify(profile),
  })
}

export function savePicoPins(profile: PinProfile): Promise<PinValidationResult> {
  return request<PinValidationResult>('/api/pico/pins', {
    method: 'PUT',
    body: JSON.stringify(profile),
  })
}
