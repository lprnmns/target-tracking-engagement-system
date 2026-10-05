import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  applyCameraRuntimeProfile,
  applyCameraRuntimeControls,
  applyVisionRuntimePreset,
  applyVisionRuntimeSettings,
  benchmarkCameraRuntime,
  benchmarkVisionRuntime,
  fetchCameraRuntimeStatus,
  fetchDevices,
  fetchVisionRuntimePresets,
  fetchVisionRuntimeStatus,
  probeCamera,
  probeCurrentCameraRuntime,
  refreshDevices,
  reloadVisionRuntimeModels,
  resetCameraRuntimeProfile,
  resetVisionRuntimeSettings,
  snapshotCameraRuntime,
  testActiveVisionModel,
  verifyActiveVisionRuntime,
  warmupVisionRuntime,
} from '../api/deviceRuntime'
import type {
  CameraRuntimeApplyResult,
  CameraRuntimeProfile,
  CameraRuntimeStatus,
  DeviceInventory,
  VisionRuntimePreset,
  VisionRuntimeProfile,
  VisionRuntimeStatus,
} from '../types/deviceRuntime'

const defaultInventory: DeviceInventory = {
  devices: [],
  cameras: [],
  serial: [],
  pico_candidates: [],
  scanned_at: 0,
  warnings: ['Backend disconnected.'],
  no_physical_command_generated: true,
}

const defaultCameraProfile: CameraRuntimeProfile = {
  source_type: 'mock',
  device_id: null,
  device_path: null,
  stable_path: null,
  width: 640,
  height: 360,
  fps: 15,
  pixel_format: 'auto',
  exposure_auto: true,
  exposure_value: null,
  gain: null,
  focus_auto: null,
  focus_value: null,
  white_balance_auto: null,
  white_balance_value: null,
  brightness: null,
  contrast: null,
  saturation: null,
  sharpness: null,
  flip_horizontal: false,
  flip_vertical: false,
  rotate_deg: 0,
  lens_profile: 'unknown',
  stream_width: 640,
  stream_height: 360,
  inference_width: 640,
  inference_height: 360,
  roi: { enabled: false, x: 0, y: 0, w: 0, h: 0 },
}

const defaultCameraStatus: CameraRuntimeStatus = {
  profile: defaultCameraProfile,
  running: false,
  selected_camera: 'mock',
  requested_width: 640,
  requested_height: 360,
  requested_fps: 15,
  requested_pixel_format: 'auto',
  actual_width: 640,
  actual_height: 360,
  actual_fps: 15,
  actual_fps_measured: 15,
  actual_pixel_format: 'auto',
  backend_api: 'mock',
  warmup_ms: 0,
  dropped_frames: 0,
  last_probe_result: null,
  recommendation_score: 50,
  last_apply_ok: true,
  last_error: null,
  warnings: [],
  selected_device: null,
  selected_backend: 'fallback',
  source_mode: 'MOCK_OR_FIXTURE',
  input_format: 'auto',
  resolution: '640x360',
  last_frame_age_ms: null,
  last_capture_error: null,
  frame_mean_luma: null,
  frame_brightness_state: 'NO_FRAME',
  frame_sequence: 0,
  is_real_camera_evidence: false,
  is_external_usb_camera: false,
  is_laptop_camera: false,
  hardware_presence_note: 'MOCK/SURROGATE — NOT REAL CAMERA EVIDENCE',
  updated_at: 0,
  no_physical_command_generated: true,
}

const defaultVisionProfile: VisionRuntimeProfile = {
  inference_adapter: 'opencv_circle_test',
  active_body_model_id: null,
  active_balloon_model_id: null,
  device: 'auto',
  imgsz: 640,
  conf: 0.25,
  iou: 0.45,
  max_det: 20,
  classes: null,
  agnostic_nms: false,
  half: false,
  parallel_models: true,
  vid_stride: 1,
  stream_buffer: false,
  frame_skip: 0,
  augment: false,
  retina_masks: null,
  tracker_enabled: false,
  tracker_type: 'none',
  body_conf_threshold: 0.35,
  balloon_conf_threshold: 0.35,
  min_box_area_px: 0,
  max_box_area_px: null,
  target_class_map: {},
  friend_enemy_color_mode: 'hsv',
  latency_budget_ms: 120,
  target_fps: 60,
  warmup_on_load: false,
  benchmark_on_apply: false,
  circle_min_radius: 8,
  circle_max_radius: 90,
  circle_blur_kernel: 5,
  circle_threshold: 80,
  circle_edge_param: 80,
  circle_min_area: 80,
  circle_circularity: 0.55,
  circle_target_color_mode: 'any',
  circle_roi_enabled: false,
  circle_smoothing: false,
}

const defaultVisionStatus: VisionRuntimeStatus = {
  profile: defaultVisionProfile,
  active_model_summary: {},
  active_model_details: {},
  selected_adapter: 'opencv_circle_test',
  effective_adapter: 'test_adapter',
  production_yolo_loaded: false,
  test_adapter_active: true,
  model_package_id: null,
  runtime_source: 'test_adapter',
  surrogate_source_kind: null,
  frame_origin: null,
  advisory_only: true,
  reload_required: false,
  adapter_available: true,
  requested_device: 'auto',
  resolved_device: null,
  cuda_available: false,
  device_reason: 'auto_pending_runtime_probe',
  latest_parameter_version: 1,
  current_fps: 0,
  latest_latency_ms: 0,
  warnings: [],
  errors: [],
  updated_at: 0,
  no_physical_command_generated: true,
}

export const useDeviceRuntimeStore = defineStore('deviceRuntime', () => {
  const inventory = ref<DeviceInventory>(defaultInventory)
  const cameraStatus = ref<CameraRuntimeStatus>(defaultCameraStatus)
  const cameraDraft = ref<CameraRuntimeProfile>({ ...defaultCameraProfile, roi: { ...defaultCameraProfile.roi } })
  const visionStatus = ref<VisionRuntimeStatus>(defaultVisionStatus)
  const presets = ref<VisionRuntimePreset[]>([])
  const visionDraft = ref<VisionRuntimeProfile>({ ...defaultVisionProfile })
  const lastCameraResult = ref<CameraRuntimeApplyResult | Record<string, unknown> | null>(null)
  const lastVisionResult = ref<unknown | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [devices, cameraRuntime, visionRuntime, presetList] = await Promise.all([
        fetchDevices(),
        fetchCameraRuntimeStatus(),
        fetchVisionRuntimeStatus(),
        fetchVisionRuntimePresets(),
      ])
      inventory.value = devices
      cameraStatus.value = cameraRuntime
      cameraDraft.value = { ...cameraRuntime.profile, roi: { ...cameraRuntime.profile.roi } }
      visionStatus.value = visionRuntime
      presets.value = presetList
      visionDraft.value = { ...visionRuntime.profile }
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Device/runtime refresh failed'
    }
  }

  /**
   * Fetch only the two runtime records needed to attach an already prepared
   * camera to the cockpit.  A hard refresh can establish the WebSocket before
   * its first camera.runtime_status envelope arrives; in that short window the
   * store still contains its mock defaults.  The full refresh also scans the
   * device inventory and presets, which is unnecessary on the cockpit hot
   * path and noticeably slower over a remote Windows connection.
   */
  async function refreshRuntimeStatus(): Promise<void> {
    error.value = null
    try {
      const [cameraRuntime, visionRuntime] = await Promise.all([
        fetchCameraRuntimeStatus(),
        fetchVisionRuntimeStatus(),
      ])
      applyCameraStatus(cameraRuntime)
      applyVisionStatus(visionRuntime)
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Runtime status refresh failed'
    }
  }

  async function refreshInventory(): Promise<void> {
    inventory.value = await refreshDevices()
  }

  async function probe(deviceId: string): Promise<void> {
    lastCameraResult.value = await probeCamera(deviceId)
  }

  async function applyCamera(): Promise<void> {
    const result = await applyCameraRuntimeProfile(cameraDraft.value)
    lastCameraResult.value = result
    cameraStatus.value = { ...cameraStatus.value, profile: result.profile, actual_width: result.actual_width, actual_height: result.actual_height, actual_fps: result.actual_fps, warnings: result.warnings, last_apply_ok: result.accepted }
  }

  async function applyCameraControls(): Promise<void> {
    const controls = {
      exposure_auto: cameraDraft.value.exposure_auto,
      exposure_value: cameraDraft.value.exposure_value,
      gain: cameraDraft.value.gain,
      white_balance_auto: cameraDraft.value.white_balance_auto,
      white_balance_value: cameraDraft.value.white_balance_value,
      brightness: cameraDraft.value.brightness,
      contrast: cameraDraft.value.contrast,
      saturation: cameraDraft.value.saturation,
      sharpness: cameraDraft.value.sharpness,
    }
    const status = await applyCameraRuntimeControls(controls)
    cameraStatus.value = status
    cameraDraft.value = { ...status.profile, roi: { ...status.profile.roi } }
  }

  async function resetCamera(): Promise<void> {
    const result = await resetCameraRuntimeProfile()
    lastCameraResult.value = result
    cameraStatus.value = { ...cameraStatus.value, profile: result.profile }
    cameraDraft.value = { ...result.profile, roi: { ...result.profile.roi } }
  }

  async function probeCurrent(): Promise<void> {
    lastCameraResult.value = await probeCurrentCameraRuntime()
  }

  async function benchmarkCamera(): Promise<void> {
    lastCameraResult.value = await benchmarkCameraRuntime()
  }

  async function snapshotCamera(): Promise<void> {
    lastCameraResult.value = await snapshotCameraRuntime()
  }

  async function applyVision(): Promise<void> {
    const result = await applyVisionRuntimeSettings(visionDraft.value)
    lastVisionResult.value = result
    visionStatus.value = result.status
    visionDraft.value = { ...result.profile }
  }

  async function resetVision(): Promise<void> {
    const result = await resetVisionRuntimeSettings()
    lastVisionResult.value = result
    visionStatus.value = result.status
    visionDraft.value = { ...result.profile }
  }

  async function reloadModels(): Promise<void> {
    lastVisionResult.value = await reloadVisionRuntimeModels()
    visionStatus.value = await fetchVisionRuntimeStatus()
    visionDraft.value = { ...visionStatus.value.profile }
  }

  async function warmup(): Promise<void> {
    lastVisionResult.value = await warmupVisionRuntime()
  }

  async function benchmarkVision(): Promise<void> {
    lastVisionResult.value = await benchmarkVisionRuntime()
  }

  async function applyPreset(name: string): Promise<void> {
    const result = await applyVisionRuntimePreset(name)
    lastVisionResult.value = result
    visionStatus.value = result.status
    visionDraft.value = { ...result.profile }
  }

  async function verifyActiveVision(): Promise<void> {
    lastVisionResult.value = await verifyActiveVisionRuntime()
  }

  async function testActiveModel(): Promise<void> {
    lastVisionResult.value = await testActiveVisionModel()
  }

  function applyCameraStatus(status: CameraRuntimeStatus): void {
    cameraStatus.value = status
    cameraDraft.value = { ...status.profile, roi: { ...status.profile.roi } }
  }

  function applyVisionStatus(status: VisionRuntimeStatus): void {
    visionStatus.value = status
    visionDraft.value = { ...status.profile }
  }

  return {
    inventory,
    cameraStatus,
    cameraDraft,
    visionStatus,
    presets,
    visionDraft,
    lastCameraResult,
    lastVisionResult,
    error,
    refresh,
    refreshRuntimeStatus,
    refreshInventory,
    probe,
    applyCamera,
    applyCameraControls,
    resetCamera,
    probeCurrent,
    benchmarkCamera,
    snapshotCamera,
    applyVision,
    resetVision,
    reloadModels,
    warmup,
    benchmarkVision,
    applyPreset,
    verifyActiveVision,
    testActiveModel,
    applyCameraStatus,
    applyVisionStatus,
  }
})
