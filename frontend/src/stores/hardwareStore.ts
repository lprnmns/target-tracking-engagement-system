import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  connectHardwareReadonly,
  disconnectHardware,
  fetchHardwareCapabilities,
  fetchHardwarePorts,
  fetchHardwareStatus,
} from '../api/hardware'
import type { HardwareCapabilities, HardwareConnectResult, HardwareSerialPort, HardwareStatus, HardwareTelemetry } from '../types/hardware'

const defaultStatus: HardwareStatus = {
  connection_state: 'DISCONNECTED',
  mock_pico_active: true,
  physical_pico: 'disconnected',
  transport_mode: 'mock',
  readonly: false,
  hardware_discovery_enabled: false,
  physical_command_enabled: false,
  telemetry_available: false,
  port_open: false,
  telemetry_received: false,
  pico_verified: false,
  telemetry_firmware_detected: false,
  physical_commands_disabled: true,
  transport_source: 'mock',
  telemetry: {
    connection_state: 'DISCONNECTED',
    transport_mode: 'mock',
    port: null,
    baudrate: 115200,
    heartbeat_age_ms: null,
    device: null,
    firmware_version: null,
    estop_state: null,
    driver_enabled: false,
    pan_position_steps: 0,
    tilt_position_steps: 0,
    pan_limit_left: false,
    pan_limit_right: false,
    tilt_limit_up: false,
    tilt_limit_down: false,
    safe_state: null,
    physical_outputs_enabled: null,
    telemetry_timestamp_ms: null,
    port_open: false,
    telemetry_received: false,
    pico_verified: false,
    telemetry_firmware_detected: false,
    physical_commands_disabled: true,
    last_raw_message: null,
    last_error: 'backend_disconnected',
    parse_errors: [],
    updated_at: 0,
    no_physical_command_generated: true,
  },
  warnings: ['Backend disconnected.'],
  no_physical_command_generated: true,
}

const defaultCapabilities: HardwareCapabilities = {
  hardware_discovery_enabled: false,
  allow_real_serial_readonly: false,
  physical_command_enabled: false,
  allow_physical_motion: false,
  allow_physical_fire: false,
  supported_transport_modes: ['mock', 'real_readonly'],
  risky_command_blocker_enabled: true,
  no_physical_command_generated: true,
}

export const useHardwareStore = defineStore('hardware', () => {
  const status = ref<HardwareStatus>(defaultStatus)
  const ports = ref<HardwareSerialPort[]>([])
  const capabilities = ref<HardwareCapabilities>(defaultCapabilities)
  const lastResult = ref<HardwareConnectResult | null>(null)
  const error = ref<string | null>(null)
  const isReadonlyConnected = computed(() => ['PORT_OPEN_NO_TELEMETRY', 'READONLY_CONNECTED_UNVERIFIED', 'PICO_READONLY_VERIFIED', 'MOCK_READONLY_CONNECTED'].includes(status.value.connection_state))

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextStatus, nextPorts, nextCapabilities] = await Promise.all([
        fetchHardwareStatus(),
        fetchHardwarePorts(),
        fetchHardwareCapabilities(),
      ])
      status.value = nextStatus
      ports.value = nextPorts
      capabilities.value = nextCapabilities
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Hardware refresh failed'
    }
  }

  async function connectReadonly(port: string, baudrate: number): Promise<void> {
    lastResult.value = await connectHardwareReadonly(port, baudrate)
    status.value = lastResult.value.status
    await refresh()
  }

  async function disconnect(): Promise<void> {
    lastResult.value = await disconnectHardware()
    status.value = lastResult.value.status
    await refresh()
  }

  function applyStatus(nextStatus: HardwareStatus): void {
    status.value = nextStatus
  }

  function applyTelemetry(telemetry: HardwareTelemetry): void {
    status.value = { ...status.value, telemetry, connection_state: telemetry.connection_state, transport_mode: telemetry.transport_mode }
  }

  return {
    status,
    ports,
    capabilities,
    lastResult,
    error,
    isReadonlyConnected,
    refresh,
    connectReadonly,
    disconnect,
    applyStatus,
    applyTelemetry,
  }
})
