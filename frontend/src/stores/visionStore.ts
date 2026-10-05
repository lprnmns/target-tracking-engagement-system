import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  cameraFrameUrl,
  cameraStreamUrl,
  captureRealCameraEvidence,
  diagnoseCameraHost,
  fetchCameraStatus,
  fetchCameraHostStatus,
  fetchLatestRealCameraEvidence,
  fetchLatestVision,
  fetchLegacyPerceptionPresets,
  fetchRealCameraAcceptance,
  fetchRealCameraEvidenceStatus,
  fetchVisionStatus,
  selectRealCamera,
  snapshotVision,
  startVision,
  stopVision,
} from '../api/vision'
import type { CameraHostDiagnostic, CameraStatus, LegacyPerceptionPresetList, RealCameraAcceptance, RealCameraEvidence, RealCameraEvidenceStatus, VisionEvent, VisionStatus } from '../types/vision'

const defaultCameraStatus: CameraStatus = {
  camera_mode: 'mock',
  source: null,
  connected: false,
  running: false,
  stream_enabled: true,
  width: 640,
  height: 360,
  fps: 15,
  last_error: 'backend_disconnected',
  selected_device: null,
  selected_backend: null,
  source_mode: null,
  input_format: null,
  resolution: null,
  last_frame_age_ms: null,
  last_capture_error: 'backend_disconnected',
  is_real_camera_evidence: false,
  is_external_usb_camera: false,
  is_laptop_camera: false,
  hardware_presence_note: 'backend_disconnected',
}

const defaultVisionStatus: VisionStatus = {
  running: false,
  vision_mode: 'mock',
  model_loading_required: false,
  body_model_path: null,
  balloon_model_path: null,
  body_model_loaded: false,
  balloon_model_loaded: false,
  fps: 0,
  camera_fps: null,
  detector_fps: null,
  latest_frame_id: 0,
  latest_latency_ms: 0,
  latest_total_ms: null,
  camera_source_kind: null,
  frame_origin: null,
  detector_kind: null,
  body_count: 0,
  balloon_count: 0,
  model_count: 0,
  model_execution: 'none',
  model_timings_ms: {},
  warnings: ['backend_disconnected'],
  advisory_only: true,
}

const defaultLegacyPresets: LegacyPerceptionPresetList = {
  presets: [],
  source_reports: [],
  forbidden_runtime_tokens_present: false,
  advisory_only: true,
  no_physical_command_generated: true,
}

const defaultRealCameraEvidenceStatus: RealCameraEvidenceStatus = {
  status: 'not_available',
  camera_source: 'not_available',
  camera_device_path: null,
  frame_origin: 'real_camera_not_available',
  detector: 'legacy_opencv_perception_evidence',
  preset_id: null,
  frame_width: null,
  frame_height: null,
  fps_estimate: null,
  detections_count: 0,
  target_center_metadata: {},
  latest_evidence_id: null,
  warnings: ['backend_disconnected'],
  advisory_only: true,
  no_physical_command_generated: true,
  physical_command_enabled: false,
}

const defaultCameraHostDiagnostic: CameraHostDiagnostic = {
  diagnostic_id: 'not_checked',
  created_at: 0,
  platform: 'not_available',
  host_camera_devices_detected: false,
  dev_video_entries: [],
  camera_groups: [],
  recommended_usb_device_path: null,
  selected_camera_device: null,
  selected_camera_name: null,
  camera_kind: 'unknown_camera',
  v4l2_available: false,
  ffmpeg_available: false,
  user_in_video_group: false,
  camera_app_not_seen_note: true,
  real_camera_capture_attempted: false,
  real_camera_frame_captured: false,
  camera_acceptance_status: 'blocked_by_host_os',
  blocker_reason: 'backend_disconnected',
  commands: [],
  suggested_actions: [],
  advisory_only: true,
  physical_command_enabled: false,
  no_physical_command_generated: true,
}

const defaultRealCameraAcceptance: RealCameraAcceptance = {
  status: 'blocked',
  camera_tooling_status: 'blocked_by_host_os',
  frame_captured: false,
  device_path: null,
  width: null,
  height: null,
  fps_estimate: null,
  frame_hash: null,
  frame_path: null,
  capture_method: null,
  selected_camera_device: null,
  selected_camera_name: null,
  camera_kind: 'unknown_camera',
  internal_camera_passed: false,
  external_usb_camera_passed: false,
  blocker_reason: 'backend_disconnected',
  camera_host: defaultCameraHostDiagnostic,
  latest_evidence: {
    evidence_id: 'not_recorded',
    status: 'not_available',
    created_at: 0,
    camera_source: 'not_available',
    camera_device_path: null,
    frame_origin: 'real_camera_not_available',
    detector: 'legacy_opencv_perception_evidence',
    preset_id: null,
    frame_width: null,
    frame_height: null,
    fps_estimate: null,
    detections_count: 0,
    target_center_metadata: {},
    warnings: ['backend_disconnected'],
    advisory_only: true,
    no_physical_command_generated: true,
    physical_command_enabled: false,
  },
  advisory_only: true,
  physical_command_enabled: false,
  no_physical_command_generated: true,
}

export const useVisionStore = defineStore('vision', () => {
  const cameraStatus = ref<CameraStatus>(defaultCameraStatus)
  const visionStatus = ref<VisionStatus>(defaultVisionStatus)
  const latestEvent = ref<VisionEvent | null>(null)
  const legacyPresets = ref<LegacyPerceptionPresetList>(defaultLegacyPresets)
  const realCameraEvidenceStatus = ref<RealCameraEvidenceStatus>(defaultRealCameraEvidenceStatus)
  const latestRealCameraEvidence = ref<RealCameraEvidence | null>(null)
  const cameraHostDiagnostic = ref<CameraHostDiagnostic>(defaultCameraHostDiagnostic)
  const realCameraAcceptance = ref<RealCameraAcceptance>(defaultRealCameraAcceptance)
  const warning = ref<string | null>(null)
  const error = ref<string | null>(null)
  // Bounded 720p/JPEG85 keeps operator screen display latency below 20-30ms
  // without browser TCP socket bloat. Inference/YOLO uses native 1080p in backend RAM.
  const savedQuality = Number(typeof window !== 'undefined' ? sessionStorage.getItem('istiklal_preview_quality_v2') : null)
  const initialQuality = [95, 85, 86, 72].includes(savedQuality) ? (savedQuality === 86 ? 85 : savedQuality) : 85
  const operatorPreview = { width: 1280, height: 720, quality: initialQuality }
  const streamUrl = ref(cameraStreamUrl(operatorPreview))
  const frameUrl = ref(cameraFrameUrl(operatorPreview))
  let liveRefreshBusy = false

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [camera, status, latest] = await Promise.all([
        fetchCameraStatus(),
        fetchVisionStatus(),
        fetchLatestVision(),
      ])
      cameraStatus.value = camera
      visionStatus.value = status
      latestEvent.value = latest
      warning.value = latest.warnings[0] ?? status.warnings[0] ?? null
      await refreshLegacyEvidence()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Vision refresh failed'
    }
  }

  // Lightweight live telemetry refresh for the Vision screen.  The global
  // WebSocket normally supplies these events; this fallback keeps the screen
  // truthful when the socket is reconnecting or a browser is opened directly
  // on /vision without the rest of the shell mounted.
  async function refreshLive(): Promise<void> {
    if (liveRefreshBusy) return
    liveRefreshBusy = true
    try {
      const [status, latest] = await Promise.all([fetchVisionStatus(), fetchLatestVision()])
      visionStatus.value = status
      latestEvent.value = latest
      warning.value = latest.warnings[0] ?? status.warnings[0] ?? warning.value
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Vision live refresh failed'
    } finally {
      liveRefreshBusy = false
    }
  }

  async function refreshLegacyEvidence(): Promise<void> {
    const [presets, realStatus, realLatest, hostStatus, acceptance] = await Promise.all([
      fetchLegacyPerceptionPresets(),
      fetchRealCameraEvidenceStatus(),
      fetchLatestRealCameraEvidence(),
      fetchCameraHostStatus(),
      fetchRealCameraAcceptance(),
    ])
    legacyPresets.value = presets
    realCameraEvidenceStatus.value = realStatus
    latestRealCameraEvidence.value = realLatest
    cameraHostDiagnostic.value = hostStatus
    realCameraAcceptance.value = acceptance
  }

  async function diagnoseHostCamera(): Promise<void> {
    cameraHostDiagnostic.value = await diagnoseCameraHost()
    await refreshLegacyEvidence()
  }

  async function selectUsbCamera(): Promise<void> {
    const device = cameraHostDiagnostic.value.recommended_usb_device_path ?? '/dev/video2'
    await selectRealCamera(device, 'external_usb_camera')
    await refreshLegacyEvidence()
  }

  async function captureRealEvidence(presetId?: string, devicePath?: string): Promise<void> {
    latestRealCameraEvidence.value = await captureRealCameraEvidence(presetId, devicePath)
    await refreshLegacyEvidence()
  }

  async function captureUsbEvidence(presetId?: string): Promise<void> {
    const device = cameraHostDiagnostic.value.recommended_usb_device_path ?? '/dev/video2'
    await selectRealCamera(device, 'external_usb_camera')
    await captureRealEvidence(presetId, device)
  }

  async function start(): Promise<void> {
    visionStatus.value = await startVision()
    await refresh()
  }

  async function stop(): Promise<void> {
    visionStatus.value = await stopVision()
    cameraStatus.value.running = false
  }

  async function snapshot(): Promise<void> {
    await snapshotVision()
  }

  function applyCameraStatus(status: CameraStatus): void {
    cameraStatus.value = status
  }

  function applyVisionStatus(status: VisionStatus): void {
    visionStatus.value = status
    warning.value = status.warnings[0] ?? warning.value
  }

  function applyVisionEvent(event: VisionEvent): void {
    latestEvent.value = event
    warning.value = event.warnings[0] ?? warning.value
  }

  function applyWarning(nextWarning: string): void {
    warning.value = nextWarning
  }

  return {
    cameraStatus,
    visionStatus,
    latestEvent,
    legacyPresets,
    realCameraEvidenceStatus,
    latestRealCameraEvidence,
    cameraHostDiagnostic,
    realCameraAcceptance,
    warning,
    error,
    streamUrl,
    frameUrl,
    refresh,
    refreshLive,
    refreshLegacyEvidence,
    diagnoseHostCamera,
    selectUsbCamera,
    captureRealEvidence,
    captureUsbEvidence,
    start,
    stop,
    snapshot,
    applyCameraStatus,
    applyVisionStatus,
    applyVisionEvent,
    applyWarning,
  }
})
