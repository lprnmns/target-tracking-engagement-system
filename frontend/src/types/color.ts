import type { BBox } from './vision'

export type TeamValue = 'enemy' | 'friend' | 'unknown'

export interface HSVRange {
  h_min: number
  h_max: number
  s_min: number
  v_min: number
}

export interface ColorClassifierConfig {
  color_space: 'HSV' | 'LAB'
  enemy_hsv_ranges: HSVRange[]
  friend_hsv_ranges: HSVRange[]
  saturation_min: number
  value_min: number
  lab_enabled: boolean
  min_body_pixels: number
  decision_threshold: number
  temporal_window: number
  required_consistent_frames: number
  balloon_mask_enabled: boolean
  balloon_hsv_ranges: HSVRange[]
  morphology_kernel: number
  updated_at: number
}

export interface ColorClassifySampleRequest {
  frame_id: number
  detection_id: number
  body_crop_bbox: BBox | null
  mock_team: TeamValue
  balloon_bbox_present: boolean
  body_pixel_count?: number
}

export interface ColorDecisionResult {
  frame_id: number
  detection_id: number
  body_crop_bbox: BBox | null
  balloon_mask_applied: boolean
  body_pixel_count: number
  enemy_pixel_ratio: number
  friend_pixel_ratio: number
  unknown_pixel_ratio: number
  decision: TeamValue
  confidence: number
  blocking_warnings: string[]
  debug_masks_available: boolean
  evidence_source: string
  body_track_id: number | null
  temporal_frames: number
  consistent_frames: number
  profile_hash: string | null
  frame_hash: string | null
  usable_for_live_fire: boolean
  updated_at: number
}

export interface ColorCalibrationReference {
  expected_team: Exclude<TeamValue, 'unknown'>
  capture_id: string
  frame_id: number
  detection_id: number
  body_track_id: number | null
  body_pixel_count: number
  decision: TeamValue
  confidence: number
  profile_hash: string
  frame_hash: string | null
  recorded_at: number
}

export interface ColorCalibrationStatus {
  valid: boolean
  profile_hash: string | null
  enemy_reference_count: number
  friend_reference_count: number
  references: ColorCalibrationReference[]
  reason_codes: string[]
  updated_at: number
}

export interface MaskPreviewResult {
  frame_id: number
  detection_id: number
  balloon_mask_enabled: boolean
  balloon_mask_applied: boolean
  debug_masks_available: boolean
  warnings: string[]
  updated_at: number
}
