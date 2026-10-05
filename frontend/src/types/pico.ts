export type PicoConnectionStatus = 'DISCONNECTED' | 'MOCK_CONNECTED' | 'CONNECTED'
export type EstopState = 'UNKNOWN' | 'RELEASED' | 'ACTIVE'
export type PinFunction =
  | 'PAN_STEP'
  | 'PAN_DIR'
  | 'TILT_STEP'
  | 'TILT_DIR'
  | 'TRIGGER_SERVO_PWM'
  | 'ESTOP_IN'
  | 'LIMIT_LEFT'
  | 'LIMIT_RIGHT'
  | 'LIMIT_UP'
  | 'LIMIT_DOWN'
  | 'DRIVER_ENABLE'
  | 'UART_TX'
  | 'UART_RX'
  | 'UNUSED'

export type PinDirection = 'IN' | 'OUT' | 'BIDIRECTIONAL' | 'UNUSED'
export type PinMode = 'GPIO' | 'PWM' | 'UART' | 'UNUSED'
export type PinValidationLevel = 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface PicoTelemetry {
  connection_status: PicoConnectionStatus
  port: string | null
  baudrate: number
  heartbeat_age_ms: number | null
  firmware_version: string
  estop_state: EstopState
  driver_enabled: boolean
  pan_position_steps: number
  tilt_position_steps: number
  pan_limit_left: boolean
  pan_limit_right: boolean
  tilt_limit_up: boolean
  tilt_limit_down: boolean
  last_error: string | null
  updated_at: number
}

export interface PicoStatus {
  mock_mode: boolean
  telemetry: PicoTelemetry
  reason: string
  blocking_reasons: string[]
}

export interface PicoPort {
  device: string
  label: string
  mock: boolean
}

export interface PicoConnectionEvent {
  connection_status: PicoConnectionStatus
  port: string | null
  baudrate: number
  reason: string
}

export interface PicoDiscoveryPort {
  port: string
  description: string
  hwid: string | null
  vid: string | null
  pid: string | null
  serial_number: string | null
  manufacturer: string | null
  is_candidate: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoDiscoveryPortsResponse {
  ports: PicoDiscoveryPort[]
  candidates_count: number
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoReadOnlyStatus {
  connected: boolean
  selected_port: string | null
  baudrate: number
  rx_only: boolean
  tx_disabled: boolean
  serial_write_enabled: boolean
  command_tx_enabled: boolean
  last_seen_at: number | null
  heartbeat_seen: boolean
  firmware_version: string | null
  telemetry_frames: number
  parse_errors: number
  dtr_rts_reset_risk: string
  warnings: string[]
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoReadOnlyTelemetry {
  raw_line_sample: string | null
  parsed: Record<string, unknown>
  heartbeat: boolean
  firmware_version: string | null
  estop_state: string | null
  limit_states: Record<string, unknown>
  motor_driver_state: Record<string, unknown>
  warning_fault_state: Record<string, unknown>
  no_command_generated: boolean
  serial_write_enabled: boolean
  command_tx_enabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoReadOnlyEvidence {
  evidence_id: string
  status: string
  created_at: number
  status_snapshot: PicoReadOnlyStatus
  latest_telemetry: PicoReadOnlyTelemetry
  port_inventory: PicoDiscoveryPort[]
  advisory_only: boolean
  serial_write_enabled: boolean
  command_tx_enabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoPermissionDiagnosis {
  port: string | null
  status: string
  blocker_class: string
  user: string
  groups: string[]
  user_in_dialout: boolean
  device_exists: boolean
  device_mode: string | null
  device_owner: string | null
  device_group: string | null
  id_output: string
  groups_output: string
  ls_output: string
  udevadm_output: string
  dmesg_output: string
  manual_recommendations: string[]
  serial_write_enabled: boolean
  command_tx_enabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PicoProtocolLimitState {
  pan_left: boolean
  pan_right: boolean
  tilt_up: boolean
  tilt_down: boolean
}

export interface PicoProtocolFaultState {
  active: boolean
  code: string | null
  message: string | null
}

export interface PicoProtocolTelemetry {
  protocol_name: string
  protocol_version: number
  pico_connected: boolean
  telemetry_fresh: boolean
  telemetry_missing: boolean
  port: string | null
  last_heartbeat_age_ms: number | null
  last_packet_type: string | null
  last_packet_seq_id: number | null
  pan_deg: number | null
  tilt_deg: number | null
  x_steps: number | null
  y_steps: number | null
  driver_enabled: boolean
  limit_state: PicoProtocolLimitState
  fault_state: PicoProtocolFaultState
  pose_source: 'telemetry' | 'tracker_estimate' | 'fixture' | string
  packet_parse_status: string
  crc_status: string
  physical_tx_disabled: boolean
  serial_tx_enabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
  updated_at: number | null
}

export interface PicoProtocolPort {
  port: string
  description: string
  hwid: string | null
  is_candidate: boolean
  no_physical_command_generated: boolean
}

export interface PicoProtocolStatus {
  protocol_name: string
  protocol_version: number
  selected_port: string | null
  baudrate: number
  pico_connected: boolean
  telemetry_fresh: boolean
  telemetry_missing: boolean
  latest_telemetry: PicoProtocolTelemetry
  discovered_ports: PicoProtocolPort[]
  packet_parse_status: string
  crc_status: string
  parse_errors: number
  crc_failures: number
  serial_tx_enabled: boolean
  physical_tx_disabled: boolean
  physical_command_enabled: boolean
  no_physical_command_generated: boolean
}

export interface PinAssignment {
  pin_name: string
  physical_pin: number
  function: PinFunction
  direction: PinDirection
  mode: PinMode
  pwm_capable: boolean
  uart_capable: boolean
  note: string | null
}

export interface PinProfile {
  profile_name: string
  note: string
  final_approved: boolean
  pins: PinAssignment[]
}

export interface PinValidationIssue {
  level: PinValidationLevel
  code: string
  message: string
  pin_name: string | null
  function: PinFunction | null
}

export interface PinValidationResult {
  valid: boolean
  can_apply: boolean
  system_mode: string
  system_armed: boolean
  issues: PinValidationIssue[]
}

export const PIN_FUNCTIONS: PinFunction[] = [
  'UNUSED',
  'PAN_STEP',
  'PAN_DIR',
  'TILT_STEP',
  'TILT_DIR',
  'TRIGGER_SERVO_PWM',
  'ESTOP_IN',
  'LIMIT_LEFT',
  'LIMIT_RIGHT',
  'LIMIT_UP',
  'LIMIT_DOWN',
  'DRIVER_ENABLE',
  'UART_TX',
  'UART_RX',
]
