export type DecisionStateValue = 'NO_TARGET' | 'TRACKING' | 'WAIT' | 'LOCKED' | 'FIRE_READY' | 'NO_FIRE' | 'FAULT'
export type GateStatus = 'pass' | 'fail' | 'warning' | 'not_applicable'
export type GateSeverity = 'info' | 'warning' | 'critical'

export interface SafetyGate {
  name: string
  status: GateStatus
  severity: GateSeverity
  reason: string
  updated_at: number
}

export interface DecisionState {
  decision_state: DecisionStateValue
  fire_policy: string
  active_target_id: number | null
  logical_target_id: string | null
  logical_target_name: string | null
  logical_target_state: string | null
  logical_balloon_target_id: string | null
  selected_body_detection_id: number | null
  selected_balloon_detection_id: number | null
  target_class: string | null
  target_team: string
  range_m: number | null
  stable_frames: number
  required_stable_frames: number
  gates: SafetyGate[]
  blocking_reasons: string[]
  decision_reason: string
  updated_at: number
  aim_point: Record<string, unknown> | null
  person_safety: PersonSafetyGateStatus | null
}

export interface PersonSafetyGateStatus {
  enabled: boolean
  person_detected: boolean
  fire_gate_blocked_reason: string | null
  recommended_state: 'CLEAR' | 'SAFE_HOLD' | 'FIRE_BLOCKED'
  confidence_threshold: number
  hold_ms: number
  clear_after_ms: number
  last_detection_confidence: number | null
  last_detection_class: string | null
  last_detection_id: number | null
  last_detection_timestamp_ms: number | null
  active_until_ms: number | null
  source: string
  no_physical_command_generated: boolean
}

export interface FireEvaluationResult {
  accepted: boolean
  dry_run: boolean
  decision_state: DecisionStateValue
  blocking_reasons: string[]
  gates: SafetyGate[]
  reason: string
}

export interface ArmDisarmResult {
  accepted: boolean
  armed: boolean
  reason: string
  blocking_reasons: string[]
  decision: DecisionState | null
}
