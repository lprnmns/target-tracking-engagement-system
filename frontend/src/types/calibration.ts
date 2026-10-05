export type CalibrationStatusValue = 'not_started' | 'partial' | 'valid' | 'invalid'
export type WarningLevel = 'good' | 'marginal' | 'poor'

export interface CameraCalibrationConfig {
  camera_id: string
  camera_name: string
  lens_profile: '3.6mm' | '8mm' | '12mm' | 'varifocal_custom' | 'unknown'
  resolution_width: number
  resolution_height: number
  fps: number
  camera_height_cm: number
  target_height_cm: number
  table_height_cm: number
  hfov_deg: number
  vfov_deg: number | null
  distortion_enabled: boolean
  homography_enabled: boolean
  calibration_status: CalibrationStatusValue
  updated_at: number
}

export interface CalibrationPoint {
  id: string
  label: string
  world_x_m: number
  world_y_m: number
  image_x_px: number
  image_y_px: number
}

export type CalibrationPointCreate = Omit<CalibrationPoint, 'id'>

export interface CalibrationStatus {
  config: CameraCalibrationConfig
  calibration_points: CalibrationPoint[]
  homography_matrix: number[][] | null
  reprojection_error_px: number | null
  inlier_count: number
  calibration_hash: string | null
  homography_direction: string
  valid: boolean
  warnings: string[]
  updated_at: number
}

export interface FovEstimateRequest {
  hfov_deg: number
  distance_m: number
  object_width_m: number
  image_width_px: number
}

export interface FovEstimateResponse {
  visible_width_m: number
  object_width_px: number
  warning_level: WarningLevel
}

export interface DirectionCalibrationProfile {
  profile_id: string
  created_at: number
  updated_at: number
  source: string
  image_x_positive: string
  image_y_positive: string
  camera_mirror_x: boolean
  camera_mirror_y: boolean
  axis_swap: boolean
  pan_positive_label: string
  tilt_positive_label: string
  x_axis_multiplier: 1 | -1
  y_axis_multiplier: 1 | -1
  target_error_convention: string
  expected_pan_response: string
  expected_tilt_response: string
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
  notes: string
}

export interface DirectionSimulationRequest {
  target_position: 'left' | 'right' | 'up' | 'down' | 'center'
  target_center_x?: number | null
  target_center_y?: number | null
  frame_width?: number
  frame_height?: number
}

export interface DirectionSimulationResult {
  target_visual_side: string
  target_error_x: number
  target_error_y: number
  required_camera_motion: string
  expected_image_response: string
  frame_center_x: number
  frame_center_y: number
  target_center_x: number
  target_center_y: number
  advisory_motion_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface DirectionObservationRequest {
  simulated_axis: 'x' | 'y'
  system_expected_motion: 'camera_left' | 'camera_right' | 'camera_up' | 'camera_down' | 'no_motion' | 'unknown'
  operator_observed_motion: 'camera_left' | 'camera_right' | 'camera_up' | 'camera_down' | 'no_motion' | 'unknown'
  operator_confidence: 'confirmed' | 'manual' | 'needs_retest'
  note?: string | null
}

export interface DirectionObservationResult extends DirectionObservationRequest {
  observation_id: string
  suggested_x_axis_multiplier: 1 | -1
  suggested_y_axis_multiplier: 1 | -1
  axis_swap_suspected: boolean
  confidence: string
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface DirectionCalibrationStatus {
  profile: DirectionCalibrationProfile
  latest_simulation: DirectionSimulationResult | null
  latest_observation: DirectionObservationResult | null
  observation_count: number
  advisory_only: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}
