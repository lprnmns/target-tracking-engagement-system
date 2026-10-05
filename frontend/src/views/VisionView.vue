<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useColorStore } from '../stores/colorStore'
import { useDataLabStore } from '../stores/dataLabStore'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useModelPackageStore } from '../stores/modelPackageStore'
import { useMotionStore } from '../stores/motionStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import type { TrackingConfigUpdate } from '../types/tracking'

const vision = useVisionStore()
const color = useColorStore()
const dataLab = useDataLabStore()
const runtime = useDeviceRuntimeStore()
const modelPackages = useModelPackageStore()
const motion = useMotionStore()
const system = useSystemStore()
const showBody = ref(true)
const showBalloon = ref(true)
const showAim = ref(true)
const showLabels = ref(true)
const showLatency = ref(true)
const selectedPreset = ref('balanced')
const selectedBalloonId = ref<number | null>(null)
const targetSelectBusy = ref(false)
const pidApplyBusy = ref(false)
const pidAutoApply = ref(true)
const pidLastApplied = ref('Beklemede')
const laptopTestBusy = ref(false)
const laptopTestMessage = ref('Hazır')
let pidApplyTimer: ReturnType<typeof setTimeout> | null = null
let liveTelemetryTimer: ReturnType<typeof window.setInterval> | null = null
const pidDraft = ref({
  pid_kp_x: motion.trackingStatus.pid_kp_x,
  pid_ki_x: motion.trackingStatus.pid_ki_x,
  pid_kd_x: motion.trackingStatus.pid_kd_x,
  pid_kp_y: motion.trackingStatus.pid_kp_y,
  pid_ki_y: motion.trackingStatus.pid_ki_y,
  pid_kd_y: motion.trackingStatus.pid_kd_y,
  smoothing_alpha: motion.trackingStatus.smoothing_alpha,
  command_rate_hz: motion.trackingStatus.command_rate_hz,
  max_speed: motion.trackingStatus.max_speed,
  invert_x: motion.trackingStatus.invert_x,
  invert_y: motion.trackingStatus.invert_y,
})

const frame = computed(() => vision.latestEvent)
const overlayWidth = computed(() => runtime.cameraStatus.actual_width || runtime.cameraStatus.requested_width || vision.cameraStatus.width)
const overlayHeight = computed(() => runtime.cameraStatus.actual_height || runtime.cameraStatus.requested_height || vision.cameraStatus.height)
const viewBox = computed(() => `0 0 ${overlayWidth.value} ${overlayHeight.value}`)
const overlayCenterX = computed(() => overlayWidth.value / 2)
const overlayCenterY = computed(() => overlayHeight.value / 2)
const aimCrosshairX = computed(() => overlayCenterX.value + (motion.trackingStatus.aim_offset_x_px ?? 0))
const aimCrosshairY = computed(() => overlayCenterY.value + (motion.trackingStatus.aim_offset_y_px ?? 0))
const activeBodyDetections = computed(() => vision.visionStatus.running ? frame.value?.body_detections ?? [] : [])
const activeBalloonDetections = computed(() => vision.visionStatus.running ? frame.value?.balloon_detections ?? [] : [])
const surrogateSourceKind = computed(() => runtime.visionStatus.surrogate_source_kind ?? vision.latestEvent?.camera_source_kind ?? null)
const surrogateActive = computed(() => runtime.visionStatus.effective_adapter === 'mock_camera_surrogate' || runtime.visionStatus.effective_adapter === 'live_camera_surrogate')
const overlaySource = computed(() => {
  if (surrogateActive.value && surrogateSourceKind.value === 'mock') return 'MOCK CAMERA SURROGATE / MOCK FRAME'
  if (surrogateActive.value && surrogateSourceKind.value === 'real_camera') return 'LIVE CAMERA SURROGATE / REAL CAPTURE'
  return vision.cameraStatus.camera_mode === 'mock' ? 'MOCK FRAME' : 'CAMERA STREAM'
})
const detectionSource = computed(() => {
  if (runtime.visionStatus.effective_adapter === 'mock_camera_surrogate') return 'MOCK_CAMERA_SURROGATE'
  if (runtime.visionStatus.effective_adapter === 'live_camera_surrogate') return 'LIVE_CAMERA_SURROGATE'
  return vision.visionStatus.vision_mode === 'mock' ? 'MOCK DATA' : 'YOLO METADATA'
})

function colorDecisionFor(detectionId: number) {
  return color.latest?.detection_id === detectionId ? color.latest : null
}

function modelDetail(key: string): string {
  const value = runtime.visionStatus.active_model_details[key]
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'not tested / none detected'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value === null || value === undefined || value === '') return 'not_available'
  return String(value)
}

function mappingTone(status: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'complete') return 'good'
  if (status === 'missing_model' || status === 'model_missing') return 'bad'
  if (status === 'class_names_missing') return 'warn'
  return 'neutral'
}

const directModelRuntime = computed(() => Boolean(runtime.visionStatus.active_model_details.direct_runtime))
const productionModelMissing = computed(() => !runtime.visionStatus.production_yolo_loaded && !directModelRuntime.value)
const activePackage = computed(() => modelPackages.activePackage)
const expectedCompetitionClasses = 'f16, helicopter, ballistic_missile, mini_micro_uav, balloon'
const runtimeAdapterLabel = computed(() => {
  if (runtime.visionStatus.production_yolo_loaded || directModelRuntime.value) return runtime.visionStatus.effective_adapter
  if (surrogateActive.value) return runtime.visionStatus.effective_adapter
  if (runtime.visionStatus.test_adapter_active) return 'test_adapter'
  return 'YOLO runtime inactive / production model not loaded'
})
const runtimeCompatibility = computed(() => {
  const pkg = activePackage.value
  if (!pkg?.metadata || !pkg.thresholds) return 'recommended not_available / current runtime visible below'
  return `recommended imgsz ${pkg.metadata.recommended_imgsz}, conf ${pkg.thresholds.default_conf}, iou ${pkg.thresholds.default_iou}, max_det ${pkg.thresholds.max_det}`
})
const lastModelTestNoPhysical = computed(() => {
  const result = runtime.lastVisionResult
  if (result && typeof result === 'object' && 'no_physical_command_generated' in result) {
    return String((result as { no_physical_command_generated?: boolean }).no_physical_command_generated ?? false)
  }
  return 'not run'
})
const activeLegacyPreset = computed(() => vision.legacyPresets.presets.find((preset) => preset.hsv_lower) ?? vision.legacyPresets.presets[0] ?? null)
const realEvidence = computed(() => vision.latestRealCameraEvidence ?? null)
const detectionMetadataPreview = computed(() => {
  const metadata = realEvidence.value?.target_center_metadata
  return metadata && Object.keys(metadata).length ? metadata : { status: 'not_available', reason: 'real camera evidence not recorded' }
})
const measuredCameraFps = computed(() => frame.value?.camera_fps ?? vision.visionStatus.camera_fps ?? runtime.cameraStatus.actual_fps_measured)
const measuredDetectorFps = computed(() => frame.value?.detector_fps ?? vision.visionStatus.detector_fps ?? vision.visionStatus.fps)
const measuredLatencyMs = computed(() => frame.value?.inference_ms ?? vision.visionStatus.latest_latency_ms)
const measuredModelCount = computed(() => frame.value?.model_count ?? vision.visionStatus.model_count)
const measuredModelExecution = computed(() => frame.value?.model_execution ?? vision.visionStatus.model_execution)
function metric(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}

onMounted(() => {
  void vision.refresh()
  void color.refresh()
  void dataLab.refresh()
  void runtime.refresh()
  void modelPackages.refresh()
  void motion.refreshTrackingStatus().then(() => {
    syncPidDraftFromStatus()
  })
  // Keep the camera overlay metrics live even when the global WebSocket is
  // reconnecting or this route is opened directly. The backend worker owns
  // inference; this timer only reads its latest measured event.
  liveTelemetryTimer = window.setInterval(() => {
    // WebSocket already carries the latest worker event and measured FPS.
    // Poll only while the socket is reconnecting or when this view is opened
    // in a standalone shell without a live telemetry connection.
    if (system.connectionStatus !== 'connected') void vision.refreshLive()
  }, 500)
})

onBeforeUnmount(() => {
  if (pidApplyTimer) clearTimeout(pidApplyTimer)
  if (liveTelemetryTimer) window.clearInterval(liveTelemetryTimer)
  liveTelemetryTimer = null
})

async function applyMotorSpeed(): Promise<void> {
  await applyPidConfig()
}

async function startLaptopCameraTest(): Promise<void> {
  laptopTestBusy.value = true
  laptopTestMessage.value = 'Kamera aranıyor…'
  try {
    await runtime.refresh()
    const camera = runtime.inventory.cameras.find((item) => item.device_path.endsWith('/video0'))
      ?? runtime.inventory.cameras.find((item) => item.device_path.endsWith('/video1'))
      ?? runtime.inventory.cameras[0]
    const devicePath = camera?.device_path ?? runtime.cameraStatus.selected_camera
    if (!devicePath || devicePath === 'mock') {
      laptopTestMessage.value = 'LAPTOP_CAMERA_NOT_FOUND — /devices ekranından kamera iznini kontrol et.'
      return
    }
    runtime.cameraDraft = {
      ...runtime.cameraStatus.profile,
      source_type: 'laptop',
      device_id: camera?.device_id ?? devicePath,
      device_path: devicePath,
      stable_path: camera?.stable_path ?? null,
      width: 640,
      height: 360,
      fps: 15,
      stream_width: 640,
      stream_height: 360,
      inference_width: 640,
      inference_height: 360,
    }
    await runtime.applyCamera()
    runtime.visionDraft = {
      ...runtime.visionStatus.profile,
      inference_adapter: 'opencv_live_circle_surrogate',
      device: 'auto',
    }
    await runtime.applyVision()
    await vision.start()
    await runtime.refresh()
    laptopTestMessage.value = runtime.cameraStatus.is_laptop_camera
      ? `HAZIR — ${runtime.cameraStatus.selected_camera ?? devicePath} / gerçek laptop frame / fiziksel çıkış yok`
      : `KAMERA UYARISI — ${runtime.cameraStatus.last_capture_error ?? runtime.cameraStatus.warnings[0] ?? 'kamera kaynağını kontrol et'}`
  } catch (error) {
    laptopTestMessage.value = error instanceof Error ? error.message : 'LAPTOP_CAMERA_TEST_FAILED'
  } finally {
    laptopTestBusy.value = false
  }
}

function syncPidDraftFromStatus(): void {
  pidDraft.value = {
    pid_kp_x: motion.trackingStatus.pid_kp_x,
    pid_ki_x: motion.trackingStatus.pid_ki_x,
    pid_kd_x: motion.trackingStatus.pid_kd_x,
    pid_kp_y: motion.trackingStatus.pid_kp_y,
    pid_ki_y: motion.trackingStatus.pid_ki_y,
    pid_kd_y: motion.trackingStatus.pid_kd_y,
    smoothing_alpha: motion.trackingStatus.smoothing_alpha,
    command_rate_hz: motion.trackingStatus.command_rate_hz,
    max_speed: motion.trackingStatus.max_speed,
    invert_x: motion.trackingStatus.invert_x,
    invert_y: motion.trackingStatus.invert_y,
  }
}

function pidPayload(): TrackingConfigUpdate {
  return {
    pid_kp_x: Number(pidDraft.value.pid_kp_x),
    pid_ki_x: Number(pidDraft.value.pid_ki_x),
    pid_kd_x: Number(pidDraft.value.pid_kd_x),
    pid_kp_y: Number(pidDraft.value.pid_kp_y),
    pid_ki_y: Number(pidDraft.value.pid_ki_y),
    pid_kd_y: Number(pidDraft.value.pid_kd_y),
    smoothing_alpha: Math.max(0, Math.min(1, Number(pidDraft.value.smoothing_alpha))),
    command_rate_hz: Math.max(5, Math.min(60, Number(pidDraft.value.command_rate_hz))),
    max_speed: Math.max(20, Math.min(2000, Math.trunc(Number(pidDraft.value.max_speed)))),
    invert_x: Boolean(pidDraft.value.invert_x),
    invert_y: Boolean(pidDraft.value.invert_y),
  }
}

async function applyPidConfig(): Promise<void> {
  pidApplyBusy.value = true
  try {
    await motion.updateTrackingConfig(pidPayload())
    pidLastApplied.value = new Date().toLocaleTimeString('tr-TR')
  } finally {
    pidApplyBusy.value = false
  }
}

function schedulePidApply(): void {
  if (!pidAutoApply.value) return
  if (pidApplyTimer) clearTimeout(pidApplyTimer)
  pidApplyTimer = setTimeout(() => {
    void applyPidConfig()
  }, 250)
}

function applyPidPreset(kind: 'soft' | 'fast' | 'stable'): void {
  const presets = {
    soft: { pid_kp_x: 1200, pid_ki_x: 0, pid_kd_x: 60, pid_kp_y: 1050, pid_ki_y: 0, pid_kd_y: 60, smoothing_alpha: 0.85, command_rate_hz: 30, max_speed: 800 },
    stable: { pid_kp_x: 1800, pid_ki_x: 0, pid_kd_x: 80, pid_kp_y: 1600, pid_ki_y: 0, pid_kd_y: 80, smoothing_alpha: 0.9, command_rate_hz: 30, max_speed: 1000 },
    fast: { pid_kp_x: 2200, pid_ki_x: 0, pid_kd_x: 110, pid_kp_y: 2000, pid_ki_y: 0, pid_kd_y: 100, smoothing_alpha: 1, command_rate_hz: 35, max_speed: 1000 },
  }[kind]
  pidDraft.value = { ...pidDraft.value, ...presets }
  void applyPidConfig()
}

watch(
  pidDraft,
  () => schedulePidApply(),
  { deep: true },
)

async function selectBalloonTarget(balloon: { id: number, center_x: number, center_y: number }): Promise<void> {
  targetSelectBusy.value = true
  selectedBalloonId.value = balloon.id
  try {
    await motion.selectTarget({
      x: balloon.center_x,
      y: balloon.center_y,
      detection_id: balloon.id,
      frame_id: frame.value?.frame_id,
    })
  } finally {
    targetSelectBusy.value = false
  }
}
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-cyan-400/25 bg-cyan-400/8 px-4 py-3 text-sm text-cyan-100">
      Vision output is advisory only. No fire command is generated in this phase.
    </div>

    <section class="rounded-xl border border-emerald-400/30 bg-emerald-400/8 p-4">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 class="text-base font-semibold text-emerald-100">Laptop kamera hızlı testi</h2>
          <p class="mt-1 text-sm text-slate-300">Tek tıkla laptop kamerasını seçer, OpenCV canlı daire algısını açar ve görüntü akışını başlatır.</p>
          <p class="mt-1 font-mono text-xs text-emerald-200">DRY_RUN · no physical command generated · yarışma YOLO kanıtı değildir</p>
        </div>
        <button class="focus-ring rounded-lg bg-emerald-400 px-4 py-3 text-sm font-bold text-slate-950 disabled:opacity-50" :disabled="laptopTestBusy" @click="startLaptopCameraTest">
          {{ laptopTestBusy ? 'KAMERA HAZIRLANIYOR…' : 'LAPTOP KAMERA TESTİNİ BAŞLAT' }}
        </button>
      </div>
      <p class="mt-3 rounded-md border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-slate-200">{{ laptopTestMessage }}</p>
    </section>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Camera Status" subtitle="Mock camera default">
        <MetricRow label="Camera mode" :value="vision.cameraStatus.camera_mode" />
        <MetricRow label="Camera connected" :value="vision.cameraStatus.connected" />
        <MetricRow label="Camera stream state" :value="vision.cameraStatus.running ? 'running' : 'stopped'" />
        <MetricRow label="Stream enabled" :value="vision.cameraStatus.stream_enabled" />
        <MetricRow label="Size" :value="`${vision.cameraStatus.width}x${vision.cameraStatus.height}`" />
      </DashboardCard>

      <DashboardCard title="Canlı Motor Hızı" subtitle="Tracking max speed">
        <div class="grid gap-3">
          <label class="grid gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Max Speed: <span class="text-sm normal-case tracking-normal text-cyan-100">{{ pidDraft.max_speed }}</span>
            <input
              v-model.number="pidDraft.max_speed"
              type="range"
              min="80"
              max="2000"
              step="20"
              class="w-full accent-cyan-400"
            />
          </label>
          <div class="flex items-center justify-between gap-3 text-xs text-slate-400">
            <span>Yavaş</span>
            <span>Mevcut: {{ motion.trackingStatus.max_speed }}</span>
            <span>Hızlı</span>
          </div>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="applyMotorSpeed">
            Hızı Uygula
          </button>
        </div>
      </DashboardCard>

      <DashboardCard title="PID Canlı Tuning" subtitle="Değişince tracking'e anında uygulanır">
        <div class="grid gap-3">
          <div class="grid grid-cols-3 gap-2">
            <label class="grid gap-1 text-xs text-slate-400">Kp X<input v-model.number="pidDraft.pid_kp_x" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <label class="grid gap-1 text-xs text-slate-400">Ki X<input v-model.number="pidDraft.pid_ki_x" type="number" step="0.001" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <label class="grid gap-1 text-xs text-slate-400">Kd X<input v-model.number="pidDraft.pid_kd_x" type="number" step="0.1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <label class="grid gap-1 text-xs text-slate-400">Kp Y<input v-model.number="pidDraft.pid_kp_y" type="number" step="1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <label class="grid gap-1 text-xs text-slate-400">Ki Y<input v-model.number="pidDraft.pid_ki_y" type="number" step="0.001" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
            <label class="grid gap-1 text-xs text-slate-400">Kd Y<input v-model.number="pidDraft.pid_kd_y" type="number" step="0.1" class="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-white" /></label>
          </div>

          <label class="grid gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Smoothing α: <span class="text-sm normal-case tracking-normal text-cyan-100">{{ pidDraft.smoothing_alpha }}</span>
            <input v-model.number="pidDraft.smoothing_alpha" type="range" min="0" max="1" step="0.01" class="w-full accent-cyan-400" />
          </label>

          <label class="grid gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
            Komut Hz: <span class="text-sm normal-case tracking-normal text-cyan-100">{{ pidDraft.command_rate_hz }}</span>
            <input v-model.number="pidDraft.command_rate_hz" type="range" min="5" max="60" step="1" class="w-full accent-cyan-400" />
          </label>

          <div class="flex flex-wrap items-center gap-3 text-sm text-slate-300">
            <label class="flex items-center gap-2"><input v-model="pidAutoApply" type="checkbox" /> Canlı uygula</label>
            <label class="flex items-center gap-2"><input v-model="pidDraft.invert_x" type="checkbox" /> X ters</label>
            <label class="flex items-center gap-2"><input v-model="pidDraft.invert_y" type="checkbox" /> Y ters</label>
          </div>

          <div class="grid grid-cols-3 gap-2">
            <button class="focus-ring rounded-md bg-slate-700 px-2 py-2 text-xs font-semibold text-white hover:bg-slate-600" @click="applyPidPreset('soft')">Yumuşak</button>
            <button class="focus-ring rounded-md bg-slate-700 px-2 py-2 text-xs font-semibold text-white hover:bg-slate-600" @click="applyPidPreset('stable')">Mevcut</button>
            <button class="focus-ring rounded-md bg-slate-700 px-2 py-2 text-xs font-semibold text-white hover:bg-slate-600" @click="applyPidPreset('fast')">Agresif</button>
          </div>

          <div class="flex items-center justify-between gap-3">
            <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-60" :disabled="pidApplyBusy" @click="applyPidConfig">
              {{ pidApplyBusy ? 'Uygulanıyor' : 'Uygula' }}
            </button>
            <span class="text-xs text-slate-400">Son: {{ pidLastApplied }}</span>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Camera Runtime" subtitle="Live source settings">
        <MetricRow label="Source" :value="runtime.cameraStatus.profile.source_type" />
        <MetricRow label="Selected" :value="runtime.cameraStatus.selected_camera" />
        <MetricRow label="Requested" :value="`${runtime.cameraStatus.requested_width}x${runtime.cameraStatus.requested_height}@${runtime.cameraStatus.requested_fps}`" />
        <MetricRow label="Actual" :value="`${runtime.cameraStatus.actual_width}x${runtime.cameraStatus.actual_height}@${runtime.cameraStatus.actual_fps_measured}`" />
        <MetricRow label="Inference size" :value="`${runtime.cameraStatus.profile.inference_width}x${runtime.cameraStatus.profile.inference_height}`" />
      </DashboardCard>

      <DashboardCard title="Vision Pipeline" subtitle="YOLO integration point is optional">
        <MetricRow label="Vision inference state" :value="vision.visionStatus.running ? 'running' : 'stopped'" />
        <MetricRow label="Detection source" :value="detectionSource" />
        <MetricRow label="Body model loaded" :value="vision.visionStatus.body_model_loaded" />
        <MetricRow label="Balloon model loaded" :value="vision.visionStatus.balloon_model_loaded" />
        <MetricRow label="Advisory only" :value="vision.visionStatus.advisory_only" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="overlaySource" tone="warn" />
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
        </div>
        <div class="mt-4 flex gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="vision.start">
            Start Vision
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="vision.stop">
            Stop
          </button>
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="vision.snapshot">
            Snapshot
          </button>
        </div>
      </DashboardCard>

      <DashboardCard title="FPS / Latency" subtitle="Latest vision event">
        <MetricRow label="Camera FPS (measured)" :value="`${metric(measuredCameraFps)} FPS`" />
        <MetricRow label="Detector FPS (measured)" :value="`${metric(measuredDetectorFps)} FPS`" />
        <MetricRow label="Preprocess" :value="`${frame?.preprocess_ms ?? 0} ms`" />
        <MetricRow label="Inference" :value="`${metric(measuredLatencyMs, 2)} ms`" />
        <MetricRow label="Postprocess" :value="`${frame?.postprocess_ms ?? 0} ms`" />
        <MetricRow label="Total" :value="`${vision.visionStatus.latest_total_ms ?? vision.visionStatus.latest_latency_ms} ms`" />
        <MetricRow label="Models" :value="`${measuredModelCount} · ${measuredModelExecution}`" />
        <MetricRow label="Per-model timing" :value="JSON.stringify(frame?.model_timings_ms ?? {})" />
        <MetricRow label="Frame origin" :value="vision.visionStatus.frame_origin ?? runtime.visionStatus.frame_origin ?? 'not_available'" />
      </DashboardCard>

      <DashboardCard title="Active Models" subtitle="Vision team adapter summary">
        <MetricRow label="Body model" :value="dataLab.activeModels.active_body_model_id ?? 'none'" />
        <MetricRow label="Balloon model" :value="dataLab.activeModels.active_balloon_model_id ?? 'none'" />
        <MetricRow label="Combined model" :value="dataLab.activeModels.active_combined_model_id ?? runtime.visionStatus.model_package_id ?? 'none'" />
        <MetricRow label="Active adapter" :value="runtime.visionStatus.effective_adapter" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge v-if="runtime.visionStatus.test_adapter_active" label="TEST ADAPTER ACTIVE, NOT PRODUCTION YOLO" tone="warn" />
          <StatusBadge v-if="runtime.visionStatus.production_yolo_loaded" label="PRODUCTION YOLO ACTIVE" tone="good" />
          <StatusBadge v-else-if="directModelRuntime" label="DIRECT YOLO ACTIVE · METADATA PENDING" tone="good" />
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
        </div>
      </DashboardCard>

      <DashboardCard title="YOLO Runtime" subtitle="Adapter and live parameters">
        <MetricRow label="Selected adapter" :value="runtime.visionStatus.selected_adapter" />
        <MetricRow label="Effective adapter" :value="runtimeAdapterLabel" />
        <MetricRow label="YOLO weights loaded" :value="runtime.visionStatus.production_yolo_loaded || directModelRuntime" />
        <MetricRow label="Package verification" :value="runtime.visionStatus.production_yolo_loaded ? 'verified' : directModelRuntime ? 'direct file · metadata pending' : 'not loaded'" />
        <MetricRow label="Device" :value="runtime.visionStatus.profile.device" />
        <MetricRow label="imgsz" :value="runtime.visionStatus.profile.imgsz" />
        <MetricRow label="conf/iou" :value="`${runtime.visionStatus.profile.conf} / ${runtime.visionStatus.profile.iou}`" />
        <MetricRow label="Runtime source" :value="runtime.visionStatus.runtime_source" />
        <StatusBadge v-if="runtime.visionStatus.test_adapter_active" label="OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir." tone="warn" />
        <div v-if="surrogateActive" class="mt-3 grid gap-2 rounded-md border border-amber-400/30 bg-amber-400/10 p-3">
          <StatusBadge :label="surrogateSourceKind === 'mock' ? 'MOCK CAMERA SURROGATE' : 'LIVE CAMERA SURROGATE'" tone="warn" />
          <StatusBadge :label="surrogateSourceKind === 'mock' ? 'MOCK/SYNTHETIC EVIDENCE' : 'REAL CAMERA FRAME EVIDENCE'" :tone="surrogateSourceKind === 'mock' ? 'warn' : 'good'" />
          <StatusBadge label="NOT PRODUCTION YOLO" tone="bad" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
          <StatusBadge label="UI/PIPELINE TEST ONLY" tone="neutral" />
        </div>
      </DashboardCard>

      <DashboardCard title="Active Model Panel" subtitle="Competition model state">
        <div v-if="productionModelMissing" class="mb-3 rounded-md border border-red-400/40 bg-red-500/12 px-3 py-3 text-sm font-semibold text-red-100">
          Production YOLO modeli yüklü değil. OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.
        </div>
        <MetricRow label="active_model_id" :value="modelDetail('active_model_id')" />
        <MetricRow label="package" :value="activePackage?.package_name ?? 'none'" />
        <MetricRow label="model_file" :value="modelDetail('model_file')" />
        <MetricRow label="model_type" :value="modelDetail('model_type')" />
        <MetricRow label="checksum" :value="activePackage?.checksum_sha256?.slice(0, 16) ?? 'none'" />
        <div class="border-t border-white/8 py-2 text-sm">
          <span class="text-slate-400">expected_classes</span>
          <p class="mt-1 break-words font-mono text-xs text-slate-200">{{ modelDetail('expected_classes') }}</p>
        </div>
        <div class="border-t border-white/8 py-2 text-sm">
          <span class="text-slate-400">competition_expected_classes</span>
          <p class="mt-1 break-words font-mono text-xs text-slate-200">{{ expectedCompetitionClasses }}</p>
        </div>
        <div class="border-t border-white/8 py-2 text-sm">
          <span class="text-slate-400">detected_classes</span>
          <p class="mt-1 break-words font-mono text-xs text-slate-200">{{ modelDetail('detected_classes') }}</p>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
          <span class="text-slate-400">class_mapping_status</span>
          <StatusBadge :label="modelDetail('class_mapping_status')" :tone="mappingTone(modelDetail('class_mapping_status'))" />
        </div>
        <MetricRow label="loaded" :value="modelDetail('loaded')" />
        <MetricRow label="last_test_status" :value="modelDetail('last_test_status')" />
        <MetricRow label="adapter_mode" :value="modelDetail('adapter_mode')" />
        <MetricRow label="runtime compatibility" :value="runtimeCompatibility" />
        <MetricRow label="recommended_imgsz" :value="activePackage?.metadata?.recommended_imgsz ?? 'none'" />
        <MetricRow label="recommended conf/iou" :value="activePackage ? `${activePackage.thresholds?.default_conf} / ${activePackage.thresholds?.default_iou}` : 'none'" />
        <MetricRow label="Model test no physical command" :value="lastModelTestNoPhysical" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-xs font-semibold text-cyan-100" @click="runtime.verifyActiveVision()">Verify active</button>
          <button class="focus-ring rounded-md border border-emerald-400/40 bg-emerald-400/12 px-3 py-2 text-xs font-semibold text-emerald-100" @click="runtime.testActiveModel()">Model test</button>
          <button class="focus-ring rounded-md border border-amber-400/40 bg-amber-400/12 px-3 py-2 text-xs font-semibold text-amber-100 disabled:opacity-50" :disabled="!activePackage" @click="modelPackages.applyRecommended()">Apply recommended settings</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Legacy Perception Presets" subtitle="Audit-derived advisory settings">
        <MetricRow label="Preset count" :value="vision.legacyPresets.presets.length" />
        <MetricRow label="Active advisory preset" :value="activeLegacyPreset?.preset_id ?? 'not_available'" />
        <MetricRow label="Source audit file" :value="activeLegacyPreset?.source_file ?? 'not_available'" />
        <MetricRow label="HSV lower" :value="JSON.stringify(activeLegacyPreset?.hsv_lower ?? 'not_configured')" />
        <MetricRow label="HSV upper" :value="JSON.stringify(activeLegacyPreset?.hsv_upper ?? 'not_configured')" />
        <MetricRow label="Target selection" :value="activeLegacyPreset?.target_selection_rule ?? 'not_available'" />
        <MetricRow label="Kalman metadata" :value="activeLegacyPreset?.kalman_enabled ?? false" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
          <StatusBadge label="no_physical_command_generated=true" tone="good" />
        </div>
      </DashboardCard>

      <DashboardCard title="Real Camera Evidence" subtitle="No mock fallback for this check">
        <MetricRow label="Acceptance status" :value="vision.realCameraAcceptance.status" />
        <MetricRow label="Tooling status" :value="vision.realCameraAcceptance.camera_tooling_status" />
        <MetricRow label="Selected camera" :value="vision.realCameraAcceptance.selected_camera_device ?? vision.cameraHostDiagnostic.selected_camera_device ?? 'not_selected'" />
        <MetricRow label="Camera kind" :value="vision.realCameraAcceptance.camera_kind" />
        <MetricRow label="Frame captured" :value="vision.realCameraAcceptance.frame_captured" />
        <MetricRow label="Device path" :value="vision.realCameraAcceptance.device_path ?? 'not_available'" />
        <MetricRow label="Capture method" :value="vision.realCameraAcceptance.capture_method ?? 'not_available'" />
        <MetricRow label="Frame path" :value="vision.realCameraAcceptance.frame_path ?? 'not_available'" />
        <MetricRow label="Frame size" :value="vision.realCameraAcceptance.width && vision.realCameraAcceptance.height ? `${vision.realCameraAcceptance.width}x${vision.realCameraAcceptance.height}` : 'not_available'" />
        <MetricRow label="Frame hash" :value="vision.realCameraAcceptance.frame_hash ?? 'not_available'" />
        <MetricRow label="Internal camera passed" :value="vision.realCameraAcceptance.internal_camera_passed" />
        <MetricRow label="External USB passed" :value="vision.realCameraAcceptance.external_usb_camera_passed" />
        <MetricRow label="Status" :value="vision.realCameraEvidenceStatus.status" />
        <MetricRow label="Camera source" :value="vision.realCameraEvidenceStatus.camera_source" />
        <MetricRow label="Frame origin" :value="vision.realCameraEvidenceStatus.frame_origin" />
        <MetricRow label="Detector" :value="vision.realCameraEvidenceStatus.detector" />
        <MetricRow label="Latest evidence" :value="vision.realCameraEvidenceStatus.latest_evidence_id ?? 'not_recorded'" />
        <MetricRow label="Detections" :value="vision.realCameraEvidenceStatus.detections_count" />
        <MetricRow label="FPS estimate" :value="vision.realCameraEvidenceStatus.fps_estimate ?? 'not_available'" />
        <MetricRow label="physical_command_enabled" :value="vision.realCameraEvidenceStatus.physical_command_enabled" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-xs font-semibold text-cyan-100" @click="vision.captureRealEvidence(activeLegacyPreset?.preset_id)">
            Capture evidence
          </button>
          <button class="focus-ring rounded-md border border-emerald-400/40 bg-emerald-400/12 px-3 py-2 text-xs font-semibold text-emerald-100" @click="vision.captureUsbEvidence(activeLegacyPreset?.preset_id)">
            Capture from USB Camera
          </button>
          <StatusBadge label="no_physical_command_generated=true" tone="good" />
        </div>
        <p v-if="vision.realCameraEvidenceStatus.warnings.length" class="mt-3 break-words text-xs text-amber-100">
          {{ vision.realCameraEvidenceStatus.warnings.join(' ') }}
        </p>
      </DashboardCard>

      <DashboardCard title="Camera Host Discovery" subtitle="Linux camera blocker diagnosis">
        <MetricRow label="Acceptance status" :value="vision.cameraHostDiagnostic.camera_acceptance_status" />
        <MetricRow label="Host devices detected" :value="vision.cameraHostDiagnostic.host_camera_devices_detected" />
        <MetricRow label="/dev/video entries" :value="vision.cameraHostDiagnostic.dev_video_entries.length ? vision.cameraHostDiagnostic.dev_video_entries.join(', ') : 'none'" />
        <MetricRow label="Recommended USB path" :value="vision.cameraHostDiagnostic.recommended_usb_device_path ?? 'not_available'" />
        <MetricRow label="Selected camera" :value="vision.cameraHostDiagnostic.selected_camera_device ?? 'not_selected'" />
        <MetricRow label="Selected kind" :value="vision.cameraHostDiagnostic.camera_kind" />
        <MetricRow label="v4l2 available" :value="vision.cameraHostDiagnostic.v4l2_available" />
        <MetricRow label="ffmpeg available" :value="vision.cameraHostDiagnostic.ffmpeg_available" />
        <MetricRow label="User in video group" :value="vision.cameraHostDiagnostic.user_in_video_group" />
        <MetricRow label="Ubuntu camera app note" :value="vision.cameraHostDiagnostic.camera_app_not_seen_note" />
        <MetricRow label="Capture attempted" :value="vision.cameraHostDiagnostic.real_camera_capture_attempted" />
        <MetricRow label="Frame captured" :value="vision.cameraHostDiagnostic.real_camera_frame_captured" />
        <MetricRow label="physical_command_enabled" :value="vision.cameraHostDiagnostic.physical_command_enabled" />
        <MetricRow label="no_physical_command_generated" :value="vision.cameraHostDiagnostic.no_physical_command_generated" />
        <div class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100">
          <p class="break-words">Blocker: {{ vision.cameraHostDiagnostic.blocker_reason }}</p>
          <p class="mt-2">Mock/surrogate evidence is not real camera acceptance.</p>
        </div>
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-xs font-semibold text-cyan-100" @click="vision.diagnoseHostCamera">
            Diagnose host camera
          </button>
          <button class="focus-ring rounded-md border border-emerald-400/40 bg-emerald-400/12 px-3 py-2 text-xs font-semibold text-emerald-100" @click="vision.selectUsbCamera">
            Select USB Camera
          </button>
          <StatusBadge label="READ-ONLY HOST DISCOVERY" tone="warn" />
          <StatusBadge label="no_physical_command_generated=true" tone="good" />
        </div>
        <div v-if="vision.cameraHostDiagnostic.camera_groups.length" class="mt-3 space-y-2">
          <div v-for="group in vision.cameraHostDiagnostic.camera_groups" :key="`${group.camera_kind}-${group.preferred_capture_path}`" class="rounded-md border border-white/10 bg-white/5 p-3 text-xs text-slate-200">
            <p class="font-semibold text-white">{{ group.name }} - {{ group.camera_kind }}</p>
            <p class="break-words">Paths: {{ group.paths.join(', ') || 'none' }}</p>
            <p>Preferred capture path: {{ group.preferred_capture_path ?? 'not_available' }}</p>
          </div>
        </div>
        <div v-if="vision.cameraHostDiagnostic.commands.length" class="mt-3 max-h-44 overflow-auto rounded-md bg-black/30 p-3 text-xs text-slate-300">
          <p v-for="command in vision.cameraHostDiagnostic.commands" :key="command.command" class="break-words">
            <span class="font-mono text-cyan-100">{{ command.command }}</span> - {{ command.status }}
          </p>
        </div>
      </DashboardCard>

      <DashboardCard title="Detection Metadata Preview" subtitle="Metadata only; no command output">
        <pre class="max-h-[220px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(detectionMetadataPreview, null, 2) }}</pre>
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="TARGET CENTER METADATA ONLY" tone="neutral" />
          <StatusBadge label="NO MOTOR / NO FIRE" tone="bad" />
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Camera Source Settings" subtitle="Apply atomically with rollback on failure">
        <div class="grid gap-3 md:grid-cols-2">
          <label class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Source</label>
          <select v-model="runtime.cameraDraft.source_type" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="mock">mock</option>
            <option value="laptop">laptop cam</option>
            <option value="usb">USB camera</option>
            <option value="replay">replay</option>
            <option value="video_file">video file</option>
          </select>
          <label class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Device</label>
          <select v-model="runtime.cameraDraft.device_id" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option :value="null">mock / none</option>
            <option v-for="camera in runtime.inventory.cameras" :key="camera.device_id" :value="camera.device_id">{{ camera.device_path }} - {{ camera.description }}</option>
          </select>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Capture width<input v-model.number="runtime.cameraDraft.width" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Capture height<input v-model.number="runtime.cameraDraft.height" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Requested FPS<input v-model.number="runtime.cameraDraft.fps" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Pixel format<select v-model="runtime.cameraDraft.pixel_format" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option value="auto">auto</option>
            <option value="MJPG">MJPG</option>
            <option value="YUYV">YUYV</option>
          </select></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Stream width<input v-model.number="runtime.cameraDraft.stream_width" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Stream height<input v-model.number="runtime.cameraDraft.stream_height" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Inference width<input v-model.number="runtime.cameraDraft.inference_width" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Inference height<input v-model.number="runtime.cameraDraft.inference_height" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Lens profile<select v-model="runtime.cameraDraft.lens_profile" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option value="unknown">unknown lens</option>
            <option value="3.6mm">3.6mm</option>
            <option value="8mm">8mm</option>
            <option value="12mm">12mm</option>
          </select></label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.cameraDraft.roi.enabled" type="checkbox" /> ROI enabled</label>
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="runtime.applyCamera">Apply camera profile</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="runtime.probeCurrent">Probe current</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="runtime.benchmarkCamera">Benchmark</button>
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="runtime.snapshotCamera">Snapshot</button>
          <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="runtime.resetCamera">Reset</button>
        </div>
        <pre class="mt-3 max-h-[180px] overflow-auto rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(runtime.lastCameraResult ?? runtime.cameraStatus.warnings, null, 2) }}</pre>
      </DashboardCard>

      <DashboardCard title="Inference / YOLO Runtime Settings" subtitle="Interface settings only, no model training">
        <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
          OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.
        </div>
        <div class="mt-3 grid gap-3 md:grid-cols-2">
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Runtime preset<select v-model="selectedPreset" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option v-for="preset in runtime.presets" :key="preset.name" :value="preset.name">{{ preset.name }}</option>
          </select></label>
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100" @click="runtime.applyPreset(selectedPreset)">Apply preset</button>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Adapter<select v-model="runtime.visionDraft.inference_adapter" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option value="mock">mock</option>
            <option value="opencv_circle_test">opencv circle test</option>
            <option value="opencv_live_circle_surrogate">OpenCV Live Circle Surrogate</option>
            <option value="ultralytics_yolo">ultralytics YOLO</option>
          </select></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Device<select v-model="runtime.visionDraft.device" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option value="cpu">cpu</option>
            <option value="auto">auto</option>
            <option value="cuda">cuda (host + config doğrulaması gerekir)</option>
          </select></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">imgsz<input v-model.number="runtime.visionDraft.imgsz" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">max_det<input v-model.number="runtime.visionDraft.max_det" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="text-sm text-slate-300">conf {{ runtime.visionDraft.conf }}<input v-model.number="runtime.visionDraft.conf" type="range" min="0" max="1" step="0.01" class="w-full" /></label>
          <label class="text-sm text-slate-300">iou {{ runtime.visionDraft.iou }}<input v-model.number="runtime.visionDraft.iou" type="range" min="0" max="1" step="0.01" class="w-full" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">frame_skip<input v-model.number="runtime.visionDraft.frame_skip" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">vid_stride<input v-model.number="runtime.visionDraft.vid_stride" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.half" type="checkbox" /> half</label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.parallel_models" type="checkbox" /> parallel model inference</label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.agnostic_nms" type="checkbox" /> agnostic NMS</label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.tracker_enabled" type="checkbox" /> tracker enabled</label>
          <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Tracker type<select v-model="runtime.visionDraft.tracker_type" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
            <option value="none">none</option>
            <option value="bytetrack">bytetrack</option>
            <option value="botsort">botsort</option>
          </select></label>
        </div>
        <div class="mt-4 rounded-md border border-amber-400/30 bg-amber-400/8 p-3">
          <div class="mb-2 flex flex-wrap gap-2">
            <StatusBadge label="OpenCV Live Circle Surrogate" tone="warn" />
            <StatusBadge label="SURROGATE ONLY" tone="warn" />
            <StatusBadge label="NOT PRODUCTION YOLO" tone="bad" />
          </div>
          <div class="grid gap-3 md:grid-cols-2">
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">min_radius<input v-model.number="runtime.visionDraft.circle_min_radius" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">max_radius<input v-model.number="runtime.visionDraft.circle_max_radius" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">blur/kernel<input v-model.number="runtime.visionDraft.circle_blur_kernel" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">threshold<input v-model.number="runtime.visionDraft.circle_threshold" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">edge param<input v-model.number="runtime.visionDraft.circle_edge_param" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">min_area<input v-model.number="runtime.visionDraft.circle_min_area" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white" /></label>
            <label class="text-sm text-slate-300">circularity {{ runtime.visionDraft.circle_circularity }}<input v-model.number="runtime.visionDraft.circle_circularity" type="range" min="0" max="1" step="0.01" class="w-full" /></label>
            <label class="grid gap-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">target color mode<select v-model="runtime.visionDraft.circle_target_color_mode" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm font-normal normal-case tracking-normal text-white">
              <option value="any">any</option>
              <option value="red">red</option>
              <option value="green">green</option>
              <option value="blue">blue</option>
              <option value="bright">bright</option>
            </select></label>
            <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.circle_roi_enabled" type="checkbox" /> ROI enabled</label>
            <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="runtime.visionDraft.circle_smoothing" type="checkbox" /> smoothing</label>
          </div>
          <p class="mt-3 text-xs text-amber-100">OpenCV yuvarlak algılayıcı yalnızca arayüz/görüntü aktarımı/overlay/loglama testi içindir; production YOLO veya yarışma modeli değildir.</p>
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="runtime.applyVision">Apply settings</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="runtime.warmup">Warmup</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="runtime.benchmarkVision">Benchmark</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="runtime.reloadModels">Reload models</button>
          <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="runtime.resetVision">Reset</button>
        </div>
        <pre class="mt-3 max-h-[180px] overflow-auto rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(runtime.lastVisionResult ?? runtime.visionStatus.warnings, null, 2) }}</pre>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.4fr_0.6fr]">
      <section class="rounded-md border border-white/10 bg-[#14181d] p-4">
        <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="text-base font-semibold text-white">Stream Overlay</h3>
            <p class="mt-1 text-xs text-slate-400">MJPEG stream with SVG overlay</p>
          </div>
          <StatusBadge :label="vision.visionStatus.running ? 'RUNNING' : 'STOPPED'" :tone="vision.visionStatus.running ? 'good' : 'warn'" />
        </div>

        <div class="relative overflow-hidden rounded-md border border-white/10 bg-black" :style="{ aspectRatio: `${overlayWidth} / ${overlayHeight}` }">
          <img :src="vision.streamUrl" class="h-full w-full object-contain opacity-80" alt="Camera stream" />
          <svg class="absolute inset-0 h-full w-full" :viewBox="viewBox">
            <g>
              <line
                :x1="overlayCenterX"
                y1="0"
                :x2="overlayCenterX"
                :y2="overlayHeight"
                stroke="#22d3ee"
                stroke-width="1.5"
                stroke-opacity="0.75"
                stroke-dasharray="10 8"
              />
              <line
                x1="0"
                :y1="overlayCenterY"
                :x2="overlayWidth"
                :y2="overlayCenterY"
                stroke="#22d3ee"
                stroke-width="1.5"
                stroke-opacity="0.75"
                stroke-dasharray="10 8"
              />
              <circle :cx="overlayCenterX" :cy="overlayCenterY" r="18" fill="none" stroke="#22d3ee" stroke-width="2.5" />
              <line :x1="overlayCenterX - 30" :y1="overlayCenterY" :x2="overlayCenterX - 8" :y2="overlayCenterY" stroke="#e0f2fe" stroke-width="3" />
              <line :x1="overlayCenterX + 8" :y1="overlayCenterY" :x2="overlayCenterX + 30" :y2="overlayCenterY" stroke="#e0f2fe" stroke-width="3" />
              <line :x1="overlayCenterX" :y1="overlayCenterY - 30" :x2="overlayCenterX" :y2="overlayCenterY - 8" stroke="#e0f2fe" stroke-width="3" />
              <line :x1="overlayCenterX" :y1="overlayCenterY + 8" :x2="overlayCenterX" :y2="overlayCenterY + 30" stroke="#e0f2fe" stroke-width="3" />
              <text :x="overlayCenterX + 34" :y="overlayCenterY - 10" fill="#e0f2fe" font-size="13">X</text>
              <text :x="overlayCenterX + 10" :y="overlayCenterY - 34" fill="#e0f2fe" font-size="13">Y</text>
              <text :x="overlayCenterX + 12" :y="overlayCenterY + 22" fill="#22d3ee" font-size="12">
                {{ Math.round(overlayCenterX) }},{{ Math.round(overlayCenterY) }}
              </text>
            </g>
            <g v-if="showBody">
              <g v-for="body in activeBodyDetections" :key="`body-${body.id}`">
                <rect :x="body.bbox.x" :y="body.bbox.y" :width="body.bbox.w" :height="body.bbox.h" fill="none" stroke="#38bdf8" stroke-width="3" />
                <text v-if="showLabels" :x="body.bbox.x" :y="Math.max(14, body.bbox.y - 6)" fill="#67e8f9" font-size="13">
                  {{ body.class_name }} {{ Math.round(body.confidence * 100) }}%
                </text>
              </g>
            </g>
            <g v-if="showBalloon">
              <g
                v-for="balloon in activeBalloonDetections"
                :key="`balloon-${balloon.id}`"
                class="cursor-pointer"
                @click.stop="selectBalloonTarget(balloon)"
              >
                <rect
                  :x="balloon.bbox.x"
                  :y="balloon.bbox.y"
                  :width="balloon.bbox.w"
                  :height="balloon.bbox.h"
                  fill="rgba(245,158,11,0.08)"
                  :stroke="selectedBalloonId === balloon.id ? '#22c55e' : '#f59e0b'"
                  :stroke-width="selectedBalloonId === balloon.id ? 5 : 3"
                />
                <circle :cx="balloon.center_x" :cy="balloon.center_y" r="5" fill="#f59e0b" />
                <!-- Fire Zone: çapı bbox kısa kenarının yarısıdır -->
                <circle :cx="balloon.center_x" :cy="balloon.center_y" :r="Math.min(balloon.bbox.w, balloon.bbox.h) / 4" fill="none" stroke="#ec4899" stroke-width="2" stroke-dasharray="4 4" />
                <text v-if="selectedBalloonId === balloon.id" :x="balloon.bbox.x" :y="balloon.bbox.y + balloon.bbox.h + 16" fill="#22c55e" font-size="13">
                  TAKIP
                </text>
              </g>
            </g>
            <g v-if="showAim">
              <line :x1="aimCrosshairX - 22" :y1="aimCrosshairY" :x2="aimCrosshairX + 22" :y2="aimCrosshairY" stroke="#22c55e" stroke-width="3" />
              <line :x1="aimCrosshairX" :y1="aimCrosshairY - 22" :x2="aimCrosshairX" :y2="aimCrosshairY + 22" stroke="#22c55e" stroke-width="3" />
              <circle :cx="aimCrosshairX" :cy="aimCrosshairY" r="7" fill="none" stroke="#22c55e" stroke-width="2" />
              <g v-for="aim in frame?.aim_points ?? []" :key="`aim-${aim.id}`">
                <line :x1="aim.x - 12" :y1="aim.y" :x2="aim.x + 12" :y2="aim.y" stroke="#ef4444" stroke-width="2" />
                <line :x1="aim.x" :y1="aim.y - 12" :x2="aim.x" :y2="aim.y + 12" stroke="#ef4444" stroke-width="2" />
              </g>
            </g>
            <text v-if="showLatency" x="12" y="24" fill="#e2e8f0" font-size="13">
              detector {{ metric(measuredDetectorFps) }} FPS / camera {{ metric(measuredCameraFps) }} FPS / {{ metric(measuredLatencyMs, 2) }} ms
            </text>
            <text x="12" y="44" fill="#fbbf24" font-size="13">{{ overlaySource }} · models {{ measuredModelCount }} {{ measuredModelExecution }}</text>
          </svg>
        </div>
      </section>

      <DashboardCard title="Overlay Layers" subtitle="Display toggles">
        <label class="flex items-center gap-2 py-2 text-sm"><input v-model="showBody" type="checkbox" /> Body boxes</label>
        <label class="flex items-center gap-2 py-2 text-sm"><input v-model="showBalloon" type="checkbox" /> Balloon boxes</label>
        <label class="flex items-center gap-2 py-2 text-sm"><input v-model="showAim" type="checkbox" /> Aim points</label>
        <label class="flex items-center gap-2 py-2 text-sm"><input v-model="showLabels" type="checkbox" /> Labels</label>
        <label class="flex items-center gap-2 py-2 text-sm"><input v-model="showLatency" type="checkbox" /> Latency</label>
        <div class="mt-4 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
          {{ vision.warning ?? 'No warnings' }}
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Body Detections" subtitle="Pixel bbox format">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[620px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">ID</th><th>Class</th><th>Conf</th><th>BBox</th><th>Stable</th><th>Color Decision</th></tr>
            </thead>
            <tbody>
              <tr v-for="body in activeBodyDetections" :key="body.id" class="border-t border-white/8">
                <td class="py-2 font-mono text-cyan-100">#{{ body.id }}</td>
                <td>{{ body.class_name }}</td>
                <td>{{ Math.round(body.confidence * 100) }}%</td>
                <td class="font-mono text-xs">{{ body.bbox.x }},{{ body.bbox.y }},{{ body.bbox.w }},{{ body.bbox.h }}</td>
                <td>{{ body.stable_frames }}</td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <StatusBadge :label="colorDecisionFor(body.id)?.decision ?? body.target_team ?? 'unknown'" :tone="(colorDecisionFor(body.id)?.decision ?? body.target_team) === 'friend' ? 'good' : (colorDecisionFor(body.id)?.decision ?? body.target_team) === 'enemy' ? 'bad' : 'warn'" />
                    <StatusBadge :label="`conf ${colorDecisionFor(body.id)?.confidence ?? 0}`" tone="neutral" />
                    <StatusBadge :label="colorDecisionFor(body.id)?.balloon_mask_applied ? 'mask applied' : 'mask advisory'" :tone="colorDecisionFor(body.id)?.balloon_mask_applied ? 'good' : 'warn'" />
                    <StatusBadge v-for="warning in colorDecisionFor(body.id)?.blocking_warnings ?? []" :key="warning" :label="warning" tone="warn" />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="activeBodyDetections.length === 0" class="py-3 text-sm text-slate-400">No active body detections while inference is stopped.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Balloon Detections" subtitle="Aim point source">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[540px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">ID</th><th>Conf</th><th>Center</th><th>BBox</th><th>Source</th></tr>
            </thead>
            <tbody>
              <tr v-for="balloon in activeBalloonDetections" :key="balloon.id" class="border-t border-white/8">
                <td class="py-2 font-mono text-amber-100">#{{ balloon.id }}</td>
                <td>{{ Math.round(balloon.confidence * 100) }}%</td>
                <td class="font-mono text-xs">{{ balloon.center_x }},{{ balloon.center_y }}</td>
                <td class="font-mono text-xs">{{ balloon.bbox.x }},{{ balloon.bbox.y }},{{ balloon.bbox.w }},{{ balloon.bbox.h }}</td>
                <td>{{ balloon.source }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="activeBalloonDetections.length === 0" class="py-3 text-sm text-slate-400">No active balloon detections while inference is stopped.</p>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Latest Vision Events" subtitle="WebSocket metadata">
      <div class="flex flex-wrap gap-2">
        <StatusBadge :label="`frame ${frame?.frame_id ?? 0}`" tone="neutral" />
        <StatusBadge :label="`body ${activeBodyDetections.length}`" tone="good" />
        <StatusBadge :label="`balloon ${activeBalloonDetections.length}`" tone="warn" />
        <StatusBadge :label="detectionSource" tone="warn" />
        <StatusBadge v-for="warning in frame?.warnings ?? []" :key="warning" :label="warning" tone="warn" />
      </div>
    </DashboardCard>
  </div>
</template>
