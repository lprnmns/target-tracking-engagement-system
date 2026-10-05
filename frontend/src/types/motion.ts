export type MotionStateValue = 'IDLE' | 'JOGGING' | 'HOMING' | 'SCANNING' | 'TRACKING_DRY_RUN' | 'STOPPED' | 'FAULT'

export interface MotionSettings {
  pan_min_deg: number
  pan_max_deg: number
  tilt_min_deg: number
  tilt_max_deg: number
  pan_steps_per_degree: number
  tilt_steps_per_degree: number
  pan_max_speed_deg_s: number
  tilt_max_speed_deg_s: number
  pan_accel_deg_s2: number
  tilt_accel_deg_s2: number
  jog_step_deg: number
  deadband_px: number
  tracking_gain_x: number
  tracking_gain_y: number
  backlash_compensation_enabled: boolean
  soft_limits_enabled: boolean
  scan_enabled: boolean
  scan_min_deg: number
  scan_max_deg: number
  scan_speed_deg_s: number
}

export interface MotionState {
  motion_state: MotionStateValue
  pan_position_deg: number
  tilt_position_deg: number
  pan_target_deg: number
  tilt_target_deg: number
  pan_position_steps: number
  tilt_position_steps: number
  pan_error_deg: number
  tilt_error_deg: number
  pan_limit_left: boolean
  pan_limit_right: boolean
  tilt_limit_up: boolean
  tilt_limit_down: boolean
  homed: boolean
  homing_phase: string
  limit_reason_code: string | null
  physical_pan_deg: number | null
  physical_tilt_deg: number | null
  driver_enabled: boolean
  estop_state: boolean
  dry_run: boolean
  last_command: string | null
  last_error: string | null
  updated_at: number
}

export interface MotionJogRequest {
  axis: 'pan' | 'tilt'
  direction: 'positive' | 'negative'
  step_deg?: number
}

export interface MotionGoToRequest {
  pan_target_deg: number
  tilt_target_deg: number
}

export interface MotionTrackDryRunRequest {
  frame_width: number
  frame_height: number
  target_center_x: number
  target_center_y: number
}

export interface TrackingDryRunPreview {
  frame_center_x: number
  frame_center_y: number
  target_center_x: number
  target_center_y: number
  error_x_px: number
  error_y_px: number
  computed_pan_delta_deg: number
  computed_tilt_delta_deg: number
}

export interface MotionCommandResponse {
  accepted: boolean
  dry_run: boolean
  command_id: string
  command_type: string
  requested_target: Record<string, unknown>
  clamped_target: Record<string, unknown> | null
  blocking_reasons: string[]
  safety_gates: Array<Record<string, unknown>>
  generated_steps: Record<string, number> | null
  no_physical_command_generated: boolean
  reason: string
  state: MotionState
  tracking_preview: TrackingDryRunPreview | null
}
