<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  Crosshair,
  Flame,
  Gamepad2,
  Gauge,
  MousePointerClick,
  Play,
  RefreshCw,
  Save,
  Square,
  Trash2,
  Zap,
} from '@lucide/vue'
import {
  fetchBallisticsLive,
  fetchBallisticsProfile,
  saveStation,
  startStage3Simulation,
  stopStage3Simulation,
  updateBallisticsProfile,
} from '../api/ballistics'
import { cameraStreamUrl } from '../api/vision'
import { sendStage1ManualMotion } from '../api/mission'
import { stopHardwareMotion } from '../api/hardware'
import { evaluateFireRequest } from '../api/decision'
import { useGamepadController } from '../composables/useGamepadController'
import type { BallisticsLiveState, BallisticsProfile, BallisticsStation } from '../types/ballistics'

// State
const activeStationKey = ref<'15' | '10' | '5'>('15')
const profile = ref<BallisticsProfile | null>(null)
const live = ref<BallisticsLiveState | null>(null)
const loading = ref(true)
const saving = ref(false)
const saveMessage = ref('')
const saveTone = ref<'neutral' | 'success' | 'error'>('neutral')

// Editable draft for active station (defaults to 0.0)
const stationDraft = reactive({
  offset_y_px: 0.0,
  offset_x_px: 0.0,
  flight_time_ms: 214.0,
  reference_balloon_w_px: 28.0,
  notes: '',
})

// Stage 3 simulation inputs
const simDraft = reactive({
  start_distance_m: 15.0,
  approach_speed_m_s: 0.30,
  enable_hybrid_vision: true,
})

// Gamepad & Manual Motion
const gamepad = useGamepadController()
let gamepadMotionTimer: ReturnType<typeof setInterval> | null = null
let gamepadMoving = false
const manualJogSpeed = ref(450)
const firing = ref(false)
const fireStatus = ref('')

// Interactive Click-to-Zero (Vuruş Noktası İşaretle)
const clickToZeroActive = ref(true)
const markedImpact = ref<{
  pixel_x: number
  pixel_y: number
  delta_x: number
  delta_y: number
  cm_x: number
  cm_y: number
} | null>(null)

// Polling interval for live interpolation
let pollTimer: ReturnType<typeof setInterval> | null = null
const streamNonce = ref(Date.now())

const activeStation = computed<BallisticsStation | null>(() => {
  if (!profile.value) return null
  return profile.value.stations[activeStationKey.value] ?? null
})

const streamUrl = computed(() => {
  return cameraStreamUrl({ width: 1280, height: 720, quality: 85, nonce: streamNonce.value })
})

// Displayed offsets for the reticle
const currentReticleX = computed(() => {
  if (live.value?.active) return live.value.total_offset_x_px
  return stationDraft.offset_x_px
})

const currentReticleY = computed(() => {
  if (live.value?.active) return live.value.total_offset_y_px
  return stationDraft.offset_y_px
})

// Pixel to cm equivalent estimate (Native 1280x720, 14cm balloon scale: k = 280.0 px*m)
const cmEquivalentY = computed(() => {
  const d = Number(activeStationKey.value)
  const pxPerCm = (280.0 / d) / 14.0
  const cm = stationDraft.offset_y_px / pxPerCm
  return cm.toFixed(1)
})

const cmEquivalentX = computed(() => {
  const d = Number(activeStationKey.value)
  const pxPerCm = (280.0 / d) / 14.0
  const cm = stationDraft.offset_x_px / pxPerCm
  return cm.toFixed(1)
})

const gamepadConnected = computed(() => {
  return Boolean(gamepad.activeDevice.value) && gamepad.profile.value.enabled
})

function selectStation(key: '15' | '10' | '5') {
  activeStationKey.value = key
  const s = profile.value?.stations[key]
  if (s) {
    stationDraft.offset_y_px = s.offset_y_px
    stationDraft.offset_x_px = s.offset_x_px
    stationDraft.flight_time_ms = s.flight_time_ms
    stationDraft.reference_balloon_w_px = s.reference_balloon_w_px
    stationDraft.notes = s.notes
  }
}

function adjustY(delta: number) {
  stationDraft.offset_y_px = Math.round((stationDraft.offset_y_px + delta) * 10) / 10
}

function adjustX(delta: number) {
  stationDraft.offset_x_px = Math.round((stationDraft.offset_x_px + delta) * 10) / 10
}

function resetHorizontal() {
  stationDraft.offset_x_px = 0.0
}

// Click to zero on video stream (Native 1280x720)
function handleVideoClick(event: MouseEvent) {
  if (!clickToZeroActive.value) return
  const target = event.currentTarget as HTMLElement
  const rect = target.getBoundingClientRect()
  const relX = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  const relY = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
  const frameX = Math.round(relX * 1280)
  const frameY = Math.round(relY * 720)
  // Optical center is (640, 360) in native 1280x720
  const dx = frameX - 640
  const dy = frameY - 360
  const d = Number(activeStationKey.value)
  const pxPerCm = (280.0 / d) / 14.0
  const cmX = Math.round((dx / pxPerCm) * 10) / 10
  const cmY = Math.round((dy / pxPerCm) * 10) / 10

  markedImpact.value = {
    pixel_x: frameX,
    pixel_y: frameY,
    delta_x: dx,
    delta_y: dy,
    cm_x: cmX,
    cm_y: cmY,
  }

  // Pre-fill the draft
  stationDraft.offset_x_px = dx
  stationDraft.offset_y_px = dy
  saveTone.value = 'neutral'
  saveMessage.value = `Vuruş işaretlendi: ${cmY > 0 ? '+' : ''}${cmY}cm (${dy}px) dikey, ${cmX > 0 ? '+' : ''}${cmX}cm (${dx}px) yatay. Tavsiye: Sadece dikey sapmayı kaydetmeyi seçin.`
}

function clearMarkedImpact() {
  markedImpact.value = null
}

function applyVerticalOnlyImpact() {
  if (!markedImpact.value) return
  // Keep horizontal at 0.0 so unintentional mouse clicks don't divert yaw!
  stationDraft.offset_x_px = 0.0
  stationDraft.offset_y_px = markedImpact.value.delta_y
  void saveCurrentStation()
}

function applyMarkedImpactDirectly() {
  if (!markedImpact.value) return
  stationDraft.offset_x_px = markedImpact.value.delta_x
  stationDraft.offset_y_px = markedImpact.value.delta_y
  void saveCurrentStation()
}

// Manual Jog Actions
async function nudgeTurret(sx: number, sy: number) {
  gamepadMoving = true
  await sendStage1ManualMotion({ speed_x: sx, speed_y: sy, duration_ms: 320 }).catch(() => undefined)
}

async function stopTurret() {
  gamepadMoving = false
  await stopHardwareMotion().catch(() => undefined)
}

async function handleTestFire() {
  if (firing.value) return
  firing.value = true
  fireStatus.value = 'ATIŞ TETİKLENİYOR...'
  try {
    const res = await evaluateFireRequest(true)
    if (res.accepted) {
      fireStatus.value = '✓ ATEŞ EDİLDİ (Pico Onayladı)'
    } else {
      fireStatus.value = res.reason || 'Atış engellendi'
    }
  } catch (err) {
    fireStatus.value = err instanceof Error ? err.message : 'Atış hatası'
  } finally {
    firing.value = false
    setTimeout(() => { fireStatus.value = '' }, 4000)
  }
}

// Gamepad polling loop
async function sendGamepadMotionPulse() {
  if (!gamepad.profile.value.enabled || !gamepad.activeDevice.value) {
    if (gamepadMoving) {
      gamepadMoving = false
      void stopHardwareMotion().catch(() => undefined)
    }
    return
  }
  const sx = gamepad.speedX.value
  const sy = gamepad.speedY.value
  if (sx === 0 && sy === 0) {
    if (gamepadMoving) {
      gamepadMoving = false
      void stopHardwareMotion().catch(() => undefined)
    }
    return
  }
  gamepadMoving = true
  await sendStage1ManualMotion({ speed_x: sx, speed_y: sy, duration_ms: 420 }).catch(() => undefined)
}

watch(gamepad.triggerPressed, (pressed, prev) => {
  if (pressed && !prev && gamepad.profile.value.enabled) {
    void handleTestFire()
  }
})

// Keyboard listeners
function handleKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
  const spd = manualJogSpeed.value
  let sx = 0
  let sy = 0
  if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') sx = -spd
  if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') sx = spd
  if (e.key === 'ArrowUp' || e.key === 'w' || e.key === 'W') sy = spd
  if (e.key === 'ArrowDown' || e.key === 's' || e.key === 'S') sy = -spd
  if (e.key === ' ') {
    e.preventDefault()
    void handleTestFire()
    return
  }
  if (sx !== 0 || sy !== 0) {
    e.preventDefault()
    gamepadMoving = true
    void sendStage1ManualMotion({ speed_x: sx, speed_y: sy, duration_ms: 320 }).catch(() => undefined)
  }
}

function handleKeyup(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
  if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'a', 'd', 'w', 's', 'A', 'D', 'W', 'S'].includes(e.key)) {
    if (gamepadMoving) {
      gamepadMoving = false
      void stopHardwareMotion().catch(() => undefined)
    }
  }
}

async function loadProfile() {
  loading.value = true
  try {
    profile.value = await fetchBallisticsProfile()
    simDraft.start_distance_m = profile.value.start_distance_m
    simDraft.approach_speed_m_s = profile.value.approach_speed_m_s
    simDraft.enable_hybrid_vision = profile.value.enable_hybrid_vision
    selectStation(activeStationKey.value)
  } catch (err) {
    saveTone.value = 'error'
    saveMessage.value = 'Profil yüklenemedi'
  } finally {
    loading.value = false
  }
}

async function saveCurrentStation() {
  saving.value = true
  saveTone.value = 'neutral'
  saveMessage.value = 'Kaydediliyor...'
  try {
    profile.value = await saveStation({
      distance_m: Number(activeStationKey.value),
      offset_y_px: stationDraft.offset_y_px,
      offset_x_px: stationDraft.offset_x_px,
      flight_time_ms: stationDraft.flight_time_ms,
      reference_balloon_w_px: stationDraft.reference_balloon_w_px,
      notes: stationDraft.notes,
    })
    saveTone.value = 'success'
    saveMessage.value = `${activeStationKey.value}M İstasyonu Kaydedildi! (Y: ${stationDraft.offset_y_px}px, X: ${stationDraft.offset_x_px}px)`
  } catch (err) {
    saveTone.value = 'error'
    saveMessage.value = err instanceof Error ? err.message : 'Kayıt başarısız'
  } finally {
    saving.value = false
  }
}

async function toggleMasterEnabled() {
  if (!profile.value) return
  profile.value.enabled = !profile.value.enabled
  try {
    profile.value = await updateBallisticsProfile(profile.value)
  } catch {
    profile.value.enabled = !profile.value.enabled
  }
}

async function handleStartStage3() {
  try {
    live.value = await startStage3Simulation({
      start_distance_m: simDraft.start_distance_m,
      approach_speed_m_s: simDraft.approach_speed_m_s,
      enable_hybrid_vision: simDraft.enable_hybrid_vision,
    })
  } catch (err) {
    saveTone.value = 'error'
    saveMessage.value = 'Aşama 3 başlatılamadı'
  }
}

async function handleStopStage3() {
  try {
    live.value = await stopStage3Simulation()
  } catch (err) {
    saveTone.value = 'error'
    saveMessage.value = 'Aşama 3 durdurulamadı'
  }
}

async function pollLive() {
  try {
    live.value = await fetchBallisticsLive()
  } catch {
    // suppress poll errors
  }
}

function refreshStream() {
  streamNonce.value = Date.now()
}

onMounted(async () => {
  await loadProfile()
  await pollLive()
  pollTimer = setInterval(pollLive, 200)

  // Start gamepad
  gamepad.start()
  gamepadMotionTimer = setInterval(() => { void sendGamepadMotionPulse() }, 120)

  // Register keyboard
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('keyup', handleKeyup)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (gamepadMotionTimer) clearInterval(gamepadMotionTimer)
  gamepad.stop()
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('keyup', handleKeyup)
})
</script>

<template>
  <div class="ballistics-lab">
    <!-- Top Header Bar -->
    <header class="lab-header">
      <div class="header-left">
        <RouterLink to="/cockpit" class="back-link">
          <ArrowLeft :size="16" />
          <span>KOKPİT</span>
        </RouterLink>
        <div class="title-group">
          <h1>
            <Crosshair :size="20" class="text-cyan-400" />
            <span>BALİSTİK & MESAFE KALİBRASYON LAB</span>
          </h1>
          <p>5m / 10m / 15m İstasyon Boresight Sıfırlama & Aşama 3 Dinamik Enterpolasyon</p>
        </div>
      </div>
      <div class="header-right">
        <div class="header-status-pills">
          <span :class="['gamepad-pill', { active: gamepadConnected }]">
            <Gamepad2 :size="14" />
            <span>{{ gamepadConnected ? 'USB JOYSTICK AKTİF' : 'JOYSTICK BEKLENİYOR' }}</span>
          </span>
          <button
            type="button"
            :class="['toggle-btn', { active: profile?.enabled }]"
            @click="toggleMasterEnabled"
          >
            <Zap :size="15" />
            <span>{{ profile?.enabled ? 'BALİSTİK OFSET AKTİF' : 'OFSET DEVRE DIŞI' }}</span>
          </button>
        </div>
      </div>
    </header>

    <!-- Main 2-Column Grid -->
    <main class="lab-grid">
      <!-- Left Column: Scope & Live Stream -->
      <section class="scope-panel">
        <div class="stream-card">
          <div class="stream-container" :class="{ 'cursor-crosshair': clickToZeroActive }" @click="handleVideoClick">
            <img :src="streamUrl" alt="Canlı Kamera Akışı" class="camera-feed" @error="refreshStream" />

            <!-- Scope Reticle SVG Overlay (Native 1280x720) -->
            <svg class="scope-overlay" viewBox="0 0 1280 720" preserveAspectRatio="none">
              <!-- Camera Optical Center Crosshair (Default 640, 360 in 1280x720) -->
              <g class="optic-center">
                <line x1="640" y1="310" x2="640" y2="410" stroke="rgba(103,232,249,0.35)" stroke-width="1.5" stroke-dasharray="4,4" />
                <line x1="590" y1="360" x2="690" y2="360" stroke="rgba(103,232,249,0.35)" stroke-width="1.5" stroke-dasharray="4,4" />
                <circle cx="640" cy="360" r="5" fill="none" stroke="rgba(103,232,249,0.5)" stroke-width="1.5" />
                <text x="650" y="352" fill="rgba(103,232,249,0.6)" font-size="10" font-family="monospace">OPTİK MERKEZ (640, 360)</text>
              </g>

              <!-- Dynamic Ballistics Aim Reticle (Always shown in Lab) -->
              <g
                class="ballistic-aim"
                :transform="`translate(${640 + currentReticleX}, ${360 + currentReticleY})`"
              >
                <line x1="0" y1="-26" x2="0" y2="-6" stroke="#f59e0b" stroke-width="2.5" />
                <line x1="0" y1="6" x2="0" y2="26" stroke="#f59e0b" stroke-width="2.5" />
                <line x1="-26" y1="0" x2="-6" y2="0" stroke="#f59e0b" stroke-width="2.5" />
                <line x1="6" y1="0" x2="26" y2="0" stroke="#f59e0b" stroke-width="2.5" />
                <circle cx="0" cy="0" r="9" fill="none" stroke="#f59e0b" stroke-width="2" />
                <circle cx="0" cy="0" r="2.5" fill="#ef4444" />
                <text x="14" y="-12" fill="#f59e0b" font-size="11" font-weight="bold" font-family="monospace">
                  NAMLU VURUŞ NOKTASI ({{ activeStationKey }}m: {{ currentReticleY }}px dikey, {{ currentReticleX }}px yatay)
                </text>
              </g>

              <!-- Marked Impact Point (Red POI) -->
              <g
                v-if="markedImpact"
                class="marked-impact"
                :transform="`translate(${markedImpact.pixel_x}, ${markedImpact.pixel_y})`"
              >
                <circle cx="0" cy="0" r="14" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="4,3" />
                <circle cx="0" cy="0" r="3" fill="#ef4444" />
                <line x1="-18" y1="0" x2="18" y2="0" stroke="#ef4444" stroke-width="2" />
                <line x1="0" y1="-18" x2="0" y2="18" stroke="#ef4444" stroke-width="2" />
                <text x="14" y="20" fill="#ef4444" font-size="11" font-weight="bold" font-family="monospace">
                  İŞARETLENEN VURUŞ İZİ ({{ markedImpact.cm_y > 0 ? '+' : '' }}{{ markedImpact.cm_y }}cm)
                </text>
              </g>
            </svg>

            <!-- Click to Zero Notification Banner -->
            <div v-if="clickToZeroActive && !markedImpact" class="click-hint-badge">
              <MousePointerClick :size="14" />
              <span>Atış sonrası merminin vurduğu yere ekranda tıklayın; ofset otomatik hesaplansın</span>
            </div>

            <!-- Live Telemetry HUD Overlay -->
            <div class="scope-hud">
              <div class="hud-item">
                <span class="hud-label">HESAPLANAN MESAFE</span>
                <span class="hud-val font-bold text-cyan-300">{{ live?.estimated_distance_m ?? 15.0 }} m</span>
              </div>
              <div class="hud-item">
                <span class="hud-label">DİKEY TELAFİ (Y)</span>
                <span class="hud-val text-amber-400 font-bold">{{ currentReticleY }} px ({{ cmEquivalentY }} cm)</span>
              </div>
              <div class="hud-item">
                <span class="hud-label">YANAL TELAFİ (X)</span>
                <span class="hud-val text-cyan-300 font-bold">{{ currentReticleX }} px</span>
              </div>
              <div class="hud-item">
                <span class="hud-label">UÇUŞ SÜRESİ</span>
                <span class="hud-val text-emerald-400 font-bold">{{ stationDraft.flight_time_ms }} ms</span>
              </div>
            </div>
          </div>

          <!-- Impact Action Bar (When impact is clicked) -->
          <div v-if="markedImpact" class="impact-action-bar">
            <div class="impact-text">
              <span class="impact-title">🎯 İŞARETLENEN VURUŞ İZİ:</span>
              <span class="impact-metrics">
                Dikey: <b>{{ markedImpact.cm_y > 0 ? '+' : '' }}{{ markedImpact.cm_y }} cm</b> ({{ markedImpact.delta_y }} px) ·
                Yatay: <b>{{ markedImpact.cm_x > 0 ? '+' : '' }}{{ markedImpact.cm_x }} cm</b> ({{ markedImpact.delta_x }} px)
              </span>
            </div>
            <div class="impact-btns">
              <button
                type="button"
                class="apply-vertical-btn"
                title="Yatay sapmayı 0 tutar, yalnızca dikey düşmeyi/yüksekliği düzeltir"
                @click="applyVerticalOnlyImpact"
              >
                <Check :size="15" />
                <span>SADECE DİKEY SAPMAYI KAYDET (YATAY = 0)</span>
              </button>
              <button
                type="button"
                class="apply-impact-btn"
                title="Hem dikey hem yatay sapmayı kaydeder"
                @click="applyMarkedImpactDirectly"
              >
                <span>TAM SAPMAYI KAYDET</span>
              </button>
              <button type="button" class="clear-impact-btn" title="İşareti kaldır" @click="clearMarkedImpact">
                <Trash2 :size="14" />
              </button>
            </div>
          </div>

          <!-- Scope Action Footer: Manual Jog Controls & Test Fire -->
          <div class="scope-footer-bar">
            <div class="manual-jog-cluster">
              <span class="cluster-label">TARET MANUEL HAREKET:</span>
              <div class="dpad-buttons">
                <button type="button" class="jog-btn" title="Sola Çevir (A / Sol Ok)" @click="nudgeTurret(-manualJogSpeed, 0)">
                  <ArrowLeft :size="14" />
                </button>
                <button type="button" class="jog-btn" title="Yukarı Kaldır (W / Yukarı Ok)" @click="nudgeTurret(0, manualJogSpeed)">
                  <ArrowUp :size="14" />
                </button>
                <button type="button" class="jog-btn" title="Aşağı Eğ (S / Aşağı Ok)" @click="nudgeTurret(0, -manualJogSpeed)">
                  <ArrowDown :size="14" />
                </button>
                <button type="button" class="jog-btn" title="Sağa Çevir (D / Sağ Ok)" @click="nudgeTurret(manualJogSpeed, 0)">
                  <ArrowRight :size="14" />
                </button>
                <button type="button" class="jog-btn stop-btn" title="Hareketi Durdur" @click="stopTurret">
                  <Square :size="12" />
                </button>
              </div>
            </div>

            <div class="fire-cluster">
              <button
                type="button"
                :class="['test-fire-btn', { firing }]"
                :disabled="firing"
                title="Pico'ya ateş darbesi gönderir (Boşluk tuşu / Joystick tetiği)"
                @click="handleTestFire"
              >
                <Flame :size="16" />
                <span>{{ firing ? 'ATEŞLENİYOR...' : 'TEST ATIŞI YAP (TETİK)' }}</span>
              </button>
              <span v-if="fireStatus" class="fire-feedback">{{ fireStatus }}</span>
            </div>

            <button type="button" class="action-btn secondary refresh-btn" @click="refreshStream">
              <RefreshCw :size="14" />
              <span>Yenile</span>
            </button>
          </div>
        </div>
      </section>

      <!-- Right Column: Station Configuration & Stage 3 Simulation -->
      <aside class="controls-panel">
        <!-- Station Selector Tabs -->
        <div class="panel-card stations-card">
          <div class="card-head">
            <Gauge :size="16" class="text-cyan-400" />
            <h2>MESAFE İSTASYONU KALİBRASYONU</h2>
          </div>

          <div class="station-tabs">
            <button
              type="button"
              :class="['tab-btn', { active: activeStationKey === '15' }]"
              @click="selectStation('15')"
            >
              <b>15 METRE</b>
              <small>Uzak Ray</small>
            </button>
            <button
              type="button"
              :class="['tab-btn', { active: activeStationKey === '10' }]"
              @click="selectStation('10')"
            >
              <b>10 METRE</b>
              <small>Orta Ray</small>
            </button>
            <button
              type="button"
              :class="['tab-btn', { active: activeStationKey === '5' }]"
              @click="selectStation('5')"
            >
              <b>5 METRE</b>
              <small>Yakın Ray</small>
            </button>
          </div>

          <!-- Active Station Tuning Form -->
          <div class="tuning-body">
            <!-- Vertical Offset (Y) -->
            <div class="tune-group">
              <div class="group-title">
                <span>DİKEY OFSET (Y) — NAMLU DÜŞÜM / YÜKSELME</span>
                <b class="text-amber-400">{{ stationDraft.offset_y_px }} px</b>
              </div>
              <div class="stepper-row">
                <button type="button" class="step-btn" @click="adjustY(-10)">-10</button>
                <button type="button" class="step-btn" @click="adjustY(-5)">-5</button>
                <button type="button" class="step-btn" @click="adjustY(-1)">-1</button>
                <input
                  v-model.number="stationDraft.offset_y_px"
                  type="number"
                  step="0.5"
                  class="tune-input"
                />
                <button type="button" class="step-btn" @click="adjustY(1)">+1</button>
                <button type="button" class="step-btn" @click="adjustY(5)">+5</button>
                <button type="button" class="step-btn" @click="adjustY(10)">+10</button>
              </div>
              <p class="tune-hint">
                Fiziksel karşılık: Yaklaşık <b>{{ cmEquivalentY }} cm</b> namlu düzeltmesi. Eksi (-) değer namluyu aşağı eğer. (Kayıtlı: {{ activeStation?.offset_y_px ?? 0 }} px)
              </p>
            </div>

            <!-- Horizontal Offset (X) -->
            <div class="tune-group">
              <div class="group-title">
                <span>YATAY OFSET (X) — BORESIGHT SIFIRLAMA</span>
                <b class="text-cyan-400">{{ stationDraft.offset_x_px }} px</b>
              </div>
              <div class="stepper-row">
                <button type="button" class="step-btn" @click="adjustX(-5)">-5</button>
                <button type="button" class="step-btn" @click="adjustX(-1)">-1</button>
                <input
                  v-model.number="stationDraft.offset_x_px"
                  type="number"
                  step="0.5"
                  class="tune-input"
                />
                <button type="button" class="step-btn" @click="adjustX(1)">+1</button>
                <button type="button" class="step-btn" @click="adjustX(5)">+5</button>
                <button type="button" class="step-btn zero-btn" title="Yatay ofseti sıfırla (0 px)" @click="resetHorizontal">Sıfırla (0)</button>
              </div>
              <p class="tune-hint">
                Fiziksel karşılık: Yaklaşık <b>{{ cmEquivalentX }} cm</b> yanal sapma düzeltmesi.
              </p>
            </div>

            <!-- Flight time & Balloon scale -->
            <div class="double-group">
              <label>
                <span>Mermi Uçuş Süresi (ms)</span>
                <input v-model.number="stationDraft.flight_time_ms" type="number" step="1" class="tune-input" />
              </label>
              <label>
                <span>14cm Balon Çapı (px)</span>
                <input v-model.number="stationDraft.reference_balloon_w_px" type="number" step="1" class="tune-input" />
              </label>
            </div>

            <!-- Save Station Button -->
            <div class="save-row">
              <button
                type="button"
                class="save-station-btn"
                :disabled="saving"
                @click="saveCurrentStation"
              >
                <Save :size="16" />
                <span>{{ saving ? 'KAYDEDİLİYOR...' : `${activeStationKey}M İSTASYONUNU KAYDET` }}</span>
              </button>
              <span v-if="saveMessage" :class="['status-msg', saveTone]">{{ saveMessage }}</span>
            </div>
          </div>
        </div>

        <!-- Stage 3 Kinematics Simulation Card -->
        <div class="panel-card stage3-card">
          <div class="card-head">
            <Activity :size="16" class="text-purple-400" />
            <h2>AŞAMA 3 YAKLAŞMA MOTORU (15M ➔ 5M)</h2>
          </div>

          <div class="stage3-body">
            <p class="stage3-desc">
              Hedef 15 metreden taret yönünde ilerlerken, geçen süreye göre ofset <b>15m ➔ 10m ➔ 5m</b> arasında dinamik enterpolasyonla anlık uygulanır.
            </p>

            <div class="stage3-inputs">
              <label>
                <span>Başlangıç (m)</span>
                <input v-model.number="simDraft.start_distance_m" type="number" step="0.5" class="tune-input" />
              </label>
              <label>
                <span>Yaklaşma Hızı (m/s)</span>
                <input v-model.number="simDraft.approach_speed_m_s" type="number" step="0.05" class="tune-input" />
              </label>
            </div>

            <div class="checkbox-row">
              <label class="hybrid-check">
                <input v-model="simDraft.enable_hybrid_vision" type="checkbox" />
                <span>Hibrit Görsel Düzeltme (YOLO balon piksel boyutu ile zamanı harmanla)</span>
              </label>
            </div>

            <!-- Simulation Controls -->
            <div class="sim-actions">
              <button
                v-if="!live?.active"
                type="button"
                class="sim-btn start"
                @click="handleStartStage3"
              >
                <Play :size="16" />
                <span>AŞAMA 3 SİMÜLASYONU BAŞLAT</span>
              </button>
              <button
                v-else
                type="button"
                class="sim-btn stop"
                @click="handleStopStage3"
              >
                <Square :size="16" />
                <span>SİMÜLASYONU DURDUR</span>
              </button>
            </div>

            <!-- Live Sim Status Indicator -->
            <div v-if="live?.active" class="sim-live-meter">
              <div class="meter-bar">
                <div
                  class="meter-fill"
                  :style="{ width: `${Math.max(0, Math.min(100, ((15.0 - (live.estimated_distance_m || 5.0)) / 10.0) * 100))}%` }"
                ></div>
              </div>
              <div class="meter-labels">
                <span>15m</span>
                <span>Geçen Süre: <b>{{ live.elapsed_s }} s</b></span>
                <span>Hesaplanan: <b class="text-cyan-400">{{ live.estimated_distance_m }} m</b></span>
                <span>Dikey Ofset: <b class="text-amber-400">{{ live.total_offset_y_px }} px</b></span>
                <span>5m</span>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.ballistics-lab {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #030a12;
  color: #e2e8f0;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

/* Header */
.lab-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 18px;
  background: rgba(10, 24, 38, 0.95);
  border-bottom: 1px solid rgba(56, 189, 248, 0.2);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #94a3b8;
  font-size: 0.75rem;
  font-weight: 700;
  text-decoration: none;
  transition: all 0.2s;
}
.back-link:hover {
  background: rgba(56, 189, 248, 0.15);
  color: #38bdf8;
  border-color: rgba(56, 189, 248, 0.4);
}
.title-group h1 {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font: 800 1.05rem ui-monospace, monospace;
  letter-spacing: 0.05em;
  color: #f8fafc;
}
.title-group p {
  margin: 2px 0 0;
  font-size: 0.7rem;
  color: #64748b;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-status-pills {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gamepad-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 6px;
  font: 700 0.65rem ui-monospace, monospace;
  background: rgba(15, 23, 42, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #64748b;
}
.gamepad-pill.active {
  background: rgba(16, 185, 129, 0.15);
  border-color: rgba(16, 185, 129, 0.4);
  color: #34d399;
}
.toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  font: 800 0.72rem ui-monospace, monospace;
  cursor: pointer;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(15, 23, 42, 0.6);
  color: #94a3b8;
  transition: all 0.2s ease;
}
.toggle-btn.active {
  background: linear-gradient(180deg, #0284c7, #0369a1);
  border-color: #38bdf8;
  color: #ffffff;
  box-shadow: 0 0 16px rgba(56, 189, 248, 0.35);
}

/* Grid */
.lab-grid {
  display: grid;
  grid-template-columns: 1fr 430px;
  gap: 14px;
  padding: 14px;
  flex: 1;
  overflow: hidden;
}

/* Scope & Stream Panel */
.scope-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.stream-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #06111d;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid rgba(56, 189, 248, 0.25);
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.6);
}
.stream-container {
  position: relative;
  flex: 1;
  background: #000000;
  overflow: hidden;
}
.stream-container.cursor-crosshair {
  cursor: crosshair;
}
.camera-feed {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.scope-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.click-hint-badge {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(15, 23, 42, 0.88);
  border: 1px solid rgba(245, 158, 11, 0.5);
  border-radius: 20px;
  padding: 6px 14px;
  font: 700 0.72rem sans-serif;
  color: #fef3c7;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}
.scope-hud {
  position: absolute;
  bottom: 12px;
  left: 12px;
  display: flex;
  gap: 10px;
  background: rgba(3, 10, 18, 0.9);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 8px;
  padding: 6px 12px;
  backdrop-filter: blur(8px);
}
.hud-item {
  display: flex;
  flex-direction: column;
}
.hud-label {
  font: 700 0.5rem ui-monospace, monospace;
  color: #64748b;
  letter-spacing: 0.05em;
}
.hud-val {
  font: 800 0.8rem ui-monospace, monospace;
}

/* Impact Action Bar */
.impact-action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 14px;
  background: rgba(239, 68, 68, 0.12);
  border-top: 1px solid rgba(239, 68, 68, 0.4);
  border-bottom: 1px solid rgba(239, 68, 68, 0.2);
}
.impact-text {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.75rem;
}
.impact-title {
  font-weight: 800;
  color: #f87171;
  font-family: ui-monospace, monospace;
}
.impact-metrics b {
  color: #fef08a;
}
.impact-btns {
  display: flex;
  align-items: center;
  gap: 6px;
}
.apply-vertical-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  background: #059669;
  border: 1px solid #34d399;
  color: #ffffff;
  font: 800 0.7rem ui-monospace, monospace;
  cursor: pointer;
  transition: all 0.15s;
}
.apply-vertical-btn:hover {
  background: #047857;
  box-shadow: 0 0 12px rgba(52, 211, 153, 0.4);
}
.apply-impact-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  background: #dc2626;
  border: 1px solid #f87171;
  color: #ffffff;
  font: 800 0.7rem ui-monospace, monospace;
  cursor: pointer;
  transition: all 0.15s;
}
.apply-impact-btn:hover {
  background: #b91c1c;
  box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
}
.zero-btn {
  background: rgba(6, 182, 212, 0.2) !important;
  border-color: rgba(6, 182, 212, 0.4) !important;
  color: #67e8f9 !important;
  font-size: 0.68rem !important;
  padding: 0 8px !important;
}
.zero-btn:hover {
  background: rgba(6, 182, 212, 0.35) !important;
}
.clear-impact-btn {
  padding: 5px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #94a3b8;
  cursor: pointer;
}
.clear-impact-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
}

/* Scope Footer: Jog and Fire Bar */
.scope-footer-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 14px;
  background: rgba(15, 23, 42, 0.75);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.manual-jog-cluster {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cluster-label {
  font: 700 0.65rem ui-monospace, monospace;
  color: #64748b;
}
.dpad-buttons {
  display: flex;
  gap: 4px;
}
.jog-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s;
}
.jog-btn:hover {
  background: rgba(56, 189, 248, 0.2);
  border-color: #38bdf8;
  color: #38bdf8;
}
.jog-btn:active {
  background: #0284c7;
  color: #ffffff;
}
.jog-btn.stop-btn {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.3);
  color: #f87171;
}
.jog-btn.stop-btn:hover {
  background: #dc2626;
  color: #ffffff;
}
.fire-cluster {
  display: flex;
  align-items: center;
  gap: 10px;
}
.test-fire-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 6px;
  background: linear-gradient(180deg, #dc2626, #991b1b);
  border: 1px solid #f87171;
  color: #ffffff;
  font: 800 0.72rem ui-monospace, monospace;
  cursor: pointer;
  box-shadow: 0 0 12px rgba(220, 38, 38, 0.35);
  transition: all 0.15s;
}
.test-fire-btn:hover:not(:disabled) {
  background: linear-gradient(180deg, #ef4444, #b91c1c);
  box-shadow: 0 0 16px rgba(239, 68, 68, 0.5);
}
.test-fire-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.fire-feedback {
  font: 700 0.7rem ui-monospace, monospace;
  color: #34d399;
}
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #94a3b8;
  font-size: 0.7rem;
  cursor: pointer;
}
.refresh-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #f1f5f9;
}

/* Controls Panel */
.controls-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}
.panel-card {
  background: rgba(10, 24, 38, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-head h2 {
  margin: 0;
  font: 800 0.78rem ui-monospace, monospace;
  letter-spacing: 0.08em;
  color: #f1f5f9;
}

/* Station Tabs */
.station-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.tab-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 6px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(15, 23, 42, 0.6);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}
.tab-btn b {
  font: 800 0.78rem ui-monospace, monospace;
}
.tab-btn small {
  font-size: 0.6rem;
  color: #64748b;
}
.tab-btn.active {
  background: linear-gradient(180deg, rgba(14, 116, 144, 0.5), rgba(3, 105, 161, 0.3));
  border-color: #38bdf8;
  color: #38bdf8;
  box-shadow: 0 0 12px rgba(56, 189, 248, 0.25);
}

/* Tuning Body */
.tuning-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tune-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.group-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font: 700 0.65rem ui-monospace, monospace;
  color: #94a3b8;
}
.stepper-row {
  display: flex;
  gap: 4px;
}
.step-btn {
  padding: 5px 8px;
  border-radius: 6px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #cbd5e1;
  font: 800 0.72rem ui-monospace, monospace;
  cursor: pointer;
}
.step-btn:hover {
  background: rgba(56, 189, 248, 0.15);
  border-color: #38bdf8;
  color: #38bdf8;
}
.tune-input {
  flex: 1;
  text-align: center;
  font: 800 0.85rem ui-monospace, monospace;
  background: rgba(3, 10, 18, 0.8);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 6px;
  color: #f1f5f9;
  padding: 4px 6px;
}
.tune-hint {
  margin: 0;
  font-size: 0.65rem;
  color: #64748b;
  line-height: 1.3;
}
.tune-hint b {
  color: #f59e0b;
}

.double-group {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.double-group label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font: 700 0.62rem ui-monospace, monospace;
  color: #94a3b8;
}

.save-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}
.save-station-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 10px;
  border-radius: 8px;
  background: linear-gradient(180deg, #059669, #047857);
  border: 1px solid #10b981;
  color: #ffffff;
  font: 800 0.78rem ui-monospace, monospace;
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: 0 4px 14px rgba(16, 185, 129, 0.25);
}
.save-station-btn:hover:not(:disabled) {
  background: linear-gradient(180deg, #10b981, #059669);
  box-shadow: 0 0 16px rgba(16, 185, 129, 0.4);
}
.save-station-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.status-msg {
  font-size: 0.7rem;
  text-align: center;
}
.status-msg.success {
  color: #34d399;
}
.status-msg.error {
  color: #f87171;
}

/* Stage 3 Card */
.stage3-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stage3-desc {
  margin: 0;
  font-size: 0.68rem;
  color: #94a3b8;
  line-height: 1.35;
}
.stage3-inputs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.stage3-inputs label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font: 700 0.62rem ui-monospace, monospace;
  color: #94a3b8;
}
.checkbox-row {
  margin: 2px 0;
}
.hybrid-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.65rem;
  color: #cbd5e1;
  cursor: pointer;
}
.sim-actions {
  display: flex;
  gap: 8px;
}
.sim-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border-radius: 8px;
  font: 800 0.74rem ui-monospace, monospace;
  cursor: pointer;
  transition: all 0.15s;
}
.sim-btn.start {
  background: linear-gradient(180deg, #7c3aed, #6d28d9);
  border: 1px solid #a78bfa;
  color: #ffffff;
  box-shadow: 0 4px 14px rgba(124, 58, 237, 0.3);
}
.sim-btn.start:hover {
  background: linear-gradient(180deg, #8b5cf6, #7c3aed);
}
.sim-btn.stop {
  background: rgba(239, 68, 68, 0.2);
  border: 1px solid #ef4444;
  color: #f87171;
}
.sim-live-meter {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}
.meter-bar {
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
  overflow: hidden;
}
.meter-fill {
  height: 100%;
  background: linear-gradient(90deg, #38bdf8, #a855f7);
  transition: width 0.2s linear;
}
.meter-labels {
  display: flex;
  justify-content: space-between;
  font: 700 0.6rem ui-monospace, monospace;
  color: #64748b;
}
</style>
