export type DigitalTwinTone = 'good' | 'warn' | 'bad' | 'neutral'

export interface DigitalTwinVector3 {
  x: number
  y: number
  z: number
}

export interface DigitalTwinBBox {
  x: number
  y: number
  w: number
  h: number
  format: string
}

export interface DigitalTwinDevicePose {
  pan_deg: number
  tilt_deg: number
  servo_angle_deg: number
  pose_quality: 'fixture' | 'estimated' | 'runtime' | 'unavailable'
  pose_source: 'telemetry' | 'gateway_open_loop_estimate' | 'tracker_estimate' | 'fixture' | 'replay_fixture' | 'static_demo_pose'
  pan_steps: number
  tilt_steps: number
  source: string
}

export type ManualTwinMotionIntentStatus = 'pending' | 'accepted' | 'rejected' | 'released'

export interface ManualTwinMotionIntent {
  sequence: number
  speedX: number
  speedY: number
  issuedAtMs: number
  active: boolean
  status: ManualTwinMotionIntentStatus
}

export interface DigitalTwinCameraState {
  selected_camera: string
  selected_device: string | null
  device_path: string | null
  source_type: string
  running: boolean
  real_camera_stream: boolean
  is_real_camera_evidence: boolean
  width: number
  height: number
  fps: number
  frame_age_ms: number | null
  source_mode: string
  selected_backend: string
  input_format: string
  last_capture_error: string | null
  is_external_usb_camera: boolean
  is_laptop_camera: boolean
  hardware_presence_note: string
}

export interface DigitalTwinTargetState {
  detected: boolean
  selected_target_id: number | null
  track_id: number | null
  class_id: string
  class_label: string
  confidence: number
  bbox: DigitalTwinBBox | null
  center_px: DigitalTwinVector3 | null
  normalized_x: number | null
  normalized_y: number | null
  estimated_scene_position_m: DigitalTwinVector3 | null
  source: string
}

export interface DigitalTwinTargetProjectionEstimate {
  target_id: number | null
  class_name: string
  target_team?: 'enemy' | 'friend' | 'unknown' | string | null
  confidence: number
  confidence_label: string
  bbox: DigitalTwinBBox
  normalized_center_x: number
  normalized_center_y: number
  normalized_width: number
  normalized_height: number
  normalized_screen_x: number
  normalized_screen_y: number
  bbox_area_ratio: number
  azimuth_deg: number
  elevation_deg: number
  relative_depth: number
  estimated_range_band: 'near' | 'mid' | 'far'
  reference_size_m: number | null
  estimated_range_m: number | null
  range_uncertainty_m: number | null
  range_source: string
  scene_position_m: DigitalTwinVector3
  selected: boolean
  mapping_source: string
  depth_source: string
  projection_is_calibrated: boolean
  camera_fov_horizontal_deg: number
  camera_fov_vertical_deg: number
  camera_to_launcher_offset_z_mm: number
  camera_to_launcher_offset_y_mm: number
  no_physical_command_generated: boolean
  visual_live?: boolean
  visual_age_ms?: number
  visual_logical_name?: string | null
}

export interface DigitalTwinTrackerState {
  tracking_enabled: boolean
  state: string
  error_x_px: number
  error_y_px: number
  latency_ms: number | null
  command_rate_hz: number
  max_speed: number
  source: string
}

export interface DigitalTwinEngagementState {
  fire_allowed: boolean
  fire_gate_state: string
  fire_blocked_reason: string
  last_event: string
  target_loss_after_engagement: boolean
  magazine_remaining: number | null
  person_safety_blocked: boolean
  person_detection_confidence: number | null
}

export interface DigitalTwinLatencyMetrics {
  camera_frame_age_ms: number | null
  inference_ms: number | null
  tracking_loop_ms: number | null
  serial_ack_rtt_ms: number | null
  total_pipeline_ms: number | null
}

export interface DigitalTwinRuntimeState {
  queue_length: number
  camera_mode: string
  pico_connection_state: string
  selected_target_id: number | null
  latency: DigitalTwinLatencyMetrics
}

export interface DigitalTwinTelemetryProtocol {
  protocol_name: string
  protocol_version: number
  pico_connected: boolean
  telemetry_fresh: boolean
  telemetry_missing: boolean
  port: string | null
  last_heartbeat_age_ms: number | null
  pan_deg: number | null
  tilt_deg: number | null
  x_steps: number | null
  y_steps: number | null
  driver_enabled: boolean
  limit_state: Record<string, boolean>
  fault_state: { active: boolean, code: string | null, message: string | null }
  pose_source: 'telemetry' | 'tracker_estimate' | 'fixture' | string
  packet_parse_status: string
  crc_status: string
  physical_tx_disabled: boolean
  serial_tx_enabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface DigitalTwinSafetyState {
  e_stop: string
  fire_policy: string
  hardware_enabled: boolean
  physical_command_enabled: boolean
  digital_twin_read_only: boolean
  digital_twin_command_authority: boolean
  hardware_acceptance_required: boolean
  no_physical_command_generated: boolean
  forbidden_actions: string[]
}

export interface DigitalTwinSceneNode {
  id: string
  label: string
  kind: string
  parent: string | null
  transform_source: string
}

export interface DigitalTwinState {
  schema_version: string
  timestamp_ms: number
  mode: 'fixture' | 'live_read_only' | 'replay' | 'degraded'
  feature_enabled: boolean
  source: string
  camera_fov_horizontal_deg: number
  camera_fov_vertical_deg: number
  camera_to_launcher_offset_z_mm: number
  camera_to_launcher_offset_y_mm: number
  projection_is_calibrated: boolean
  depth_source: string
  device_pose: DigitalTwinDevicePose
  camera: DigitalTwinCameraState
  target: DigitalTwinTargetState
  target_projection_estimates: DigitalTwinTargetProjectionEstimate[]
  tracker: DigitalTwinTrackerState
  engagement: DigitalTwinEngagementState
  runtime: DigitalTwinRuntimeState
  telemetry_protocol: DigitalTwinTelemetryProtocol
  safety: DigitalTwinSafetyState
  scene_nodes: DigitalTwinSceneNode[]
  evidence: Record<string, string | boolean>
  no_physical_command_generated: boolean
}

export interface DigitalTwinAsset {
  class_id: string
  label: string
  model_path: string
  source_file: string | null
  source_sha256: string | null
  source_size_bytes: number | null
  scale: DigitalTwinVector3
  rotation_offset_deg: DigitalTwinVector3
  position_offset_m: DigitalTwinVector3
  confidence_min: number
  status: 'planned' | 'placeholder' | 'available' | 'missing' | 'converted'
  notes: string
}

export interface DigitalTwinAssetsResponse {
  schema_version: string
  device_model: DigitalTwinAsset
  target_assets: DigitalTwinAsset[]
  available_model_files: string[]
  preferred_browser_asset: string | null
  selected_asset_type: 'REAL_GLB' | 'REAL_STL' | 'CAD_SOURCE_ONLY' | 'PROCEDURAL_FALLBACK' | string
  selected_asset_path: string | null
  source_cad_path: string | null
  conversion_status: string
  scale_units: string
  coordinate_notes: string
  asset_transform: Record<string, unknown>
  camera_mount_reference_available: boolean
  launcher_axis_reference_available: boolean
  asset_fallback_reason: string
  no_physical_command_generated: boolean
  digital_twin_read_only: boolean
}

export interface DigitalTwinReplayGenerateResult {
  accepted: boolean
  run_id: string
  report_path: string
  event_count: number
  no_physical_command_generated: boolean
  digital_twin_read_only: boolean
}

export interface DigitalTwinReplayEvent {
  t_ms: number
  target: DigitalTwinTargetState
  target_projection_estimates: DigitalTwinTargetProjectionEstimate[]
  device_pose: DigitalTwinDevicePose
  tracker: DigitalTwinTrackerState
  note: string
  no_physical_command_generated: boolean
}

export interface DigitalTwinReplaySummary {
  run_id: string
  source: string
  mode: string
  duration_ms: number
  event_count: number
  events: DigitalTwinReplayEvent[]
  no_physical_command_generated: boolean
}
