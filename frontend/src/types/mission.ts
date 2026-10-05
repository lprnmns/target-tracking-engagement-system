export type MissionStage = 'stage1' | 'stage2' | 'stage3'
export type Stage1Target = 'Balistik Füze' | 'Helikopter' | 'F16' | 'Mini/Micro İHA'
export type Stage1RangeScore = 5 | 10 | 20
export type Stage3TargetClass = 'f16' | 'helicopter' | 'ballistic_missile' | 'mini_micro_uav'

export interface Stage1Event {
  kind: 'hit' | 'wrong_target'
  target: Stage1Target
  score_awarded: number
  penalty: number
  elapsed_s: number
  timestamp: number
}

export interface Stage2RoundEvent {
  round_number: number
  confirmed_hits: number
  points: number
  zero_hit_streak: number
  timestamp: number
}

export interface Stage2EngagementStatus {
  current_round: number
  fired_track_ids: number[]
  pending_track_ids: number[]
  confirmed_track_ids: number[]
  reengage_track_ids: number[]
  ready_to_close: boolean
  reason_codes: string[]
  updated_at: number
}

export interface Stage3FriendLink {
  balloon_track_id: number
  body_track_id: number
}

export interface Stage3EngagementStatus {
  current_round: number
  enemy_class: Stage3TargetClass | null
  enemy_balloon_track_id: number | null
  friend_links: Stage3FriendLink[]
  enemy_confirmation_state: string | null
  enemy_hit_confirmed: boolean
  friend_safety_verified: boolean
  friend_hit_suspected: boolean
  ready_to_close: boolean
  reason_codes: string[]
  shot_at: number | null
  updated_at: number
}

export interface Stage3RoundEvent {
  round_number: number
  enemy_class: Stage3TargetClass
  enemy_hit: boolean
  friend_hit: boolean
  points: number
  penalty: number
  miss_streak: number
  timestamp: number
}

export interface MissionState {
  active_stage: MissionStage
  elapsed_s: number
  timer_running: boolean
  stage1_hits: number
  stage1_wrong_hits: number
  stage1_order: string[]
  stage1_order_locked: boolean
  stage1_completed_targets: Stage1Target[]
  stage1_raw_points: number
  stage1_penalty_points: number
  stage1_events: Stage1Event[]
  stage2_round: number
  stage2_hits: number
  stage2_completed_rounds: number
  stage2_round_events: Stage2RoundEvent[]
  stage2_zero_hit_streak: number
  stage2_failed: boolean
  stage3_round: number
  stage3_hits: number
  stage3_friend_or_miss_penalties: number
  stage3_completed_rounds: number
  stage3_round_events: Stage3RoundEvent[]
  stage3_miss_streak: number
  stage3_failed: boolean
  updated_at: number
}

export interface MissionUpdate {
  active_stage?: MissionStage
  elapsed_s?: number
  timer_running?: boolean
}

export interface MissionScore {
  stage1_score: number
  stage2_score: number
  stage3_score: number
  active_score: number
  remaining_s: number
  total_estimated_score: number
  stage1_raw_points: number
  stage1_penalty_points: number
  stage1_bonus_points: number
  stage1_next_target: Stage1Target | null
  stage1_plan_locked: boolean
  stage2_round_scores: number[]
  stage2_zero_hit_streak: number
  stage2_failed: boolean
  stage2_passing_threshold_met: boolean
  stage3_round_scores: number[]
  stage3_miss_streak: number
  stage3_failed: boolean
  stage3_award_threshold_met: boolean
}

export interface MissionSnapshot {
  state: MissionState
  score: MissionScore
  no_physical_command_generated: boolean
}
