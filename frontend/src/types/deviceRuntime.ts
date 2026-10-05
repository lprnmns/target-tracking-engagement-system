export type DeviceKind = 'camera' | 'serial' | 'pico_candidate' | 'unknown'

export interface ManagedDevice {
  device_id: string
  device_path: string
  stable_path: string | null
  kind: DeviceKind
  name: string
  description: string
  manufacturer: string | null
  vid: string | null
  pid: string | null
  serial_number: string | null
  bus_path: string | null
  driver: string | null
  permissions_ok: boolean
  busy: boolean
  connected: boolean
  candidate_score: number
  recommendation_score: number
  warnings: string[]
  suggested_action: string | null
  identity_verified: boolean
  identity_source: string | null
  is_external_camera: boolean
  is_laptop_camera: boolean
}

export interface CameraCapability {
  device_id: string
  device_path: string
  stable_path: string | null
  supported_resolutions: string[]
  supported_fps: number[]
  supported_pixel_formats: string[]
  open_ok: boolean
  frame_grab_ok: boolean
  actual_width: number | null
  actual_height: number | null
  actual_fps: number | null
  latency_ms: number | null
  warnings: string[]
  suggested_action: string | null
}

export interface DeviceInventory {
  devices: ManagedDevice[]
  cameras: ManagedDevice[]
  serial: ManagedDevice[]
  pico_candidates: ManagedDevice[]
  scanned_at: number
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface RoiProfile {
  enabled: boolean
  x: number
  y: number
  w: number
  h: number
}

export interface CameraRuntimeProfile {
  source_type: 'laptop' | 'usb' | 'video_file' | 'replay' | 'mock'
  device_id: string | null
  device_path: string | null
  stable_path: string | null
  width: number
  height: number
  fps: number
  pixel_format: 'MJPG' | 'YUYV' | 'auto'
  exposure_auto: boolean
  exposure_value: number | null
  gain: number | null
  focus_auto: boolean | null
  focus_value: number | null
  white_balance_auto: boolean | null
  white_balance_value: number | null
  brightness: number | null
  contrast: number | null
  saturation: number | null
  sharpness: number | null
  flip_horizontal: boolean
  flip_vertical: boolean
  rotate_deg: number
  lens_profile: 'unknown' | '3.6mm' | '8mm' | '12mm'
  stream_width: number
  stream_height: number
  inference_width: number
  inference_height: number
  roi: RoiProfile
}

export interface CameraRuntimeStatus {
  profile: CameraRuntimeProfile
  running: boolean
  selected_camera: string
  requested_width: number
  requested_height: number
  requested_fps: number
  requested_pixel_format: 'MJPG' | 'YUYV' | 'auto'
  actual_width: number
  actual_height: number
  actual_fps: number
  actual_fps_measured: number
  actual_pixel_format: string
  backend_api: string
  warmup_ms: number
  dropped_frames: number
  last_probe_result: Record<string, unknown> | null
  recommendation_score: number
  last_apply_ok: boolean
  last_error: string | null
  warnings: string[]
  selected_device: string | null
  selected_backend: string
  source_mode: string
  input_format: string
  resolution: string
  last_frame_age_ms: number | null
  last_capture_error: string | null
  frame_mean_luma: number | null
  frame_brightness_state: string
  frame_sequence: number
  is_real_camera_evidence: boolean
  is_external_usb_camera: boolean
  is_laptop_camera: boolean
  hardware_presence_note: string
  updated_at: number
  no_physical_command_generated: boolean
}

export interface CameraRuntimeApplyResult {
  accepted: boolean
  applied: boolean
  rollback_performed: boolean
  profile: CameraRuntimeProfile
  actual_width: number
  actual_height: number
  actual_fps: number
  warnings: string[]
  suggested_action: string | null
  no_physical_command_generated: boolean
}

export interface VisionRuntimeProfile {
  inference_adapter: 'mock' | 'opencv_circle_test' | 'opencv_live_circle_surrogate' | 'ultralytics_yolo'
  active_body_model_id: string | null
  active_balloon_model_id: string | null
  device: 'cpu' | 'cuda' | 'auto'
  imgsz: number
  conf: number
  iou: number
  max_det: number
  classes: number[] | null
  agnostic_nms: boolean
  half: boolean
  parallel_models: boolean
  vid_stride: number
  stream_buffer: boolean
  frame_skip: number
  augment: boolean
  retina_masks: boolean | null
  tracker_enabled: boolean
  tracker_type: 'none' | 'current_custom' | 'bytetrack' | 'botsort_no_cmc' | 'botsort_sparse' | 'botsort_ecc' | 'ocsort' | 'norfair_custom'
  body_conf_threshold: number
  balloon_conf_threshold: number
  min_box_area_px: number
  max_box_area_px: number | null
  target_class_map: Record<string, number>
  friend_enemy_color_mode: 'disabled' | 'hsv' | 'lab' | 'model_metadata'
  latency_budget_ms: number
  target_fps: number
  warmup_on_load: boolean
  benchmark_on_apply: boolean
  circle_min_radius: number
  circle_max_radius: number
  circle_blur_kernel: number
  circle_threshold: number
  circle_edge_param: number
  circle_min_area: number
  circle_circularity: number
  circle_target_color_mode: 'any' | 'red' | 'green' | 'blue' | 'bright'
  circle_roi_enabled: boolean
  circle_smoothing: boolean
}

export interface TrackerProfileInfo {
  profile_id: VisionRuntimeProfile['tracker_type']
  label: string
  family: string
  description: string
  available: boolean
  reason: string | null
}

export interface TrackerRuntimeCatalog {
  active_profile: VisionRuntimeProfile['tracker_type']
  body_profile: VisionRuntimeProfile['tracker_type']
  balloon_profile: VisionRuntimeProfile['tracker_type']
  body_error: string | null
  balloon_error: string | null
  profiles: TrackerProfileInfo[]
  switch_requires_manual: boolean
  hot_apply: boolean
  no_physical_command_generated: boolean
}

export interface VisionRuntimeStatus {
  profile: VisionRuntimeProfile
  active_model_summary: Record<string, string | null>
  active_model_details: Record<string, unknown>
  selected_adapter: string
  effective_adapter: string
  production_yolo_loaded: boolean
  test_adapter_active: boolean
  model_package_id: string | null
  runtime_source: string
  surrogate_source_kind: string | null
  frame_origin: string | null
  advisory_only: boolean
  reload_required: boolean
  adapter_available: boolean
  requested_device: 'cpu' | 'cuda' | 'auto'
  resolved_device: 'cpu' | 'cuda' | null
  cuda_available: boolean
  device_reason: string
  cuda_fallback_active?: boolean
  cuda_fallback_reason?: string | null
  cuda_last_failure_at?: number | null
  cuda_last_recovery_attempt?: number | null
  cuda_last_recovery_success_at?: number | null
  cuda_device_name?: string | null
  cuda_free_memory_mb?: number | null
  cuda_total_memory_mb?: number | null
  cuda_model_devices?: string[]
  latest_parameter_version: number
  current_fps: number
  latest_latency_ms: number
  warnings: string[]
  errors: string[]
  updated_at: number
  no_physical_command_generated: boolean
}

export interface VisionRuntimePreset {
  name: string
  capture_width: number
  capture_height: number
  stream_width: number
  stream_height: number
  inference_width: number
  inference_height: number
  fps: number
  imgsz: number
  conf: number
  iou: number
  max_det: number
  frame_skip: number
  vid_stride: number
  tracker: 'none' | 'bytetrack' | 'botsort'
  half: boolean
}

export interface VisionRuntimeVerifyResult {
  accepted: boolean
  profile: VisionRuntimeProfile
  active_model_details: Record<string, unknown>
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface VisionRuntimeTestResult {
  accepted: boolean
  active_model_id: string | null
  adapter: string
  detections: Record<string, unknown>[]
  latency_ms: number
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface VisionRuntimeApplyResult {
  accepted: boolean
  applied: boolean
  rollback_performed: boolean
  reload_required: boolean
  profile: VisionRuntimeProfile
  status: VisionRuntimeStatus
  warnings: string[]
  errors: string[]
  suggested_action: string | null
  no_physical_command_generated: boolean
}
