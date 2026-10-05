import type { CameraHostDiagnostic, CameraStatus, LegacyPerceptionPreset, LegacyPerceptionPresetList, RealCameraAcceptance, RealCameraEvidence, RealCameraEvidenceStatus, RealCameraSelection, VisionConfig, VisionEvent, VisionStatus } from '../types/vision'
import type { CameraRuntimeStatus } from '../types/deviceRuntime'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

export interface CameraPreviewOptions {
  width: number
  height: number
  quality: number
  nonce?: number
}

function cameraPreviewQuery(options?: CameraPreviewOptions): string {
  if (!options) return 'preview=ui'
  const params = new URLSearchParams({
    preview: 'operator',
    width: String(options.width),
    height: String(options.height),
    quality: String(options.quality),
    v: String(options.nonce ?? Date.now()),
  })
  return params.toString()
}

export function cameraStreamUrl(options?: CameraPreviewOptions): string {
  return `${apiBaseUrl()}/api/camera/stream.mjpg?${cameraPreviewQuery(options)}`
}

export function cameraFrameUrl(options?: CameraPreviewOptions): string {
  // Keep native capture/inference untouched but use a bounded browser
  // transport so remote Windows/Tailscale preview bandwidth cannot starve the
  // real camera/YOLO path.
  return `${apiBaseUrl()}/api/camera/frame.jpg?${cameraPreviewQuery(options)}`
}

export function cameraOverlayStreamUrl(): string {
  return `${apiBaseUrl()}/api/camera/stream-overlay.mjpg?preview=ui`
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

export function fetchVisionStatus(): Promise<VisionStatus> {
  return request<VisionStatus>('/api/vision/status')
}

export function fetchCameraStatus(): Promise<CameraStatus> {
  return request<CameraStatus>('/api/camera/status')
}

export function fetchLatestVision(): Promise<VisionEvent> {
  return request<VisionEvent>('/api/vision/latest')
}

export function processBrowserVisionFrame(payload: { image_base64: string; width: number; height: number; device_label: string }): Promise<VisionEvent> {
  return request<VisionEvent>('/api/vision/browser-frame', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function startVision(): Promise<VisionStatus> {
  return request<VisionStatus>('/api/vision/start', { method: 'POST' })
}

export function startCameraPreview(): Promise<CameraRuntimeStatus> {
  return request<CameraRuntimeStatus>('/api/camera/runtime/start-preview', { method: 'POST' })
}

export function stopVision(): Promise<VisionStatus> {
  return request<VisionStatus>('/api/vision/stop', { method: 'POST' })
}

export function snapshotVision(): Promise<Response> {
  return fetch(`${apiBaseUrl()}/api/vision/snapshot`, { method: 'POST' })
}

export function updateVisionConfig(config: VisionConfig): Promise<VisionStatus> {
  return request<VisionStatus>('/api/vision/config', {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export async function uploadVisionModel(file: File): Promise<{ path: string, file_name: string }> {
  const response = await fetch(`${apiBaseUrl()}/api/vision/models/upload`, {
    method: 'POST', headers: { 'X-File-Name': file.name, 'Content-Type': 'application/octet-stream' }, body: file,
  })
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as { path: string, file_name: string }
}

export function fetchLegacyPerceptionPresets(): Promise<LegacyPerceptionPresetList> {
  return request<LegacyPerceptionPresetList>('/api/vision/legacy-presets')
}

export function fetchLegacyPerceptionPreset(presetId: string): Promise<LegacyPerceptionPreset> {
  return request<LegacyPerceptionPreset>(`/api/vision/legacy-presets/${encodeURIComponent(presetId)}`)
}

export function fetchRealCameraEvidenceStatus(): Promise<RealCameraEvidenceStatus> {
  return request<RealCameraEvidenceStatus>('/api/vision/real-camera/status')
}

export function selectRealCamera(devicePath: string, cameraKind: string): Promise<RealCameraSelection> {
  return request<RealCameraSelection>('/api/vision/real-camera/select', {
    method: 'POST',
    body: JSON.stringify({ device_path: devicePath, camera_kind: cameraKind }),
  })
}

export function captureRealCameraEvidence(presetId?: string, devicePath?: string): Promise<RealCameraEvidence> {
  const params = new URLSearchParams()
  if (presetId) params.set('preset_id', presetId)
  if (devicePath) params.set('device_path', devicePath)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return request<RealCameraEvidence>(`/api/vision/real-camera/capture-evidence${suffix}`, { method: 'POST' })
}

export function fetchLatestRealCameraEvidence(): Promise<RealCameraEvidence> {
  return request<RealCameraEvidence>('/api/vision/real-camera/latest')
}

export function fetchRealCameraAcceptance(): Promise<RealCameraAcceptance> {
  return request<RealCameraAcceptance>('/api/vision/real-camera/acceptance')
}

export function fetchCameraHostStatus(): Promise<CameraHostDiagnostic> {
  return request<CameraHostDiagnostic>('/api/vision/camera-host/status')
}

export function diagnoseCameraHost(): Promise<CameraHostDiagnostic> {
  return request<CameraHostDiagnostic>('/api/vision/camera-host/diagnose', { method: 'POST' })
}

export function fetchLatestCameraHostDiagnostic(): Promise<CameraHostDiagnostic> {
  return request<CameraHostDiagnostic>('/api/vision/camera-host/latest')
}
