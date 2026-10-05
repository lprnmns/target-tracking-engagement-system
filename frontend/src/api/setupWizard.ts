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

export function resetSetupSession(): Promise<{ reset: boolean }> {
  return request<{ reset: boolean }>('/api/setup/reset-session', { method: 'POST' })
}

export interface SetupSafetyResponse {
  visualization_only: boolean
  physical_command_enabled: false
  serial_tx_enabled: false
  no_physical_command_generated: true
  /** Legacy setup endpoint; operational UI must use CommandGateway APIs. */
  deprecated?: boolean
  replacement?: string
}

export interface SetupCamera {
  id: string
  name: string
  device_path: string
  stable_path: string
  resolutions: string[]
  fps_options: number[]
  pixel_formats?: string[]
  last_status: string
  busy_by?: string[]
  supports_controls: Record<string, string>
}

export interface SetupSerialDevice {
  port: string
  name: string
  description: string
  hwid: string
  baudrate: number
  last_status: string
  platform_hint: string
}

export interface CameraSettings {
  brightness: number
  contrast: number
  saturation: number
  exposure: number
  auto_exposure: boolean
}

export interface ModelConfig {
  air_target_model_path: string
  balloon_model_path: string
  person_safety_model_path: string
  air_target_confidence: number
  balloon_confidence: number
  person_safety_confidence: number
}

export interface SetupProfile {
  profile_name: string
  operation_mode: string
  selected_camera_id: string
  selected_camera_path: string
  camera_resolution: string
  camera_fps: number
  camera_settings: CameraSettings
  selected_pico_port: string
  baudrate: number
  models: ModelConfig
  motion: Record<string, unknown>
  safety: {
    physical_command_enabled: false
    serial_tx_enabled: false
    no_physical_command_generated: true
  }
}

export interface StageResult {
  command_generated?: boolean
  serial_written?: boolean
  reached_pico?: boolean
  ack_received?: boolean
  telemetry_received?: boolean
  pico_ack?: boolean
  driver_status_received?: boolean
  digital_twin_preview_updated?: boolean
  actuator_command_accepted?: boolean
}

export const fetchSetupCameras = () => request<{ ok: boolean; cameras: SetupCamera[]; message: string } & SetupSafetyResponse>('/api/setup/cameras')
export const applySetupCameraSettings = (settings: CameraSettings) => request<{ ok: boolean; applied: boolean; settings: CameraSettings; message: string; unsupported_controls: string[] } & SetupSafetyResponse>('/api/setup/camera/apply', { method: 'POST', body: JSON.stringify(settings) })
export const releaseCameraRuntimeCapture = () => request<{ ok: boolean; released: boolean; message: string; no_physical_command_generated: true }>('/api/camera/runtime/release', { method: 'POST' })

export const fetchSetupSerialDevices = () => request<{ ok: boolean; devices: SetupSerialDevice[]; permission_help: string[] } & SetupSafetyResponse>('/api/setup/serial-devices')
export const setupPicoConnect = (port: string, baudrate: number) => request<{ ok: boolean; connected: boolean; port: string; baudrate: number; stages: StageResult; message: string } & SetupSafetyResponse>('/api/setup/pico/connect', { method: 'POST', body: JSON.stringify({ port, baudrate }) })
export const setupPicoHeartbeat = (port: string, baudrate: number) => request<{ ok: boolean; port: string; stages: StageResult; message: string } & SetupSafetyResponse>('/api/setup/pico/heartbeat', { method: 'POST', body: JSON.stringify({ port, baudrate }) })
export const setupPicoAckTest = (port: string, baudrate: number) => request<{ ok: boolean; port: string; stages: StageResult; message: string } & SetupSafetyResponse>('/api/setup/pico/ack-test', { method: 'POST', body: JSON.stringify({ port, baudrate }) })

export const validateSetupModel = (path: string, runTestInference = false) => request<{ ok: boolean; path: string; exists: boolean; loadable: boolean; class_names: string[]; size_mb: number; test_inference: string; message: string } & SetupSafetyResponse>('/api/setup/models/validate', { method: 'POST', body: JSON.stringify({ path, run_test_inference: runTestInference }) })

export const loadSetupConfig = () => request<{ ok: boolean; path: string; profile: SetupProfile } & SetupSafetyResponse>('/api/setup/config/load')
export const saveSetupConfig = (profile: SetupProfile) => request<{ ok: boolean; saved: boolean; path: string; profile: SetupProfile; message: string } & SetupSafetyResponse>('/api/setup/config/save', { method: 'POST', body: JSON.stringify(profile) })

export const runSetupMotorTest = (axis: string, direction: string) => request<{ ok: boolean; mode: string; axis: string; direction: string; stages: StageResult; message: string } & SetupSafetyResponse>('/api/setup/motor/test', { method: 'POST', body: JSON.stringify({ axis, direction }) })
export const runSetupActuatorSafeTest = (checklist: Record<string, boolean>, explicitUnlock: boolean) => request<{ ok: boolean; locked: boolean; missing_checklist: string[]; stages: StageResult; message: string; no_real_firing: true } & SetupSafetyResponse>('/api/setup/actuator/safe-test', { method: 'POST', body: JSON.stringify({ checklist, explicit_unlock: explicitUnlock }) })
