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

export interface SafeConfigResponse<T> {
  ok: boolean
  applied: boolean
  config: T
  visualization_only: boolean
  physical_command_enabled: false
  serial_tx_enabled: false
  no_physical_command_generated: true
}

export interface PerceptionConfig {
  confidence_threshold: number
  yolo_enabled: boolean
}

export interface CameraImageConfig {
  brightness: number
  contrast: number
  saturation: number
  exposure_auto: boolean
  exposure: number
  preview_filter_only: boolean
}

export interface MotionConfig {
  motion_mode: string
  yaw_max_speed: number
  pitch_max_speed: number
  acceleration_limit: number
  deadzone: number
  smoothing: number
  yaw_kp: number
  yaw_ki: number
  yaw_kd: number
  pitch_kp: number
  pitch_ki: number
  pitch_kd: number
}

export const fetchPerceptionConfig = () => request<SafeConfigResponse<PerceptionConfig>>('/api/perception/config')
export const applyPerceptionConfig = (config: PerceptionConfig) => request<SafeConfigResponse<PerceptionConfig>>('/api/perception/config', { method: 'POST', body: JSON.stringify(config) })

export const fetchCameraImageConfig = () => request<SafeConfigResponse<CameraImageConfig>>('/api/camera/config')
export const applyCameraImageConfig = (config: CameraImageConfig) => request<SafeConfigResponse<CameraImageConfig>>('/api/camera/config', { method: 'POST', body: JSON.stringify(config) })

export const fetchMotionConfig = () => request<SafeConfigResponse<MotionConfig>>('/api/motion/config')
export const applyMotionConfig = (config: MotionConfig) => request<SafeConfigResponse<MotionConfig>>('/api/motion/config', { method: 'POST', body: JSON.stringify(config) })
