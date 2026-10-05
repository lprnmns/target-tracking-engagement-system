import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  clearSerialLogs,
  fetchSerialLogs,
  fetchSerialStatus,
  resetSerialMagazine,
  sendSerialJson,
  simulateSerialRx,
} from '../api/serial'
import type { SerialCommandResult, SerialLogEntry, SerialStatus } from '../types/serial'

const defaultStatus: SerialStatus = {
  connection_state: 'DISCONNECTED',
  transport_mode: 'mock',
  transport_source: 'mock',
  protocol_mode: 'json-line',
  real_serial_enabled: false,
  real_serial_readonly: true,
  readonly: false,
  telemetry_received: false,
  pico_verified: false,
  transport_healthy: false,
  consecutive_ping_failures: 0,
  transport_reconnect_count: 0,
  last_protocol_nack: null,
  last_query_timeout: null,
  last_transport_fault: null,
  physical_command_enabled: false,
  last_tx: null,
  last_rx: null,
  pending_ack_count: 0,
  command_queue_depth: 0,
  last_command_age_ms: null,
  last_command_kind: null,
  last_command_raw: null,
  last_command_ack_state: 'unknown',
  last_command_rtt_ms: null,
  last_command_error: null,
  magazine_capacity: 8,
  magazine_remaining: 8,
  magazine_empty: false,
  magazine_state: 'READY',
  acknowledged_shot_count: 0,
  magazine_reload_count: 0,
  magazine_updated_at: null,
  heartbeat_age_ms: null,
  ack_timeout_ms: 300,
  heartbeat_timeout_ms: 750,
  last_error: 'backend_disconnected',
}

export const useSerialStore = defineStore('serial', () => {
  const status = ref<SerialStatus>(defaultStatus)
  const logs = ref<SerialLogEntry[]>([])
  const lastResult = ref<SerialCommandResult | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextStatus, nextLogs] = await Promise.all([fetchSerialStatus(), fetchSerialLogs()])
      status.value = nextStatus
      logs.value = nextLogs
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Serial refresh failed'
    }
  }

  function applyStatus(nextStatus: SerialStatus): void {
    status.value = nextStatus
  }

  function upsertLog(entry: SerialLogEntry): void {
    logs.value = [entry, ...logs.value.filter((item) => item.id !== entry.id)].slice(0, 200)
  }

  async function send(message: Record<string, unknown>): Promise<void> {
    lastResult.value = await sendSerialJson(message)
    status.value = lastResult.value.status
    if (lastResult.value.log_entry) upsertLog(lastResult.value.log_entry)
  }

  async function simulate(message: Record<string, unknown>): Promise<void> {
    lastResult.value = await simulateSerialRx(message)
    status.value = lastResult.value.status
    if (lastResult.value.log_entry) upsertLog(lastResult.value.log_entry)
  }

  async function clear(): Promise<void> {
    lastResult.value = await clearSerialLogs()
    status.value = lastResult.value.status
    logs.value = lastResult.value.log_entry ? [lastResult.value.log_entry] : []
  }

  async function resetMagazine(capacity?: number): Promise<void> {
    lastResult.value = await resetSerialMagazine(capacity)
    status.value = lastResult.value.status
    if (lastResult.value.log_entry) upsertLog(lastResult.value.log_entry)
  }

  return { status, logs, lastResult, error, refresh, applyStatus, upsertLog, send, simulate, clear, resetMagazine }
})
