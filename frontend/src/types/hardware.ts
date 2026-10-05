export type HardwareTransportMode = 'mock' | 'real_readonly' | 'real_command_disabled' | 'real_command'
export type HardwareConnectionState =
  | 'DISCONNECTED'
  | 'PORT_OPEN_NO_TELEMETRY'
  | 'READONLY_CONNECTED_UNVERIFIED'
  | 'PICO_READONLY_VERIFIED'
  | 'PICO_VERIFIED'
  | 'MOCK_READONLY_CONNECTED'
  | 'FAULT'

export interface HardwareSerialPort {
  device: string
  description: string
  hwid: string
  manufacturer: string | null
  is_candidate_pico: boolean
  warning: string | null
}

export interface HardwareTelemetry {
  connection_state: HardwareConnectionState
  transport_mode: HardwareTransportMode
  port: string | null
  baudrate: number
  heartbeat_age_ms: number | null
  device: string | null
  firmware_version: string | null
  estop_state: boolean | null
  driver_enabled: boolean
  pan_position_steps: number
  tilt_position_steps: number
  pan_limit_left: boolean
  pan_limit_right: boolean
  tilt_limit_up: boolean
  tilt_limit_down: boolean
  safe_state: boolean | null
  physical_outputs_enabled: boolean | null
  telemetry_timestamp_ms: number | null
  port_open: boolean
  telemetry_received: boolean
  pico_verified: boolean
  telemetry_firmware_detected: boolean
  physical_commands_disabled: boolean
  last_raw_message: string | null
  last_error: string | null
  parse_errors: string[]
  updated_at: number
  no_physical_command_generated: boolean
}

export interface HardwareStatus {
  connection_state: HardwareConnectionState
  mock_pico_active: boolean
  physical_pico: string
  transport_mode: HardwareTransportMode
  readonly: boolean
  hardware_discovery_enabled: boolean
  physical_command_enabled: boolean
  telemetry_available: boolean
  port_open: boolean
  telemetry_received: boolean
  pico_verified: boolean
  telemetry_firmware_detected: boolean
  physical_commands_disabled: boolean
  transport_source: string
  telemetry: HardwareTelemetry
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface HardwareCapabilities {
  hardware_discovery_enabled: boolean
  allow_real_serial_readonly: boolean
  physical_command_enabled: boolean
  allow_physical_motion: boolean
  allow_physical_fire: boolean
  supported_transport_modes: string[]
  risky_command_blocker_enabled: boolean
  no_physical_command_generated: boolean
}

export interface HardwareConnectResult {
  accepted: boolean
  reason: string
  status: HardwareStatus
  no_physical_command_generated: boolean
}
