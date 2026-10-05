/**
 * Tracking Types — Kapalı çevrim hedef takip sistemi TypeScript tipleri.
 */

import type { CockpitTargetVerdict } from './vision'

export type TrackingState =
  | 'IDLE'
  | 'SEARCHING'
  | 'TRACKING'
  | 'LOCKED'
  | 'TARGET_LOST'
  | 'STOPPED'
  | 'ERROR'

export interface TrackingUpdate {
  state: TrackingState
  speed_x: number
  speed_y: number
  error_x_px: number
  error_y_px: number
  raw_pid_x: number
  raw_pid_y: number
  target_center_x: number | null
  target_center_y: number | null
  frame_center_x: number
  frame_center_y: number
  aim_offset_x_px: number
  aim_offset_y_px: number
  target_lost_frames: number
  distance_to_center: number
  deadband_zone: string
  bbox_lock_enabled: boolean
  lock_zone_center_x?: number | null
  lock_zone_center_y?: number | null
  lock_zone_width: number
  lock_zone_height: number
  target_velocity_x_px_s: number
  target_velocity_y_px_s: number
  momentum_lock_active: boolean
  momentum_shift_x_px: number
  momentum_shift_y_px: number
  using_kalman_prediction: boolean
  lead_horizon_ms: number
  predicted_target_center_x: number | null
  predicted_target_center_y: number | null
  selected_track_id: number | null
  selected_detection_id: number | null
  selected_target_kind: 'body' | 'balloon' | null
  selected_target_confidence: number | null
  selected_bbox_x: number | null
  selected_bbox_y: number | null
  selected_bbox_w: number | null
  selected_bbox_h: number | null
  selected_track_age_frames: number
  selected_track_misses: number
  selected_track_last_seen_at: number | null
  selected_track_last_seen_age_ms: number | null
  selected_track_occluded: boolean
  vision_timestamp_ms: number | null
  vision_age_ms: number | null
  measurement_status: 'NEW' | 'REUSED' | 'STALE_REJECTED' | 'MISSING'
  frame_id: number
  dt: number
  controller_mode?: string
  target_verdicts?: CockpitTargetVerdict[]
  updated_at: number
}

export interface TrackingFireResult {
  accepted: boolean
  command: string
  reason_codes: string[]
  detail: string
  physical_command_generated: boolean
  updated_at: number
}

export interface MultiTargetTrack {
  track_id: number
  detection_id: number | null
  center_x: number
  center_y: number
  velocity_x: number
  velocity_y: number
  age_frames: number
  hits: number
  misses: number
  confidence: number
  bbox_x: number
  bbox_y: number
  bbox_w: number
  bbox_h: number
  predicted: boolean
  fresh: boolean
  last_seen_at: number | null
  last_seen_age_ms: number | null
  updated_at: number
}

export interface MultiTargetTrackingStatus {
  tracker_kind: string
  active_track_count: number
  id_switch_count: number
  rejected_stale_frames: number
  last_frame_id: number | null
  last_timestamp_ms: number | null
  tracks: MultiTargetTrack[]
  updated_at: number
}

export interface TargetPriorityCandidate {
  balloon_track_id: number
  body_detection_id: number
  score: number
  time_to_exit_s: number | null
  solution_quality: number
  return_cost: number
  logical_target_id?: string | null
  logical_target_state?: string | null
  reasons: string[]
}

export interface TargetPriorityStatus {
  selected_track_id: number | null
  ranked_candidates: TargetPriorityCandidate[]
  excluded_track_ids: number[]
  updated_at: number
}

export type LogicalTargetState =
  | 'STANDALONE'
  | 'ASSOCIATION_CANDIDATE'
  | 'READY'
  | 'REENGAGE'
  | 'PENDING_CONFIRMATION'
  | 'DESTROYED'

export interface LogicalTarget {
  target_id: string
  display_name: string
  balloon_target_id: string
  balloon_display_name: string
  target_class: string
  target_team: string
  body_track_id: number | null
  body_detection_id: number | null
  balloon_track_id: number | null
  balloon_detection_id: number | null
  engagement_allowed: boolean
  state: LogicalTargetState
  association_started_at: number | null
  association_duration_s: number
  last_seen_at: number | null
  last_body_seen_at: number | null
  last_balloon_seen_at: number | null
  dropout_until: number | null
  consumed: boolean
  destroyed_at: number | null
  reason_code: string | null
  updated_at: number
}

export interface TargetRegistryStatus {
  targets: LogicalTarget[]
  selected_target_id: string | null
  selected_balloon_track_id: number | null
  destroyed_target_ids: string[]
  consumed_balloon_track_ids: number[]
  association_min_duration_s: number
  dropout_grace_s: number
  updated_at: number
}

export interface TrackingStatus {
  active: boolean
  state: TrackingState
  target_count: number
  lost_count: number
  total_frames: number
  pid_kp_x: number
  pid_ki_x: number
  pid_kd_x: number
  pid_kp_y: number
  pid_ki_y: number
  pid_kd_y: number
  config_revision: number
  config_applied_at: number
  smoothing_alpha: number
  command_rate_hz: number
  max_speed: number
  max_speed_x: number
  max_speed_y: number
  bbox_lock_enabled: boolean
  momentum_lock_enabled: boolean
  momentum_velocity_threshold_px_s: number
  aim_offset_x_px: number
  aim_offset_y_px: number
  invert_x: boolean
  invert_y: boolean
  lead_enabled: boolean
  lead_latency_multiplier: number
  lead_max_horizon_ms: number
  preferred_target_x?: number | null
  preferred_target_y?: number | null
  preferred_target_detection_id?: number | null
  preferred_target_kind?: 'body' | 'balloon' | null
  target_policy?: string
  controller_mode?: string
  last_update: TrackingUpdate | null
  last_fire_result: TrackingFireResult | null
  multi_target_tracker: MultiTargetTrackingStatus
  updated_at: number
}

export interface TrackingConfigUpdate {
  pid_kp_x?: number
  pid_ki_x?: number
  pid_kd_x?: number
  pid_kp_y?: number
  pid_ki_y?: number
  pid_kd_y?: number
  smoothing_alpha?: number
  command_rate_hz?: number
  max_speed?: number
  max_speed_x?: number
  max_speed_y?: number
  bbox_lock_enabled?: boolean
  momentum_lock_enabled?: boolean
  momentum_velocity_threshold_px_s?: number
  aim_offset_x_px?: number
  aim_offset_y_px?: number
  invert_x?: boolean
  invert_y?: boolean
  lead_enabled?: boolean
  lead_latency_multiplier?: number
  lead_max_horizon_ms?: number
  max_lost_frames?: number
  controller_mode?: string
}
