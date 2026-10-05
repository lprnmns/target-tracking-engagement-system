<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ApiRequestError,
  connectPico,
  connectPicoReadOnly,
  capturePicoReadOnlyEvidence,
  disconnectPico,
  disconnectPicoReadOnly,
  fetchPicoDiscoveryPorts,
  fetchPicoPins,
  fetchPicoPorts,
  fetchPicoReadOnlyEvidence,
  fetchPicoReadOnlyPermissionStatus,
  fetchPicoReadOnlyStatus,
  fetchPicoReadOnlyTelemetry,
  fetchPicoStatus,
  savePicoPins,
  validatePicoPins,
} from '../api/pico'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import PicoBoard from '../components/pico/PicoBoard.vue'
import PinValidationPanel from '../components/pico/PinValidationPanel.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useHardwareStore } from '../stores/hardwareStore'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useSystemStore } from '../stores/systemStore'
import { testServoTune } from '../api/hardware'
import type { PicoDiscoveryPortsResponse, PicoPermissionDiagnosis, PicoPort, PicoReadOnlyEvidence, PicoReadOnlyStatus, PicoReadOnlyTelemetry, PicoStatus, PinAssignment, PinFunction, PinProfile, PinValidationResult } from '../types/pico'
import { PIN_FUNCTIONS } from '../types/pico'

const store = useSystemStore()
const hardware = useHardwareStore()
const deviceRuntime = useDeviceRuntimeStore()
const ports = ref<PicoPort[]>([])
const selectedPort = ref('MOCK_PICO')
const selectedHardwarePort = ref('')
const baudrate = ref(115200)
const status = ref<PicoStatus | null>(null)
const profile = ref<PinProfile | null>(null)
const validation = ref<PinValidationResult | null>(null)
const selectedPinName = ref<string | null>(null)
const loading = ref(false)
const actionError = ref<string | null>(null)
const actionErrorDetail = ref<{ endpoint: string; method: string; status: string; suggestion: string } | null>(null)
const discovery = ref<PicoDiscoveryPortsResponse | null>(null)
const readonlyStatus = ref<PicoReadOnlyStatus | null>(null)
const readonlyTelemetry = ref<PicoReadOnlyTelemetry | null>(null)
const readonlyEvidence = ref<PicoReadOnlyEvidence | null>(null)
const readonlyPermission = ref<PicoPermissionDiagnosis | null>(null)
const selectedReadonlyPort = ref('')
const servoReleaseDeg = ref(0)
const servoFireDeg = ref(175)
const servoPulseS = ref(1.0)
const servoTuneBusy = ref(false)
const servoTuneResult = ref('Hazir')
const servoTuneProgress = ref(0)
const servoTuneStep = ref('Beklemede')
const servoTuneDebug = ref<string[]>(['Hazir'])
let servoTuneTimers: ReturnType<typeof setTimeout>[] = []

const canEditPins = computed(() => store.systemState.mode === 'DISARMED' && !store.systemState.armed)
const hasSelectablePort = computed(() => ports.value.length > 0 && selectedPort.value.length > 0)
const realPorts = computed(() => ports.value.filter((port) => !port.mock))
const hardwareCandidatePorts = computed(() => hardware.ports.filter((port) => port.is_candidate_pico))
const hardwareConnectionLabel = computed(() => {
  if (hardware.status.connection_state === 'MOCK_READONLY_CONNECTED') return 'MOCK_READONLY_CONNECTED'
  if (hardware.status.connection_state === 'PORT_OPEN_NO_TELEMETRY') return 'PORT_OPEN_NO_TELEMETRY'
  if (hardware.status.connection_state === 'READONLY_CONNECTED_UNVERIFIED') return 'READONLY_CONNECTED_UNVERIFIED'
  if (hardware.status.connection_state === 'PICO_READONLY_VERIFIED') return 'PICO_READONLY_VERIFIED'
  return hardware.status.connection_state
})
const physicalOutputsUnexpected = computed(() => hardware.status.telemetry.physical_outputs_enabled === true)
const hasCriticalValidationIssue = computed(() => (validation.value?.issues ?? []).some((issue) => issue.level === 'CRITICAL'))
const canApplyPins = computed(() => Boolean(profile.value && canEditPins.value && validation.value?.valid && validation.value?.can_apply && !hasCriticalValidationIssue.value && !loading.value))
const selectedPin = computed(() => profile.value?.pins.find((pin) => pin.pin_name === selectedPinName.value) ?? null)
const invalidPins = computed(() => {
  const names = new Set<string>()
  for (const issue of validation.value?.issues ?? []) {
    if (issue.pin_name && (issue.level === 'ERROR' || issue.level === 'CRITICAL')) {
      names.add(issue.pin_name)
    }
  }
  return names
})

onMounted(async () => {
  await refreshPico()
})

onBeforeUnmount(() => {
  clearServoTuneTimers()
})

async function refreshPico(): Promise<void> {
  loading.value = true
  actionError.value = null
  actionErrorDetail.value = null
  try {
    const [nextStatus, nextPorts, nextProfile] = await Promise.all([
      fetchPicoStatus(),
      fetchPicoPorts(),
      fetchPicoPins(),
    ])
    status.value = nextStatus
    ports.value = nextPorts
    profile.value = structuredClone(nextProfile)
    selectedPinName.value = nextProfile.pins[0]?.pin_name ?? null
    validation.value = await validatePicoPins(nextProfile)
    await hardware.refresh()
    await deviceRuntime.refresh()
    await refreshReadonly()
    selectedHardwarePort.value = hardware.ports[0]?.device ?? ''
    selectedReadonlyPort.value ||= discovery.value?.ports[0]?.port ?? ''
  } catch (error) {
    setActionError(error, 'Pico API request failed')
  } finally {
    loading.value = false
  }
}

async function refreshReadonly(): Promise<void> {
  const [portsResponse, roStatus, telemetry, evidence, permission] = await Promise.all([
    fetchPicoDiscoveryPorts(),
    fetchPicoReadOnlyStatus(),
    fetchPicoReadOnlyTelemetry(),
    fetchPicoReadOnlyEvidence(),
    fetchPicoReadOnlyPermissionStatus(),
  ])
  discovery.value = portsResponse
  readonlyStatus.value = roStatus
  readonlyTelemetry.value = telemetry
  readonlyEvidence.value = evidence
  readonlyPermission.value = permission
}

async function connectReadonly(): Promise<void> {
  if (!selectedReadonlyPort.value) return
  readonlyStatus.value = await connectPicoReadOnly(selectedReadonlyPort.value, baudrate.value)
  await refreshReadonly()
}

async function disconnectReadonly(): Promise<void> {
  readonlyStatus.value = await disconnectPicoReadOnly()
  await refreshReadonly()
}

async function captureReadonlyEvidence(): Promise<void> {
  readonlyEvidence.value = await capturePicoReadOnlyEvidence()
  await refreshReadonly()
}

async function refreshHardwarePorts(): Promise<void> {
  await hardware.refresh()
  await deviceRuntime.refreshInventory()
  selectedHardwarePort.value ||= hardware.ports[0]?.device ?? ''
}

async function connectHardwareReadonly(): Promise<void> {
  if (!selectedHardwarePort.value) return
  await hardware.connectReadonly(selectedHardwarePort.value, baudrate.value)
}

async function connect(): Promise<void> {
  actionError.value = null
  actionErrorDetail.value = null
  try {
    await connectPico(selectedPort.value, baudrate.value)
    status.value = await fetchPicoStatus()
  } catch (error) {
    setActionError(error, 'Connect failed')
  }
}

async function disconnect(): Promise<void> {
  actionError.value = null
  actionErrorDetail.value = null
  try {
    await disconnectPico()
    status.value = await fetchPicoStatus()
  } catch (error) {
    setActionError(error, 'Disconnect failed')
  }
}

function selectPin(pinName: string): void {
  selectedPinName.value = pinName
}

function updatePinFunction(functionName: PinFunction): void {
  if (!selectedPin.value) return
  selectedPin.value.function = functionName
  selectedPin.value.direction = directionFor(functionName)
  selectedPin.value.mode = modeFor(functionName)
}

async function runValidation(): Promise<void> {
  if (!profile.value) return
  actionError.value = null
  actionErrorDetail.value = null
  try {
    validation.value = await validatePicoPins(profile.value)
  } catch (error) {
    setActionError(error, 'Validation failed')
  }
}

async function applyPins(): Promise<void> {
  if (!profile.value) return
  actionError.value = null
  actionErrorDetail.value = null
  try {
    validation.value = await savePicoPins(profile.value)
    profile.value = await fetchPicoPins()
  } catch (error) {
    setActionError(error, 'Pin update rejected')
  }
}

async function runServoTune(): Promise<void> {
  servoTuneBusy.value = true
  servoTuneProgress.value = 5
  servoTuneStep.value = 'Hazirlaniyor'
  servoTuneDebug.value = []
  addServoTuneDebug(`Başlangıç=${servoReleaseDeg.value}°, bitiş=${servoFireDeg.value}°, süre=${servoPulseS.value}s`)
  scheduleServoTuneProgress()
  actionError.value = null
  actionErrorDetail.value = null
  try {
    const result = await testServoTune({
      release_deg: Number(servoReleaseDeg.value),
      fire_deg: Number(servoFireDeg.value),
      pulse_s: Number(servoPulseS.value),
    })
    clearServoTuneTimers()
    servoTuneProgress.value = 100
    servoTuneStep.value = result.accepted ? 'Tamamlandi' : 'Reddedildi'
    servoTuneResult.value = result.message
    addServoTuneDebug(result.message)
    await hardware.refresh()
  } catch (error) {
    clearServoTuneTimers()
    servoTuneProgress.value = 100
    servoTuneStep.value = 'Hata'
    addServoTuneDebug(error instanceof Error ? error.message : 'Servo tune failed')
    setActionError(error, 'Servo tune failed')
  } finally {
    servoTuneBusy.value = false
  }
}

function addServoTuneDebug(message: string): void {
  const time = new Date().toLocaleTimeString()
  servoTuneDebug.value = [...servoTuneDebug.value, `${time}  ${message}`].slice(-8)
}

function clearServoTuneTimers(): void {
  for (const timer of servoTuneTimers) clearTimeout(timer)
  servoTuneTimers = []
}

function scheduleServoTuneProgress(): void {
  clearServoTuneTimers()
  const pulseMs = Math.max(100, Number(servoPulseS.value) * 1000)
  const steps: Array<[number, number, string]> = [
    [120, 20, 'Motor durdurma komutu'],
    [260, 40, 'Servo derece ayari gonderildi'],
    [420, 60, 'Tetik cekildi'],
    [420 + pulseMs, 82, 'Tetik birakiliyor'],
    [620 + pulseMs, 92, 'Backend sonucu bekleniyor'],
  ]
  for (const [delay, progress, label] of steps) {
    servoTuneTimers.push(setTimeout(() => {
      servoTuneProgress.value = progress
      servoTuneStep.value = label
      addServoTuneDebug(label)
    }, delay))
  }
}

function setActionError(error: unknown, fallback: string): void {
  if (error instanceof ApiRequestError) {
    actionError.value = `${error.method} ${error.endpoint} failed${error.status === null ? '' : ` with status ${error.status}`}.`
    actionErrorDetail.value = {
      endpoint: error.endpoint,
      method: error.method,
      status: error.status === null ? 'network_error' : String(error.status),
      suggestion: error.suggestion,
    }
    return
  }
  actionError.value = error instanceof Error ? error.message : fallback
}

function directionFor(functionName: PinFunction): PinAssignment['direction'] {
  if (['ESTOP_IN', 'LIMIT_LEFT', 'LIMIT_RIGHT', 'LIMIT_UP', 'LIMIT_DOWN'].includes(functionName)) {
    return 'IN'
  }
  if (functionName === 'UNUSED') return 'UNUSED'
  return 'OUT'
}

function modeFor(functionName: PinFunction): PinAssignment['mode'] {
  if (functionName === 'TRIGGER_SERVO_PWM') return 'PWM'
  if (functionName === 'UART_TX' || functionName === 'UART_RX') return 'UART'
  if (functionName === 'UNUSED') return 'UNUSED'
  return 'GPIO'
}

function pinSafetyDetail(pin: PinAssignment): string {
  if (pin.function === 'ESTOP_IN') return 'Safety critical: E-stop input must remain valid.'
  if (pin.function === 'TRIGGER_SERVO_PWM') return 'Trigger/servo output: requires PWM-capable output pin.'
  if (['PAN_STEP', 'PAN_DIR', 'TILT_STEP', 'TILT_DIR', 'DRIVER_ENABLE'].includes(pin.function)) return 'Motion control group: dry-run only in current phase.'
  if (pin.function.startsWith('LIMIT_')) return 'Limit switch safety input.'
  return 'No special safety marker for this assignment.'
}
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Pico Connection" subtitle="Mock Pico remains default">
        <MetricRow label="Physical connection" :value="store.picoTelemetry.connection_status === 'CONNECTED' ? 'connected' : 'not connected'" />
        <MetricRow label="Mock Pico active" :value="status?.mock_mode ?? true" />
        <MetricRow label="Port" :value="store.picoTelemetry.port" />
        <MetricRow label="Baudrate" :value="store.picoTelemetry.baudrate" />
        <MetricRow label="Firmware" :value="store.picoTelemetry.firmware_version" />
        <div class="mt-4 flex flex-wrap gap-2">
          <StatusBadge :label="status?.mock_mode ? 'MOCK DATA' : 'REAL TELEMETRY'" tone="warn" />
          <StatusBadge :label="store.systemState.hardware_enabled ? 'HARDWARE ENABLED' : 'REAL HARDWARE DISABLED'" tone="bad" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="warn" />
        </div>
      </DashboardCard>

      <DashboardCard title="Port Control" subtitle="No physical command is produced">
        <label class="block text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Port</label>
        <select
          v-model="selectedPort"
          class="mt-2 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
        >
          <option v-for="port in ports" :key="port.device" :value="port.device">
            {{ port.label }}
          </option>
        </select>
        <label class="mt-3 block text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Baudrate</label>
        <input
          v-model.number="baudrate"
          type="number"
          class="mt-2 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
        />
        <div class="mt-4 flex gap-2">
          <button
            class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!hasSelectablePort || loading"
            @click="connect"
          >
            Connect Mock
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="disconnect">
            Disconnect
          </button>
        </div>
        <p class="mt-3 text-xs text-slate-400">
          Real ports detected: {{ realPorts.length }}. Mock port is dry-run only.
        </p>
      </DashboardCard>

      <DashboardCard title="Telemetry Detail" subtitle="WebSocket pico.telemetry">
        <MetricRow label="E-stop" :value="store.picoTelemetry.estop_state" />
        <MetricRow label="Driver enabled" :value="store.picoTelemetry.driver_enabled" />
        <MetricRow label="Pan steps" :value="store.picoTelemetry.pan_position_steps" />
        <MetricRow label="Tilt steps" :value="store.picoTelemetry.tilt_position_steps" />
        <MetricRow label="Heartbeat age" :value="store.picoTelemetry.heartbeat_age_ms === null ? 'not available' : `${store.picoTelemetry.heartbeat_age_ms} ms`" />
      </DashboardCard>
    </div>

    <DashboardCard title="Servo Tetik Kalibrasyonu" subtitle="Dereceyi canlı gönder, tetiği test et">
      <div class="grid gap-3 md:grid-cols-3">
        <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
          Başlangıç
          <input v-model.number="servoReleaseDeg" type="number" min="0" max="180" step="1" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" />
        </label>
        <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
          Bitiş
          <input v-model.number="servoFireDeg" type="number" min="0" max="180" step="1" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" />
        </label>
        <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
          Süre
          <input v-model.number="servoPulseS" type="number" min="0.1" max="5" step="0.1" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" />
        </label>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <button class="focus-ring rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50" :disabled="servoTuneBusy" @click="runServoTune">
          Dereceyi Gönder ve Tetiği Çek
        </button>
        <StatusBadge :label="servoTuneBusy ? 'TEST EDILIYOR' : 'HAZIR'" :tone="servoTuneBusy ? 'warn' : 'good'" />
        <span class="text-sm text-slate-300">{{ servoTuneResult }}</span>
      </div>
      <div class="mt-4 grid gap-2">
        <div class="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
          <span>{{ servoTuneStep }}</span>
          <span>{{ servoTuneProgress }}%</span>
        </div>
        <div class="h-2 overflow-hidden rounded-full bg-black/40">
          <div class="h-full rounded-full bg-amber-400 transition-all duration-200" :style="{ width: `${servoTuneProgress}%` }"></div>
        </div>
      </div>
      <div class="mt-4 rounded-md border border-white/10 bg-black/30 p-3">
        <div class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Debug</div>
        <pre class="max-h-36 overflow-auto whitespace-pre-wrap break-words text-xs text-amber-100">{{ servoTuneDebug.join('\n') }}</pre>
      </div>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Read-Only Hardware Discovery" subtitle="RX-only port inventory">
        <div class="flex flex-wrap gap-2">
          <StatusBadge label="NO COMMAND WILL BE SENT" tone="bad" />
          <StatusBadge label="RX ONLY" tone="good" />
          <StatusBadge label="no_physical_command_generated=true" tone="good" />
        </div>
        <MetricRow label="Detected serial ports" :value="discovery?.ports.length ?? 0" />
        <MetricRow label="Recommended candidates" :value="discovery?.candidates_count ?? 0" />
        <select v-model="selectedReadonlyPort" class="mt-3 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
          <option value="">No serial port selected</option>
          <option v-for="port in discovery?.ports ?? []" :key="port.port" :value="port.port">
            {{ port.port }} - {{ port.description }}{{ port.is_candidate ? ' [candidate]' : '' }}
          </option>
        </select>
        <div class="mt-3 max-h-40 overflow-auto rounded-md border border-white/10 bg-black/20 p-2 text-xs text-slate-300">
          <p v-for="port in discovery?.ports ?? []" :key="port.port" class="break-words">
            <strong>{{ port.port }}</strong> · VID={{ port.vid ?? 'n/a' }} PID={{ port.pid ?? 'n/a' }} · {{ port.manufacturer ?? 'unknown' }}
          </p>
          <p v-if="(discovery?.ports.length ?? 0) === 0" class="text-amber-100">No serial port detected.</p>
        </div>
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="refreshReadonly">Refresh read-only ports</button>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" :disabled="!selectedReadonlyPort" @click="connectReadonly">Connect read-only</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Read-Only Connection Status" subtitle="Telemetry RX without TX">
        <MetricRow label="Connected" :value="readonlyStatus?.connected ?? false" />
        <MetricRow label="Selected port" :value="readonlyStatus?.selected_port ?? 'none'" />
        <MetricRow label="Baudrate" :value="readonlyStatus?.baudrate ?? baudrate" />
        <MetricRow label="rx_only" :value="readonlyStatus?.rx_only ?? true" />
        <MetricRow label="tx_disabled" :value="readonlyStatus?.tx_disabled ?? true" />
        <MetricRow label="serial_write_enabled" :value="readonlyStatus?.serial_write_enabled ?? false" />
        <MetricRow label="command_tx_enabled" :value="readonlyStatus?.command_tx_enabled ?? false" />
        <MetricRow label="physical_command_enabled" :value="readonlyStatus?.physical_command_enabled ?? false" />
        <MetricRow label="no_physical_command_generated" :value="readonlyStatus?.no_physical_command_generated ?? true" />
        <button class="focus-ring mt-3 rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="disconnectReadonly">Disconnect read-only</button>
      </DashboardCard>

      <DashboardCard title="Pico Permission Diagnosis" subtitle="Manual host fix guidance only">
        <MetricRow label="Port" :value="readonlyPermission?.port ?? selectedReadonlyPort ?? 'not_available'" />
        <MetricRow label="Status" :value="readonlyPermission?.status ?? 'not_checked'" />
        <MetricRow label="Blocker" :value="readonlyPermission?.blocker_class ?? 'not_checked'" />
        <MetricRow label="User in dialout" :value="readonlyPermission?.user_in_dialout ?? false" />
        <MetricRow label="Device mode" :value="readonlyPermission?.device_mode ?? 'not_available'" />
        <MetricRow label="Device group" :value="readonlyPermission?.device_group ?? 'not_available'" />
        <MetricRow label="serial_write_enabled" :value="readonlyPermission?.serial_write_enabled ?? false" />
        <MetricRow label="command_tx_enabled" :value="readonlyPermission?.command_tx_enabled ?? false" />
        <MetricRow label="no_physical_command_generated" :value="readonlyPermission?.no_physical_command_generated ?? true" />
        <div class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100">
          <p v-for="item in readonlyPermission?.manual_recommendations ?? ['Run permission status after connecting Pico.']" :key="item" class="break-words">{{ item }}</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Latest Read-Only Telemetry" subtitle="Raw RX sample and parsed fields">
        <MetricRow label="Heartbeat" :value="readonlyTelemetry?.heartbeat ?? false" />
        <MetricRow label="Firmware" :value="readonlyTelemetry?.firmware_version ?? 'not_available'" />
        <MetricRow label="E-stop" :value="readonlyTelemetry?.estop_state ?? 'not_available'" />
        <MetricRow label="Parse errors" :value="readonlyStatus?.parse_errors ?? 0" />
        <pre class="mt-3 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(readonlyTelemetry?.parsed ?? { status: 'not_available' }, null, 2) }}</pre>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Hardware Risk Notes" subtitle="Read-only discovery limitations">
        <div class="grid gap-2 text-sm text-slate-300">
          <p>DTR/RTS reset behavior may reboot microcontroller on some adapters.</p>
          <p>This is not motor control.</p>
          <p>This is not direction test.</p>
          <p>This is not fire control.</p>
          <p class="font-mono text-emerald-200">no_physical_command_generated=true</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Capture Read-Only Telemetry Evidence" subtitle="Data Lab / Reports compatible">
        <MetricRow label="Latest evidence" :value="readonlyEvidence?.evidence_id ?? 'none'" />
        <MetricRow label="Evidence status" :value="readonlyEvidence?.status ?? 'not_recorded'" />
        <MetricRow label="Telemetry frames" :value="readonlyEvidence?.status_snapshot.telemetry_frames ?? readonlyStatus?.telemetry_frames ?? 0" />
        <MetricRow label="physical_command_enabled" :value="readonlyEvidence?.physical_command_enabled ?? false" />
        <MetricRow label="serial_write_enabled" :value="readonlyEvidence?.serial_write_enabled ?? false" />
        <MetricRow label="command_tx_enabled" :value="readonlyEvidence?.command_tx_enabled ?? false" />
        <MetricRow label="no_physical_command_generated" :value="readonlyEvidence?.no_physical_command_generated ?? true" />
        <button class="focus-ring mt-3 rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="captureReadonlyEvidence">
          Capture read-only telemetry evidence
        </button>
      </DashboardCard>
    </div>

    <DashboardCard title="Real Hardware Discovery" subtitle="Phase 12 read-only serial telemetry">
      <div class="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div class="grid gap-3">
          <div class="flex flex-wrap gap-2">
            <StatusBadge :label="hardware.status.transport_mode === 'real_readonly' ? 'REAL READ-ONLY' : 'MOCK'" :tone="hardware.status.transport_mode === 'real_readonly' ? 'warn' : 'neutral'" />
            <StatusBadge :label="hardware.status.transport_source === 'real_serial' ? 'REAL SERIAL' : 'MOCK SOURCE'" :tone="hardware.status.transport_source === 'real_serial' ? 'warn' : 'neutral'" />
            <StatusBadge label="PHYSICAL COMMANDS DISABLED" tone="bad" />
            <StatusBadge label="NO STEP/DIR/PWM OUTPUT" tone="bad" />
            <StatusBadge :label="hardware.status.hardware_discovery_enabled ? 'DISCOVERY ENABLED' : 'DISCOVERY DISABLED'" :tone="hardware.status.hardware_discovery_enabled ? 'warn' : 'neutral'" />
          </div>
          <label class="block text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Physical serial port</label>
          <select v-model="selectedHardwarePort" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="">No physical port selected</option>
            <option v-for="port in hardware.ports" :key="port.device" :value="port.device">
              {{ port.device }} - {{ port.description }}{{ port.is_candidate_pico ? ' [Pico candidate]' : '' }}
            </option>
          </select>
          <div v-if="hardware.ports.length === 0 || hardwareCandidatePorts.length === 0" class="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            No Pico candidate found. Connect Pico 2 USB, refresh ports, and keep read-only mode enabled.
          </div>
          <div v-else class="grid gap-2">
            <div
              v-for="port in hardwareCandidatePorts"
              :key="port.device"
              class="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100"
            >
              <div class="flex flex-wrap items-center gap-2">
                <StatusBadge label="PICO CANDIDATE" tone="good" />
                <span class="font-mono">{{ port.device }}</span>
              </div>
              <p class="mt-1 text-xs text-emerald-100/80">{{ port.description }} · {{ port.hwid }}</p>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="refreshHardwarePorts">
              Refresh Ports
            </button>
            <button
              class="focus-ring rounded-md bg-amber-400 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!selectedHardwarePort || !hardware.capabilities.allow_real_serial_readonly"
              @click="connectHardwareReadonly"
            >
              Connect Read-Only
            </button>
            <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="hardware.disconnect">
              Disconnect Read-Only
            </button>
          </div>
          <p class="text-xs text-slate-400">
            Candidate Pico ports: {{ hardwareCandidatePorts.length }}. Read-only connect opens serial for telemetry only and sends no DISARM, motor, servo or fire command.
          </p>
          <div class="rounded-md border border-white/10 bg-black/18 p-3">
            <p class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Device Manager Pico Candidates</p>
            <div v-if="deviceRuntime.inventory.pico_candidates.length" class="grid gap-2">
              <div v-for="candidate in deviceRuntime.inventory.pico_candidates" :key="candidate.device_id" class="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge label="CANDIDATE" tone="good" />
                  <StatusBadge :label="`score ${candidate.candidate_score}`" tone="neutral" />
                  <span class="font-mono">{{ candidate.device_path }}</span>
                </div>
                <p class="mt-1 text-xs text-emerald-100/80">{{ candidate.description }} · verified only after telemetry-only firmware reports device=pico2.</p>
              </div>
            </div>
            <p v-else class="text-sm text-amber-100">No Pico candidate from Device Manager. Candidate is not the same as verified Pico.</p>
          </div>
          <div v-if="physicalOutputsUnexpected" class="rounded-md border border-red-400/40 bg-red-500/10 px-3 py-2 text-sm font-semibold text-red-100">
            Unexpected physical output enabled flag from firmware. This phase cannot be considered ready.
          </div>
          <p v-if="hardware.lastResult" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm text-slate-200">
            {{ hardware.lastResult.accepted ? 'Accepted' : 'Rejected' }}: {{ hardware.lastResult.reason }}
          </p>
          <p v-if="hardware.error" class="rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">
            {{ hardware.error }}
          </p>
        </div>

        <div class="grid gap-2">
          <MetricRow label="Selected port" :value="hardware.status.telemetry.port ?? selectedHardwarePort ?? 'none'" />
          <MetricRow label="Port open" :value="hardware.status.port_open" />
          <MetricRow label="Telemetry received" :value="hardware.status.telemetry_received" />
          <MetricRow label="Pico verified" :value="hardware.status.pico_verified" />
          <MetricRow label="Physical Pico" :value="hardware.status.physical_pico" />
          <MetricRow label="Connection" :value="hardwareConnectionLabel" />
          <MetricRow label="Firmware" :value="hardware.status.telemetry.firmware_version ?? 'not available'" />
          <MetricRow label="Safe state" :value="hardware.status.telemetry.safe_state === null ? 'unknown' : hardware.status.telemetry.safe_state" />
          <MetricRow label="Physical outputs enabled" :value="hardware.status.telemetry.physical_outputs_enabled === null ? 'not reported' : hardware.status.telemetry.physical_outputs_enabled" />
          <MetricRow label="Heartbeat age" :value="hardware.status.telemetry.heartbeat_age_ms === null ? 'not available' : `${hardware.status.telemetry.heartbeat_age_ms} ms`" />
          <MetricRow label="Last raw message" :value="hardware.status.telemetry.last_raw_message ?? 'none'" />
          <MetricRow label="Parse errors" :value="hardware.status.telemetry.parse_errors.length" />
          <div v-if="hardware.status.warnings.length" class="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            <p v-for="warning in hardware.status.warnings" :key="warning">{{ warning }}</p>
          </div>
        </div>
      </div>
    </DashboardCard>

    <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
      Pin degisikligi icin sistem DISARMED olmalidir. Placeholder profil final/onayli pinout degildir.
    </div>

    <div v-if="actionError" class="rounded-md border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-100">
      <p class="font-semibold">{{ actionError }}</p>
      <div v-if="actionErrorDetail" class="mt-2 grid gap-1 text-xs text-red-100/90 sm:grid-cols-4">
        <span>Endpoint: {{ actionErrorDetail.endpoint }}</span>
        <span>Method: {{ actionErrorDetail.method }}</span>
        <span>Status: {{ actionErrorDetail.status }}</span>
        <span>{{ actionErrorDetail.suggestion }}</span>
      </div>
    </div>

    <div v-if="profile" class="grid gap-4 2xl:grid-cols-[1.2fr_0.8fr]">
      <PicoBoard
        :pins="profile.pins"
        :selected-pin-name="selectedPinName"
        :invalid-pins="invalidPins"
        @select="selectPin"
      />

      <section class="rounded-md border border-white/10 bg-[#14181d] p-4">
        <div class="mb-4">
          <h3 class="text-base font-semibold text-white">Pin Detail</h3>
          <p class="mt-1 text-xs text-slate-400">Preview changes before backend validation</p>
        </div>

        <div v-if="selectedPin" class="grid gap-3">
          <MetricRow label="Pin" :value="selectedPin.pin_name" />
          <MetricRow label="Physical pin" :value="selectedPin.physical_pin" />
          <MetricRow label="PWM capable" :value="selectedPin.pwm_capable" />
          <MetricRow label="UART capable" :value="selectedPin.uart_capable" />
          <label class="block text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Function</label>
          <select
            :value="selectedPin.function"
            :disabled="!canEditPins"
            class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
            @change="updatePinFunction(($event.target as HTMLSelectElement).value as PinFunction)"
          >
            <option v-for="functionName in PIN_FUNCTIONS" :key="functionName" :value="functionName">
              {{ functionName }}
            </option>
          </select>
          <MetricRow label="Direction" :value="selectedPin.direction" />
          <MetricRow label="Mode" :value="selectedPin.mode" />
          <MetricRow label="Safety critical" :value="pinSafetyDetail(selectedPin)" />
          <MetricRow label="Capabilities" :value="`PWM=${selectedPin.pwm_capable}, UART=${selectedPin.uart_capable}`" />
        </div>
      </section>
    </div>

    <PinValidationPanel :result="validation" />

    <div class="flex flex-wrap gap-2">
      <button
        class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="!profile || loading"
        @click="runValidation"
      >
        Validate Preview
      </button>
      <button
        class="focus-ring rounded-md px-3 py-2 text-sm font-semibold"
        :class="canApplyPins ? 'bg-emerald-500 text-slate-950' : 'cursor-not-allowed border border-white/10 bg-slate-700 text-slate-400 opacity-70'"
        :disabled="!canApplyPins"
        @click="applyPins"
      >
        Apply / Save
      </button>
      <p v-if="!canApplyPins" class="w-full text-sm text-amber-200">
        Pin changes require DISARMED mode and a valid profile.
      </p>
    </div>

    <DashboardCard v-if="profile" title="Pin Assignment Table" :subtitle="profile.note">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[760px] text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2">Pin</th>
              <th class="py-2">Physical</th>
              <th class="py-2">Function</th>
              <th class="py-2">Direction</th>
              <th class="py-2">Mode</th>
              <th class="py-2">PWM</th>
              <th class="py-2">UART</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="pin in profile.pins"
              :key="pin.pin_name"
              class="cursor-pointer border-t border-white/8 hover:bg-white/5"
              :class="{ 'bg-cyan-400/8': selectedPinName === pin.pin_name }"
              @click="selectPin(pin.pin_name)"
            >
              <td class="py-2 font-mono text-cyan-100">{{ pin.pin_name }}</td>
              <td class="py-2 text-slate-300">{{ pin.physical_pin }}</td>
              <td class="py-2 text-slate-100">{{ pin.function }}</td>
              <td class="py-2 text-slate-300">{{ pin.direction }}</td>
              <td class="py-2 text-slate-300">{{ pin.mode }}</td>
              <td class="py-2 text-slate-300">{{ pin.pwm_capable }}</td>
              <td class="py-2 text-slate-300">{{ pin.uart_capable }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </DashboardCard>
  </div>
</template>
