<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import { useRuntimeTruth } from '../composables/useRuntimeTruth'

type Tone = 'good' | 'warn' | 'bad' | 'neutral'

interface FlowNode {
  id: string
  label: string
  subtitle: string
  x: number
  y: number
  tone: Tone
  value: string
}

interface FlowEdge {
  from: FlowNode
  to: FlowNode
  label: string
  tone: Tone
  value: string
}

const system = useSystemStore()
const vision = useVisionStore()
const serial = useSerialStore()
const hardware = useHardwareStore()
const motion = useMotionStore()
const runtime = useDeviceRuntimeStore()
const truth = useRuntimeTruth()

const performance = computed(() => system.latestEvents.find((event) => event.type === 'performance.status')?.payload as any | undefined)

function toneFromMetric(key: string): Tone {
  const tone = performance.value?.metrics?.[key]?.tone
  return tone === 'good' || tone === 'warn' || tone === 'bad' ? tone : 'neutral'
}

const selectedPhysicalCamera = computed(() => {
  const profile = runtime.cameraStatus.profile
  return runtime.inventory.cameras.find((camera) => (
    (profile.device_path && camera.device_path === profile.device_path)
    || (profile.stable_path && camera.stable_path === profile.stable_path)
    || (profile.device_id && camera.device_id === profile.device_id)
  )) ?? null
})
const cameraProfileIsMock = computed(() => runtime.cameraStatus.profile.source_type === 'mock')
const physicalCameraConnected = computed(() => (
  !cameraProfileIsMock.value
  && !!selectedPhysicalCamera.value
  && selectedPhysicalCamera.value.connected
))
const testVisionActive = computed(() => (
  cameraProfileIsMock.value
  || runtime.visionStatus.test_adapter_active
  || !!runtime.visionStatus.surrogate_source_kind
))
const realCameraStreamHealthy = computed(() => (
  physicalCameraConnected.value
  && runtime.cameraStatus.running
  && vision.cameraStatus.running
  && vision.cameraStatus.connected
))
const cameraTone = computed<Tone>(() => {
  if (realCameraStreamHealthy.value && toneFromMetric('camera_frame_age') !== 'bad') return 'good'
  if (physicalCameraConnected.value || testVisionActive.value) return 'warn'
  return 'bad'
})
const visionTone = computed<Tone>(() => {
  if (runtime.visionStatus.production_yolo_loaded && vision.visionStatus.running && realCameraStreamHealthy.value) return toneFromMetric('yolo_inference')
  if (testVisionActive.value || runtime.visionStatus.production_yolo_loaded) return 'warn'
  return 'bad'
})
const trackingTone = computed<Tone>(() => motion.trackingStatus.active ? toneFromMetric('tracking_loop') : 'warn')
const serialTone = computed<Tone>(() => serial.status.command_queue_depth > 4 || serial.status.last_command_ack_state === 'timeout' ? 'bad' : serial.status.command_queue_depth > 1 ? 'warn' : 'good')
const picoConnected = computed(() => truth.picoHealthy.value)
const picoTone = computed<Tone>(() => picoConnected.value ? toneFromMetric('pico_heartbeat') : truth.picoTone.value)
const motorTone = computed<Tone>(() => {
  if (!picoConnected.value) return 'bad'
  return hardware.status.telemetry.driver_enabled || motion.trackingStatus.active ? 'good' : 'warn'
})
const servoTone = computed<Tone>(() => {
  if (!picoConnected.value) return 'bad'
  if (serial.status.magazine_empty) return 'bad'
  return hardware.capabilities.allow_physical_fire ? 'good' : 'warn'
})
const cameraNodeValue = computed(() => {
  if (realCameraStreamHealthy.value) return `${runtime.cameraStatus.actual_width || vision.cameraStatus.width}x${runtime.cameraStatus.actual_height || vision.cameraStatus.height}`
  if (cameraProfileIsMock.value) return 'TEST FRAME'
  if (testVisionActive.value) return 'SURROGATE'
  if (physicalCameraConnected.value) return 'USB PRESENT'
  return 'NO DEVICE'
})
const visionNodeValue = computed(() => {
  if (runtime.visionStatus.production_yolo_loaded && realCameraStreamHealthy.value) return `${vision.visionStatus.fps} FPS`
  if (testVisionActive.value) return 'TEST MODE'
  if (runtime.visionStatus.production_yolo_loaded) return 'YOLO READY'
  return 'YOLO OFF'
})

const nodes = computed<FlowNode[]>(() => [
  { id: 'camera', label: 'USB Kamera', subtitle: selectedPhysicalCamera.value?.device_path ?? runtime.cameraStatus.profile.source_type, x: 70, y: 120, tone: cameraTone.value, value: cameraNodeValue.value },
  { id: 'vision', label: 'YOLO / Vision', subtitle: runtime.visionStatus.effective_adapter || vision.visionStatus.detector_kind || vision.visionStatus.vision_mode, x: 270, y: 120, tone: visionTone.value, value: visionNodeValue.value },
  { id: 'tracker', label: 'Tracker / PID', subtitle: motion.trackingStatus.state, x: 470, y: 120, tone: trackingTone.value, value: `${motion.trackingStatus.max_speed} max` },
  { id: 'serial', label: 'Serial TX', subtitle: serial.status.transport_mode, x: 670, y: 120, tone: serialTone.value, value: `${serial.status.command_queue_depth} queue` },
  { id: 'pico', label: 'Pico 2', subtitle: hardware.status.connection_state, x: 870, y: 120, tone: picoTone.value, value: picoConnected.value ? hardware.status.telemetry.firmware_version ?? 'fw ?' : truth.picoSimulated.value ? 'SIMÜLASYON' : 'NO USB' },
  { id: 'motor', label: 'TMC2209 X/Y', subtitle: 'NEMA17 pan/tilt', x: 770, y: 300, tone: motorTone.value, value: picoConnected.value ? `X ${motion.trackingUpdate?.speed_x ?? 0} / Y ${motion.trackingUpdate?.speed_y ?? 0}` : 'PICO REQUIRED' },
  { id: 'servo', label: 'Servo / Lazer', subtitle: 'tetik hattı', x: 970, y: 300, tone: servoTone.value, value: picoConnected.value ? `${serial.status.magazine_remaining}/${serial.status.magazine_capacity}` : 'PICO REQUIRED' },
])

const nodeById = computed(() => Object.fromEntries(nodes.value.map((node) => [node.id, node])))
const edges = computed<FlowEdge[]>(() => [
  { from: nodeById.value.camera, to: nodeById.value.vision, label: 'frame', tone: cameraTone.value, value: `${performance.value?.camera_frame_age_ms ?? 'n/a'} ms` },
  { from: nodeById.value.vision, to: nodeById.value.tracker, label: 'detections', tone: visionTone.value, value: `${vision.visionStatus.balloon_count} hedef` },
  { from: nodeById.value.tracker, to: nodeById.value.serial, label: 'SPD/LZR karar', tone: trackingTone.value, value: `${performance.value?.tracking_loop_ms ?? 'n/a'} ms` },
  { from: nodeById.value.serial, to: nodeById.value.pico, label: 'USB CDC', tone: serialTone.value, value: `${serial.status.last_command_rtt_ms ?? 'n/a'} ms` },
  { from: nodeById.value.pico, to: nodeById.value.motor, label: 'STEP/DIR', tone: motorTone.value, value: !picoConnected.value ? 'blocked' : hardware.status.telemetry.driver_enabled ? 'enabled' : 'unknown' },
  { from: nodeById.value.pico, to: nodeById.value.servo, label: 'PWM/LZR', tone: servoTone.value, value: !picoConnected.value ? 'blocked' : serial.status.magazine_empty ? 'empty' : 'ready' },
].filter((edge) => edge.from && edge.to))

function color(tone: Tone): string {
  if (tone === 'good') return '#22c55e'
  if (tone === 'warn') return '#f59e0b'
  if (tone === 'bad') return '#ef4444'
  return '#64748b'
}

function edgePath(edge: FlowEdge): string {
  const x1 = edge.from.x + 70
  const y1 = edge.from.y
  const x2 = edge.to.x - 70
  const y2 = edge.to.y
  const mid = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`
}

const faultSummary = computed(() => {
  const bad = [...nodes.value.filter((node) => node.tone === 'bad').map((node) => node.label), ...edges.value.filter((edge) => edge.tone === 'bad').map((edge) => edge.label)]
  return bad.length ? bad.join(', ') : 'Akışta kırmızı hata yok'
})

onMounted(() => {
  void runtime.refresh()
  void hardware.refresh()
  void serial.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <DashboardCard title="Canlı Sistem Haritası" subtitle="Veri ve komut akışı: kamera → karar → Pico → motor/tetik">
      <div class="mb-4 flex flex-wrap gap-2">
        <StatusBadge :label="faultSummary" :tone="faultSummary === 'Akışta kırmızı hata yok' ? 'good' : 'bad'" />
        <StatusBadge :label="`Toplam ${performance?.total_pipeline_ms ?? 'n/a'} ms`" :tone="toneFromMetric('total_pipeline')" />
        <StatusBadge :label="`Queue ${serial.status.command_queue_depth}`" :tone="serialTone" />
      </div>

      <div class="overflow-x-auto rounded-md border border-white/10 bg-black/25">
        <svg viewBox="0 0 1080 390" class="min-w-[980px]">
          <defs>
            <marker id="arrow-good" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="#22c55e" />
            </marker>
            <marker id="arrow-warn" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="#f59e0b" />
            </marker>
            <marker id="arrow-bad" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="#ef4444" />
            </marker>
            <marker id="arrow-neutral" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="#64748b" />
            </marker>
          </defs>

          <g v-for="edge in edges" :key="`${edge.from.id}-${edge.to.id}`">
            <path class="flow-edge" :class="{ 'flow-edge-bad': edge.tone === 'bad' }" :d="edgePath(edge)" fill="none" :stroke="color(edge.tone)" stroke-width="4" :stroke-dasharray="edge.tone === 'bad' ? '8 8' : '12 10'" :marker-end="`url(#arrow-${edge.tone})`" />
            <text :x="(edge.from.x + edge.to.x) / 2" :y="(edge.from.y + edge.to.y) / 2 - 12" fill="#e2e8f0" font-size="13" text-anchor="middle">{{ edge.label }}</text>
            <text :x="(edge.from.x + edge.to.x) / 2" :y="(edge.from.y + edge.to.y) / 2 + 6" :fill="color(edge.tone)" font-size="12" text-anchor="middle">{{ edge.value }}</text>
          </g>

          <g v-for="node in nodes" :key="node.id">
            <rect :x="node.x - 72" :y="node.y - 42" width="144" height="84" rx="8" :fill="`${color(node.tone)}22`" :stroke="color(node.tone)" stroke-width="2" />
            <text :x="node.x" :y="node.y - 14" fill="#f8fafc" font-size="15" font-weight="700" text-anchor="middle">{{ node.label }}</text>
            <text :x="node.x" :y="node.y + 6" fill="#cbd5e1" font-size="12" text-anchor="middle">{{ node.subtitle }}</text>
            <text :x="node.x" :y="node.y + 26" :fill="color(node.tone)" font-size="13" font-weight="700" text-anchor="middle">{{ node.value }}</text>
          </g>
        </svg>
      </div>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Kamera → Vision" subtitle="Frame ve inference hattı">
        <MetricRow label="Frame age" :value="`${performance?.camera_frame_age_ms ?? 'n/a'} ms`" />
        <MetricRow label="Camera FPS" :value="performance?.camera_fps ?? vision.visionStatus.fps" />
        <MetricRow label="Inference" :value="`${performance?.inference_ms ?? 'n/a'} ms`" />
        <MetricRow label="Detections" :value="`${vision.visionStatus.body_count} body / ${vision.visionStatus.balloon_count} balloon`" />
      </DashboardCard>

      <DashboardCard title="Karar → Serial" subtitle="Tracking, queue ve komut">
        <MetricRow label="Tracking state" :value="motion.trackingStatus.state" />
        <MetricRow label="Loop" :value="`${performance?.tracking_loop_ms ?? 'n/a'} ms`" />
        <MetricRow label="Last command" :value="serial.status.last_command_raw ?? 'none'" />
        <MetricRow label="Queue" :value="serial.status.command_queue_depth" />
      </DashboardCard>

      <DashboardCard title="Pico → Aktüatörler" subtitle="Heartbeat, motor ve servo">
        <MetricRow label="Pico" :value="hardware.status.connection_state" />
        <MetricRow label="Heartbeat" :value="`${performance?.pico_heartbeat_age_ms ?? 'n/a'} ms`" />
        <MetricRow label="Driver" :value="hardware.status.telemetry.driver_enabled" />
        <MetricRow label="Şarjör" :value="`${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`" />
      </DashboardCard>
    </div>
  </div>
</template>

<style scoped>
.flow-edge {
  animation: flowDash 1.2s linear infinite;
}

.flow-edge-bad {
  animation-duration: 0.55s;
}

@keyframes flowDash {
  from {
    stroke-dashoffset: 0;
  }
  to {
    stroke-dashoffset: -44;
  }
}
</style>
