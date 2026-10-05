export type CommandProfile = 'DRY_RUN' | 'LIVE_TEST' | 'VIDEO_DEMO' | 'COMPETITION'

export interface PreflightGate {
  code: string
  ready: boolean
  detail: string
}

export interface GatewayPreflightResult {
  profile: CommandProfile
  physical_motion_enabled: boolean
  physical_fire_enabled: boolean
  ready: boolean
  reason_codes: string[]
  gates: PreflightGate[]
  pico_protocol: string | null
  actuator_armed: boolean
}

export interface AngularSafetyZone {
  name: string
  pan_min_deg: number
  pan_max_deg: number
  tilt_min_deg: number
  tilt_max_deg: number
  enabled: boolean
}

export interface SafetyZoneProfile {
  motion_zones: AngularSafetyZone[]
  fire_zones: AngularSafetyZone[]
  profile_hash: string
  source: string
  updated_at: number
}

export interface SafetyZoneProfileUpdate {
  motion_zones: AngularSafetyZone[]
  fire_zones: AngularSafetyZone[]
}

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
  if (!response.ok) throw new Error(body?.detail ?? JSON.stringify(body))
  return body as T
}

export function fetchCommandProfile(): Promise<GatewayPreflightResult> {
  return request('/api/safety/command-profile')
}

export function selectCommandProfile(profile: CommandProfile, actuatorArm: boolean): Promise<GatewayPreflightResult> {
  return request('/api/safety/command-profile', {
    method: 'POST', body: JSON.stringify({ profile, actuator_arm: actuatorArm }),
  })
}

export function runCommandPreflight(actuatorArm: boolean): Promise<GatewayPreflightResult> {
  return request('/api/safety/preflight', {
    method: 'POST', body: JSON.stringify({ actuator_arm: actuatorArm }),
  })
}

export function connectGatewayPico(port: string, baudrate: number): Promise<{ connected: boolean; reason_code: string; preflight: GatewayPreflightResult }> {
  return request('/api/safety/pico-connect', {
    method: 'POST', body: JSON.stringify({ port, baudrate }),
  })
}

export function fetchSafetyZoneProfile(): Promise<SafetyZoneProfile> {
  return request('/api/safety-zones/profile')
}

export function replaceSafetyZoneProfile(profile: SafetyZoneProfileUpdate): Promise<SafetyZoneProfile> {
  return request('/api/safety-zones/profile', {
    method: 'PUT', body: JSON.stringify(profile),
  })
}
