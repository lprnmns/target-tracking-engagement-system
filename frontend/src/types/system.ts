export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

export interface SystemState {
  mode: string
  armed: boolean
  fire_policy: string
  dry_run: boolean
  hardware_enabled: boolean
  ready: boolean
  uptime_s: number
  reason: string
  blocking_reasons: string[]
}

export interface SafetyGateState {
  armed: boolean
  estop_released: boolean
  pico_heartbeat: boolean
  track_stable: boolean
  target_enemy: boolean
  balloon_detected: boolean
  range_valid: boolean
  aim_point_valid: boolean
  zone_valid: boolean
  operator_or_auto_permission: boolean
  hardware_enabled: boolean
  dry_run: boolean
  motion_soft_limits: boolean
  motion_estop: boolean
  motion_fault_clear: boolean
  motion_driver: boolean
  motion_dry_run: boolean
  person_safety_clear: boolean
}

export interface SafetyState {
  decision: string
  gates: SafetyGateState
  reason: string
  blocking_reasons: string[]
}

export type { PicoTelemetry } from './pico'

export type { VisionEvent, VisionStatus, CameraStatus } from './vision'

export interface VisionTargetsPayload {
  targets: Array<{
    track_id: number
    body_class: string
    body_conf: number
    team: string
    team_conf: number
    balloon_found: boolean
    range_m: number | null
    stable_frames: number
    decision: string
    reason: string
  }>
}

export interface WebSocketEnvelope<TPayload = unknown> {
  type: string
  ts: number
  seq: number
  payload: TPayload
}

export interface RecentEvent {
  type: string
  ts: number
  seq: number
  summary: string
  payload?: unknown
  legacy_format?: boolean
  format_warning?: string
}
