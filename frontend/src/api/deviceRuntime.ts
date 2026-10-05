import type {
  CameraCapability,
  CameraRuntimeApplyResult,
  CameraRuntimeProfile,
  CameraRuntimeStatus,
  DeviceInventory,
  ManagedDevice,
  TrackerRuntimeCatalog,
  VisionRuntimeApplyResult,
  VisionRuntimePreset,
  VisionRuntimeTestResult,
  VisionRuntimeVerifyResult,
  VisionRuntimeProfile,
  VisionRuntimeStatus,
} from '../types/deviceRuntime'

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

export const fetchDevices = () => request<DeviceInventory>('/api/devices')
export const refreshDevices = () => request<DeviceInventory>('/api/devices/refresh', { method: 'POST' })
export const fetchCameraDevices = () => request<ManagedDevice[]>('/api/devices/cameras')
export const fetchSerialDevices = () => request<ManagedDevice[]>('/api/devices/serial')
export const fetchPicoCandidates = () => request<ManagedDevice[]>('/api/devices/pico-candidates')
export const probeCamera = (deviceId: string) => request<{ accepted: boolean; capabilities: CameraCapability | null; warnings: string[]; suggested_action: string | null }>(`/api/devices/cameras/${deviceId}/probe`, { method: 'POST' })
export const fetchCameraCapabilities = (deviceId: string) => request<CameraCapability>(`/api/devices/cameras/${deviceId}/capabilities`)
export const fetchCameraRuntimeStatus = () => request<CameraRuntimeStatus>('/api/camera/runtime/status')
export const fetchCameraRuntimeProfile = () => request<CameraRuntimeProfile>('/api/camera/runtime/profile')
export const applyCameraRuntimeProfile = (profile: CameraRuntimeProfile) => request<CameraRuntimeApplyResult>('/api/camera/runtime/apply-profile', { method: 'POST', body: JSON.stringify(profile) })
export const applyCameraRuntimeControls = (controls: Partial<Pick<CameraRuntimeProfile, 'exposure_auto' | 'exposure_value' | 'gain' | 'white_balance_auto' | 'white_balance_value' | 'brightness' | 'contrast' | 'saturation' | 'sharpness'>>) => request<CameraRuntimeStatus>('/api/camera/runtime/controls', { method: 'PATCH', body: JSON.stringify(controls) })
export const resetCameraRuntimeProfile = () => request<CameraRuntimeApplyResult>('/api/camera/runtime/reset-defaults', { method: 'POST' })
export const probeCurrentCameraRuntime = () => request<CameraRuntimeApplyResult>('/api/camera/runtime/probe-current', { method: 'POST' })
export const benchmarkCameraRuntime = () => request<Record<string, unknown>>('/api/camera/runtime/benchmark', { method: 'POST' })
export const snapshotCameraRuntime = () => request<Record<string, unknown>>('/api/camera/runtime/snapshot', { method: 'POST' })
export const fetchVisionRuntimeStatus = () => request<VisionRuntimeStatus>('/api/vision/runtime/status')
export const fetchTrackerRuntimeCatalog = () => request<TrackerRuntimeCatalog>('/api/vision/runtime/trackers')
export const fetchVisionRuntimeSettings = () => request<VisionRuntimeProfile>('/api/vision/runtime/settings')
export const applyVisionRuntimeSettings = (profile: VisionRuntimeProfile) => request<VisionRuntimeApplyResult>('/api/vision/runtime/apply-settings', { method: 'POST', body: JSON.stringify(profile) })
export const resetVisionRuntimeSettings = () => request<VisionRuntimeApplyResult>('/api/vision/runtime/reset-defaults', { method: 'POST' })
export const reloadVisionRuntimeModels = () => request<Record<string, unknown>>('/api/vision/runtime/reload-models', { method: 'POST' })
export const warmupVisionRuntime = () => request<Record<string, unknown>>('/api/vision/runtime/warmup', { method: 'POST' })
export const benchmarkVisionRuntime = () => request<Record<string, unknown>>('/api/vision/runtime/benchmark', { method: 'POST' })
export const fetchVisionRuntimePresets = () => request<VisionRuntimePreset[]>('/api/vision/runtime/presets')
export const applyVisionRuntimePreset = (presetName: string) => request<VisionRuntimeApplyResult>('/api/vision/runtime/apply-preset', { method: 'POST', body: JSON.stringify({ preset_name: presetName }) })
export const verifyActiveVisionRuntime = () => request<VisionRuntimeVerifyResult>('/api/vision/runtime/verify-active', { method: 'POST' })
export const testActiveVisionModel = () => request<VisionRuntimeTestResult>('/api/vision/runtime/test-active-model', { method: 'POST' })
