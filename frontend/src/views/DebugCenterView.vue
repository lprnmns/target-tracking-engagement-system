<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useRuntimeTruth, type TruthTone } from '../composables/useRuntimeTruth'
import { fetchPicoProtocolStatus } from '../api/pico'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import type { PicoProtocolStatus } from '../types/pico'

type PerfMetric = { value: number | null, unit: string, tone: TruthTone, label: string, green_max: number, yellow_max: number }
type PerfStatus = {
  cpu_percent: number | null
  process_cpu_percent: number | null
  memory_percent: number | null
  gpu_util_percent: number | null
  camera_frame_age_ms: number | null
  inference_ms: number | null
  tracking_loop_ms: number | null
  serial_ack_rtt_ms: number | null
  pico_heartbeat_age_ms: number | null
  total_pipeline_ms: number | null
  serial_queue_depth: number
  primary_bottleneck: string
  bottleneck_summary: string
  recommended_actions: string[]
  metrics: Record<string, PerfMetric>
}

const system = useSystemStore()
const vision = useVisionStore()
const runtime = useDeviceRuntimeStore()
const hardware = useHardwareStore()
const serial = useSerialStore()
const motion = useMotionStore()
const truth = useRuntimeTruth()
const protocolStatus = ref<PicoProtocolStatus | null>(null)
const protocolError = ref<string | null>(null)

const performanceStatus = computed(() => (
  system.latestEvents.find((event) => event.type === 'performance.status')?.payload as PerfStatus | undefined
))

const flowNodes = computed(() => [
  { label: 'Kamera', value: truth.cameraLabel.value, detail: runtime.cameraStatus.selected_camera, tone: truth.cameraTone.value },
  { label: 'Vision', value: runtime.visionStatus.effective_adapter || vision.visionStatus.detector_kind || 'n/a', detail: `${vision.visionStatus.balloon_count} hedef`, tone: runtime.visionStatus.production_yolo_loaded ? 'good' : truth.testVisionActive.value ? 'warn' : 'bad' },
  { label: 'Tracker', value: motion.trackingStatus.state, detail: `${motion.trackingStatus.max_speed} max`, tone: truth.trackingTone.value },
  { label: 'Serial', value: `${serial.status.command_queue_depth} queue`, detail: serial.status.last_command_ack_state, tone: truth.commandLineTone.value },
  { label: 'Pico', value: truth.picoLabel.value, detail: hardware.status.connection_state, tone: truth.picoTone.value },
  { label: 'Motor', value: hardware.status.telemetry.driver_enabled ? 'driver on' : 'driver off', detail: `X ${motion.trackingUpdate?.speed_x ?? 0} / Y ${motion.trackingUpdate?.speed_y ?? 0}`, tone: truth.picoHealthy.value ? 'warn' : 'bad' },
  { label: 'Servo', value: truth.magazineLabel.value, detail: truth.fireLabel.value, tone: truth.fireTone.value },
] satisfies Array<{ label: string, value: string, detail: string, tone: TruthTone }>)

const metricRows = computed(() => [
  ['camera_frame_age', 'Kamera frame'],
  ['yolo_inference', 'YOLO inference'],
  ['tracking_loop', 'PID loop'],
  ['serial_ack', 'Serial ACK'],
  ['pico_heartbeat', 'Pico heartbeat'],
  ['tx_queue', 'TX queue'],
  ['total_pipeline', 'Toplam pipeline'],
].map(([key, fallback]) => {
  const metric = performanceStatus.value?.metrics?.[key]
  return {
    key,
    label: metric?.label ?? fallback,
    value: metric?.value ?? null,
    unit: metric?.unit ?? '',
    tone: metric?.tone ?? 'neutral',
    green: metric?.green_max ?? null,
    yellow: metric?.yellow_max ?? null,
  }
}))

function toneClass(tone: TruthTone): string {
  if (tone === 'good') return 'border-emerald-400/35 bg-emerald-400/10 text-emerald-200'
  if (tone === 'warn') return 'border-amber-400/35 bg-amber-400/10 text-amber-200'
  if (tone === 'bad') return 'border-red-400/35 bg-red-400/10 text-red-200'
  return 'border-slate-500/35 bg-slate-500/10 text-slate-200'
}

function display(value: number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined) return 'n/a'
  return `${Math.round(value * 10) / 10}${suffix}`
}

async function refreshProtocol(): Promise<void> {
  protocolError.value = null
  try {
    protocolStatus.value = await fetchPicoProtocolStatus()
  } catch (error) {
    protocolError.value = error instanceof Error ? error.message : 'Protocol status unavailable'
  }
}

onMounted(() => {
  void Promise.all([
    vision.refresh(),
    runtime.refresh(),
    hardware.refresh(),
    serial.refresh(),
    motion.refresh(),
    motion.refreshTrackingStatus(),
    refreshProtocol(),
  ])
})
</script>

<template>
  <div class="grid gap-4">
    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 class="text-lg font-semibold text-white">Canlı Sağlık Merkezi</h3>
          <p class="text-sm text-slate-500">Backend, kamera, Pico, YOLO, serial ve fire gate otomatik değerlendirilir.</p>
        </div>
        <StatusBadge :label="truth.overallLabel.value" :tone="truth.overallTone.value" />
      </div>
      <div class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="issue in truth.healthIssues.value"
          :key="issue.id"
          class="rounded-md border p-3"
          :class="toneClass(issue.tone)"
        >
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] opacity-70">{{ issue.area }}</p>
          <p class="mt-2 text-sm font-semibold">{{ issue.label }}</p>
          <p class="mt-1 text-xs opacity-80">{{ issue.detail }}</p>
        </div>
        <div v-if="truth.healthIssues.value.length === 0" class="rounded-md border border-emerald-400/35 bg-emerald-400/10 p-3 text-emerald-100">
          <p class="text-sm font-semibold">Canlı sağlık temiz</p>
          <p class="mt-1 text-xs opacity-80">Cihaz ve pipeline tarafında uyarı yok.</p>
        </div>
      </div>
    </section>

    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 class="text-lg font-semibold text-white">Canlı Akış Haritası</h3>
          <p class="text-sm text-slate-500">Kamera → vision → tracker → serial → Pico → aktüatörler</p>
        </div>
        <StatusBadge :label="performanceStatus?.primary_bottleneck ?? 'waiting'" :tone="performanceStatus?.primary_bottleneck === 'none' ? 'good' : 'warn'" />
      </div>
      <div class="grid gap-2 md:grid-cols-7">
        <div v-for="node in flowNodes" :key="node.label" class="rounded-md border p-3" :class="toneClass(node.tone)">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] opacity-70">{{ node.label }}</p>
          <p class="mt-2 text-sm font-semibold">{{ node.value }}</p>
          <p class="mt-1 truncate font-mono text-xs opacity-80">{{ node.detail }}</p>
        </div>
      </div>
    </section>

    <section class="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <div class="rounded-md border border-white/10 bg-[#10161d] p-4">
        <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="text-lg font-semibold text-white">Latency ve Darboğaz</h3>
            <p class="text-sm text-slate-500">{{ performanceStatus?.bottleneck_summary ?? 'Performans metriği bekleniyor.' }}</p>
          </div>
          <StatusBadge :label="`Toplam ${display(performanceStatus?.total_pipeline_ms, ' ms')}`" :tone="performanceStatus?.metrics?.total_pipeline?.tone ?? 'neutral'" />
        </div>
        <div class="grid gap-2 md:grid-cols-2">
          <div v-for="row in metricRows" :key="row.key" class="rounded-md border border-white/10 bg-black/20 p-3">
            <div class="flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-white">{{ row.label }}</span>
              <StatusBadge :label="`${display(row.value)} ${row.unit}`" :tone="row.tone" />
            </div>
            <p class="mt-2 text-xs text-slate-500">Yeşil ≤ {{ row.green ?? 'n/a' }}, sarı ≤ {{ row.yellow ?? 'n/a' }} {{ row.unit }}</p>
          </div>
        </div>
      </div>

      <div class="rounded-md border border-white/10 bg-[#10161d] p-4">
        <h3 class="text-lg font-semibold text-white">PC ve Komut Hattı</h3>
        <div class="mt-4 grid gap-2 text-sm">
          <div class="flex justify-between gap-3"><span class="text-slate-500">CPU</span><span class="font-mono">{{ display(performanceStatus?.cpu_percent, '%') }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Backend CPU</span><span class="font-mono">{{ display(performanceStatus?.process_cpu_percent, '%') }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">RAM</span><span class="font-mono">{{ display(performanceStatus?.memory_percent, '%') }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">GPU</span><span class="font-mono">{{ display(performanceStatus?.gpu_util_percent, '%') }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Pending ACK</span><span class="font-mono">{{ serial.status.pending_ack_count }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Son ACK</span><span class="font-mono">{{ serial.status.last_command_ack_state }}</span></div>
        </div>
        <div class="mt-4 grid gap-2">
          <div v-for="action in performanceStatus?.recommended_actions ?? ['Metrik bekleniyor.']" :key="action" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-xs text-slate-300">
            {{ action }}
          </div>
        </div>
      </div>
    </section>

    <section class="rounded-md border border-cyan-400/15 bg-[#10161d] p-4">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 class="text-lg font-semibold text-white">Telemetry Protocol</h3>
          <p class="text-sm text-slate-500">ISTIKLAL Serial Packet Protocol v1 · RX/read-only foundation</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <StatusBadge :label="protocolStatus?.protocol_name ?? 'protocol waiting'" :tone="protocolStatus ? 'good' : 'neutral'" />
          <StatusBadge :label="protocolStatus?.physical_tx_disabled ? 'PHYSICAL TX DISABLED' : 'TX CHECK'" :tone="protocolStatus?.physical_tx_disabled ? 'good' : 'bad'" />
          <StatusBadge :label="protocolStatus?.latest_telemetry.pose_source ? `POSE ${protocolStatus.latest_telemetry.pose_source}` : 'POSE n/a'" :tone="protocolStatus?.latest_telemetry.pose_source === 'telemetry' ? 'good' : 'warn'" />
        </div>
      </div>
      <div v-if="protocolError" class="mb-3 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">
        {{ protocolError }}
      </div>
      <div class="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
        <div class="rounded-md border border-white/10 bg-black/20 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Pico port</p>
          <p class="mt-2 truncate font-mono text-sm text-slate-100">{{ protocolStatus?.selected_port ?? 'none' }}</p>
        </div>
        <div class="rounded-md border border-white/10 bg-black/20 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Version</p>
          <p class="mt-2 font-mono text-sm text-slate-100">v{{ protocolStatus?.protocol_version ?? 1 }}</p>
        </div>
        <div class="rounded-md border border-white/10 bg-black/20 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Heartbeat</p>
          <p class="mt-2 font-mono text-sm text-slate-100">{{ display(protocolStatus?.latest_telemetry.last_heartbeat_age_ms, ' ms') }}</p>
        </div>
        <div class="rounded-md border border-white/10 bg-black/20 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Parse</p>
          <p class="mt-2 font-mono text-sm text-slate-100">{{ protocolStatus?.packet_parse_status ?? 'no_packet' }}</p>
        </div>
        <div class="rounded-md border border-white/10 bg-black/20 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">CRC</p>
          <p class="mt-2 font-mono text-sm" :class="protocolStatus?.crc_status === 'passed' ? 'text-emerald-300' : 'text-slate-100'">{{ protocolStatus?.crc_status ?? 'not_checked' }}</p>
        </div>
        <div class="rounded-md border border-emerald-400/20 bg-emerald-400/8 p-3">
          <p class="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">Safety</p>
          <p class="mt-2 font-mono text-xs text-emerald-100">no_physical_command_generated={{ protocolStatus?.no_physical_command_generated === true }}</p>
        </div>
      </div>
    </section>

    <section class="grid gap-4 xl:grid-cols-2">
      <div class="rounded-md border border-white/10 bg-[#10161d] p-4">
        <h3 class="text-lg font-semibold text-white">Device Truth</h3>
        <div class="mt-4 grid gap-2 text-sm">
          <div class="flex justify-between gap-3"><span class="text-slate-500">Runtime camera</span><span class="text-right font-mono">{{ runtime.cameraStatus.selected_camera }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Inventory match</span><span class="text-right font-mono">{{ truth.selectedPhysicalCamera.value?.device_path ?? 'none' }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Camera API</span><span class="text-right font-mono">{{ vision.cameraStatus.connected }} / {{ vision.cameraStatus.running }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Pico port</span><span class="text-right font-mono">{{ hardware.status.telemetry.port ?? 'none' }}</span></div>
          <div class="flex justify-between gap-3"><span class="text-slate-500">Pico firmware</span><span class="text-right font-mono">{{ hardware.status.telemetry.firmware_version ?? 'n/a' }}</span></div>
        </div>
      </div>

      <div class="rounded-md border border-white/10 bg-[#10161d] p-4">
        <h3 class="text-lg font-semibold text-white">Serial Log</h3>
        <div class="mt-4 max-h-[300px] overflow-auto rounded-md border border-white/10 bg-black/20">
          <div v-for="entry in serial.logs.slice(0, 12)" :key="entry.id" class="border-t border-white/8 px-3 py-2 text-xs first:border-t-0">
            <div class="flex items-center justify-between gap-3">
              <span class="font-semibold uppercase tracking-wide" :class="{ 'text-emerald-300': entry.kind === 'ack' || entry.kind === 'rx', 'text-red-300': entry.kind === 'error' || entry.kind === 'timeout' || entry.kind === 'nack', 'text-slate-300': entry.kind === 'tx' || entry.kind === 'status' }">{{ entry.kind }}</span>
              <span class="font-mono text-slate-500">{{ new Date(entry.ts * 1000).toLocaleTimeString() }}</span>
            </div>
            <p class="mt-1 truncate font-mono text-slate-300">{{ entry.raw ?? JSON.stringify(entry.message) }}</p>
            <p v-if="entry.error" class="mt-1 text-red-300">{{ entry.error }}</p>
          </div>
          <div v-if="serial.logs.length === 0" class="px-3 py-6 text-center text-sm text-slate-500">Log yok.</div>
        </div>
      </div>
    </section>
  </div>
</template>
