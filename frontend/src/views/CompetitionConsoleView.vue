<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useMissionStore } from '../stores/missionStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import { useRuntimeTruth } from '../composables/useRuntimeTruth'
import type { MissionStage, MissionUpdate } from '../types/mission'

type Tone = 'good' | 'warn' | 'bad' | 'neutral'

interface PerfMetric {
  value: number | null
  unit: string
  green_max: number
  yellow_max: number
  tone: Tone
  label: string
}

interface PerfStatus {
  cpu_percent: number | null
  process_cpu_percent: number | null
  memory_percent: number | null
  process_rss_mb: number | null
  load_avg_1m: number | null
  gpu_util_percent: number | null
  gpu_memory_percent: number | null
  camera_frame_age_ms: number | null
  camera_fps: number | null
  dropped_frames: number
  preprocess_ms: number | null
  inference_ms: number | null
  postprocess_ms: number | null
  vision_total_ms: number | null
  tracking_loop_ms: number | null
  serial_ack_rtt_ms: number | null
  serial_pending_ack_count: number
  serial_queue_depth: number
  pico_heartbeat_age_ms: number | null
  total_pipeline_ms: number | null
  metrics: Record<string, PerfMetric>
  warnings: string[]
  primary_bottleneck: string
  bottleneck_summary: string
  recommended_actions: string[]
  updated_at: number
}

const system = useSystemStore()
const vision = useVisionStore()
const motion = useMotionStore()
const serial = useSerialStore()
const hardware = useHardwareStore()
const runtime = useDeviceRuntimeStore()
const mission = useMissionStore()
const truth = useRuntimeTruth()

const magazineResetBusy = ref(false)
const liveVisionApply = ref(true)
const liveCameraApply = ref(true)
const livePidApply = ref(true)
const visionApplyBusy = ref(false)
const cameraApplyBusy = ref(false)
const pidApplyBusy = ref(false)
const targetSelectBusy = ref(false)
const selectedBalloonId = ref<number | null>(null)
const lastTuneApply = ref('hazır')
const selectedCameraId = ref('')
const selectedPicoPort = ref('')
let visionApplyTimer: ReturnType<typeof setTimeout> | null = null
let cameraApplyTimer: ReturnType<typeof setTimeout> | null = null
let pidApplyTimer: ReturnType<typeof setTimeout> | null = null

const performance = computed(() => (
  system.latestEvents.find((event) => event.type === 'performance.status')?.payload as PerfStatus | undefined
))

const latestFrame = computed(() => vision.latestEvent)
const activeBalloons = computed(() => latestFrame.value?.balloon_detections ?? [])
const activeBodies = computed(() => latestFrame.value?.body_detections ?? [])
const overlayWidth = computed(() => latestFrame.value ? Math.max(1, vision.cameraStatus.width || runtime.cameraStatus.actual_width || 1280) : 1280)
const overlayHeight = computed(() => latestFrame.value ? Math.max(1, vision.cameraStatus.height || runtime.cameraStatus.actual_height || 720) : 720)
const viewBox = computed(() => `0 0 ${overlayWidth.value} ${overlayHeight.value}`)
const centerX = computed(() => overlayWidth.value / 2)
const centerY = computed(() => overlayHeight.value / 2)
const aimX = computed(() => centerX.value + motion.trackingStatus.aim_offset_x_px)
const aimY = computed(() => centerY.value + motion.trackingStatus.aim_offset_y_px)
const missionState = computed(() => mission.snapshot.state)
const missionScore = computed(() => mission.snapshot.score)
const selectedMission = computed<MissionStage>({
  get: () => missionState.value.active_stage,
  set: (value) => updateMission({ active_stage: value }),
})

const missionLabel = computed(() => {
  if (selectedMission.value === 'stage1') return 'Aşama 1 - Manuel İmha'
  if (selectedMission.value === 'stage2') return 'Aşama 2 - Sürü Otonom'
  return 'Aşama 3 - Dost/Düşman'
})

const picoHealthy = computed(() => truth.picoHealthy.value)
const picoStatusLabel = computed(() => (
  picoHealthy.value ? 'PICO SAĞLIKLI' : truth.picoSimulated.value ? 'PICO SİMÜLASYON' : 'PICO KOPUK'
))
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
const testFrameActive = computed(() => (
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
const streamHealthy = computed(() => (
  realCameraStreamHealthy.value
  && performance.value?.metrics.camera_frame_age?.tone !== 'bad'
))
const cameraBadgeLabel = computed(() => {
  if (realCameraStreamHealthy.value) return 'KAMERA AKIŞI VAR'
  if (cameraProfileIsMock.value) return 'TEST KAMERASI'
  if (testFrameActive.value) return 'TEST/SURROGATE GÖRÜNTÜ'
  if (physicalCameraConnected.value) return 'KAMERA VAR / AKIŞ YOK'
  return 'USB KAMERA YOK'
})
const cameraBadgeTone = computed<Tone>(() => {
  if (realCameraStreamHealthy.value) return 'good'
  if (testFrameActive.value || physicalCameraConnected.value) return 'warn'
  return 'bad'
})
const cameraTruthMessage = computed(() => {
  if (realCameraStreamHealthy.value) return ''
  if (testFrameActive.value) return 'Bu görüntü test/surrogate hattından geliyor; gerçek USB kamera doğrulanmadı.'
  if (physicalCameraConnected.value) return 'USB kamera envanterde var fakat canlı frame akışı yok.'
  return 'USB kamera bağlı değil. Bu ekrandaki siyah/test görüntü gerçek kamera kanıtı değildir.'
})
const displayedActiveBalloons = computed(() => realCameraStreamHealthy.value || testFrameActive.value ? activeBalloons.value : [])
const displayedActiveBodies = computed(() => realCameraStreamHealthy.value || testFrameActive.value ? activeBodies.value : [])
const commandHealthy = computed(() => serial.status.command_queue_depth <= 1 && serial.status.last_command_ack_state !== 'timeout')
const fireBlocked = computed(() => serial.status.magazine_empty || !hardware.status.physical_command_enabled || !hardware.capabilities.allow_physical_fire)
const bottleneckTone = computed<Tone>(() => {
  if (!performance.value || performance.value.primary_bottleneck === 'none') return 'good'
  return performance.value.warnings.length ? 'bad' : 'warn'
})
const firePolicyRows = computed(() => {
  if (selectedMission.value === 'stage1') {
    return [
      'Manuel atış: operatör komutu olmadan ateş yok',
      'Sıra dışı hedef yanlış imha cezası olarak işlenir',
      'Tracking yardımcı olabilir, fire gate manuel kalır',
    ]
  }
  if (selectedMission.value === 'stage2') {
    return [
      'Otonom takip açık; balon bbox seçimi hedef kilidini başlatır',
      'Şarjör sıfırsa veya komut hattı sıkışırsa atış bloklanır',
      'Dost/düşman sınıflandırması bu aşamada puana dahil değil',
    ]
  }
  return [
    'Dost hedefte fire block zorunlu',
    'Menzil ve hedef sınıfı geçerli değilse atış yapılmaz',
    'Yanlış/dost hedef cezası görev kaydına işlenir',
  ]
})

const selectedCamera = computed(() => {
  const byProfile = runtime.inventory.cameras.find((camera) => camera.device_path === runtime.cameraStatus.profile.device_path || camera.stable_path === runtime.cameraStatus.profile.stable_path)
  return byProfile ?? runtime.inventory.cameras[0] ?? null
})
const selectedCameraOption = computed(() => runtime.inventory.cameras.find((camera) => camera.device_id === selectedCameraId.value) ?? selectedCamera.value)

const picoCandidate = computed(() => {
  const activePort = hardware.status.telemetry.port
  return hardware.ports.find((port) => port.device === activePort) ?? hardware.ports.find((port) => port.is_candidate_pico) ?? null
})
const selectedPicoOption = computed(() => hardware.ports.find((port) => port.device === selectedPicoPort.value) ?? picoCandidate.value)

const bottleneckRows = computed(() => {
  const metrics = performance.value?.metrics ?? {}
  return [
    ['camera_frame_age', 'Kamera frame yaşı'],
    ['yolo_inference', 'YOLO inference'],
    ['tracking_loop', 'Tracking loop'],
    ['serial_ack', 'Serial ACK RTT'],
    ['pico_heartbeat', 'Pico heartbeat'],
    ['tx_queue', 'TX queue'],
    ['total_pipeline', 'Toplam gecikme'],
  ].map(([key, fallback]) => {
    const metric = metrics[key]
    return {
      key,
      label: metric?.label ?? fallback,
      value: metric?.value ?? null,
      unit: metric?.unit ?? '',
      tone: metric?.tone ?? 'neutral',
      green: metric?.green_max ?? null,
      yellow: metric?.yellow_max ?? null,
    }
  })
})

const commandRows = computed(() => serial.logs.slice(0, 10).map((entry) => ({
  id: entry.id,
  time: new Date(entry.ts * 1000).toLocaleTimeString(),
  direction: entry.direction.toUpperCase(),
  kind: entry.kind.toUpperCase(),
  raw: entry.raw ?? JSON.stringify(entry.message),
  error: entry.error,
  tone: entry.kind === 'error' || entry.kind === 'timeout' || entry.kind === 'nack' ? 'bad' : entry.kind === 'ack' || entry.kind === 'rx' ? 'good' : 'neutral',
})))

const stageChecklist = computed(() => {
  if (selectedMission.value === 'stage1') {
    return ['Manuel mod', '5-10-15 m hedef seçimi', 'Kullanıcı ateş komutu', 'Sıra/yanlış hedef ceza takibi']
  }
  if (selectedMission.value === 'stage2') {
    return ['Otonom takip', '3 yaklaşma kolu', '4 tur ilerleme', 'Parkurdan çıkmadan imha']
  }
  return ['Dost/düşman ayrımı', 'Hedef tipine göre menzil kapısı', 'Dost hedefte fire block', '8 tur kayıt']
})

function toneForMetric(tone?: string): Tone {
  if (tone === 'good' || tone === 'warn' || tone === 'bad') return tone
  return 'neutral'
}

function boolTone(value: boolean): Tone {
  return value ? 'good' : 'bad'
}

function displayValue(value: number | string | boolean | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || value === '') return 'n/a'
  if (typeof value === 'number') return `${Math.round(value * 10) / 10}${suffix}`
  return `${value}${suffix}`
}

async function resetMagazine(): Promise<void> {
  magazineResetBusy.value = true
  try {
    await serial.resetMagazine(8)
  } finally {
    magazineResetBusy.value = false
  }
}

function updateMission(update: MissionUpdate): void {
  void mission.update(update).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Mission update failed'
  })
}

function recordMissionHit(): void {
  if (selectedMission.value === 'stage1') {
    mission.error = 'Aşama 1 hit kaydı yalnız kilitli sıra ve 5/10/20 puanla Mission Modes ekranından yapılır.'
  }
  else if (selectedMission.value === 'stage2') {
    mission.error = 'Aşama 2 sonucu yalnız 0/1/2/3 onaylı hit ile Mission Modes ekranında tur bazlı kaydedilir.'
  }
  else mission.error = 'Aşama 3 sonucu yalnız sınıf, düşman hit ve dost vuruşu ile Mission Modes ekranında tur bazlı kaydedilir.'
}

function recordMissionPenalty(): void {
  if (selectedMission.value === 'stage1') {
    mission.error = 'Aşama 1 yanlış hedef kaydı yalnız kilitli görev akışından yapılır.'
  }
  else if (selectedMission.value === 'stage3') mission.error = 'Aşama 3 cezası yalnız kanonik tur sonucu içinde kaydedilir.'
}

async function resetMission(): Promise<void> {
  await mission.reset()
}

async function selectBalloonTarget(balloon: { id: number, center_x: number, center_y: number }): Promise<void> {
  targetSelectBusy.value = true
  selectedBalloonId.value = balloon.id
  try {
    await motion.selectTarget({
      x: balloon.center_x,
      y: balloon.center_y,
      detection_id: balloon.id,
      frame_id: latestFrame.value?.frame_id,
    })
  } finally {
    targetSelectBusy.value = false
  }
}

function scheduleVisionApply(): void {
  if (!liveVisionApply.value) return
  if (visionApplyTimer) clearTimeout(visionApplyTimer)
  visionApplyTimer = setTimeout(() => {
    void applyVisionTuning()
  }, 250)
}

function scheduleCameraApply(): void {
  if (!liveCameraApply.value) return
  if (cameraApplyTimer) clearTimeout(cameraApplyTimer)
  cameraApplyTimer = setTimeout(() => {
    void applyCameraControls()
  }, 180)
}

function schedulePidApply(): void {
  if (!livePidApply.value) return
  if (pidApplyTimer) clearTimeout(pidApplyTimer)
  pidApplyTimer = setTimeout(() => {
    void applyPidTuning()
  }, 220)
}

async function applyVisionTuning(): Promise<void> {
  if (visionApplyBusy.value) return
  visionApplyBusy.value = true
  try {
    await runtime.applyVision()
    lastTuneApply.value = `vision ${new Date().toLocaleTimeString()}`
  } finally {
    visionApplyBusy.value = false
  }
}

async function applyCameraControls(): Promise<void> {
  if (cameraApplyBusy.value) return
  cameraApplyBusy.value = true
  try {
    await runtime.applyCameraControls()
    lastTuneApply.value = `camera ${new Date().toLocaleTimeString()}`
  } finally {
    cameraApplyBusy.value = false
  }
}

async function applyPidTuning(): Promise<void> {
  if (pidApplyBusy.value) return
  pidApplyBusy.value = true
  try {
    await motion.updateTrackingConfig({
      pid_kp_x: motion.trackingStatus.pid_kp_x,
      pid_ki_x: motion.trackingStatus.pid_ki_x,
      pid_kd_x: motion.trackingStatus.pid_kd_x,
      pid_kp_y: motion.trackingStatus.pid_kp_y,
      pid_ki_y: motion.trackingStatus.pid_ki_y,
      pid_kd_y: motion.trackingStatus.pid_kd_y,
      smoothing_alpha: motion.trackingStatus.smoothing_alpha,
      command_rate_hz: motion.trackingStatus.command_rate_hz,
      max_speed: motion.trackingStatus.max_speed,
      aim_offset_x_px: motion.trackingStatus.aim_offset_x_px,
      aim_offset_y_px: motion.trackingStatus.aim_offset_y_px,
      invert_x: motion.trackingStatus.invert_x,
      invert_y: motion.trackingStatus.invert_y,
    })
    lastTuneApply.value = `pid ${new Date().toLocaleTimeString()}`
  } finally {
    pidApplyBusy.value = false
  }
}

async function useSelectedCamera(): Promise<void> {
  const camera = selectedCameraOption.value
  if (!camera) return
  runtime.cameraDraft.source_type = 'usb'
  runtime.cameraDraft.device_id = camera.device_id
  runtime.cameraDraft.device_path = camera.device_path
  runtime.cameraDraft.stable_path = camera.stable_path
  await runtime.applyCamera()
}

async function connectSelectedPico(): Promise<void> {
  const port = selectedPicoOption.value?.device
  if (!port) return
  await hardware.connectReadonly(port, hardware.status.telemetry.baudrate || 115200)
  await serial.refresh()
}

watch(
  () => [
    runtime.visionDraft.conf,
    runtime.visionDraft.iou,
    runtime.visionDraft.max_det,
    runtime.visionDraft.frame_skip,
    runtime.visionDraft.vid_stride,
    runtime.visionDraft.tracker_enabled,
  ],
  scheduleVisionApply,
)

watch(
  () => [
    motion.trackingStatus.pid_kp_x,
    motion.trackingStatus.pid_ki_x,
    motion.trackingStatus.pid_kd_x,
    motion.trackingStatus.pid_kp_y,
    motion.trackingStatus.pid_ki_y,
    motion.trackingStatus.pid_kd_y,
    motion.trackingStatus.smoothing_alpha,
    motion.trackingStatus.command_rate_hz,
    motion.trackingStatus.max_speed,
    motion.trackingStatus.aim_offset_x_px,
    motion.trackingStatus.aim_offset_y_px,
    motion.trackingStatus.invert_x,
    motion.trackingStatus.invert_y,
  ],
  schedulePidApply,
)

watch(
  () => [
    runtime.cameraDraft.brightness,
    runtime.cameraDraft.contrast,
    runtime.cameraDraft.saturation,
    runtime.cameraDraft.gain,
    runtime.cameraDraft.exposure_auto,
    runtime.cameraDraft.exposure_value,
    runtime.cameraDraft.white_balance_auto,
    runtime.cameraDraft.white_balance_value,
  ],
  scheduleCameraApply,
)

onMounted(() => {
  void hardware.refresh()
  void runtime.refresh()
  void serial.refresh()
  void motion.refreshTrackingStatus()
  void mission.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <section class="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_420px]">
      <div class="rounded-md border border-white/10 bg-[#111418] p-3">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="text-base font-semibold text-white">Yarışma Görüşü</h3>
            <p class="mt-1 text-xs text-slate-400">{{ missionLabel }} · canlı kamera, hedef, aim ve fire gate</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <StatusBadge :label="cameraBadgeLabel" :tone="cameraBadgeTone" />
            <StatusBadge :label="streamHealthy ? 'FRAME TAZE' : 'FRAME GECİKİYOR'" :tone="boolTone(streamHealthy)" />
            <StatusBadge :label="picoStatusLabel" :tone="picoHealthy ? 'good' : 'warn'" />
            <StatusBadge :label="commandHealthy ? 'KOMUT HATTI TEMİZ' : 'KOMUT SIKIŞIYOR'" :tone="boolTone(commandHealthy)" />
            <StatusBadge :label="fireBlocked ? 'FIRE BLOCKED' : 'FIRE READY GATE'" :tone="fireBlocked ? 'bad' : 'good'" />
          </div>
        </div>

        <div class="relative overflow-hidden rounded-md border border-white/10 bg-black" :style="{ aspectRatio: `${overlayWidth} / ${overlayHeight}` }">
          <img :src="vision.streamUrl" class="h-full w-full object-contain" alt="Camera stream" />
          <div
            v-if="cameraTruthMessage"
            class="absolute left-4 right-4 top-4 z-10 rounded-md border border-amber-400/40 bg-black/80 px-4 py-3 text-sm font-semibold text-amber-100"
          >
            {{ cameraTruthMessage }}
          </div>
          <svg class="absolute inset-0 h-full w-full" :viewBox="viewBox">
            <line :x1="centerX" y1="0" :x2="centerX" :y2="overlayHeight" stroke="#22d3ee" stroke-width="1.5" stroke-dasharray="9 7" />
            <line x1="0" :y1="centerY" :x2="overlayWidth" :y2="centerY" stroke="#22d3ee" stroke-width="1.5" stroke-dasharray="9 7" />
            <circle :cx="centerX" :cy="centerY" r="18" fill="none" stroke="#22d3ee" stroke-width="2" />
            <line :x1="aimX - 24" :y1="aimY" :x2="aimX + 24" :y2="aimY" stroke="#22c55e" stroke-width="3" />
            <line :x1="aimX" :y1="aimY - 24" :x2="aimX" :y2="aimY + 24" stroke="#22c55e" stroke-width="3" />
            <circle :cx="aimX" :cy="aimY" r="8" fill="none" stroke="#22c55e" stroke-width="2" />
            <text :x="aimX + 12" :y="aimY - 14" fill="#bbf7d0" font-size="13">NAMLU AIM</text>

            <g v-for="body in displayedActiveBodies" :key="`body-${body.id}`">
              <rect :x="body.bbox.x" :y="body.bbox.y" :width="body.bbox.w" :height="body.bbox.h" fill="rgba(56,189,248,0.08)" stroke="#38bdf8" stroke-width="3" />
              <text :x="body.bbox.x" :y="Math.max(16, body.bbox.y - 6)" fill="#a5f3fc" font-size="14">
                {{ body.class_name }} {{ Math.round(body.confidence * 100) }}%
              </text>
            </g>

            <g v-for="balloon in displayedActiveBalloons" :key="`balloon-${balloon.id}`" class="cursor-pointer" @click.stop="selectBalloonTarget(balloon)">
              <rect :x="balloon.bbox.x" :y="balloon.bbox.y" :width="balloon.bbox.w" :height="balloon.bbox.h" fill="rgba(245,158,11,0.1)" :stroke="selectedBalloonId === balloon.id ? '#22c55e' : '#f59e0b'" :stroke-width="selectedBalloonId === balloon.id ? 5 : 3" />
              <circle :cx="balloon.center_x" :cy="balloon.center_y" r="5" fill="#fbbf24" />
              <circle :cx="balloon.center_x" :cy="balloon.center_y" :r="Math.min(balloon.bbox.w, balloon.bbox.h) / 4" fill="none" stroke="#ec4899" stroke-width="2" stroke-dasharray="4 4" />
              <text v-if="selectedBalloonId === balloon.id" :x="balloon.bbox.x" :y="balloon.bbox.y + balloon.bbox.h + 16" fill="#22c55e" font-size="13">
                TRACKING TARGET
              </text>
            </g>
          </svg>
        </div>
      </div>

      <div class="grid gap-4">
        <DashboardCard title="Canlı Tuning" subtitle="Conf, kamera ışık ayarları ve hızlı runtime kontrolleri">
          <div class="mb-3 flex flex-wrap items-center gap-3 text-sm text-slate-300">
            <label class="flex items-center gap-2"><input v-model="liveVisionApply" type="checkbox" /> Vision canlı</label>
            <label class="flex items-center gap-2"><input v-model="liveCameraApply" type="checkbox" /> Kamera canlı</label>
            <label class="flex items-center gap-2"><input v-model="livePidApply" type="checkbox" /> PID canlı</label>
            <StatusBadge :label="visionApplyBusy || cameraApplyBusy || pidApplyBusy ? 'UYGULANIYOR' : lastTuneApply" :tone="visionApplyBusy || cameraApplyBusy || pidApplyBusy ? 'warn' : 'neutral'" />
          </div>
          <div class="grid gap-3">
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              YOLO conf {{ runtime.visionDraft.conf }}
              <input v-model.number="runtime.visionDraft.conf" type="range" min="0" max="1" step="0.001" class="w-full accent-cyan-400" />
            </label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              IoU {{ runtime.visionDraft.iou }}
              <input v-model.number="runtime.visionDraft.iou" type="range" min="0" max="1" step="0.01" class="w-full accent-cyan-400" />
            </label>
            <div class="grid grid-cols-2 gap-2">
              <label class="grid gap-1 text-xs text-slate-400">max_det<input v-model.number="runtime.visionDraft.max_det" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">frame_skip<input v-model.number="runtime.visionDraft.frame_skip" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            </div>
            <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.tracker_enabled" type="checkbox" /> YOLO tracker</label>
            <div class="grid grid-cols-2 gap-2">
              <label class="grid gap-1 text-xs text-slate-400">Brightness<input v-model.number="runtime.cameraDraft.brightness" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Contrast<input v-model.number="runtime.cameraDraft.contrast" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Saturation<input v-model.number="runtime.cameraDraft.saturation" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Gain<input v-model.number="runtime.cameraDraft.gain" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            </div>
            <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.cameraDraft.exposure_auto" type="checkbox" /> Auto exposure</label>
            <label class="grid gap-1 text-xs text-slate-400">Exposure<input v-model.number="runtime.cameraDraft.exposure_value" type="number" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <div class="grid grid-cols-3 gap-2">
              <label class="grid gap-1 text-xs text-slate-400">Kp X<input v-model.number="motion.trackingStatus.pid_kp_x" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Kd X<input v-model.number="motion.trackingStatus.pid_kd_x" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Max speed<input v-model.number="motion.trackingStatus.max_speed" type="number" step="20" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Kp Y<input v-model.number="motion.trackingStatus.pid_kp_y" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Kd Y<input v-model.number="motion.trackingStatus.pid_kd_y" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Komut Hz<input v-model.number="motion.trackingStatus.command_rate_hz" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            </div>
            <div class="grid grid-cols-2 gap-2">
              <label class="grid gap-1 text-xs text-slate-400">Aim X<input v-model.number="motion.trackingStatus.aim_offset_x_px" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
              <label class="grid gap-1 text-xs text-slate-400">Aim Y<input v-model.number="motion.trackingStatus.aim_offset_y_px" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            </div>
            <div class="flex flex-wrap gap-3 text-sm text-slate-300">
              <label class="flex items-center gap-2"><input v-model="motion.trackingStatus.invert_x" type="checkbox" /> X ters</label>
              <label class="flex items-center gap-2"><input v-model="motion.trackingStatus.invert_y" type="checkbox" /> Y ters</label>
            </div>
          </div>
          <div class="mt-3 grid grid-cols-2 gap-2">
            <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-60" :disabled="visionApplyBusy" @click="applyVisionTuning">Vision uygula</button>
            <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="cameraApplyBusy" @click="applyCameraControls">Kamera uygula</button>
            <button class="focus-ring col-span-2 rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-60" :disabled="pidApplyBusy" @click="applyPidTuning">PID / motor uygula</button>
          </div>
        </DashboardCard>

        <DashboardCard title="Görev ve Angajman" subtitle="Yarışma aşaması, atış hakkı ve güvenlik kapıları">
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Görev modu
            <select v-model="selectedMission" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm normal-case tracking-normal text-white">
              <option value="stage1">Aşama 1 - Manuel</option>
              <option value="stage2">Aşama 2 - Sürü</option>
              <option value="stage3">Aşama 3 - Dost/Düşman</option>
            </select>
          </label>
          <div class="mt-3 grid gap-2">
            <div v-for="item in stageChecklist" :key="item" class="rounded-md border border-white/8 bg-black/18 px-3 py-2 text-sm text-slate-200">
              {{ item }}
            </div>
          </div>
          <div class="mt-3 grid gap-2">
            <div v-for="item in firePolicyRows" :key="item" class="rounded-md border border-amber-400/20 bg-amber-400/8 px-3 py-2 text-xs text-amber-50">
              {{ item }}
            </div>
          </div>
          <div class="mt-4 grid gap-2 md:grid-cols-2">
            <MetricRow label="Aktif puan" :value="missionScore.active_score" />
            <MetricRow label="Toplam tahmin" :value="missionScore.total_estimated_score" />
            <MetricRow label="Kalan süre" :value="`${missionScore.remaining_s} sn`" />
            <MetricRow label="Seçili hedef" :value="selectedBalloonId ?? 'none'" />
            <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="recordMissionHit">
              İmha + / Puan İşle
            </button>
            <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="selectedMission === 'stage2'" @click="recordMissionPenalty">
              Ceza / Yanlış İşle
            </button>
            <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" :disabled="targetSelectBusy" @click="motion.startTracking">
              {{ targetSelectBusy ? 'Hedef seçiliyor' : 'Tracking Aç' }}
            </button>
            <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="resetMission">
              Görev Kaydını Resetle
            </button>
          </div>
          <div class="mt-4 rounded-md border border-white/10 bg-black/25 p-3">
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Şarjör / lazer kapasitesi</span>
              <StatusBadge :label="serial.status.magazine_empty ? 'BOŞ' : `${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`" :tone="serial.status.magazine_empty ? 'bad' : serial.status.magazine_remaining <= 2 ? 'warn' : 'good'" />
            </div>
            <div class="h-3 overflow-hidden rounded bg-slate-800">
              <div class="h-full bg-emerald-400" :style="{ width: `${serial.status.magazine_capacity ? (serial.status.magazine_remaining / serial.status.magazine_capacity) * 100 : 0}%` }"></div>
            </div>
            <div class="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-400">
              <span>Pico ACK atış: <b class="font-mono text-slate-200">{{ serial.status.acknowledged_shot_count }}</b></span>
              <span>Yükleme: <b class="font-mono text-slate-200">{{ serial.status.magazine_reload_count }}</b></span>
            </div>
            <button class="focus-ring mt-3 rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="magazineResetBusy" @click="resetMagazine">
              {{ magazineResetBusy ? 'Resetleniyor' : 'Şarjörü 8’e Resetle' }}
            </button>
          </div>
        </DashboardCard>

        <DashboardCard title="Canlı Sağlık" subtitle="Kamera, Pico, komut ve fire gate">
          <MetricRow label="Kamera" :value="realCameraStreamHealthy ? 'real stream ok' : cameraBadgeLabel" />
          <MetricRow label="Frame age" :value="displayValue(performance?.camera_frame_age_ms, ' ms')" />
          <MetricRow label="Pico" :value="picoHealthy ? hardware.status.connection_state : truth.picoSimulated ? 'simulation' : 'disconnected'" />
          <MetricRow label="Heartbeat" :value="displayValue(performance?.pico_heartbeat_age_ms, ' ms')" />
          <MetricRow label="Komut queue" :value="serial.status.command_queue_depth" />
          <MetricRow label="Son komut" :value="serial.status.last_command_raw ?? 'none'" />
          <MetricRow label="Komut cevabı" :value="serial.status.last_command_ack_state" />
          <MetricRow label="Son hata" :value="serial.status.last_command_error ?? serial.status.last_error ?? 'none'" />
        </DashboardCard>
      </div>
    </section>

    <section class="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
      <DashboardCard title="Performans ve Darboğaz" subtitle="Beklenen değer yeşil, sınır değer sarı, kötü değer kırmızı">
        <div class="mb-3 rounded-md border border-white/10 bg-black/25 p-3">
          <div class="mb-2 flex flex-wrap items-center justify-between gap-3">
            <span class="text-sm font-semibold text-slate-100">{{ performance?.bottleneck_summary ?? 'Metrik bekleniyor.' }}</span>
            <StatusBadge :label="performance?.primary_bottleneck ?? 'waiting'" :tone="bottleneckTone" />
          </div>
          <div class="grid gap-2 md:grid-cols-3">
            <div v-for="action in performance?.recommended_actions ?? ['WebSocket performans metriği bekleniyor.']" :key="action" class="rounded-md border border-white/8 bg-white/5 px-3 py-2 text-xs text-slate-300">
              {{ action }}
            </div>
          </div>
        </div>
        <div class="grid gap-2 md:grid-cols-2">
          <div v-for="row in bottleneckRows" :key="row.key" class="rounded-md border border-white/8 bg-black/18 p-3">
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-slate-100">{{ row.label }}</span>
              <StatusBadge :label="`${displayValue(row.value)} ${row.unit}`" :tone="toneForMetric(row.tone)" />
            </div>
            <p class="text-xs text-slate-400">Yeşil ≤ {{ row.green ?? 'n/a' }}, sarı ≤ {{ row.yellow ?? 'n/a' }} {{ row.unit }}</p>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="PC Yükleri" subtitle="CPU/GPU/RAM ve backend process yükü">
        <MetricRow label="CPU" :value="displayValue(performance?.cpu_percent, '%')" />
        <MetricRow label="Backend CPU" :value="displayValue(performance?.process_cpu_percent, '%')" />
        <MetricRow label="RAM" :value="displayValue(performance?.memory_percent, '%')" />
        <MetricRow label="Backend RSS" :value="displayValue(performance?.process_rss_mb, ' MB')" />
        <MetricRow label="Load avg 1m" :value="displayValue(performance?.load_avg_1m)" />
        <MetricRow label="GPU util" :value="displayValue(performance?.gpu_util_percent, '%')" />
        <MetricRow label="GPU mem" :value="displayValue(performance?.gpu_memory_percent, '%')" />
      </DashboardCard>
    </section>

    <section class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Cihaz ve Port Yönetimi" subtitle="USB kamera veya Pico yer değiştirirse buradan görünür">
        <MetricRow label="Aktif kamera" :value="runtime.cameraStatus.selected_camera" />
        <MetricRow label="Kamera path" :value="runtime.cameraStatus.profile.device_path ?? selectedCamera?.device_path ?? 'none'" />
        <MetricRow label="Stable path" :value="runtime.cameraStatus.profile.stable_path ?? selectedCamera?.stable_path ?? 'none'" />
        <MetricRow label="USB bus" :value="selectedCamera?.bus_path ?? 'unknown'" />
        <MetricRow label="Kamera FPS" :value="`${runtime.cameraStatus.actual_width}x${runtime.cameraStatus.actual_height}@${runtime.cameraStatus.actual_fps_measured}`" />
        <MetricRow label="Pico port" :value="hardware.status.telemetry.port ?? picoCandidate?.device ?? 'none'" />
        <MetricRow label="Pico hwid" :value="picoCandidate?.hwid ?? 'unknown'" />
        <MetricRow label="Pico firmware" :value="hardware.status.telemetry.firmware_version ?? 'unknown'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="`${runtime.inventory.cameras.length} kamera`" tone="neutral" />
          <StatusBadge :label="`${hardware.ports.length} serial port`" tone="neutral" />
          <StatusBadge :label="picoCandidate ? 'PICO ADAYI VAR' : 'PICO ADAYI YOK'" :tone="picoCandidate ? 'good' : 'bad'" />
        </div>
        <div class="mt-4 grid gap-3">
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Kamera seç
            <select v-model="selectedCameraId" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm normal-case tracking-normal text-white">
              <option value="">Önerilen / aktif kamera</option>
              <option v-for="camera in runtime.inventory.cameras" :key="camera.device_id" :value="camera.device_id">
                {{ camera.name }} · {{ camera.device_path }} · {{ camera.bus_path ?? 'bus ?' }}
              </option>
            </select>
          </label>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="useSelectedCamera">
            Bu Kamerayı Aktif Yap
          </button>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Pico port seç
            <select v-model="selectedPicoPort" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm normal-case tracking-normal text-white">
              <option value="">Önerilen / aktif Pico</option>
              <option v-for="port in hardware.ports" :key="port.device" :value="port.device">
                {{ port.device }} · {{ port.description }} · {{ port.hwid }}
              </option>
            </select>
          </label>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="connectSelectedPico">
            Bu Pico Portuna Bağlan
          </button>
        </div>
      </DashboardCard>

      <DashboardCard title="Komut Debug" subtitle="Pico/motor/servo komutları ve cevap durumları">
        <MetricRow label="Son komut türü" :value="serial.status.last_command_kind ?? 'none'" />
        <MetricRow label="Son komut yaşı" :value="displayValue(serial.status.last_command_age_ms, ' ms')" />
        <MetricRow label="Son RTT" :value="displayValue(serial.status.last_command_rtt_ms, ' ms')" />
        <MetricRow label="Pending ACK" :value="serial.status.pending_ack_count" />
        <div class="mt-3 max-h-80 overflow-auto rounded-md border border-white/10">
          <table class="w-full text-left text-xs">
            <thead class="sticky top-0 bg-[#111418] text-slate-500">
              <tr>
                <th class="px-3 py-2">Saat</th>
                <th class="px-3 py-2">Yön</th>
                <th class="px-3 py-2">Tip</th>
                <th class="px-3 py-2">Komut</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in commandRows" :key="row.id" class="border-t border-white/8">
                <td class="px-3 py-2 text-slate-400">{{ row.time }}</td>
                <td class="px-3 py-2"><StatusBadge :label="row.direction" :tone="toneForMetric(row.tone)" /></td>
                <td class="px-3 py-2 text-slate-200">{{ row.kind }}</td>
                <td class="px-3 py-2 font-mono text-slate-300">{{ row.error ?? row.raw }}</td>
              </tr>
              <tr v-if="commandRows.length === 0">
                <td colspan="4" class="px-3 py-4 text-slate-500">Henüz komut logu yok.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>
    </section>
  </div>
</template>
