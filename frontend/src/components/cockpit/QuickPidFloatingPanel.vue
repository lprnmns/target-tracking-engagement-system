<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Camera, Check, ChevronDown, RotateCcw, ScanSearch, SlidersHorizontal, Zap } from '@lucide/vue'
import { fetchCameraCapabilities, fetchTrackerRuntimeCatalog } from '../../api/deviceRuntime'
import { cameraFrameUrl, cameraStreamUrl, startCameraPreview } from '../../api/vision'
import { useDeviceRuntimeStore } from '../../stores/deviceRuntimeStore'
import { useMotionStore } from '../../stores/motionStore'
import { useVisionStore } from '../../stores/visionStore'
import type { CameraCapability, TrackerRuntimeCatalog, VisionRuntimeProfile } from '../../types/deviceRuntime'
import type { TrackingStatus } from '../../types/tracking'

const motion = useMotionStore()
const runtime = useDeviceRuntimeStore()
const vision = useVisionStore()
const open = ref(true)
const busy = ref(false)
const dirty = ref(false)
const message = ref('Canlı değerler hazır')
const tone = ref<'neutral' | 'success' | 'error'>('neutral')
const confidenceBusy = ref(false)
const confidenceMessage = ref('İki modelin canlı eşiği')
const confidenceTone = ref<'neutral' | 'success' | 'error'>('neutral')
const confidenceDraft = reactive({ body: 0.304, balloon: 0.3 })
const cameraBusy = ref(false)
const cameraMessage = ref('Desteklenen modlar hazırlanıyor')
const cameraTone = ref<'neutral' | 'success' | 'error'>('neutral')
const cameraCapability = ref<CameraCapability | null>(null)
const resolutionOptions = ref<string[]>([])
const fpsOptions = ref<number[]>([])
const cameraDraft = reactive({ resolution: '1920x1080', fps: 30, quality: 85 })
const appliedPreviewQuality = ref(85)
const trackerCatalog = ref<TrackerRuntimeCatalog | null>(null)
const trackerDraft = ref<VisionRuntimeProfile['tracker_type']>('current_custom')
const trackerBusy = ref(false)
const trackerMessage = ref('Takip profilleri hazırlanıyor')
const trackerTone = ref<'neutral' | 'success' | 'error'>('neutral')
const draft = reactive({
  pid_kp_x: 11.0,
  pid_ki_x: 0,
  pid_kd_x: 1.1,
  max_speed_x: 8000,
  pid_kp_y: 8.0,
  pid_ki_y: 0,
  pid_kd_y: 2.0,
  max_speed_y: 8000,
})

const revision = computed(() => motion.trackingStatus.config_revision ?? 0)
const trackingLabel = computed(() => motion.trackingStatus.active ? 'TAKİP AKTİF' : 'BEKLİYOR')
const confidenceDirty = computed(() => (
  Math.abs(confidenceDraft.body - runtime.visionStatus.profile.body_conf_threshold) > 0.0005
  || Math.abs(confidenceDraft.balloon - runtime.visionStatus.profile.balloon_conf_threshold) > 0.0005
))
const cameraDirty = computed(() => {
  const profile = runtime.cameraStatus.profile
  return cameraDraft.resolution !== `${profile.width}x${profile.height}`
    || cameraDraft.fps !== profile.fps
    || cameraDraft.quality !== appliedPreviewQuality.value
})
const cameraReadback = computed(() => `${runtime.cameraStatus.actual_width}×${runtime.cameraStatus.actual_height} · ${Number(runtime.cameraStatus.actual_fps_measured || runtime.cameraStatus.actual_fps).toFixed(1)} FPS`)
const trackerReadback = computed(() => trackerCatalog.value?.active_profile ?? 'current_custom')
const trackerDirty = computed(() => trackerDraft.value !== trackerReadback.value)
const trackerProfiles = computed(() => trackerCatalog.value?.profiles ?? [])
const qualityOptions = [
  { value: 85, label: 'Akıcı (Önerilen) · JPEG 85' },
  { value: 72, label: 'Ultra Hızlı · JPEG 72' },
  { value: 95, label: 'En yüksek · JPEG 95' },
]

const modeOptions = [
  {
    id: 'A2',
    tag: 'BAZ',
    name: 'BAZ EŞİK',
    sub: 'A2 Referans',
    description: 'Atölyede test edilmiş kararlı referans takip (Kp=11, Kd=1.1, Vmax=8000, Amax=7000).',
  },
  {
    id: 'OPT_D_FILTER',
    tag: 'MOD 1',
    name: 'D-FİLTRE',
    sub: 'Jitter Sönüm',
    description: 'Baz + YOLO piksel titreşimini süzen türev alçak geçiren filtre (Kd=1.6 / 3.0, alpha=0.30).',
  },
  {
    id: 'OPT_ZONE_GAIN',
    tag: 'MOD 2',
    name: '3-BÖLGE',
    sub: '14cm Balon',
    description: 'Mod 1 + >50px hızlı yakalama (x1.30), <15px 14cm balon içi kararlı kilit (x0.75).',
  },
  {
    id: 'OPT_SINE_TRACK',
    tag: 'MOD 3',
    name: 'DALGALI',
    sub: 'Parkur Fazı',
    description: 'Mod 1 + Mod 2 + Aşama 3 sinüzoidal dalgalı parkur apeks frenlemesi (0.35 sönüm).',
  },
]

const modeBusy = ref(false)
const targetMode = ref<string | null>(null)

const currentControllerMode = computed(() => {
  const m = motion.trackingStatus.controller_mode
  if (m === 'GENERAL') return 'A2'
  return m || 'A2'
})

const activeModeOption = computed(() => {
  return modeOptions.find((m) => m.id === currentControllerMode.value) ?? modeOptions[0]
})

const liveZoneLabel = computed(() => {
  const zone = motion.trackingUpdate?.deadband_zone || 'none'
  if (zone === 'far_catchup') return 'Hızlı (>50px)'
  if (zone === 'balloon_lock') return 'Balon (<15px)'
  if (zone === 'mid_track') return 'Orta Takip'
  return zone
})

const liveErrorText = computed(() => {
  if (!motion.trackingUpdate) return '—'
  const err = motion.trackingUpdate.distance_to_center ?? Math.hypot(motion.trackingUpdate.error_x_px || 0, motion.trackingUpdate.error_y_px || 0)
  return `${Math.round(err)} px`
})

async function selectMode(modeId: string): Promise<void> {
  if (modeBusy.value || currentControllerMode.value === modeId) return
  modeBusy.value = true
  targetMode.value = modeId
  try {
    await motion.updateTrackingConfig({ controller_mode: modeId })
    syncFromStatus(motion.trackingStatus)
    const opt = modeOptions.find((m) => m.id === modeId)
    tone.value = 'success'
    message.value = `${opt?.tag ?? modeId} aktif · ${opt?.sub ?? ''}`
  } catch (err) {
    tone.value = 'error'
    message.value = err instanceof Error ? err.message : 'Mod değiştirilemedi'
  } finally {
    modeBusy.value = false
    targetMode.value = null
  }
}

function syncFromStatus(status: TrackingStatus): void {
  draft.pid_kp_x = status.pid_kp_x
  draft.pid_ki_x = status.pid_ki_x
  draft.pid_kd_x = status.pid_kd_x
  draft.max_speed_x = status.max_speed_x ?? status.max_speed
  draft.pid_kp_y = status.pid_kp_y
  draft.pid_ki_y = status.pid_ki_y
  draft.pid_kd_y = status.pid_kd_y
  draft.max_speed_y = status.max_speed_y ?? status.max_speed
  dirty.value = false
}

function markDirty(): void {
  dirty.value = true
  tone.value = 'neutral'
  message.value = 'Uygulanmamış değişiklik'
}

function validDraft(): boolean {
  const values = Object.values(draft)
  return values.every((value) => Number.isFinite(Number(value)) && Number(value) >= 0)
    && draft.max_speed_x >= 1 && draft.max_speed_x <= 15000
    && draft.max_speed_y >= 1 && draft.max_speed_y <= 15000
}

async function applyNow(): Promise<void> {
  if (!validDraft()) {
    tone.value = 'error'
    message.value = 'Değerleri kontrol edin · hız 1–15000'
    return
  }
  busy.value = true
  try {
    await motion.updateTrackingConfig({
      pid_kp_x: Number(draft.pid_kp_x),
      pid_ki_x: Number(draft.pid_ki_x),
      pid_kd_x: Number(draft.pid_kd_x),
      max_speed_x: Number(draft.max_speed_x),
      pid_kp_y: Number(draft.pid_kp_y),
      pid_ki_y: Number(draft.pid_ki_y),
      pid_kd_y: Number(draft.pid_kd_y),
      max_speed_y: Number(draft.max_speed_y),
    })
    syncFromStatus(motion.trackingStatus)
    tone.value = 'success'
    message.value = `Anında uygulandı · REV ${revision.value}`
  } catch (error) {
    tone.value = 'error'
    message.value = error instanceof Error ? error.message : 'PID uygulanamadı'
  } finally {
    busy.value = false
  }
}

function restoreReadback(): void {
  syncFromStatus(motion.trackingStatus)
  tone.value = 'neutral'
  message.value = 'Sunucudaki değerler geri yüklendi'
}

function syncConfidence(): void {
  confidenceDraft.body = runtime.visionStatus.profile.body_conf_threshold
  confidenceDraft.balloon = runtime.visionStatus.profile.balloon_conf_threshold
}

async function applyConfidence(): Promise<void> {
  const body = Math.max(0.001, Math.min(0.99, Number(confidenceDraft.body)))
  const balloon = Math.max(0.001, Math.min(0.99, Number(confidenceDraft.balloon)))
  if (!Number.isFinite(body) || !Number.isFinite(balloon)) {
    confidenceTone.value = 'error'
    confidenceMessage.value = 'Confidence değeri geçersiz'
    return
  }
  confidenceBusy.value = true
  try {
    runtime.visionDraft = {
      ...runtime.visionStatus.profile,
      conf: Math.min(body, balloon),
      body_conf_threshold: body,
      balloon_conf_threshold: balloon,
      target_fps: Math.max(60, Number(runtime.visionStatus.profile?.target_fps || 60)),
    }
    await runtime.applyVision()
    syncConfidence()
    confidenceTone.value = 'success'
    confidenceMessage.value = `Canlı uygulandı · hava ${body.toFixed(3)} · balon ${balloon.toFixed(3)}`
  } catch (error) {
    confidenceTone.value = 'error'
    confidenceMessage.value = error instanceof Error ? error.message : 'YOLO eşikleri uygulanamadı'
  } finally {
    confidenceBusy.value = false
  }
}

function sortResolutions(items: string[]): string[] {
  return [...new Set(items)].sort((a, b) => {
    const area = (value: string) => {
      const [width, height] = value.split('x').map(Number)
      return (width || 0) * (height || 0)
    }
    return area(b) - area(a)
  })
}

async function loadCameraModes(): Promise<void> {
  const profile = runtime.cameraStatus.profile
  const currentResolution = `${profile.width}x${profile.height}`
  cameraDraft.resolution = currentResolution
  cameraDraft.fps = profile.fps
  const savedQuality = Number(sessionStorage.getItem('istiklal_preview_quality_v2') ?? 85)
  cameraDraft.quality = [95, 85, 86, 72].includes(savedQuality) ? (savedQuality === 86 ? 85 : savedQuality) : 85
  appliedPreviewQuality.value = cameraDraft.quality
  resolutionOptions.value = [currentResolution]
  fpsOptions.value = [profile.fps]
  if (!profile.device_id) {
    cameraMessage.value = 'Aktif fiziksel kamera kimliği bulunamadı'
    return
  }
  try {
    cameraCapability.value = await fetchCameraCapabilities(profile.device_id)
    resolutionOptions.value = sortResolutions([
      currentResolution,
      ...cameraCapability.value.supported_resolutions,
    ])
    fpsOptions.value = [...new Set([
      profile.fps,
      ...cameraCapability.value.supported_fps,
    ])].sort((a, b) => b - a)
    cameraMessage.value = cameraCapability.value.open_ok
      ? 'Kamera modları doğrulandı'
      : 'Aktif mod + sürücü tarafından bildirilen güvenli seçenekler'
  } catch {
    cameraMessage.value = 'Aktif mod kullanılıyor · tarama kamera tarafından meşgul'
  }
}

async function applyCameraMode(): Promise<void> {
  const [width, height] = cameraDraft.resolution.split('x').map(Number)
  if (!width || !height || !fpsOptions.value.includes(Number(cameraDraft.fps))) {
    cameraTone.value = 'error'
    cameraMessage.value = 'Desteklenen bir kamera modu seçin'
    return
  }
  cameraBusy.value = true
  try {
    const current = runtime.cameraStatus.profile
    runtime.cameraDraft = {
      ...current,
      width,
      height,
      fps: Number(cameraDraft.fps),
      pixel_format: current.pixel_format === 'auto' ? 'MJPG' : current.pixel_format,
      stream_width: width,
      stream_height: height,
      inference_width: width,
      inference_height: height,
      roi: { ...current.roi },
    }
    await runtime.applyCamera()
    const status = await startCameraPreview()
    runtime.applyCameraStatus(status)
    if (!status.running || status.last_frame_age_ms === null || status.last_frame_age_ms > 1200) {
      throw new Error(status.last_capture_error || status.last_error || 'Yeni kamera modunda sağlıklı kare alınamadı')
    }
    const nonce = Date.now()
    const previewWidth = width > 1280 ? 1280 : width
    const previewHeight = width > 1280 ? Math.round((height / width) * 1280) : height
    vision.streamUrl = cameraStreamUrl({ width: previewWidth, height: previewHeight, quality: cameraDraft.quality, nonce })
    vision.frameUrl = cameraFrameUrl({ width: previewWidth, height: previewHeight, quality: cameraDraft.quality, nonce })
    appliedPreviewQuality.value = cameraDraft.quality
    sessionStorage.setItem('istiklal_preview_quality_v2', String(cameraDraft.quality))
    cameraTone.value = 'success'
    cameraMessage.value = `Uygulandı · ${cameraReadback.value} · JPEG ${cameraDraft.quality}`
  } catch (error) {
    cameraTone.value = 'error'
    cameraMessage.value = error instanceof Error ? error.message : 'Kamera modu uygulanamadı'
    await runtime.refreshRuntimeStatus().catch(() => undefined)
  } finally {
    cameraBusy.value = false
  }
}

async function loadTrackers(): Promise<void> {
  try {
    trackerCatalog.value = await fetchTrackerRuntimeCatalog()
    trackerDraft.value = trackerCatalog.value.active_profile
    trackerTone.value = 'neutral'
    trackerMessage.value = `Aktif · ${trackerCatalog.value.active_profile}`
  } catch (error) {
    trackerTone.value = 'error'
    trackerMessage.value = error instanceof Error ? error.message : 'Takip profilleri okunamadı'
  }
}

async function applyTrackerMode(): Promise<void> {
  if (motion.trackingStatus.active) {
    trackerTone.value = 'error'
    trackerMessage.value = 'Önce MANUEL moda geçip aktif takibi durdurun'
    return
  }
  const selected = trackerProfiles.value.find((item) => item.profile_id === trackerDraft.value)
  if (!selected?.available) {
    trackerTone.value = 'error'
    trackerMessage.value = selected?.reason ?? 'Bu tracker çalışma ortamında kullanılamıyor'
    return
  }
  trackerBusy.value = true
  try {
    runtime.visionDraft = {
      ...runtime.visionStatus.profile,
      tracker_enabled: true,
      tracker_type: trackerDraft.value,
    }
    await runtime.applyVision()
    trackerCatalog.value = await fetchTrackerRuntimeCatalog()
    if (trackerCatalog.value.active_profile !== trackerDraft.value) {
      throw new Error(`Read-back uyuşmadı: ${trackerCatalog.value.active_profile}`)
    }
    trackerTone.value = 'success'
    trackerMessage.value = `Canlı uygulandı · ${selected.label} · iki stream sıfırlandı`
  } catch (error) {
    trackerTone.value = 'error'
    trackerMessage.value = error instanceof Error ? error.message : 'Takip modu uygulanamadı'
    await loadTrackers()
  } finally {
    trackerBusy.value = false
  }
}

watch(
  () => [
    motion.trackingStatus.pid_kp_x,
    motion.trackingStatus.pid_ki_x,
    motion.trackingStatus.pid_kd_x,
    motion.trackingStatus.max_speed_x,
    motion.trackingStatus.pid_kp_y,
    motion.trackingStatus.pid_ki_y,
    motion.trackingStatus.pid_kd_y,
    motion.trackingStatus.max_speed_y,
  ],
  () => { if (!dirty.value && !busy.value) syncFromStatus(motion.trackingStatus) },
)

onMounted(async () => {
  try {
    await Promise.all([motion.refreshTrackingStatus(), runtime.refreshRuntimeStatus()])
    syncFromStatus(motion.trackingStatus)
    syncConfidence()
    await Promise.all([loadCameraModes(), loadTrackers()])
  } catch {
    tone.value = 'error'
    message.value = 'Canlı PID okunamadı'
  }
})
</script>

<template>
  <aside class="quick-pid" :class="{ collapsed: !open }" aria-label="Canlı PID hızlı ayarı" @click.stop>
    <header>
      <div class="title"><SlidersHorizontal :size="15" /><span><b>PID HIZLI AYAR</b><small>GEÇİCİ · CANLI</small></span></div>
      <div class="header-state"><i :class="{ active: motion.trackingStatus.active }"></i>{{ trackingLabel }} · R{{ revision }}</div>
      <button type="button" :aria-label="open ? 'PID panelini daralt' : 'PID panelini aç'" @click="open = !open">
        <ChevronDown :size="16" :class="{ rotated: !open }" />
      </button>
    </header>

    <section v-if="open" class="quick-section mode-section" @keydown.stop @keyup.stop>
      <div class="section-head">
        <div>
          <Zap :size="14" />
          <span><b>KONTROL ALGORİTMASI</b><small>PARKUR VE BALON MODLARI</small></span>
        </div>
        <em :class="'badge-' + currentControllerMode.toLowerCase()">{{ activeModeOption.tag }} · {{ activeModeOption.name }}</em>
      </div>
      <div class="mode-grid">
        <button
          v-for="m in modeOptions"
          :key="m.id"
          type="button"
          :class="['mode-btn', { active: currentControllerMode === m.id, busy: modeBusy && targetMode === m.id }]"
          :disabled="modeBusy"
          :title="m.description"
          @click="selectMode(m.id)"
        >
          <span class="mode-tag">{{ m.tag }}</span>
          <span class="mode-name">{{ m.name }}</span>
          <span class="mode-sub">{{ m.sub }}</span>
        </button>
      </div>
      <div class="mode-live-bar">
        <span class="pill">Bölge: <b>{{ liveZoneLabel }}</b></span>
        <span class="pill">Hata: <b>{{ liveErrorText }}</b></span>
        <span class="pill">Filtre: <b>{{ ['A2', 'GENERAL'].includes(currentControllerMode) ? 'KAPALI' : '0.30 D' }}</b></span>
      </div>
    </section>

    <form v-if="open" @submit.prevent="applyNow" @keydown.stop @keyup.stop>
      <section class="axis-card x-axis">
        <div class="axis-name"><b>X</b><span>PAN · YATAY</span></div>
        <label><span>Kp</span><input v-model.number="draft.pid_kp_x" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
        <label><span>Ki</span><input v-model.number="draft.pid_ki_x" type="number" min="0" max="10000" step="0.01" @input="markDirty"></label>
        <label><span>Kd</span><input v-model.number="draft.pid_kd_x" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
        <label class="speed"><span>Hız</span><input v-model.number="draft.max_speed_x" type="number" min="1" max="15000" step="100" @input="markDirty"></label>
      </section>
      <section class="axis-card y-axis">
        <div class="axis-name"><b>Y</b><span>TILT · DİKEY</span></div>
        <label><span>Kp</span><input v-model.number="draft.pid_kp_y" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
        <label><span>Ki</span><input v-model.number="draft.pid_ki_y" type="number" min="0" max="10000" step="0.01" @input="markDirty"></label>
        <label><span>Kd</span><input v-model.number="draft.pid_kd_y" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
        <label class="speed"><span>Hız</span><input v-model.number="draft.max_speed_y" type="number" min="1" max="15000" step="100" @input="markDirty"></label>
      </section>
      <footer>
        <p :class="tone" aria-live="polite">{{ message }}</p>
        <button class="restore" type="button" :disabled="busy || !dirty" title="Sunucudaki değerleri geri yükle" @click="restoreReadback"><RotateCcw :size="14" /></button>
        <button class="apply" type="submit" :disabled="busy || !dirty"><Check :size="15" />{{ busy ? 'UYGULANIYOR' : 'ANINDA UYGULA' }}</button>
      </footer>
    </form>

    <section v-if="open" class="quick-section confidence-section" @keydown.stop @keyup.stop>
      <div class="section-head"><div><ScanSearch :size="14" /><span><b>YOLO CONF</b><small>ÇALIŞAN İKİ MODEL · CANLI</small></span></div><em>MODEL BAZLI</em></div>
      <div class="confidence-grid">
        <label><span>Hava aracı</span><input v-model.number="confidenceDraft.body" type="number" min="0.001" max="0.99" step="0.001"></label>
        <label><span>Kırmızı balon</span><input v-model.number="confidenceDraft.balloon" type="number" min="0.001" max="0.99" step="0.001"></label>
        <button type="button" :disabled="confidenceBusy || !confidenceDirty" @click="applyConfidence"><Check :size="14" />{{ confidenceBusy ? 'UYGULANIYOR' : 'CONF UYGULA' }}</button>
      </div>
      <p :class="confidenceTone" aria-live="polite">{{ confidenceMessage }}</p>
    </section>

    <section v-if="open" class="quick-section camera-section" @keydown.stop @keyup.stop>
      <div class="section-head"><div><Camera :size="14" /><span><b>KAMERA MODU</b><small>AKTİF USB KAMERA · READ-BACK</small></span></div><em>{{ cameraReadback }}</em></div>
      <div class="camera-grid">
        <label><span>Çözünürlük</span><select v-model="cameraDraft.resolution"><option v-for="item in resolutionOptions" :key="item" :value="item">{{ item }}</option></select></label>
        <label><span>FPS</span><select v-model.number="cameraDraft.fps"><option v-for="item in fpsOptions" :key="item" :value="item">{{ item }} FPS</option></select></label>
        <label><span>Kalite</span><select v-model.number="cameraDraft.quality"><option v-for="item in qualityOptions" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <button type="button" :disabled="cameraBusy || !cameraDirty || !resolutionOptions.length" @click="applyCameraMode"><Check :size="14" />{{ cameraBusy ? 'KAMERA AÇILIYOR' : 'KAMERA UYGULA' }}</button>
      </div>
      <p :class="cameraTone" aria-live="polite">{{ cameraMessage }}</p>
    </section>

    <section v-if="false" class="quick-section tracker-section" @keydown.stop @keyup.stop>
      <div class="section-head"><div><ScanSearch :size="14" /><span><b>TAKİP MODU</b><small>UÇAK + BALON · CANLI READ-BACK</small></span></div><em>{{ trackerReadback }}</em></div>
      <div class="tracker-grid">
        <label><span>Algoritma</span><select v-model="trackerDraft"><option v-for="item in trackerProfiles" :key="item.profile_id" :value="item.profile_id" :disabled="!item.available">{{ item.label }}{{ item.available ? '' : ' · kullanılamıyor' }}</option></select></label>
        <button type="button" :disabled="trackerBusy || !trackerDirty || motion.trackingStatus.active" @click="applyTrackerMode"><Check :size="14" />{{ trackerBusy ? 'UYGULANIYOR' : 'TAKİBİ UYGULA' }}</button>
      </div>
      <p :class="trackerTone" aria-live="polite">{{ trackerMessage }}</p>
    </section>
  </aside>
</template>

<style scoped>
.quick-pid{position:fixed;z-index:68;top:78px;right:14px;width:min(420px,calc(100vw - 28px));max-height:calc(100vh - 92px);overflow:auto;border:1px solid rgba(68,218,224,.34);border-radius:13px;background:linear-gradient(145deg,rgba(5,21,33,.97),rgba(2,10,19,.98));box-shadow:0 18px 55px rgba(0,0,0,.48),inset 0 1px rgba(255,255,255,.035);color:#eafaff;backdrop-filter:blur(14px);scrollbar-width:thin;scrollbar-color:#265467 #07131d}
.quick-pid.collapsed{width:330px}.quick-pid header{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:9px;padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.07)}.collapsed header{border-bottom:0}.title{display:flex;align-items:center;gap:7px;color:#63e2ef}.title span{display:flex;gap:6px;align-items:baseline}.title b{font-size:.65rem;letter-spacing:.1em}.title small{color:#f5cb72;font-size:.48rem;font-weight:900;letter-spacing:.09em}.header-state{display:flex;align-items:center;gap:5px;color:#8fa9b9;font:800 .51rem ui-monospace,monospace}.header-state i{width:6px;height:6px;border-radius:50%;background:#718294}.header-state i.active{background:#42e1a1;box-shadow:0 0 8px #42e1a1}.quick-pid header button{display:grid;place-items:center;width:25px;height:25px;border:1px solid rgba(255,255,255,.1);border-radius:7px;background:#0a1c2a;color:#9edce5;cursor:pointer}.quick-pid header svg{transition:transform .18s ease}.quick-pid header svg.rotated{transform:rotate(180deg)}
form{display:grid;gap:7px;padding:9px}.axis-card{display:grid;grid-template-columns:66px repeat(4,1fr);gap:6px;align-items:end;padding:7px;border:1px solid rgba(255,255,255,.08);border-radius:9px;background:rgba(1,8,15,.7)}.axis-card.x-axis{border-left:2px solid #43dce6}.axis-card.y-axis{border-left:2px solid #e5b862}.axis-name{align-self:center}.axis-name b{display:block;font-size:.9rem}.axis-name span{display:block;color:#728c9c;font-size:.45rem;font-weight:900;letter-spacing:.06em}.axis-card label{display:grid;gap:3px}.axis-card label span{color:#8099a8;font-size:.49rem;font-weight:900}.axis-card input{box-sizing:border-box;width:100%;min-width:0;height:28px;border:1px solid rgba(135,166,184,.18);border-radius:6px;background:#020914;padding:0 5px;color:#f3fbff;font:800 .65rem ui-monospace,monospace;outline:none}.axis-card input:focus{border-color:#58dce8;box-shadow:0 0 0 2px rgba(88,220,232,.1)}.axis-card .speed input{border-color:rgba(245,203,114,.3);color:#ffe29b}
footer{display:grid;grid-template-columns:1fr 31px auto;align-items:center;gap:6px}footer p{min-width:0;margin:0;overflow:hidden;color:#8da8b8;font-size:.53rem;font-weight:700;text-overflow:ellipsis;white-space:nowrap}footer p.success{color:#65e5af}footer p.error{color:#ff8e9b}footer button{height:31px;border-radius:7px;font-size:.57rem;font-weight:900;cursor:pointer}footer button:disabled{opacity:.38;cursor:not-allowed}.restore{display:grid;place-items:center;border:1px solid rgba(255,255,255,.12);background:#091824;color:#a8becb}.apply{display:flex;align-items:center;justify-content:center;gap:5px;border:1px solid rgba(50,211,153,.46);background:linear-gradient(180deg,#0d6a50,#084735);padding:0 10px;color:#d5ffec}
.quick-section{display:grid;gap:7px;margin:0 9px 9px;padding:8px;border:1px solid rgba(255,255,255,.08);border-radius:9px;background:rgba(1,8,15,.7)}.section-head,.section-head>div{display:flex;align-items:center}.section-head{justify-content:space-between;gap:8px}.section-head>div{gap:6px;color:#67e8f9}.section-head span{display:flex;align-items:baseline;gap:6px}.section-head b{font-size:.6rem;letter-spacing:.08em}.section-head small{color:#718b9a;font-size:.44rem;font-weight:900;letter-spacing:.06em}.section-head em{color:#75d9a7;font:800 .48rem ui-monospace,monospace;font-style:normal}.confidence-grid{display:grid;grid-template-columns:1fr 1fr auto;gap:6px;align-items:end}.quick-section label{display:grid;gap:3px}.quick-section label span{color:#829baa;font-size:.49rem;font-weight:900}.quick-section input,.quick-section select{box-sizing:border-box;width:100%;height:29px;border:1px solid rgba(135,166,184,.2);border-radius:6px;background:#020914;padding:0 6px;color:#eefaff;font:800 .61rem ui-monospace,monospace;outline:none}.quick-section input:focus,.quick-section select:focus{border-color:#58dce8}.quick-section button{display:flex;align-items:center;justify-content:center;gap:5px;height:29px;border:1px solid rgba(50,211,153,.42);border-radius:7px;background:#0a543f;color:#d6ffed;padding:0 9px;font-size:.53rem;font-weight:900;cursor:pointer}.quick-section button:disabled{opacity:.38;cursor:not-allowed}.quick-section>p{margin:0;overflow:hidden;color:#829baa;font-size:.5rem;font-weight:700;text-overflow:ellipsis;white-space:nowrap}.quick-section>p.success{color:#65e5af}.quick-section>p.error{color:#ff8e9b}.camera-section{border-left:2px solid #60a5fa}.confidence-section{border-left:2px solid #c084fc}.tracker-section{border-left:2px solid #34d399}.camera-grid{display:grid;grid-template-columns:1.2fr .7fr 1.25fr auto;gap:6px;align-items:end}.tracker-grid{display:grid;grid-template-columns:1fr auto;gap:6px;align-items:end}
.mode-section{border-left:2px solid #38bdf8}.mode-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.mode-btn{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:5px 3px;border:1px solid rgba(255,255,255,.12);border-radius:7px;background:rgba(2,12,22,.8);color:#94a3b8;cursor:pointer;transition:all .15s ease;text-align:center;min-height:50px}.mode-btn:hover:not(:disabled){border-color:rgba(56,189,248,.5);background:rgba(14,35,55,.8);color:#e2e8f0}.mode-btn.active{border-color:#38bdf8;background:linear-gradient(180deg,rgba(14,116,144,.4),rgba(3,105,161,.2));color:#38bdf8;box-shadow:0 0 10px rgba(56,189,248,.25)}.mode-btn.busy{opacity:.5}.mode-tag{font:900 .5rem ui-monospace,monospace;letter-spacing:.08em;opacity:.8}.mode-name{font:800 .58rem ui-monospace,monospace;margin:1px 0}.mode-sub{font-size:.42rem;opacity:.7;line-height:1.1}.mode-live-bar{display:flex;gap:5px;justify-content:space-between;margin-top:2px;font:700 .48rem ui-monospace,monospace;color:#94a3b8}.mode-live-bar .pill{background:rgba(0,0,0,.4);padding:2px 5px;border-radius:4px;border:1px solid rgba(255,255,255,.06)}.mode-live-bar b{color:#38bdf8}.badge-general{color:#8fa9b9 !important}.badge-opt_d_filter{color:#67e8f9 !important}.badge-opt_zone_gain{color:#f59e0b !important}.badge-opt_sine_track{color:#a855f7 !important}
@media(max-width:720px){.quick-pid{top:68px;right:8px;width:calc(100vw - 16px)}.quick-pid.collapsed{width:min(330px,calc(100vw - 16px))}.axis-card{grid-template-columns:55px repeat(4,1fr)}}
@media(max-width:520px){.confidence-grid{grid-template-columns:1fr 1fr}.confidence-grid button{grid-column:1/-1}.camera-grid{grid-template-columns:1fr 1fr}.camera-grid label:nth-child(3),.camera-grid button{grid-column:1/-1}.tracker-grid{grid-template-columns:1fr}.tracker-grid button{grid-column:1/-1}.mode-grid{grid-template-columns:repeat(2,1fr)}}
</style>
