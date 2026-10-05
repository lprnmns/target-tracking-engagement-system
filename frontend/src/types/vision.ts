export interface BBox {
  x: number
  y: number
  w: number
  h: number
  format: 'pixel'
}

export interface BodyDetection {
  id: number
  class_name: string
  class_id: number
  confidence: number
  bbox: BBox
  source: string
  color_hint: string | null
  stable_frames: number
  track_id?: number | null
  target_team?: string
  range_m?: number | null
  color_decision?: Record<string, unknown> | null
  visual_track_key?: string
  visual_live?: boolean
  visual_age_ms?: number
  visual_logical_name?: string | null
}

export interface BalloonDetection {
  id: number
  confidence: number
  bbox: BBox
  center_x: number
  center_y: number
  source: string
  visual_track_key?: string
  visual_live?: boolean
  visual_age_ms?: number
  visual_logical_name?: string | null
}

export interface AimPoint {
  id: number
  x: number
  y: number
  source: string
}

export interface CockpitTargetVerdict {
  track_id?: number | null
  detection_id?: number | null
  kind: 'body' | 'balloon'
  bbox: BBox | { x: number; y: number; w: number; h: number }
  label_tr: string
  verdict_state: 'CLASSIFYING' | 'FRIEND_LOCKED' | 'RANGE_WAIT' | 'FIRE_AUTHORIZED'
  target_class: string
  target_team: string
  range_m?: number | null
  range_source?: string
  range_age_ms?: number
  fire_authorized: boolean
}

export interface VisionEvent {
  frame_id: number
  timestamp_ms: number
  source: string
  frame_width?: number | null
  frame_height?: number | null
  fps: number
  camera_fps?: number | null
  detector_fps?: number | null
  preprocess_ms: number
  inference_ms: number
  postprocess_ms: number
  total_latency_ms: number
  total_ms?: number | null
  camera_source_kind?: string | null
  camera_device_path?: string | null
  frame_origin?: string | null
  detector_kind?: string | null
  body_detections: BodyDetection[]
  balloon_detections: BalloonDetection[]
  tracks: Array<{ track_id: number; detection_id: number; stable_frames: number }>
  aim_points: AimPoint[]
  target_verdicts?: CockpitTargetVerdict[]
  model_count: number
  model_execution: string
  model_timings_ms: Record<string, number>
  capture_ms?: number
  pipeline_overhead_ms?: number
  camera_frame_sequence?: number | null
  camera_capture_timestamp_ms?: number | null
  camera_frame_age_ms?: number | null
  warnings: string[]
}

export interface VisionStatus {
  running: boolean
  vision_mode: string
  model_loading_required: boolean
  body_model_path: string | null
  balloon_model_path: string | null
  body_model_loaded: boolean
  balloon_model_loaded: boolean
  fps: number
  camera_fps?: number | null
  detector_fps?: number | null
  latest_frame_id: number
  latest_latency_ms: number
  latest_total_ms?: number | null
  camera_source_kind?: string | null
  frame_origin?: string | null
  detector_kind?: string | null
  body_count: number
  balloon_count: number
  model_count: number
  model_execution: string
  model_timings_ms: Record<string, number>
  warnings: string[]
  advisory_only: boolean
}

export interface CameraStatus {
  camera_mode: string
  source: string | number | null
  connected: boolean
  running: boolean
  stream_enabled: boolean
  width: number
  height: number
  fps: number
  last_error: string | null
  selected_device: string | null
  selected_backend: string | null
  source_mode: string | null
  input_format: string | null
  resolution: string | null
  last_frame_age_ms: number | null
  last_capture_error: string | null
  is_real_camera_evidence: boolean
  is_external_usb_camera: boolean
  is_laptop_camera: boolean
  hardware_presence_note: string | null
}

export interface VisionConfig {
  vision_mode: string
  body_model_path: string | null
  balloon_model_path: string | null
  body_conf_threshold: number
  balloon_conf_threshold: number
}

export interface LegacyPerceptionPreset {
  preset_id: string
  source_file: string
  camera_index: number | null
  width: number | null
  height: number | null
  fps: number | null
  color_space: string
  hsv_lower: number[] | number[][] | null
  hsv_upper: number[] | number[][] | null
  blur_kernel: number | number[] | null
  morphology_kernel: number | number[] | null
  min_area: number | null
  max_area: number | null
  circularity_min: number | null
  target_selection_rule: string
  smoothing_enabled: boolean
  kalman_enabled: boolean
  notes: string
  risk_class: string
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface LegacyPerceptionPresetList {
  presets: LegacyPerceptionPreset[]
  source_reports: string[]
  forbidden_runtime_tokens_present: boolean
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface RealCameraEvidence {
  evidence_id: string
  status: string
  created_at: number
  camera_source: string
  camera_device_path: string | null
  frame_origin: string
  detector: string
  preset_id: string | null
  frame_width: number | null
  frame_height: number | null
  fps_estimate: number | null
  detections_count: number
  target_center_metadata: Record<string, unknown>
  warnings: string[]
  advisory_only: boolean
  no_physical_command_generated: boolean
  physical_command_enabled: boolean
}

export interface RealCameraEvidenceStatus {
  status: string
  camera_source: string
  camera_device_path: string | null
  frame_origin: string
  detector: string
  preset_id: string | null
  frame_width: number | null
  frame_height: number | null
  fps_estimate: number | null
  detections_count: number
  target_center_metadata: Record<string, unknown>
  latest_evidence_id: string | null
  warnings: string[]
  advisory_only: boolean
  no_physical_command_generated: boolean
  physical_command_enabled: boolean
}

export interface CameraHostCommandResult {
  command: string
  status: string
  exit_code: number | null
  output: string
  error: string | null
}

export interface CameraDeviceGroup {
  camera_kind: string
  name: string
  paths: string[]
  preferred_capture_path: string | null
  evidence_status: string
  frame_captured: boolean
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface CameraHostDiagnostic {
  diagnostic_id: string
  created_at: number
  platform: string
  host_camera_devices_detected: boolean
  dev_video_entries: string[]
  camera_groups: CameraDeviceGroup[]
  recommended_usb_device_path: string | null
  selected_camera_device: string | null
  selected_camera_name: string | null
  camera_kind: string
  v4l2_available: boolean
  ffmpeg_available: boolean
  user_in_video_group: boolean
  camera_app_not_seen_note: boolean
  real_camera_capture_attempted: boolean
  real_camera_frame_captured: boolean
  camera_acceptance_status: 'passed' | 'partial' | 'blocked_by_host_os' | 'failed' | string
  blocker_reason: string
  commands: CameraHostCommandResult[]
  suggested_actions: string[]
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface RealCameraAcceptance {
  status: string
  camera_tooling_status: string
  frame_captured: boolean
  device_path: string | null
  width: number | null
  height: number | null
  fps_estimate: number | null
  frame_hash: string | null
  frame_path: string | null
  capture_method: string | null
  selected_camera_device: string | null
  selected_camera_name: string | null
  camera_kind: string
  internal_camera_passed: boolean
  external_usb_camera_passed: boolean
  blocker_reason: string
  camera_host: CameraHostDiagnostic
  latest_evidence: RealCameraEvidence
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface RealCameraSelection {
  selected_camera_device: string
  selected_camera_name: string | null
  camera_kind: string
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}
