import type { DeviceProfile, DeviceProfileResult, DeviceProfilesList } from '../types/deviceProfile'

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

export const fetchDeviceProfiles = () => request<DeviceProfilesList>('/api/device-profiles')
export const fetchActiveDeviceProfile = () => request<DeviceProfile>('/api/device-profiles/active')
export const saveDeviceProfile = (payload: {
  profile_id?: string | null
  display_name: string
  command_profile: 'DRY_RUN' | 'LIVE_TEST' | 'VIDEO_DEMO' | 'COMPETITION'
  servo_release_deg: number
  servo_fire_deg: number
  servo_pulse_s: number
}) => request<DeviceProfileResult>('/api/device-profiles/save', { method: 'POST', body: JSON.stringify(payload) })
export const saveActiveDeviceProfile = () => saveDeviceProfile({ profile_id: 'default', display_name: 'Varsayılan', command_profile: 'DRY_RUN', servo_release_deg: 35, servo_fire_deg: 175, servo_pulse_s: 1 })
export const applyDeviceProfile = (profileId: string, connectHardware = false) => request<DeviceProfileResult>('/api/device-profiles/apply', { method: 'POST', body: JSON.stringify({ profile_id: profileId, connect_hardware: connectHardware }) })
export const applyActiveDeviceProfile = () => applyDeviceProfile('default')
export const verifyActiveDeviceProfile = () => request<DeviceProfileResult>('/api/device-profiles/verify', { method: 'POST', body: JSON.stringify({ profile_id: 'default' }) })
export const resetDeviceProfile = () => request<DeviceProfileResult>('/api/device-profiles/reset', { method: 'POST' })
