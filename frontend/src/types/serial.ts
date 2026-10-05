export type SerialConnectionState =
  | 'DISCONNECTED'
  | 'MOCK_CONNECTED'
  | 'PORT_OPEN_NO_TELEMETRY'
  | 'READONLY_CONNECTED_UNVERIFIED'
  | 'PICO_READONLY_VERIFIED'
  | 'MOCK_READONLY_CONNECTED'
  | 'FAULT'
export type SerialDirection = 'tx' | 'rx' | 'system'
export type SerialLogKind = 'tx' | 'rx' | 'ack' | 'nack' | 'error' | 'timeout' | 'status'
export type MagazineState = 'READY' | 'LOW' | 'EMPTY' | 'INVALID'

export interface SerialStatus {
  connection_state: SerialConnectionState
  transport_mode: string
  transport_source: string
  protocol_mode: string
  real_serial_enabled: boolean
  real_serial_readonly: boolean
  readonly: boolean
  telemetry_received: boolean
  pico_verified: boolean
  transport_healthy: boolean
  consecutive_ping_failures: number
  transport_reconnect_count: number
  last_protocol_nack: string | null
  last_query_timeout: string | null
  last_transport_fault: string | null
  physical_command_enabled: boolean
  last_tx: Record<string, unknown> | null
  last_rx: Record<string, unknown> | null
  pending_ack_count: number
  command_queue_depth: number
  last_command_age_ms: number | null
  last_command_kind: string | null
  last_command_raw: string | null
  last_command_ack_state: string
  last_command_rtt_ms: number | null
  last_command_error: string | null
  magazine_capacity: number
  magazine_remaining: number
  magazine_empty: boolean
  magazine_state: MagazineState
  acknowledged_shot_count: number
  magazine_reload_count: number
  magazine_updated_at: number | null
  heartbeat_age_ms: number | null
  ack_timeout_ms: number
  heartbeat_timeout_ms: number
  last_error: string | null
}

export interface SerialLogEntry {
  id: number
  ts: number
  direction: SerialDirection
  kind: SerialLogKind
  message: Record<string, unknown>
  raw: string | null
  error: string | null
}

export interface SerialCommandResult {
  accepted: boolean
  reason: string
  status: SerialStatus
  log_entry: SerialLogEntry | null
  no_physical_command_generated: boolean
}
