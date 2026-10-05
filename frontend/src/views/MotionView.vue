<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import SafetyZoneProfilePanel from '../components/cockpit/SafetyZoneProfilePanel.vue'
import { stopHardwareMotion } from '../api/hardware'
import { sendStage1ManualMotion } from '../api/mission'
import { useMotionStore } from '../stores/motionStore'
import { useSystemStore } from '../stores/systemStore'
import type { MotionSettings } from '../types/motion'

const motion = useMotionStore()
const system = useSystemStore()
const stepSize = ref(1)
const goToPan = ref(0)
const goToTilt = ref(0)
const trackingX = ref(420)
const trackingY = ref(120)
const settingsDraft = ref<MotionSettings>({ ...motion.settings })
const trackingBusy = ref(false)
const manualJogBusy = ref(false)
const manualJogLastCommand = ref('Beklemede')
const manualJogSpeed = ref(220)
const manualJogDurationMs = ref(180)
const pressedKeys = new Set<string>()
const gamepadStatus = ref('Gamepad bekleniyor')
const gamepadDeadzone = ref(0.16)
let gamepadFrame: number | null = null
let gamepadWasMoving = false
let lastGamepadDispatchAt = 0
const pidDraft = ref({
  pid_kp_x: motion.trackingStatus.pid_kp_x,
  pid_ki_x: motion.trackingStatus.pid_ki_x,
  pid_kd_x: motion.trackingStatus.pid_kd_x,
  pid_kp_y: motion.trackingStatus.pid_kp_y,
  pid_ki_y: motion.trackingStatus.pid_ki_y,
  pid_kd_y: motion.trackingStatus.pid_kd_y,
  smoothing_alpha: motion.trackingStatus.smoothing_alpha,
  max_speed: motion.trackingStatus.max_speed,
  invert_x: motion.trackingStatus.invert_x,
  invert_y: motion.trackingStatus.invert_y,
  lead_enabled: motion.trackingStatus.lead_enabled,
  lead_latency_multiplier: motion.trackingStatus.lead_latency_multiplier,
  lead_max_horizon_ms: motion.trackingStatus.lead_max_horizon_ms,
})

const trackingStateColor = computed(() => {
  const s = motion.trackingStatus.state
  if (s === 'LOCKED') return 'good'
  if (s === 'TRACKING') return 'good'
  if (s === 'TARGET_LOST') return 'warn'
  if (s === 'SEARCHING') return 'warn'
  if (s === 'ERROR') return 'bad'
  return 'neutral'
})

const trackingStateLabel = computed(() => {
  const s = motion.trackingStatus.state
  const labels: Record<string, string> = {
    IDLE: 'BEKLEME', SEARCHING: 'ARAMA', TRACKING: 'TAKİP',
    LOCKED: 'KİLİTLİ', TARGET_LOST: 'HEDEF KAYIP', STOPPED: 'DURDURULDU', ERROR: 'HATA',
  }
  return labels[s] ?? s
})

const lastUpdate = computed(() => motion.trackingUpdate)
const errorMagnitude = computed(() => {
  if (!lastUpdate.value) return 0
  return Math.sqrt(lastUpdate.value.error_x_px ** 2 + lastUpdate.value.error_y_px ** 2).toFixed(1)
})

async function toggleTracking() {
  trackingBusy.value = true
  try {
    if (motion.trackingActive) {
      await motion.stopTracking()
    } else {
      await motion.startTracking()
    }
  } finally {
    trackingBusy.value = false
  }
}

async function applyPidConfig() {
  await motion.updateTrackingConfig({ ...pidDraft.value })
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
    max_speed: motion.trackingStatus.max_speed,
    invert_x: motion.trackingStatus.invert_x,
    invert_y: motion.trackingStatus.invert_y,
    lead_enabled: motion.trackingStatus.lead_enabled,
    lead_latency_multiplier: motion.trackingStatus.lead_latency_multiplier,
    lead_max_horizon_ms: motion.trackingStatus.lead_max_horizon_ms,
  }
}

async function manualJog(speedX: number, speedY: number, label: string, durationMs = manualJogDurationMs.value): Promise<void> {
  manualJogBusy.value = true
  manualJogLastCommand.value = label
  try {
    const result = await sendStage1ManualMotion({
      speed_x: speedX,
      speed_y: speedY,
      duration_ms: durationMs,
    })
    if (!result.accepted) manualJogLastCommand.value = result.reason_codes.join(' · ') || result.detail
  } finally {
    manualJogBusy.value = false
  }
}

async function stopManualJog(): Promise<void> {
  pressedKeys.clear()
  manualJogLastCommand.value = 'Durduruldu'
  await stopHardwareMotion()
}

function commandForArrow(key: string): { speedX: number, speedY: number, label: string } | null {
  const speed = Math.max(20, Math.min(1000, Math.trunc(manualJogSpeed.value)))
  if (key === 'ArrowLeft') return { speedX: speed, speedY: 0, label: `Sol (${speed}, 0)` }
  if (key === 'ArrowRight') return { speedX: -speed, speedY: 0, label: `Sağ (${-speed}, 0)` }
  if (key === 'ArrowUp') return { speedX: 0, speedY: -speed, label: `Yukarı (0, ${-speed})` }
  if (key === 'ArrowDown') return { speedX: 0, speedY: speed, label: `Aşağı (0, ${speed})` }
  return null
}

function handleKeyDown(event: KeyboardEvent): void {
  const command = commandForArrow(event.key)
  if (!command) return
  event.preventDefault()
  if (pressedKeys.has(event.key) && !event.repeat) return
  pressedKeys.add(event.key)
  void manualJog(command.speedX, command.speedY, command.label)
}

function handleKeyUp(event: KeyboardEvent): void {
  if (!commandForArrow(event.key)) return
  event.preventDefault()
  void stopManualJog()
}

function axisWithDeadzone(value: number): number {
  const magnitude = Math.abs(value)
  if (magnitude <= gamepadDeadzone.value) return 0
  return Math.sign(value) * ((magnitude - gamepadDeadzone.value) / (1 - gamepadDeadzone.value))
}

function pollGamepad(): void {
  const pads = navigator.getGamepads?.() ?? []
  const pad = Array.from(pads).find((item): item is Gamepad => item !== null && item.connected)
  if (!pad) {
    gamepadStatus.value = 'Gamepad yok'
    if (gamepadWasMoving) void stopManualJog()
    gamepadWasMoving = false
    gamepadFrame = window.requestAnimationFrame(pollGamepad)
    return
  }
  gamepadStatus.value = `Gamepad: ${pad.id.slice(0, 28)}`
  const x = axisWithDeadzone(pad.axes[0] ?? 0)
  const y = axisWithDeadzone(pad.axes[1] ?? 0)
  const moving = x !== 0 || y !== 0
  if (!moving) {
    if (gamepadWasMoving) void stopManualJog()
    gamepadWasMoving = false
    gamepadFrame = window.requestAnimationFrame(pollGamepad)
    return
  }
  gamepadWasMoving = true
  const now = performance.now()
  if (!manualJogBusy.value && now - lastGamepadDispatchAt >= 110) {
    lastGamepadDispatchAt = now
    const speed = Math.max(20, Math.min(1000, Math.trunc(manualJogSpeed.value)))
    void manualJog(Math.round(x * speed), Math.round(y * speed), 'Gamepad manuel yön', 180)
  }
  gamepadFrame = window.requestAnimationFrame(pollGamepad)
}

const panRange = computed(() => motion.settings.pan_max_deg - motion.settings.pan_min_deg)
const tiltRange = computed(() => motion.settings.tilt_max_deg - motion.settings.tilt_min_deg)
const panCurrentX = computed(() => scale(motion.state.pan_position_deg, motion.settings.pan_min_deg, motion.settings.pan_max_deg, 24, 276))
const panTargetX = computed(() => scale(motion.state.pan_target_deg, motion.settings.pan_min_deg, motion.settings.pan_max_deg, 24, 276))
const tiltCurrentY = computed(() => scale(motion.state.tilt_position_deg, motion.settings.tilt_min_deg, motion.settings.tilt_max_deg, 156, 24))
const tiltTargetY = computed(() => scale(motion.state.tilt_target_deg, motion.settings.tilt_min_deg, motion.settings.tilt_max_deg, 156, 24))
const limitWarning = computed(() => (
  motion.state.pan_limit_left
  || motion.state.pan_limit_right
  || motion.state.tilt_limit_up
  || motion.state.tilt_limit_down
))

function scale(value: number, min: number, max: number, outMin: number, outMax: number): number {
  if (max <= min) return outMin
  const ratio = Math.min(1, Math.max(0, (value - min) / (max - min)))
  return outMin + ratio * (outMax - outMin)
}

function toneForCommand(accepted: boolean): 'good' | 'bad' {
  return accepted ? 'good' : 'bad'
}

function cloneSettings(settings: MotionSettings): MotionSettings {
  return JSON.parse(JSON.stringify(settings)) as MotionSettings
}

async function saveSettings(): Promise<void> {
  await motion.saveSettings(settingsDraft.value)
}

onMounted(async () => {
  await motion.refresh()
  await motion.refreshTrackingStatus()
  syncPidDraftFromStatus()
  settingsDraft.value = cloneSettings(motion.settings)
  stepSize.value = motion.settings.jog_step_deg
  window.addEventListener('keydown', handleKeyDown)
  window.addEventListener('keyup', handleKeyUp)
  window.addEventListener('blur', stopManualJog)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  gamepadFrame = window.requestAnimationFrame(pollGamepad)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('keyup', handleKeyUp)
  window.removeEventListener('blur', stopManualJog)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  if (gamepadFrame !== null) window.cancelAnimationFrame(gamepadFrame)
  gamepadFrame = null
  void stopManualJog()
})

function handleVisibilityChange(): void {
  if (document.visibilityState !== 'visible') void stopManualJog()
}

watch(
  () => motion.settings,
  (nextSettings) => {
    settingsDraft.value = cloneSettings(nextSettings)
  },
  { deep: true },
)
</script>

<template>
  <div class="grid gap-4">
    <div class="sticky top-0 z-10 rounded-md border border-red-400/35 bg-red-500/12 p-3">
      <button class="focus-ring w-full rounded-md bg-red-500 px-4 py-3 text-sm font-bold text-white" @click="motion.stop">
        STOP DRY-RUN MOTION
      </button>
    </div>
    <!-- ═══════════════ TRACKING PANEL ═══════════════ -->
    <DashboardCard
      title="Otonom Hedef Takibi"
      subtitle="Kapalı çevrim: Kamera → PID → Motor"
    >
      <!-- Status row -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <StatusBadge :label="trackingStateLabel" :tone="trackingStateColor" />
        <StatusBadge
          v-if="lastUpdate?.using_kalman_prediction"
          label="KALMAN TAHMİN"
          tone="warn"
        />
        <StatusBadge
          :label="motion.trackingStatus.active ? 'AKTİF' : 'PASIF'"
          :tone="motion.trackingStatus.active ? 'good' : 'neutral'"
        />
        <StatusBadge
          :label="motion.trackingStatus.active ? 'AKTİF' : 'PASIF'"
          :tone="motion.trackingStatus.active ? 'good' : 'neutral'"
        />
        <StatusBadge
          label="SİLAH AKTİF (ARMED)"
          tone="bad"
          class="animate-pulse shadow-[0_0_10px_rgba(239,68,68,0.5)]"
        />
        <StatusBadge
          :label="`${motion.trackingStatus.total_frames} frame`"
          tone="neutral"
        />
      </div>

      <div class="grid gap-4 xl:grid-cols-[1fr_1fr_auto]">
        <!-- Sol: Metriki telemetri -->
        <div class="grid gap-1">
          <MetricRow label="Hedef X" :value="lastUpdate ? `${lastUpdate.target_center_x?.toFixed(0) ?? '—'} px` : '—'" />
          <MetricRow label="Hedef Y" :value="lastUpdate ? `${lastUpdate.target_center_y?.toFixed(0) ?? '—'} px` : '—'" />
          <MetricRow label="Hata X" :value="lastUpdate ? `${lastUpdate.error_x_px.toFixed(1)} px` : '—'" />
          <MetricRow label="Hata Y" :value="lastUpdate ? `${lastUpdate.error_y_px.toFixed(1)} px` : '—'" />
          <MetricRow label="Mesafe" :value="`${errorMagnitude} px`" />
          <MetricRow label="Deadband" :value="lastUpdate?.deadband_zone ?? '—'" />
          <MetricRow label="Kayıp frame" :value="lastUpdate?.target_lost_frames ?? 0" />
          <MetricRow label="Kalıcı track" :value="`${motion.trackingStatus.multi_target_tracker.active_track_count} · ${motion.trackingStatus.multi_target_tracker.tracker_kind}`" />
        </div>

        <!-- Orta: Motor hız çıkışı -->
        <div class="grid gap-1">
          <MetricRow label="Motor Hız X" :value="lastUpdate ? `${lastUpdate.speed_x}` : '—'" />
          <MetricRow label="Motor Hız Y" :value="lastUpdate ? `${lastUpdate.speed_y}` : '—'" />
          <MetricRow label="Ham PID X" :value="lastUpdate ? `${lastUpdate.raw_pid_x.toFixed(1)}` : '—'" />
          <MetricRow label="Ham PID Y" :value="lastUpdate ? `${lastUpdate.raw_pid_y.toFixed(1)}` : '—'" />
          <MetricRow label="Toplam frame" :value="motion.trackingStatus.total_frames" />
          <MetricRow label="Hedef sayısı" :value="motion.trackingStatus.target_count" />
          <MetricRow label="Kayıp sayısı" :value="motion.trackingStatus.lost_count" />
          <MetricRow label="Son fire sonucu" :value="motion.trackingStatus.last_fire_result?.accepted ? 'ACK' : motion.trackingStatus.last_fire_result?.reason_codes.join(' · ') || 'henüz yok'" />
        </div>

        <!-- Sağ: Crosshair visualizer -->
        <div class="flex flex-col items-center gap-2">
          <span class="text-xs font-semibold uppercase tracking-widest text-slate-500">Hedef</span>
          <svg viewBox="0 0 120 120" class="h-28 w-28 rounded-md border border-white/10 bg-black/30">
            <!-- Grid -->
            <line x1="60" y1="0" x2="60" y2="120" stroke="#1e293b" stroke-width="1"/>
            <line x1="0" y1="60" x2="120" y2="60" stroke="#1e293b" stroke-width="1"/>
            <!-- Frame center crosshair -->
            <circle cx="60" cy="60" r="3" fill="#334155"/>
            <line x1="54" y1="60" x2="66" y2="60" stroke="#475569" stroke-width="1.5"/>
            <line x1="60" y1="54" x2="60" y2="66" stroke="#475569" stroke-width="1.5"/>
            <!-- Deadband rings -->
            <circle cx="60" cy="60" r="8"  fill="none" stroke="#22c55e" stroke-width="1" opacity="0.4"/>
            <circle cx="60" cy="60" r="17" fill="none" stroke="#f59e0b" stroke-width="1" opacity="0.3"/>
            <circle cx="60" cy="60" r="28" fill="none" stroke="#f87171" stroke-width="1" opacity="0.2"/>
            <!-- Target dot (normalized) -->
            <circle
              v-if="lastUpdate?.target_center_x != null"
              :cx="60 + Math.max(-54, Math.min(54, (lastUpdate.error_x_px / 200) * 54))"
              :cy="60 + Math.max(-54, Math.min(54, (lastUpdate.error_y_px / 200) * 54))"
              r="5"
              :fill="motion.trackingStatus.state === 'LOCKED' ? '#22c55e' : motion.trackingStatus.state === 'TRACKING' ? '#f59e0b' : '#f87171'"
            />
            <circle v-else cx="60" cy="60" r="4" fill="#475569"/>
          </svg>
          <span class="text-xs text-slate-500">lock / slow / medium</span>
        </div>
        <div class="rounded-md border border-white/8 bg-black/20 p-3 text-xs text-slate-300">
          <p class="mb-2 font-semibold text-cyan-100">Çoklu Track Telemetrisi</p>
          <p v-if="!motion.trackingStatus.multi_target_tracker.tracks.length" class="text-slate-500">Henüz aktif track yok.</p>
          <div v-for="track in motion.trackingStatus.multi_target_tracker.tracks" :key="track.track_id" class="mb-1 grid grid-cols-[auto_1fr_auto] gap-2 font-mono text-[11px]">
            <span>#{{ track.track_id }}</span>
            <span>{{ Math.round(track.center_x) }},{{ Math.round(track.center_y) }} · h{{ track.hits }}/m{{ track.misses }}</span>
            <span :class="track.predicted ? 'text-amber-300' : 'text-emerald-300'">{{ track.predicted ? 'PRED' : 'FRESH' }}</span>
          </div>
        </div>
        <div class="rounded-md border border-white/8 bg-black/20 p-3 text-xs text-slate-300">
          <p class="mb-2 font-semibold text-cyan-100">Aşama 2 Öncelik Telemetrisi</p>
          <p v-if="!motion.targetPriority.ranked_candidates.length" class="text-slate-500">Stable association bekleniyor; fiziksel seçim yapılmaz.</p>
          <div v-for="candidate in motion.targetPriority.ranked_candidates" :key="candidate.balloon_track_id" class="mb-1 flex justify-between gap-2 font-mono text-[11px]">
            <span :class="candidate.balloon_track_id === motion.targetPriority.selected_track_id ? 'text-emerald-300' : ''">#{{ candidate.balloon_track_id }} → body {{ candidate.body_detection_id }}</span>
            <span>{{ candidate.time_to_exit_s?.toFixed(2) ?? '—' }}s · {{ candidate.score.toFixed(3) }}</span>
          </div>
        </div>
      </div>

      <!-- Start / Stop butonu -->
      <div class="mt-5 grid grid-cols-2 gap-3">
        <button
          id="tracking-toggle-btn"
          :disabled="trackingBusy"
          class="focus-ring rounded-md px-4 py-3 text-sm font-bold transition-all"
          :class="motion.trackingActive
            ? 'bg-red-600 text-white hover:bg-red-700'
            : 'bg-cyan-500 text-slate-950 hover:bg-cyan-400'"
          @click="toggleTracking"
        >
          {{ trackingBusy ? 'Bekleniyor…' : (motion.trackingActive ? '⬛ TAKİBİ DURDUR' : '▶ TAKİBİ BAŞLAT') }}
        </button>
        <div class="rounded-md border border-white/8 bg-black/20 px-3 py-2 text-xs text-slate-400">
          <p>Ateşleme Hattı (Fire Zone): <span class="font-semibold text-pink-500">AÇIK (LZR)</span></p>
          <p>Motor komut: <span :class="motion.trackingActive ? 'text-green-400' : 'text-slate-500'">{{ motion.trackingActive ? 'AÇIK (SPD)' : 'KAPALI' }}</span></p>
          <p>Hız: {{ motion.trackingStatus.command_rate_hz }} Hz</p>
        </div>
      </div>
    </DashboardCard>

    <DashboardCard title="Manuel Nişan Jog" subtitle="Fiziksel yön kalibrasyonlu motor kontrolü">
      <div class="grid gap-4 md:grid-cols-[1fr_220px]">
        <div class="grid gap-3">
          <div class="grid grid-cols-3 gap-2">
            <div></div>
            <button
              class="focus-ring rounded-md bg-slate-700 px-4 py-3 text-xl font-bold text-white hover:bg-slate-600 disabled:opacity-50"
              :disabled="manualJogBusy"
              @click="manualJog(0, -manualJogSpeed, `Yukarı (0, ${-manualJogSpeed})`, 300)"
            >
              ↑
            </button>
            <div></div>
            <button
              class="focus-ring rounded-md bg-slate-700 px-4 py-3 text-xl font-bold text-white hover:bg-slate-600 disabled:opacity-50"
              :disabled="manualJogBusy"
              @click="manualJog(manualJogSpeed, 0, `Sol (${manualJogSpeed}, 0)`, 300)"
            >
              ←
            </button>
            <button
              class="focus-ring rounded-md bg-red-500 px-4 py-3 text-sm font-bold text-white hover:bg-red-600"
              @click="stopManualJog"
            >
              STOP
            </button>
            <button
              class="focus-ring rounded-md bg-slate-700 px-4 py-3 text-xl font-bold text-white hover:bg-slate-600 disabled:opacity-50"
              :disabled="manualJogBusy"
              @click="manualJog(-manualJogSpeed, 0, `Sağ (${-manualJogSpeed}, 0)`, 300)"
            >
              →
            </button>
            <div></div>
            <button
              class="focus-ring rounded-md bg-slate-700 px-4 py-3 text-xl font-bold text-white hover:bg-slate-600 disabled:opacity-50"
              :disabled="manualJogBusy"
              @click="manualJog(0, manualJogSpeed, `Aşağı (0, ${manualJogSpeed})`, 300)"
            >
              ↓
            </button>
            <div></div>
          </div>
        </div>
        <div class="grid gap-3">
          <label class="grid gap-1 text-xs text-slate-400">
            Hız
            <input v-model.number="manualJogSpeed" type="number" min="20" max="1000" step="20" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          </label>
          <label class="grid gap-1 text-xs text-slate-400">
            Süre ms
            <input v-model.number="manualJogDurationMs" type="number" min="80" max="1200" step="20" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          </label>
          <MetricRow label="Son komut" :value="manualJogLastCommand" />
          <MetricRow label="Girdi" :value="gamepadStatus" />
          <label class="grid gap-1 text-xs text-slate-400">
            Gamepad dead-zone
            <input v-model.number="gamepadDeadzone" type="number" min="0.05" max="0.5" step="0.01" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          </label>
        </div>
      </div>
    </DashboardCard>

    <!-- ═══════════════ PID TUNING ═══════════════ -->
    <DashboardCard title="PID Ayarları (Canlı)" subtitle="Takip sırasında değiştirilebilir">
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label class="grid gap-1 text-xs text-slate-400">Kp X<input v-model.number="pidDraft.pid_kp_x" type="number" step="0.5" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Ki X<input v-model.number="pidDraft.pid_ki_x" type="number" step="0.001" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Kd X<input v-model.number="pidDraft.pid_kd_x" type="number" step="0.05" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Maks Hız<input v-model.number="pidDraft.max_speed" type="number" step="100" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Kp Y<input v-model.number="pidDraft.pid_kp_y" type="number" step="0.5" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Ki Y<input v-model.number="pidDraft.pid_ki_y" type="number" step="0.001" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Kd Y<input v-model.number="pidDraft.pid_kd_y" type="number" step="0.05" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Smoothing α<input v-model.number="pidDraft.smoothing_alpha" type="number" step="0.05" min="0" max="1" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label>
      </div>
      <div class="mt-3 flex flex-wrap gap-3">
        <label class="flex items-center gap-2 text-sm text-slate-300">
          <input v-model="pidDraft.invert_x" type="checkbox" /> X yönü ters
        </label>
        <label class="flex items-center gap-2 text-sm text-slate-300">
          <input v-model="pidDraft.invert_y" type="checkbox" /> Y yönü ters
        </label>
        <label class="flex items-center gap-2 text-sm text-slate-300">
          <input v-model="pidDraft.lead_enabled" type="checkbox" /> Ölçülmüş latency lead (yalnız HIL/replay sonrası)
        </label>
        <label class="grid gap-1 text-xs text-slate-400">Lead çarpanı<input v-model.number="pidDraft.lead_latency_multiplier" :disabled="!pidDraft.lead_enabled" type="number" min="0" max="3" step="0.1" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white disabled:opacity-40"/></label>
        <label class="grid gap-1 text-xs text-slate-400">Max lead ms<input v-model.number="pidDraft.lead_max_horizon_ms" :disabled="!pidDraft.lead_enabled" type="number" min="0" max="300" step="5" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white disabled:opacity-40"/></label>
      </div>
      <button
        id="pid-apply-btn"
        class="focus-ring mt-4 rounded-md bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950"
        @click="applyPidConfig"
      >
        Uygula (Canlı)
      </button>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Motion State" subtitle="Dry-run turret authority">
        <MetricRow label="State" :value="motion.state.motion_state" />
        <MetricRow label="Last command" :value="motion.state.last_command" />
        <MetricRow label="Last error" :value="motion.state.last_error" />
        <MetricRow label="dry_run" :value="motion.state.dry_run" />
        <MetricRow label="hardware_enabled" :value="system.systemState.hardware_enabled" />
      </DashboardCard>

      <DashboardCard title="Pan / Tilt Position" subtitle="Simulated values">
        <MetricRow label="Pan" :value="`${motion.state.pan_position_deg.toFixed(2)} deg / ${motion.state.pan_position_steps} steps`" />
        <MetricRow label="Tilt" :value="`${motion.state.tilt_position_deg.toFixed(2)} deg / ${motion.state.tilt_position_steps} steps`" />
        <MetricRow label="Pan target" :value="`${motion.state.pan_target_deg.toFixed(2)} deg`" />
        <MetricRow label="Tilt target" :value="`${motion.state.tilt_target_deg.toFixed(2)} deg`" />
        <MetricRow label="Error" :value="`${motion.state.pan_error_deg.toFixed(2)} / ${motion.state.tilt_error_deg.toFixed(2)} deg`" />
      </DashboardCard>

      <DashboardCard title="Safety Inputs" subtitle="Motion gating state">
        <MetricRow label="E-stop" :value="motion.state.estop_state" />
        <MetricRow label="Driver enabled" :value="motion.state.driver_enabled" />
        <MetricRow label="Pan limits" :value="`${motion.state.pan_limit_left ? 'left ' : ''}${motion.state.pan_limit_right ? 'right' : ''}` || 'clear'" />
        <MetricRow label="Tilt limits" :value="`${motion.state.tilt_limit_up ? 'up ' : ''}${motion.state.tilt_limit_down ? 'down' : ''}` || 'clear'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="motion.isDryRun ? 'DRY RUN' : 'LIVE'" tone="warn" />
          <StatusBadge :label="limitWarning ? 'LIMIT ACTIVE' : 'LIMIT CLEAR'" :tone="limitWarning ? 'bad' : 'good'" />
          <StatusBadge label="CALIBRATION REQUIRED" tone="warn" />
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
      <DashboardCard title="Turret Visualizer" subtitle="Current and target angles">
        <svg viewBox="0 0 320 190" class="h-64 w-full rounded-md border border-white/10 bg-black/24">
          <line x1="24" y1="88" x2="276" y2="88" stroke="#334155" stroke-width="8" stroke-linecap="round" />
          <line x1="24" y1="88" x2="276" y2="88" stroke="#0891b2" stroke-width="2" stroke-linecap="round" />
          <circle :cx="panCurrentX" cy="88" r="8" fill="#22c55e" />
          <circle :cx="panTargetX" cy="88" r="6" fill="#f59e0b" />
          <line x1="294" y1="24" x2="294" y2="156" stroke="#334155" stroke-width="8" stroke-linecap="round" />
          <line x1="294" y1="24" x2="294" y2="156" stroke="#0891b2" stroke-width="2" stroke-linecap="round" />
          <circle cx="294" :cy="tiltCurrentY" r="8" fill="#22c55e" />
          <circle cx="294" :cy="tiltTargetY" r="6" fill="#f59e0b" />
          <text x="22" y="124" fill="#94a3b8" font-size="10">{{ motion.settings.pan_min_deg }} deg</text>
          <text x="230" y="124" fill="#94a3b8" font-size="10">{{ motion.settings.pan_max_deg }} deg</text>
          <text x="38" y="30" fill="#e2e8f0" font-size="12">Pan span {{ panRange }} deg</text>
          <text x="38" y="48" fill="#e2e8f0" font-size="12">Tilt span {{ tiltRange }} deg</text>
          <text v-if="limitWarning" x="38" y="168" fill="#f87171" font-size="13">Soft/physical limit warning</text>
        </svg>
      </DashboardCard>

      <DashboardCard title="Jog Controls" subtitle="Dry-run step movement">
        <div class="grid gap-3">
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Step size deg</label>
          <select v-model.number="stepSize" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option :value="0.5">0.5</option>
            <option :value="1">1.0</option>
            <option :value="2">2.0</option>
            <option :value="5">5.0</option>
          </select>
          <div class="grid grid-cols-2 gap-2">
            <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.jog({ axis: 'pan', direction: 'negative', step_deg: stepSize })">Preview Pan -</button>
            <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.jog({ axis: 'pan', direction: 'positive', step_deg: stepSize })">Preview Pan +</button>
            <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.jog({ axis: 'tilt', direction: 'negative', step_deg: stepSize })">Preview Tilt -</button>
            <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.jog({ axis: 'tilt', direction: 'positive', step_deg: stepSize })">Preview Tilt +</button>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.home">Preview Home</button>
            <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="motion.stop">Stop</button>
          </div>
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Go-to Dry-run" subtitle="Validated target angle">
        <div class="grid gap-3">
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Pan target deg</label>
          <input v-model.number="goToPan" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <label class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Tilt target deg</label>
          <input v-model.number="goToTilt" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.goTo({ pan_target_deg: goToPan, tilt_target_deg: goToTilt })">
            Preview Go-to
          </button>
        </div>
      </DashboardCard>

      <DashboardCard title="Scan Dry-run" subtitle="Disabled unless settings allow it">
        <MetricRow label="Scan enabled" :value="motion.settings.scan_enabled" />
        <MetricRow label="Scan range" :value="`${motion.settings.scan_min_deg}..${motion.settings.scan_max_deg} deg`" />
        <MetricRow label="Scan speed" :value="`${motion.settings.scan_speed_deg_s} deg/s`" />
        <div class="mt-4 grid grid-cols-2 gap-2">
          <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.scanStart">Preview Scan</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="motion.scanStop">Stop Scan</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Tracking Preview" subtitle="Vision center error only">
        <div class="grid gap-3">
          <div class="rounded-md border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            Tracking preview only. No command generated, no serial output.
          </div>
          <input v-model.number="trackingX" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-model.number="trackingY" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="motion.trackingPreview({ frame_width: 640, frame_height: 360, target_center_x: trackingX, target_center_y: trackingY })">
            Compute Preview
          </button>
          <div v-if="motion.latestCommand?.tracking_preview" class="rounded-md border border-white/8 bg-black/18 p-3 text-sm text-slate-200">
            <p>error_x={{ motion.latestCommand.tracking_preview.error_x_px }} px</p>
            <p>error_y={{ motion.latestCommand.tracking_preview.error_y_px }} px</p>
            <p>pan_delta={{ motion.latestCommand.tracking_preview.computed_pan_delta_deg.toFixed(2) }} deg</p>
            <p>tilt_delta={{ motion.latestCommand.tracking_preview.computed_tilt_delta_deg.toFixed(2) }} deg</p>
          </div>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Motion Settings" subtitle="Placeholder values, calibration required">
      <div class="mb-4 rounded-md border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
        Settings can be changed only while DISARMED. Backend validation rejects unsafe values.
      </div>
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label class="grid gap-1 text-xs text-slate-400">Pan min<input v-model.number="settingsDraft.pan_min_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Pan max<input v-model.number="settingsDraft.pan_max_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tilt min<input v-model.number="settingsDraft.tilt_min_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tilt max<input v-model.number="settingsDraft.tilt_max_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Pan steps/deg<input v-model.number="settingsDraft.pan_steps_per_degree" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tilt steps/deg<input v-model.number="settingsDraft.tilt_steps_per_degree" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Pan speed<input v-model.number="settingsDraft.pan_max_speed_deg_s" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tilt speed<input v-model.number="settingsDraft.tilt_max_speed_deg_s" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Pan accel<input v-model.number="settingsDraft.pan_accel_deg_s2" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tilt accel<input v-model.number="settingsDraft.tilt_accel_deg_s2" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Jog step<input v-model.number="settingsDraft.jog_step_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Deadband px<input v-model.number="settingsDraft.deadband_px" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tracking gain X<input v-model.number="settingsDraft.tracking_gain_x" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Tracking gain Y<input v-model.number="settingsDraft.tracking_gain_y" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Scan min<input v-model.number="settingsDraft.scan_min_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Scan max<input v-model.number="settingsDraft.scan_max_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
      </div>
      <div class="mt-4 flex flex-wrap gap-3">
        <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="settingsDraft.soft_limits_enabled" type="checkbox" /> Soft limits enabled</label>
        <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="settingsDraft.scan_enabled" type="checkbox" /> Scan enabled</label>
        <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="settingsDraft.backlash_compensation_enabled" type="checkbox" /> Backlash compensation</label>
      </div>
      <button class="focus-ring mt-4 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="saveSettings">
        Save Settings
      </button>
    </DashboardCard>

    <SafetyZoneProfilePanel />

    <DashboardCard title="Motion Command Log" subtitle="Dry-run command responses">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[880px] text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2">Command</th>
              <th class="py-2">Result</th>
              <th class="py-2">Reason</th>
              <th class="py-2">Blocking</th>
              <th class="py-2">No physical command</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in motion.commandLog" :key="entry.command_id" class="border-t border-white/8">
              <td class="py-2 font-mono text-cyan-200">{{ entry.command_type }}</td>
              <td class="py-2"><StatusBadge :label="entry.accepted ? 'accepted' : 'rejected'" :tone="toneForCommand(entry.accepted)" /></td>
              <td class="py-2 text-slate-300">{{ entry.reason }}</td>
              <td class="py-2 text-red-200">{{ entry.blocking_reasons.join(', ') || 'none' }}</td>
              <td class="py-2 text-slate-300">{{ entry.no_physical_command_generated }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="motion.commandLog.length === 0" class="py-4 text-sm text-slate-400">No motion command yet.</p>
      </div>
    </DashboardCard>
  </div>
</template>
