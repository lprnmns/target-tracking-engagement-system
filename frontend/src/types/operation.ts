export type ControlMode = 'MANUAL' | 'AUTONOMOUS'
export type TargetPolicy = 'BALLOON' | 'AIRCRAFT' | 'BALLOON_AIRCRAFT' | 'STAGE1_INDEPENDENT'
export type CompetitionStage = 'STAGE_1' | 'STAGE_2' | 'STAGE_3'
export type FirePermission = 'DISABLED' | 'ENABLED'
export type SelectedDetectionKind = 'body' | 'balloon'

export interface PathAngleConfig {
  path1_deg: number
  path2_deg: number
  path3_deg: number
  dwell_time_s: number
  active_path: number
  patrol_enabled: boolean
}

export interface OperationState {
  control_mode: ControlMode
  target_policy: TargetPolicy
  competition_stage?: CompetitionStage | null
  fire_permission: FirePermission
  selected_logical_target_id: string | null
  selected_detection_id: number | null
  selected_detection_kind: SelectedDetectionKind | null
  selected_body_track_id: number | null
  tracking_requested: boolean
  configured: boolean
  updated_at: number
}

export interface OperationUpdate {
  control_mode?: ControlMode
  target_policy?: TargetPolicy
  competition_stage?: CompetitionStage | null
  fire_permission?: FirePermission
  selected_logical_target_id?: string | null
  selected_detection_id?: number | null
  selected_detection_kind?: SelectedDetectionKind | null
  selected_body_track_id?: number | null
  tracking_requested?: boolean
}

export interface OperationTargetSelection {
  logical_target_id?: string | null
  detection_id: number
  detection_kind: SelectedDetectionKind
  body_track_id?: number | null
  x: number
  y: number
  frame_id?: number | null
}
