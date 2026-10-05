<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useHardwareStore } from '../stores/hardwareStore'
import { useSerialStore } from '../stores/serialStore'

const store = useSerialStore()
const hardware = useHardwareStore()
const txType = ref('heartbeat')
const txSeq = ref(1)
const manualSeq = ref(false)
const txReason = ref('operator_request')
const selfTestName = ref('pico_status')
const modeName = ref('standby')
const rxType = ref('ack')
const rxSeq = ref(1)
const rxReason = ref('simulated')
const rxErrorCode = ref('MOCK_ERROR')
const usedTxSeqs = computed(() => new Set(store.logs.map((entry) => {
  const message = entry.message as { seq?: number } | null
  return Number(message?.seq)
}).filter((seq) => Number.isFinite(seq))))
const nextSeq = computed(() => Math.max(0, ...Array.from(usedTxSeqs.value)) + 1)
const duplicateSeqWarning = computed(() => manualSeq.value && usedTxSeqs.value.has(txSeq.value))
const readOnlyStates = ['PORT_OPEN_NO_TELEMETRY', 'READONLY_CONNECTED_UNVERIFIED', 'PICO_READONLY_VERIFIED', 'MOCK_READONLY_CONNECTED']
const isRealReadonly = computed(() => store.status.transport_mode === 'real_readonly' || readOnlyStates.includes(store.status.connection_state))
const connectionLabel = computed(() => {
  if (store.status.connection_state === 'MOCK_READONLY_CONNECTED') return 'MOCK_READONLY_CONNECTED'
  if (store.status.connection_state === 'PORT_OPEN_NO_TELEMETRY') return 'PORT_OPEN_NO_TELEMETRY'
  if (store.status.connection_state === 'PICO_READONLY_VERIFIED') return 'PICO_READONLY_VERIFIED'
  if (store.status.connection_state === 'READONLY_CONNECTED_UNVERIFIED') return 'READONLY_CONNECTED_UNVERIFIED'
  return store.status.connection_state
})

const safeMessage = computed<Record<string, unknown>>(() => {
  const seq = manualSeq.value ? txSeq.value : nextSeq.value
  if (txType.value === 'heartbeat') {
    return { type: 'heartbeat', seq, timestamp_ms: Date.now() }
  }
  if (txType.value === 'disarm') {
    return { type: 'disarm', seq, reason: txReason.value }
  }
  if (txType.value === 'self_test') {
    return { type: 'self_test', seq, test: selfTestName.value }
  }
  return { type: 'set_mode', seq, mode: modeName.value }
})

const rxMessage = computed<Record<string, unknown>>(() => {
  if (rxType.value === 'ack') {
    return { type: 'ack', seq: rxSeq.value, accepted: true }
  }
  if (rxType.value === 'nack') {
    return { type: 'nack', seq: rxSeq.value, reason: rxReason.value }
  }
  if (rxType.value === 'heartbeat') {
    return { type: 'heartbeat', seq: rxSeq.value, timestamp_ms: Date.now() }
  }
  if (rxType.value === 'error') {
    return { type: 'error', seq: rxSeq.value, code: rxErrorCode.value, message: rxReason.value }
  }
  return {
    type: 'telemetry',
    seq: rxSeq.value,
    estop_state: false,
    driver_enabled: false,
    pan_position_steps: 0,
    tilt_position_steps: 0,
    last_error: null,
  }
})

onMounted(() => {
  void store.refresh()
  void hardware.refresh()
})

function toneFor(kind: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (kind === 'rx' || kind === 'ack') return 'good'
  if (kind === 'tx') return 'neutral'
  if (kind === 'timeout') return 'warn'
  return 'bad'
}

async function sendSafe(): Promise<void> {
  await store.send(safeMessage.value)
  if (!manualSeq.value) txSeq.value = nextSeq.value
}
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Serial Status" subtitle="Mock or real read-only telemetry">
        <MetricRow label="Connection" :value="connectionLabel" />
        <MetricRow label="Transport" :value="isRealReadonly ? 'REAL READ-ONLY' : store.status.transport_mode" />
        <MetricRow label="Transport source" :value="store.status.transport_source" />
        <MetricRow label="Protocol" :value="store.status.protocol_mode" />
        <MetricRow label="Real serial" :value="store.status.real_serial_enabled" />
        <MetricRow label="Read-only" :value="store.status.readonly || store.status.real_serial_readonly" />
        <MetricRow label="Pico verified" :value="store.status.pico_verified || hardware.status.pico_verified" />
        <MetricRow label="Telemetry received" :value="store.status.telemetry_received || hardware.status.telemetry_received" />
        <MetricRow label="Physical commands" :value="store.status.physical_command_enabled ? 'enabled' : 'disabled'" />
        <MetricRow label="Last error" :value="store.status.last_error" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="store.status.connection_state === 'MOCK_READONLY_CONNECTED' ? 'MOCK READ-ONLY' : (isRealReadonly ? 'REAL READ-ONLY' : 'MOCK')" :tone="isRealReadonly ? 'warn' : 'neutral'" />
          <StatusBadge label="PHYSICAL COMMANDS DISABLED" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="ACK / Heartbeat" subtitle="Timeout supervision">
        <MetricRow label="Pending ACK" :value="store.status.pending_ack_count" />
        <MetricRow label="ACK timeout" :value="`${store.status.ack_timeout_ms} ms`" />
        <MetricRow label="Heartbeat timeout" :value="`${store.status.heartbeat_timeout_ms} ms`" />
        <MetricRow label="Heartbeat age" :value="store.status.heartbeat_age_ms === null ? 'none' : `${store.status.heartbeat_age_ms} ms`" />
        <MetricRow label="Last raw telemetry" :value="hardware.status.telemetry.last_raw_message ?? 'none'" />
        <MetricRow label="Parse errors" :value="hardware.status.telemetry.parse_errors.length" />
      </DashboardCard>

      <DashboardCard title="Safety Boundary" subtitle="Risky TX disabled">
        <p class="text-sm text-slate-300">
          Fire, motor and servo messages are schema-only in this phase and are rejected before transport.
        </p>
        <div class="mt-4 flex flex-wrap gap-2">
          <StatusBadge label="DISARMED DEFAULT" tone="bad" />
          <StatusBadge label="DRY RUN" tone="warn" />
          <StatusBadge :label="isRealReadonly ? 'REAL SERIAL READ-ONLY' : 'REAL SERIAL OFF'" tone="bad" />
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Safe Message Sender" subtitle="Allowlisted JSON-line TX">
        <div class="grid gap-3">
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Message type</label>
          <select v-model="txType" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="heartbeat">heartbeat</option>
            <option value="disarm">disarm</option>
            <option value="self_test">self_test</option>
            <option value="set_mode">set_mode</option>
          </select>
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Seq</label>
          <div class="grid gap-2 md:grid-cols-[1fr_auto]">
            <input v-model.number="txSeq" type="number" :disabled="!manualSeq" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white disabled:opacity-50" />
            <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="manualSeq" type="checkbox" /> Manual seq</label>
          </div>
          <div class="flex flex-wrap gap-2">
            <StatusBadge :label="`Next seq ${nextSeq}`" tone="neutral" />
            <StatusBadge v-if="duplicateSeqWarning" label="Duplicate seq warning" tone="warn" />
          </div>
          <input v-if="txType === 'disarm'" v-model="txReason" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-if="txType === 'self_test'" v-model="selfTestName" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-if="txType === 'set_mode'" v-model="modeName" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <pre class="overflow-auto rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(safeMessage, null, 2) }}</pre>
          <div v-if="isRealReadonly" class="rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">
            Real read-only mode forbids TX in Phase 12. Diagnostics are display-only; even DISARM is not sent automatically.
          </div>
          <button
            class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="isRealReadonly"
            @click="sendSafe"
          >
            {{ isRealReadonly ? 'TX Disabled in Read-Only' : 'Send Safe JSON' }}
          </button>
          <div v-if="store.lastResult && !store.lastResult.accepted" class="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            {{ store.lastResult.reason }}
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Simulate RX" subtitle="Mock/test input only">
        <div class="grid gap-3">
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">RX type</label>
          <select v-model="rxType" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="ack">ack</option>
            <option value="nack">nack</option>
            <option value="telemetry">telemetry</option>
            <option value="heartbeat">heartbeat</option>
            <option value="error">error</option>
          </select>
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Seq</label>
          <input v-model.number="rxSeq" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-if="rxType === 'nack' || rxType === 'error'" v-model="rxReason" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-if="rxType === 'error'" v-model="rxErrorCode" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <pre class="overflow-auto rounded-md bg-black/30 p-3 text-xs text-emerald-100">{{ JSON.stringify(rxMessage, null, 2) }}</pre>
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="store.simulate(rxMessage)">
            Simulate RX
          </button>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Serial Log" subtitle="TX/RX/ACK/NACK/timeout/error timeline">
      <div class="mb-3 flex flex-wrap gap-2">
        <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="store.refresh">
          Refresh
        </button>
        <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="store.clear">
          Clear Logs
        </button>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[920px] text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2">ID</th>
              <th class="py-2">Kind</th>
              <th class="py-2">Direction</th>
              <th class="py-2">Message</th>
              <th class="py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in store.logs" :key="entry.id" class="border-t border-white/8">
              <td class="py-2 font-mono text-slate-500">#{{ entry.id }}</td>
              <td class="py-2"><StatusBadge :label="entry.kind" :tone="toneFor(entry.kind)" /></td>
              <td class="py-2 text-slate-300">{{ entry.direction }}</td>
              <td class="py-2 font-mono text-xs text-slate-200">{{ JSON.stringify(entry.message) }}</td>
              <td class="py-2 text-red-200">{{ entry.error }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="store.logs.length === 0" class="py-4 text-sm text-slate-400">No serial logs yet.</p>
      </div>
    </DashboardCard>
  </div>
</template>
