<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Radar,
  Crosshair,
  Play,
  Square,
  RotateCcw,
  FastForward,
  Save,
  Compass,
  X,
  Navigation,
  Video,
  Home,
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Lock,
  Unlock,
} from '@lucide/vue'
import { setPatrolAxisLock } from '../../api/operation'

const props = defineProps<{
  open: boolean
  stage: 'STAGE_1' | 'STAGE_2' | 'STAGE_3'
  streamUrl?: string
  frameUrl?: string
  patrolState?: string
  patrolEnabled?: boolean
  activePath?: number
  path1Deg?: number
  path2Deg?: number
  path3Deg?: number
  dwellTimeS?: number
  strategy?: string
  sweepSpeedDps?: number
  sweepSectorDeg?: number
  currentRound?: number
  currentPanDeg?: number
  dwellRemainingS?: number
  onPath?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  startMission: []
  stopMission: []
  saveAngles: [angles: {
    path1_deg: number
    path2_deg: number
    path3_deg: number
    dwell_time_s: number
    strategy?: string
    sweep_speed_dps?: number
    sweep_sector_deg?: number
  }]
  captureAngle: [path: number]
  gotoPath: [path: number]
  nextRound: []
  resetRounds: []
  triggerAutoHome: []
  jog: [axis: 'pan' | 'tilt', dir: number, step?: number]
}>()

const localPath1 = ref(120.0)
const localPath2 = ref(135.0)
const localPath3 = ref(150.0)
const localDwell = ref(1.0)
const localStrategy = ref<'CORRIDOR_HOP' | 'SMOOTH_SWEEP'>('CORRIDOR_HOP')
const localSweepSpeed = ref(7.5)
const localSweepSector = ref(30.0)
const tiltLocked = ref(true)
const saveSuccess = ref(false)

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      if (tiltLocked.value) {
        setPatrolAxisLock('tilt', true).catch(() => {})
      }
    } else {
      setPatrolAxisLock('tilt', false).catch(() => {})
    }
  },
  { immediate: true },
)

function toggleTiltLock(): void {
  tiltLocked.value = !tiltLocked.value
  setPatrolAxisLock('tilt', tiltLocked.value).catch(() => {})
}

watch(
  () => [props.path1Deg, props.path2Deg, props.path3Deg, props.dwellTimeS, props.strategy, props.sweepSpeedDps, props.sweepSectorDeg, props.open],
  () => {
    if (props.path1Deg !== undefined) localPath1.value = props.path1Deg
    if (props.path2Deg !== undefined) localPath2.value = props.path2Deg
    if (props.path3Deg !== undefined) localPath3.value = props.path3Deg
    if (props.dwellTimeS !== undefined) localDwell.value = props.dwellTimeS
    if (props.strategy !== undefined) localStrategy.value = (props.strategy as 'CORRIDOR_HOP' | 'SMOOTH_SWEEP') || 'CORRIDOR_HOP'
    if (props.sweepSpeedDps !== undefined) localSweepSpeed.value = props.sweepSpeedDps
    if (props.sweepSectorDeg !== undefined) localSweepSector.value = props.sweepSectorDeg
  },
  { immediate: true },
)

const isPatrolling = computed(() => Boolean(props.patrolEnabled))
const round = computed(() => props.currentRound || 1)
const turretPan = computed(() => {
  if (props.currentPanDeg === undefined || Number.isNaN(props.currentPanDeg)) return 135.0
  return props.currentPanDeg
})

const liveStreamUrl = computed(() => {
  if (props.streamUrl) {
    const aligned = props.streamUrl.replace('/stream.mjpg', '/stream-overlay.mjpg')
    return aligned
  }
  return '/api/vision/stream-overlay.mjpg'
})

const leftViewTab = ref<'both' | 'camera' | 'radar'>('both')

function handleSave(): void {
  emit('saveAngles', {
    path1_deg: Number(localPath1.value),
    path2_deg: Number(localPath2.value),
    path3_deg: Number(localPath3.value),
    dwell_time_s: Number(localDwell.value),
    strategy: localStrategy.value,
    sweep_speed_dps: Number(localSweepSpeed.value),
    sweep_sector_deg: Number(localSweepSector.value),
  })
  saveSuccess.value = true
  setTimeout(() => {
    saveSuccess.value = false
  }, 2000)
}

function handleSelectStrategy(strat: 'CORRIDOR_HOP' | 'SMOOTH_SWEEP'): void {
  localStrategy.value = strat
  handleSave()
}

function handleCapture(path: number): void {
  emit('captureAngle', path)
  if (path === 1) localPath1.value = Math.round(turretPan.value * 10) / 10
  if (path === 2) localPath2.value = Math.round(turretPan.value * 10) / 10
  if (path === 3) localPath3.value = Math.round(turretPan.value * 10) / 10
}

function polarToXy(angleDeg: number, radius: number, cx = 150, cy = 175): { x: number, y: number } {
  // 135 deg is straight forward
  const relRad = ((angleDeg - 135.0) * Math.PI) / 180
  return {
    x: cx + radius * Math.sin(relRad),
    y: cy - radius * Math.cos(relRad),
  }
}
</script>

<template>
  <div v-if="open" class="radar-modal-backdrop" @click.self="emit('close')">
    <div class="radar-modal-dialog">
      <!-- HEADER -->
      <header class="modal-header">
        <div class="header-title-group">
          <span class="header-icon"><Radar :size="22" /></span>
          <div>
            <h2>
              {{ stage === 'STAGE_2' ? 'AŞAMA 2: OTONOM BALON AVLAMA' : 'AŞAMA 3: BİRLİKTE TAKİP (3 KOL)' }}
            </h2>
            <p>
              Taret 3 kol arasında devriye taraması yapar. Canlı taret açısını kopyalayabilir veya elle derece girebilirsiniz.
            </p>
          </div>
        </div>
        <div class="header-actions">
          <button
            type="button"
            class="btn-header-home"
            title="Limit switchleri aratarak tareti 135° donanımsal merkeze sıfırla (AutoHome)"
            :disabled="busy"
            @click="emit('triggerAutoHome')"
          >
            <Home :size="14" /> {{ busy ? 'Sıfırlanıyor...' : 'AutoHome (Sıfırla)' }}
          </button>
          <button type="button" class="btn-close" title="Kapat" @click="emit('close')">
            <X :size="18" />
          </button>
        </div>
      </header>

      <!-- BODY GRID -->
      <div class="modal-body-grid">
        <!-- SOL KOLON: CANLI KAMERA + RADAR GÖRSELİ -->
        <div class="modal-left-column">
          <!-- GÖRÜNÜM SEÇİM ÇUBUĞU -->
          <div class="view-mode-tabs">
            <button
              type="button"
              class="tab-btn"
              :class="{ active: leftViewTab === 'both' }"
              @click="leftViewTab = 'both'"
            >
              <Compass :size="13" /> + <Video :size="13" /> Kamera & Radar
            </button>
            <button
              type="button"
              class="tab-btn"
              :class="{ active: leftViewTab === 'camera' }"
              @click="leftViewTab = 'camera'"
            >
              <Video :size="13" /> Sadece Kamera
            </button>
            <button
              type="button"
              class="tab-btn"
              :class="{ active: leftViewTab === 'radar' }"
              @click="leftViewTab = 'radar'"
            >
              <Compass :size="13" /> Sadece Radar
            </button>
          </div>

          <!-- 1. CANLI KAMERA KARTI -->
          <section
            v-if="leftViewTab === 'both' || leftViewTab === 'camera'"
            class="camera-preview-card"
            :class="{ 'expanded-cam': leftViewTab === 'camera' }"
          >
            <div class="card-header">
              <span class="card-title">
                <Video :size="15" /> Canlı Kamera Görünümü (Hedef Kol Doğrulama)
              </span>
              <span class="live-angle-badge">
                PAN: {{ turretPan.toFixed(1) }}°
              </span>
            </div>

            <div class="camera-stream-container">
              <img
                :src="liveStreamUrl"
                alt="Canlı Taret Kamerası"
                class="camera-stream-img"
              />
              <!-- Crosshair Nişangâh Overlay -->
              <div class="camera-reticle">
                <div class="reticle-line reticle-h"></div>
                <div class="reticle-line reticle-v"></div>
                <div class="reticle-circle"></div>
                <div class="reticle-dot"></div>
              </div>
              <div class="camera-hud-overlay">
                <span class="hud-pill">CANLI AKIŞ</span>
                <span class="hud-pan">PAN: {{ turretPan.toFixed(1) }}°</span>
              </div>
            </div>
          </section>

          <!-- 2. TAKTİKSEL RADAR KARTI -->
          <section
            v-if="leftViewTab === 'both' || leftViewTab === 'radar'"
            class="radar-visual-card"
            :class="{ 'expanded-radar': leftViewTab === 'radar' }"
          >
            <div class="card-header">
              <span class="card-title"><Compass :size="15" /> Taktiksel Devriye Radarı</span>
              <span
                class="status-pill"
                :class="{
                  'pill-green': isPatrolling && patrolState !== 'TARGET_ENGAGING',
                  'pill-amber': patrolState === 'TARGET_ENGAGING',
                  'pill-slate': !isPatrolling,
                }"
              >
                {{ !isPatrolling ? 'BEKLEMEDE (IDLE)' : (patrolState === 'TARGET_ENGAGING' ? '🎯 HEDEFE KİLİTLENDİ' : '📡 TARANIYOR') }}
              </span>
            </div>

            <div class="radar-display-area" :class="{ 'compact-radar-area': leftViewTab === 'both' }">
              <svg viewBox="0 0 300 200" class="radar-svg">
                <!-- Radar Sector Arc Field -->
                <path
                  d="M 150 175 L 50 25 A 155 155 0 0 1 250 25 Z"
                  class="radar-sector-cone"
                />
                <!-- Concentric Distance Arcs -->
                <circle cx="150" cy="175" r="50" class="radar-arc" />
                <circle cx="150" cy="175" r="100" class="radar-arc" />
                <circle cx="150" cy="175" r="150" class="radar-arc" />

                <!-- Kol Çizgileri -->
                <!-- 1. Kol (Sol) -->
                <line
                  :x1="150"
                  :y1="175"
                  :x2="polarToXy(localPath1, 150).x"
                  :y2="polarToXy(localPath1, 150).y"
                  class="path-line path-1-line"
                  :class="{ active: activePath === 1 }"
                />
                <!-- 2. Kol (Orta) -->
                <line
                  :x1="150"
                  :y1="175"
                  :x2="polarToXy(localPath2, 150).x"
                  :y2="polarToXy(localPath2, 150).y"
                  class="path-line path-2-line"
                  :class="{ active: activePath === 2 }"
                />
                <!-- 3. Kol (Sağ) -->
                <line
                  :x1="150"
                  :y1="175"
                  :x2="polarToXy(localPath3, 150).x"
                  :y2="polarToXy(localPath3, 150).y"
                  class="path-line path-3-line"
                  :class="{ active: activePath === 3 }"
                />

                <!-- Kol Etiketleri -->
                <text :x="polarToXy(localPath1, 162).x" :y="polarToXy(localPath1, 162).y" class="path-text" text-anchor="middle">
                  1. KOL ({{ localPath1 }}°)
                </text>
                <text :x="polarToXy(localPath2, 162).x" :y="polarToXy(localPath2, 162).y - 4" class="path-text" text-anchor="middle">
                  2. KOL ({{ localPath2 }}°)
                </text>
                <text :x="polarToXy(localPath3, 162).x" :y="polarToXy(localPath3, 162).y" class="path-text" text-anchor="middle">
                  3. KOL ({{ localPath3 }}°)
                </text>

                <!-- Canlı Taret Açısı İbresi -->
                <line
                  :x1="150"
                  :y1="175"
                  :x2="polarToXy(turretPan, 140).x"
                  :y2="polarToXy(turretPan, 140).y"
                  class="turret-needle"
                />
                <circle
                  :cx="polarToXy(turretPan, 140).x"
                  :cy="polarToXy(turretPan, 140).y"
                  r="4"
                  class="turret-needle-head"
                />

                <!-- Taret Merkezi -->
                <circle cx="150" cy="175" r="10" class="turret-base" />
                <circle cx="150" cy="175" r="4" class="turret-center-dot" />
              </svg>
            </div>

            <!-- Radar Alt Bilgi Satırı -->
            <div class="radar-telemetry-row">
              <div>
                <span class="metric-label">CANLI TARET AÇISI</span>
                <span class="metric-val highlight-cyan">{{ turretPan.toFixed(1) }}°</span>
              </div>
              <div>
                <span class="metric-label">AKTİF KOL</span>
                <span class="metric-val">{{ activePath ?? 2 }}. KOL</span>
              </div>
              <div>
                <span class="metric-label">DURUM</span>
                <span class="metric-val" :class="onPath ? 'highlight-green' : 'highlight-amber'">
                  {{ onPath ? 'KOLDA BEKLİYOR' : 'DÖNÜYOR' }}
                </span>
              </div>
            </div>

            <!-- Manuel Taret Çevir / Jog Kontrolleri -->
            <div class="radar-jog-toolbar">
              <span class="jog-toolbar-title">MANUEL ÇEVİR:</span>
              <div class="jog-btn-group">
                <button
                  type="button"
                  class="btn-jog btn-jog-coarse"
                  title="Tareti 2° Sola Çevir"
                  :disabled="busy"
                  @click="emit('jog', 'pan', -1, 2.0)"
                >
                  <ChevronLeft :size="13" /> 2° Sol
                </button>
                <button
                  type="button"
                  class="btn-jog"
                  title="Tareti 0.5° Sola İnce Ayarla"
                  :disabled="busy"
                  @click="emit('jog', 'pan', -1, 0.5)"
                >
                  <ArrowLeft :size="13" /> 0.5°
                </button>
                <button
                  type="button"
                  class="btn-jog"
                  title="Tareti 0.5° Sağa İnce Ayarla"
                  :disabled="busy"
                  @click="emit('jog', 'pan', 1, 0.5)"
                >
                  0.5° <ArrowRight :size="13" />
                </button>
                <button
                  type="button"
                  class="btn-jog btn-jog-coarse"
                  title="Tareti 2° Sağa Çevir"
                  :disabled="busy"
                  @click="emit('jog', 'pan', 1, 2.0)"
                >
                  2° Sağ <ChevronRight :size="13" />
                </button>
                <button
                  type="button"
                  class="btn-jog btn-tilt-lock"
                  :class="{ 'tilt-locked-active': tiltLocked }"
                  :title="tiltLocked ? 'Tilt (Y) Ekseni Kilitli - Tıklayarak Aç' : 'Tilt (Y) Ekseni Serbest - Tıklayarak Kilitle'"
                  @click="toggleTiltLock"
                >
                  <Lock v-if="tiltLocked" :size="12" />
                  <Unlock v-else :size="12" />
                  {{ tiltLocked ? 'Y KİLİTLİ' : 'Y SERBEST' }}
                </button>
              </div>
            </div>
          </section>
        </div>

        <!-- SAĞ: KOL AYARLARI VE HAREKET -->
        <section class="controls-card">
          <!-- Strateji Seçimi: 3-Kollu Tık-Tık vs Sürekli Süpürme -->
          <div class="strategy-selector-row">
            <span class="strategy-label">Radar Modu:</span>
            <div class="strategy-btn-group">
              <button
                type="button"
                class="btn-strategy"
                :class="{ active: localStrategy === 'CORRIDOR_HOP' }"
                @click="handleSelectStrategy('CORRIDOR_HOP')"
              >
                3 Kollu Tık-Tık
              </button>
              <button
                type="button"
                class="btn-strategy"
                :class="{ active: localStrategy === 'SMOOTH_SWEEP' }"
                @click="handleSelectStrategy('SMOOTH_SWEEP')"
              >
                Sürekli Süpürme (Yolsuz)
              </button>
            </div>
          </div>

          <!-- Seçenek A: 3 Kol Derece Kalibrasyonu -->
          <div v-if="localStrategy === 'CORRIDOR_HOP'" class="paths-control-list">
            <!-- 1. Kol -->
            <div class="path-row" :class="{ 'current-patrol': activePath === 1 }">
              <div class="path-meta">
                <span class="path-badge path-badge-1">1. KOL (SOL)</span>
                <span class="path-desc">Varsayılan ~120°</span>
              </div>
              <div class="path-input-wrap">
                <input v-model.number="localPath1" type="number" step="0.5" class="deg-input" />
                <span class="unit">°</span>
              </div>
              <button
                type="button"
                class="btn-sm btn-capture"
                title="Şu anki taret açısını bu kola kaydet"
                @click="handleCapture(1)"
              >
                <Crosshair :size="13" /> Açıyı Al
              </button>
              <button
                type="button"
                class="btn-sm btn-goto"
                title="Tareti bu kola döndür"
                @click="emit('gotoPath', 1)"
              >
                <Navigation :size="13" /> Git
              </button>
            </div>

            <!-- 2. Kol (Orta) -->
            <div class="path-row" :class="{ 'current-patrol': activePath === 2 }">
              <div class="path-meta">
                <span class="path-badge path-badge-2">2. KOL (ORTA)</span>
                <span class="path-desc">Varsayılan ~135°</span>
              </div>
              <div class="path-input-wrap">
                <input v-model.number="localPath2" type="number" step="0.5" class="deg-input" />
                <span class="unit">°</span>
              </div>
              <button
                type="button"
                class="btn-sm btn-capture"
                title="Şu anki taret açısını bu kola kaydet"
                @click="handleCapture(2)"
              >
                <Crosshair :size="13" /> Açıyı Al
              </button>
              <button
                type="button"
                class="btn-sm btn-goto"
                title="Tareti bu kola döndür"
                @click="emit('gotoPath', 2)"
              >
                <Navigation :size="13" /> Git
              </button>
            </div>

            <!-- 3. Kol (Sağ) -->
            <div class="path-row" :class="{ 'current-patrol': activePath === 3 }">
              <div class="path-meta">
                <span class="path-badge path-badge-3">3. KOL (SAĞ)</span>
                <span class="path-desc">Varsayılan ~150°</span>
              </div>
              <div class="path-input-wrap">
                <input v-model.number="localPath3" type="number" step="0.5" class="deg-input" />
                <span class="unit">°</span>
              </div>
              <button
                type="button"
                class="btn-sm btn-capture"
                title="Şu anki taret açısını bu kola kaydet"
                @click="handleCapture(3)"
              >
                <Crosshair :size="13" /> Açıyı Al
              </button>
              <button
                type="button"
                class="btn-sm btn-goto"
                title="Tareti bu kola döndür"
                @click="emit('gotoPath', 3)"
              >
                <Navigation :size="13" /> Git
              </button>
            </div>

            <!-- Dwell Time (Bekleme Süresi) -->
            <div class="dwell-row">
              <span class="dwell-label">Kollarda Bekleme Süresi (sn):</span>
              <div class="path-input-wrap">
                <input v-model.number="localDwell" type="number" step="0.2" min="0.2" class="deg-input" />
                <span class="unit">sn</span>
              </div>
              <button type="button" class="btn-sm btn-save" @click="handleSave">
                <Save :size="13" /> {{ saveSuccess ? 'Kaydedildi!' : 'Açıları Kaydet' }}
              </button>
            </div>
          </div>

          <!-- Seçenek B: Sürekli Yumuşak Süpürme (Yolsuz) Ayarları -->
          <div v-else class="sweep-control-list">
            <div class="sweep-param-row">
              <div class="param-meta">
                <span class="param-title">Süpürme Hızı</span>
                <span class="param-desc">Önerilen: 7.5°/sn (Bulanıklık &lt; 4.2px)</span>
              </div>
              <div class="path-input-wrap">
                <input v-model.number="localSweepSpeed" type="number" step="0.5" min="2" max="20" class="deg-input" />
                <span class="unit">°/sn</span>
              </div>
            </div>

            <div class="sweep-param-row">
              <div class="param-meta">
                <span class="param-title">Tarama Sektörü</span>
                <span class="param-desc">Merkez 135° ± (Sektör/2) Açısı</span>
              </div>
              <div class="path-input-wrap">
                <input v-model.number="localSweepSector" type="number" step="1" min="10" max="60" class="deg-input" />
                <span class="unit">°</span>
              </div>
            </div>

            <div class="sweep-info-box">
              <span>ℹ️ Taret 135° merkezli {{ (135 - localSweepSector / 2).toFixed(1) }}° – {{ (135 + localSweepSector / 2).toFixed(1) }}° aralığında yumuşak S-eğrisi dönüşlerle sürekli tarama yapar. Adım motorlarda sıfır sarsıntı ve sıfır bulanıklık.</span>
            </div>

            <div class="dwell-row">
              <span class="dwell-label">Süpürme Ayarlarını Onayla:</span>
              <button type="button" class="btn-sm btn-save" @click="handleSave">
                <Save :size="13" /> {{ saveSuccess ? 'Kaydedildi!' : 'Kaydet' }}
              </button>
            </div>
          </div>

          <!-- Aşama 3 İse: 8 Tur Yönetimi -->
          <div v-if="stage === 'STAGE_3'" class="stage3-rounds-box">
            <div class="round-bar-header">
              <span class="round-title">TUR {{ round }} / 8 (2 DOST · 1 DÜŞMAN)</span>
              <div class="round-actions">
                <button
                  type="button"
                  class="btn-xs btn-next"
                  :disabled="round >= 8"
                  @click="emit('nextRound')"
                >
                  <FastForward :size="12" /> Sonraki Tur
                </button>
                <button
                  type="button"
                  class="btn-xs btn-reset"
                  title="Turları Sıfırla"
                  @click="emit('resetRounds')"
                >
                  <RotateCcw :size="12" /> Sıfırla
                </button>
              </div>
            </div>
            <div class="round-indicators">
              <span
                v-for="r in 8"
                :key="r"
                class="round-pip"
                :class="{ active: r === round, done: r < round }"
              >
                {{ r }}
              </span>
            </div>
          </div>
        </section>
      </div>

      <!-- FOOTER / BÜYÜK GÖREV BAŞLAT VE DURDUR BUTONLARI -->
      <footer class="modal-footer">
        <div class="footer-left-status">
          <span class="mode-text">
            Mevcut Mod: <b>{{ isPatrolling ? 'OTONOM RADAR AKTİF' : 'MANUEL / BEKLEMEDE' }}</b>
          </span>
        </div>

        <div class="footer-btn-group">
          <button
            v-if="!isPatrolling"
            type="button"
            class="btn-action-hero btn-hero-start"
            :disabled="busy"
            @click="emit('startMission')"
          >
            <Play :size="18" /> GÖREVİ & RADAR TARAMASINI BAŞLAT
          </button>
          <button
            v-else
            type="button"
            class="btn-action-hero btn-hero-stop"
            :disabled="busy"
            @click="emit('stopMission')"
          >
            <Square :size="18" /> GÖREVİ DURDUR
          </button>

          <button type="button" class="btn-secondary" @click="emit('close')">
            Kokpite Dön
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.radar-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 16px;
  background: rgba(2, 6, 15, 0.84);
  backdrop-filter: blur(8px);
}

.radar-modal-dialog {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: min(860px, calc(100vw - 24px));
  max-height: min(720px, calc(100vh - 24px));
  background: linear-gradient(180deg, rgba(8, 22, 38, 0.98), rgba(4, 12, 22, 0.99));
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 16px;
  padding: 18px 22px;
  color: #e2e8f0;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.7), 0 0 30px rgba(14, 165, 233, 0.15);
  overflow-y: auto;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(148, 163, 184, 0.15);
  padding-bottom: 12px;
}

.header-title-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: rgba(14, 165, 233, 0.18);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
}

.header-title-group h2 {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: 0.5px;
  color: #f8fafc;
}

.header-title-group p {
  margin: 2px 0 0;
  font-size: 0.74rem;
  color: #94a3b8;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-header-home {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(14, 165, 233, 0.2);
  border: 1px solid rgba(56, 189, 248, 0.45);
  color: #38bdf8;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 0.76rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-header-home:hover:not(:disabled) {
  background: rgba(14, 165, 233, 0.35);
  border-color: #38bdf8;
  color: #f0f9ff;
  box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
}

.btn-header-home:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-close {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.2);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-close:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.4);
}

.radar-jog-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 12px;
  background: rgba(15, 23, 42, 0.75);
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  gap: 8px;
  margin-top: 4px;
}

.jog-toolbar-title {
  font-size: 0.7rem;
  font-weight: 800;
  color: #94a3b8;
  letter-spacing: 0.5px;
}

.jog-btn-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.btn-jog {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 700;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.25);
  color: #cbd5e1;
  cursor: pointer;
  transition: all 0.12s ease;
}

.btn-jog:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.25);
  border-color: #38bdf8;
  color: #38bdf8;
}

.btn-jog-coarse {
  background: rgba(15, 23, 42, 0.9);
  color: #94a3b8;
}

.btn-jog:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-tilt-lock {
  border-color: rgba(245, 158, 11, 0.4);
  color: #fbbf24;
}

.btn-tilt-lock.tilt-locked-active {
  background: rgba(245, 158, 11, 0.2);
  border-color: #f59e0b;
  color: #fbbf24;
  box-shadow: 0 0 8px rgba(245, 158, 11, 0.25);
}

.radar-modal-dialog {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: min(1040px, calc(100vw - 24px));
  max-height: min(840px, calc(100vh - 24px));
  background: linear-gradient(180deg, rgba(8, 22, 38, 0.98), rgba(4, 12, 22, 0.99));
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 16px;
  padding: 18px 22px;
  color: #e2e8f0;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.7), 0 0 30px rgba(14, 165, 233, 0.15);
  overflow-y: auto;
}

.modal-body-grid {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: 16px;
}

.modal-left-column {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.view-mode-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 2px;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 700;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(56, 189, 248, 0.2);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  background: rgba(14, 165, 233, 0.12);
  color: #e2e8f0;
}

.tab-btn.active {
  background: rgba(14, 165, 233, 0.22);
  border-color: #38bdf8;
  color: #38bdf8;
  box-shadow: 0 0 8px rgba(56, 189, 248, 0.25);
}

.camera-preview-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: rgba(2, 9, 18, 0.6);
  border: 1px solid rgba(56, 189, 248, 0.25);
  border-radius: 12px;
  padding: 10px;
}

.camera-preview-card.expanded-cam .camera-stream-container {
  height: 360px;
}

.camera-stream-container {
  position: relative;
  width: 100%;
  height: 200px;
  background: #020617;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid rgba(56, 189, 248, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
}

.camera-stream-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.camera-reticle {
  position: absolute;
  inset: 0;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.reticle-line {
  position: absolute;
  background: rgba(56, 189, 248, 0.45);
}
.reticle-h {
  width: 100%;
  height: 1px;
}
.reticle-v {
  width: 1px;
  height: 100%;
}
.reticle-circle {
  position: absolute;
  width: 44px;
  height: 44px;
  border: 1.5px solid rgba(56, 189, 248, 0.65);
  border-radius: 50%;
  box-shadow: 0 0 8px rgba(56, 189, 248, 0.35);
}
.reticle-dot {
  position: absolute;
  width: 4px;
  height: 4px;
  background: #ef4444;
  border-radius: 50%;
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.8);
}

.camera-hud-overlay {
  position: absolute;
  bottom: 8px;
  left: 8px;
  right: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  pointer-events: none;
}

.hud-pill {
  background: rgba(16, 185, 129, 0.25);
  color: #34d399;
  border: 1px solid rgba(52, 211, 153, 0.4);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.65rem;
  font-weight: 800;
  letter-spacing: 0.5px;
}

.hud-pan {
  background: rgba(15, 23, 42, 0.85);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.4);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 800;
}

.live-angle-badge {
  font-size: 0.74rem;
  font-weight: 800;
  color: #38bdf8;
  background: rgba(14, 165, 233, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.35);
  padding: 2px 8px;
  border-radius: 6px;
}

.radar-visual-card,
.controls-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: rgba(2, 9, 18, 0.6);
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 12px;
  padding: 12px;
}

.radar-visual-card.expanded-radar .radar-display-area {
  height: 320px;
}

.radar-display-area.compact-radar-area {
  height: 155px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  font-weight: 700;
  color: #93c5fd;
}

.status-pill {
  font-size: 0.66rem;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 99px;
  letter-spacing: 0.5px;
}
.pill-green { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.4); }
.pill-amber { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.4); }
.pill-slate { background: rgba(100, 116, 139, 0.2); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.25); }

.radar-display-area {
  position: relative;
  width: 100%;
  height: 200px;
  background: radial-gradient(circle at 50% 88%, rgba(14, 165, 233, 0.16) 0%, rgba(2, 6, 23, 0.95) 75%);
  border-radius: 10px;
  border: 1px solid rgba(34, 211, 238, 0.2);
  overflow: hidden;
}

.radar-svg {
  width: 100%;
  height: 100%;
}

.radar-sector-cone {
  fill: rgba(14, 165, 233, 0.08);
  stroke: rgba(56, 189, 248, 0.3);
  stroke-width: 1;
}

.radar-arc {
  fill: none;
  stroke: rgba(56, 189, 248, 0.16);
  stroke-dasharray: 4 4;
}

.path-line {
  stroke-width: 2;
  transition: all 0.2s ease;
}
.path-1-line { stroke: rgba(96, 165, 250, 0.4); }
.path-2-line { stroke: rgba(52, 211, 153, 0.4); }
.path-3-line { stroke: rgba(251, 191, 36, 0.4); }

.path-line.active {
  stroke: #38bdf8;
  stroke-width: 3.5;
  filter: drop-shadow(0 0 6px rgba(56, 189, 248, 0.8));
}

.path-text {
  font-size: 8px;
  font-weight: 700;
  fill: #94a3b8;
}

.turret-needle {
  stroke: #ef4444;
  stroke-width: 2.5;
  filter: drop-shadow(0 0 4px rgba(239, 68, 68, 0.8));
}
.turret-needle-head {
  fill: #ef4444;
  stroke: #ffffff;
  stroke-width: 1.5;
}

.turret-base {
  fill: #0f172a;
  stroke: #38bdf8;
  stroke-width: 2;
}
.turret-center-dot {
  fill: #38bdf8;
}

.radar-telemetry-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  background: rgba(15, 23, 42, 0.7);
  padding: 8px;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.12);
  text-align: center;
}

.metric-label {
  display: block;
  font-size: 0.62rem;
  font-weight: 700;
  color: #64748b;
  letter-spacing: 0.3px;
}

.metric-val {
  font-size: 0.85rem;
  font-weight: 800;
  color: #e2e8f0;
  font-family: monospace;
}
.highlight-cyan { color: #38bdf8; }
.highlight-green { color: #34d399; }
.highlight-amber { color: #fbbf24; }

/* STRATEGY SELECTOR */
.strategy-selector-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.9);
  border: 1px solid rgba(56, 189, 248, 0.25);
  border-radius: 8px;
  margin-bottom: 2px;
}

.strategy-label {
  font-size: 0.72rem;
  font-weight: 800;
  color: #94a3b8;
  letter-spacing: 0.4px;
}

.strategy-btn-group {
  display: flex;
  gap: 6px;
}

.btn-strategy {
  padding: 5px 10px;
  font-size: 0.72rem;
  font-weight: 700;
  border-radius: 6px;
  background: rgba(30, 41, 59, 0.7);
  border: 1px solid rgba(100, 116, 139, 0.4);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-strategy:hover {
  background: rgba(51, 65, 85, 0.8);
  color: #e2e8f0;
}

.btn-strategy.active {
  background: rgba(14, 165, 233, 0.25);
  border-color: #38bdf8;
  color: #38bdf8;
  box-shadow: 0 0 10px rgba(56, 189, 248, 0.25);
}

/* SWEEP CONTROLS */
.sweep-control-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sweep-param-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.75);
  border: 1px solid rgba(51, 65, 85, 0.8);
  border-radius: 8px;
}

.param-meta {
  display: flex;
  flex-direction: column;
}

.param-title {
  font-size: 0.75rem;
  font-weight: 800;
  color: #38bdf8;
}

.param-desc {
  font-size: 0.62rem;
  color: #64748b;
}

.sweep-info-box {
  padding: 8px 10px;
  background: rgba(14, 165, 233, 0.08);
  border: 1px dashed rgba(56, 189, 248, 0.3);
  border-radius: 6px;
  font-size: 0.68rem;
  color: #94a3b8;
  line-height: 1.4;
}

.paths-control-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.path-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.75);
  border: 1px solid rgba(51, 65, 85, 0.8);
  border-radius: 8px;
  transition: all 0.15s ease;
}

.path-row.current-patrol {
  border-color: rgba(56, 189, 248, 0.7);
  background: rgba(14, 165, 233, 0.15);
  box-shadow: 0 0 10px rgba(14, 165, 233, 0.2);
}

.path-meta {
  display: flex;
  flex-direction: column;
  min-width: 105px;
}

.path-badge {
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.3px;
}
.path-badge-1 { color: #60a5fa; }
.path-badge-2 { color: #34d399; }
.path-badge-3 { color: #fbbf24; }

.path-desc {
  font-size: 0.6rem;
  color: #64748b;
}

.path-input-wrap {
  display: flex;
  align-items: center;
  gap: 2px;
}

.deg-input {
  width: 60px;
  padding: 4px 6px;
  background: #020617;
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 5px;
  color: #38bdf8;
  font-family: monospace;
  font-size: 0.82rem;
  font-weight: 700;
  text-align: right;
}

.unit {
  font-size: 0.75rem;
  font-weight: 700;
  color: #64748b;
}

.btn-sm {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  font-size: 0.68rem;
  font-weight: 700;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-capture {
  background: rgba(56, 189, 248, 0.16);
  color: #38bdf8;
  border-color: rgba(56, 189, 248, 0.4);
}
.btn-capture:hover {
  background: rgba(56, 189, 248, 0.3);
  color: #e0f2fe;
}

.btn-goto {
  background: rgba(100, 116, 139, 0.2);
  color: #cbd5e1;
  border-color: rgba(100, 116, 139, 0.35);
}
.btn-goto:hover {
  background: rgba(148, 163, 184, 0.35);
  color: #ffffff;
}

.dwell-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: rgba(10, 18, 30, 0.85);
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 8px;
}

.dwell-label {
  font-size: 0.72rem;
  font-weight: 700;
  color: #94a3b8;
}

.btn-save {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.4);
}
.btn-save:hover {
  background: rgba(16, 185, 129, 0.35);
}

.stage3-rounds-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: rgba(15, 23, 42, 0.9);
  padding: 10px;
  border-radius: 8px;
  border: 1px solid rgba(245, 158, 11, 0.3);
}

.round-bar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.round-title {
  font-size: 0.72rem;
  font-weight: 800;
  color: #fbbf24;
  letter-spacing: 0.4px;
}

.round-actions {
  display: flex;
  gap: 6px;
}

.btn-xs {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 6px;
  font-size: 0.65rem;
  font-weight: 700;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid transparent;
}
.btn-next { background: rgba(59, 130, 246, 0.25); color: #60a5fa; border-color: rgba(96, 165, 250, 0.4); }
.btn-reset { background: rgba(100, 116, 139, 0.25); color: #94a3b8; border-color: rgba(148, 163, 184, 0.3); }

.round-indicators {
  display: flex;
  gap: 5px;
}

.round-pip {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  height: 22px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 800;
  background: rgba(30, 41, 59, 0.8);
  color: #64748b;
  border: 1px solid rgba(51, 65, 85, 0.6);
}
.round-pip.active {
  background: #f59e0b;
  color: #0f172a;
  border-color: #fbbf24;
  box-shadow: 0 0 8px rgba(245, 158, 11, 0.6);
}
.round-pip.done {
  background: rgba(16, 185, 129, 0.25);
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.45);
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
  padding-top: 14px;
  margin-top: 4px;
}

.mode-text {
  font-size: 0.75rem;
  color: #94a3b8;
}
.mode-text b {
  color: #e2e8f0;
}

.footer-btn-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-action-hero {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 0.85rem;
  font-weight: 800;
  letter-spacing: 0.4px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s ease;
}

.btn-hero-start {
  background: linear-gradient(180deg, #059669, #047857);
  color: #ffffff;
  border-color: #10b981;
  box-shadow: 0 4px 16px rgba(16, 185, 129, 0.4);
}
.btn-hero-start:hover:not(:disabled) {
  background: #10b981;
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(16, 185, 129, 0.6);
}

.btn-hero-stop {
  background: linear-gradient(180deg, #dc2626, #b91c1c);
  color: #ffffff;
  border-color: #ef4444;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.4);
}
.btn-hero-stop:hover:not(:disabled) {
  background: #ef4444;
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(239, 68, 68, 0.6);
}

.btn-secondary {
  padding: 10px 16px;
  border-radius: 8px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(100, 116, 139, 0.4);
  color: #cbd5e1;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-secondary:hover {
  background: rgba(51, 65, 85, 0.9);
  color: #ffffff;
}

@media (max-width: 768px) {
  .modal-body-grid {
    grid-template-columns: 1fr;
  }
}
</style>
