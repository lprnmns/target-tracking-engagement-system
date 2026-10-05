export type EngagementEvidenceState = 'LOCKED_RECORDING' | 'SHOT_PENDING_CONFIRMATION' | 'COMPLETED' | 'ABORTED'

export interface EngagementEvidenceSummary {
  engagement_id: string
  shot_id: string | null
  state: EngagementEvidenceState
  created_at: number
  updated_at: number
  mission_stage: string
  command_profile: string
  body_track_id: number | null
  body_detection_id: number | null
  balloon_track_id: number | null
  balloon_detection_id: number | null
  target_class: string | null
  target_team: string | null
  association_state: string
  frame_id: number | null
  reason_codes: string[]
  evidence_path: string
  camera_capture_status: string
  outcome: 'PENDING' | 'HIT_CONFIRMED' | 'MISS_CONFIRMED' | 'UNCONFIRMED'
  no_physical_command_generated: boolean
}

export interface EngagementEvidenceStatus {
  active: EngagementEvidenceSummary | null
  recent: EngagementEvidenceSummary[]
  pre_roll_frame_count: number
  writer_queue_depth: number
  dropped_timeline_entries: number
  no_physical_command_generated: boolean
}

export interface EngagementEvidenceRecordList {
  records: EngagementEvidenceSummary[]
  no_physical_command_generated: boolean
}
