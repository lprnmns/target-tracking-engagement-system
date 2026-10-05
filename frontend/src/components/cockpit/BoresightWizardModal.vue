<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  Crosshair,
  Lock,
  RotateCcw,
  Save,
  Sparkles,
  Unlock,
  X,
  Zap,
  ZoomIn,
  ZoomOut,
} from '@lucide/vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    streamUrl: string
    frameUrl: string
    width?: number
    height?: number
    initialOffsetX?: number
    initialOffsetY?: number
    magazineRemaining?: number
    magazineCapacity?: number
  }>(),
  {
    width: 1280,
    height: 720,
    initialOffsetX: 0,
    initialOffsetY: 0,
    magazineRemaining: 8,
    magazineCapacity: 8,
  },
)

const emit = defineEmits<{
  close: []
  fire: []
  jog: [axis: 'pan' | 'tilt', direction: 1 | -1, speed: number]
  saved: [offset: { x: number, y: number, distance: number, dropCm: number }]
}>()

// Wizard state & steps:
// 1 = Aiming & Pre-Fire
// 2 = Fired & Waiting for Impact Click (Zoomed)
// 3 = Impact Marked & Fine-Tuning
const currentStep = ref<1 | 2 | 3>(1)
const hasFired = ref(false)
const selectedDistance = ref<number>(15)
const customDistance = ref<number>(15)
const isCustomDistance = ref(false)

// Active aim offsets in pixels (relative to optical center 640, 360)
const aimOffsetX = ref<number>(props.initialOffsetX ?? 0)
const aimOffsetY = ref<number>(props.initialOffsetY ?? 0)
const lockHorizontal = ref(true)

// Fired aim point (exact reticle position at the moment of firing)
const firedAimPoint = ref<{ x: number, y: number } | null>(null)

// Bullet impact point marked by user click
const impactPoint = ref<{ x: number, y: number } | null>(null)

// Zoom state:
const zoomActive = ref(false)
const zoomScale = ref<number>(2.5) // default 2.5x on auto-zoom
const zoomCenter = ref<{ x: number, y: number }>({ x: 640, y: 360 })

// Flash / recoil visual effect
const fireFlashActive = ref(false)

// Saving state:
const isSaving = ref(false)
const saveSuccess = ref(false)
const errorMessage = ref<string | null>(null)

// DOM ref to SVG overlay for accurate coordinate transforms
const svgRef = ref<SVGSVGElement | null>(null)

// Optical center in 720p coordinates
const opticalCenterX = computed(() => props.width / 2) // 640
const opticalCenterY = computed(() => props.height / 2) // 360

// Effective distance
const effectiveDistance = computed(() => (isCustomDistance.value ? customDistance.value : selectedDistance.value))

// Sensor scale: at distance d (m), 14cm target is w = 280 / d px -> px_per_cm = 20 / d
const pxPerCm = computed(() => {
  const d = Math.max(1.0, effectiveDistance.value)
  return 20.0 / d
})

const mmPerPx = computed(() => 10.0 / pxPerCm.value)

// Current crosshair coordinates
const crosshairX = computed(() => opticalCenterX.value + (lockHorizontal.value ? 0 : aimOffsetX.value))
const crosshairY = computed(() => opticalCenterY.value + aimOffsetY.value)

// Total drop & windage in cm
const dropCm = computed(() => parseFloat((aimOffsetY.value / pxPerCm.value).toFixed(1)))
const windageCm = computed(() => parseFloat((aimOffsetX.value / pxPerCm.value).toFixed(1)))

// Differences relative to INITIAL calibration (when wizard opened)
const diffXFromInitial = computed(() => Math.round(aimOffsetX.value - (props.initialOffsetX ?? 0)))
const diffYFromInitial = computed(() => Math.round(aimOffsetY.value - (props.initialOffsetY ?? 0)))
const diffXCm = computed(() => parseFloat((diffXFromInitial.value / pxPerCm.value).toFixed(1)))
const diffYCm = computed(() => parseFloat((diffYFromInitial.value / pxPerCm.value).toFixed(1)))

// Shot drift (difference between where we fired and where the bullet landed)
const shotDriftX = computed(() => {
  if (!impactPoint.value || !firedAimPoint.value) return 0
  return Math.round(impactPoint.value.x - firedAimPoint.value.x)
})
const shotDriftY = computed(() => {
  if (!impactPoint.value || !firedAimPoint.value) return 0
  return Math.round(impactPoint.value.y - firedAimPoint.value.y)
})
const shotDriftXCm = computed(() => parseFloat((shotDriftX.value / pxPerCm.value).toFixed(1)))
const shotDriftYCm = computed(() => parseFloat((shotDriftY.value / pxPerCm.value).toFixed(1)))

// Watch open state to reset or initialize
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      aimOffsetX.value = props.initialOffsetX ?? 0
      aimOffsetY.value = props.initialOffsetY ?? 0
      if (lockHorizontal.value) {
        aimOffsetX.value = 0
      }
      currentStep.value = 1
      hasFired.value = false
      firedAimPoint.value = null
      impactPoint.value = null
      zoomActive.value = false
      zoomScale.value = 2.5
      zoomCenter.value = { x: opticalCenterX.value, y: opticalCenterY.value }
      saveSuccess.value = false
      errorMessage.value = null
    }
  },
)

watch(lockHorizontal, (locked) => {
  if (locked) {
    aimOffsetX.value = 0
  }
})

// Zoom surface transform style
const zoomSurfaceStyle = computed(() => {
  if (!zoomActive.value || zoomScale.value <= 1.0) {
    return {
      transform: 'scale(1)',
      transformOrigin: 'center center',
      transition: 'transform 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
    }
  }
  const originX = (zoomCenter.value.x / props.width) * 100
  const originY = (zoomCenter.value.y / props.height) * 100
  return {
    transform: `scale(${zoomScale.value})`,
    transformOrigin: `${originX.toFixed(2)}% ${originY.toFixed(2)}%`,
    transition: 'transform 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
  }
})

function selectPresetDistance(dist: number) {
  selectedDistance.value = dist
  isCustomDistance.value = false
}

function enableCustomDistance() {
  isCustomDistance.value = true
}

// User triggers fire:
function handleFire() {
  if (props.magazineRemaining <= 0) return

  // 1. Record the exact aim point at the moment of firing
  firedAimPoint.value = {
    x: crosshairX.value,
    y: crosshairY.value,
  }
  hasFired.value = true
  currentStep.value = 2

  // 2. Visual recoil flash
  fireFlashActive.value = true
  setTimeout(() => {
    fireFlashActive.value = false
  }, 180)

  // 3. Emit physical fire command to hardware
  emit('fire')

  // 4. Automatically switch to Zoom mode centered around the fired reticle!
  zoomCenter.value = {
    x: firedAimPoint.value.x,
    y: firedAimPoint.value.y,
  }
  zoomActive.value = true
  zoomScale.value = 2.5
}

// User clicks anywhere on the camera stage
function handleStageClick(event: MouseEvent) {
  // Only accept click for marking impact if we have fired or are in step 2/3
  if (!hasFired.value && currentStep.value === 1) return
  if (!svgRef.value) return

  const pt = svgRef.value.createSVGPoint()
  pt.x = event.clientX
  pt.y = event.clientY
  const ctm = svgRef.value.getScreenCTM()
  if (!ctm) return

  // Invert transformation matrix to obtain pure 720p image coordinates
  const svgP = pt.matrixTransform(ctm.inverse())
  const clickX = Math.round(Math.max(0, Math.min(props.width, svgP.x)))
  const clickY = Math.round(Math.max(0, Math.min(props.height, svgP.y)))

  impactPoint.value = { x: clickX, y: clickY }
  currentStep.value = 3

  // Automatically adjust aim offset so crosshair matches the bullet hole!
  // aim_offset_y = clickY - opticalCenterY
  aimOffsetY.value = Math.round(clickY - opticalCenterY.value)
  if (!lockHorizontal.value) {
    aimOffsetX.value = Math.round(clickX - opticalCenterX.value)
  }
}

// Toggle or cycle zoom
function toggleZoom() {
  zoomActive.value = !zoomActive.value
  if (zoomActive.value) {
    zoomCenter.value = firedAimPoint.value ?? { x: crosshairX.value, y: crosshairY.value }
  }
}

function setZoomScale(scale: number) {
  zoomScale.value = scale
  zoomActive.value = scale > 1.0
  if (zoomActive.value) {
    zoomCenter.value = firedAimPoint.value ?? { x: crosshairX.value, y: crosshairY.value }
  }
}

function adjustOffsetY(delta: number) {
  aimOffsetY.value = Math.round(aimOffsetY.value + delta)
}

function adjustOffsetX(delta: number) {
  if (lockHorizontal.value) return
  aimOffsetX.value = Math.round(aimOffsetX.value + delta)
}

function resetToCenter() {
  aimOffsetX.value = 0
  aimOffsetY.value = 0
  impactPoint.value = null
  firedAimPoint.value = null
  hasFired.value = false
  currentStep.value = 1
  zoomActive.value = false
}

// Retest: keep current calibration but clear shot markers to fire another round
function handleRetest() {
  firedAimPoint.value = null
  impactPoint.value = null
  hasFired.value = false
  currentStep.value = 1
  zoomActive.value = false
}

// Key listener for shortcuts
function handleKeyDown(e: KeyboardEvent) {
  if (!props.open) return
  if (e.key === 'Escape') {
    emit('close')
  } else if (e.key === ' ' && !hasFired.value && currentStep.value === 1) {
    e.preventDefault()
    handleFire()
  } else if (e.key === 'z' || e.key === 'Z') {
    toggleZoom()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    emit('jog', 'tilt', 1, 1500)
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    emit('jog', 'tilt', -1, 1500)
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    emit('jog', 'pan', -1, 1500)
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    emit('jog', 'pan', 1, 1500)
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown)
})

async function saveCalibration() {
  isSaving.value = true
  errorMessage.value = null
  try {
    const finalX = lockHorizontal.value ? 0 : aimOffsetX.value
    const finalY = aimOffsetY.value

    const response = await fetch('/api/calibration/zeroing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        aim_offset_x_px: finalX,
        aim_offset_y_px: finalY,
        distance_m: effectiveDistance.value,
        persist: true,
      }),
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({}))
      throw new Error(err.detail || 'Kalibrasyon kaydedilemedi')
    }

    saveSuccess.value = true
    emit('saved', {
      x: finalX,
      y: finalY,
      distance: effectiveDistance.value,
      dropCm: dropCm.value,
    })

    setTimeout(() => {
      emit('close')
    }, 900)
  } catch (err: any) {
    errorMessage.value = err.message || 'Sıfırlama kaydedilemedi'
  } finally {
    isSaving.value = false
  }
}
</script>

<template>
  <div v-if="props.open" class="boresight-fullscreen-container" role="dialog" aria-modal="true">
    <!-- Top HUD Navigation & Status Bar -->
    <header class="hud-topbar">
      <div class="topbar-left">
        <div class="brand-badge">
          <Crosshair :size="16" class="text-cyan-400 animate-pulse" />
          <span class="brand-title">HASSAS SIFIRLAMA & KALİBRASYON LABORATUVARI</span>
          <span class="hud-tag">720p BORESIGHT LAB</span>
        </div>
        <div class="distance-pill-group">
          <span class="distance-label">MESAFE:</span>
          <button
            type="button"
            class="dist-pill"
            :class="{ active: selectedDistance === 15 && !isCustomDistance }"
            @click="selectPresetDistance(15)"
          >
            15 m
          </button>
          <button
            type="button"
            class="dist-pill"
            :class="{ active: selectedDistance === 10 && !isCustomDistance }"
            @click="selectPresetDistance(10)"
          >
            10 m
          </button>
          <button
            type="button"
            class="dist-pill"
            :class="{ active: selectedDistance === 5 && !isCustomDistance }"
            @click="selectPresetDistance(5)"
          >
            5 m
          </button>
          <button
            type="button"
            class="dist-pill"
            :class="{ active: isCustomDistance }"
            @click="enableCustomDistance"
          >
            Özel
          </button>
          <input
            v-if="isCustomDistance"
            v-model.number="customDistance"
            type="number"
            min="1"
            max="40"
            step="0.5"
            class="custom-dist-input"
          />
        </div>
      </div>

      <!-- Center Guided Step Flow Indicator -->
      <div class="topbar-center">
        <div class="step-flow">
          <div class="flow-item" :class="{ current: currentStep === 1, done: currentStep > 1 }">
            <span class="flow-num">1</span>
            <span>Nişan Al & Ateş Et</span>
          </div>
          <div class="flow-sep">▸</div>
          <div class="flow-item" :class="{ current: currentStep === 2, done: currentStep > 2 }">
            <span class="flow-num">2</span>
            <span>Vuruş Noktasına Tıkla</span>
          </div>
          <div class="flow-sep">▸</div>
          <div class="flow-item" :class="{ current: currentStep === 3 }">
            <span class="flow-num">3</span>
            <span>Onayla & Kaydet</span>
          </div>
        </div>
      </div>

      <!-- Right Controls & Zoom Toolbar -->
      <div class="topbar-right">
        <!-- Interactive Zoom Toolbar (User requested "yine zoom açma olsun kokpitteki gibi") -->
        <div class="zoom-toolbar">
          <button
            type="button"
            class="zoom-btn"
            :class="{ active: zoomActive }"
            title="Dürbün Zoom Aç/Kapat (Z tuşu)"
            @click="toggleZoom"
          >
            <ZoomIn v-if="!zoomActive" :size="15" />
            <ZoomOut v-else :size="15" />
            <span>{{ zoomActive ? `${zoomScale}X DÜRBÜN` : 'ZOOM AÇ' }}</span>
          </button>
          <div class="zoom-presets">
            <button
              type="button"
              class="zoom-chip"
              :class="{ active: !zoomActive || zoomScale === 1.0 }"
              @click="setZoomScale(1.0)"
            >
              1X
            </button>
            <button
              type="button"
              class="zoom-chip"
              :class="{ active: zoomActive && zoomScale === 2.5 }"
              @click="setZoomScale(2.5)"
            >
              2.5X
            </button>
            <button
              type="button"
              class="zoom-chip"
              :class="{ active: zoomActive && zoomScale === 4.0 }"
              @click="setZoomScale(4.0)"
            >
              4.0X
            </button>
          </div>
        </div>

        <button
          type="button"
          class="hud-close-btn"
          title="Laboratuvardan Çık (ESC)"
          @click="emit('close')"
        >
          <X :size="18" />
        </button>
      </div>
    </header>

    <!-- Main Fullscreen Body -->
    <main class="lab-main-area">
      <!-- Fullscreen Camera Stage -->
      <section class="camera-viewport-stage">
        <!-- Recoil Flash Effect -->
        <div v-if="fireFlashActive" class="fire-flash-overlay"></div>

        <!-- Camera Surface (Scaled when zoomActive is true) -->
        <div class="camera-zoom-surface" :style="zoomSurfaceStyle">
          <img :src="props.streamUrl" alt="Canlı Kalibrasyon Kamerası" class="camera-stream-image" />

          <!-- High-Precision SVG Reticle Layer -->
          <svg
            ref="svgRef"
            class="camera-svg-overlay"
            :viewBox="`0 0 ${props.width} ${props.height}`"
            :class="{ 'cursor-crosshair': hasFired || currentStep > 1 }"
            @click="handleStageClick"
          >
            <!-- Optical Center Mark (Faint Reference 640, 360) -->
            <g class="optical-center-reference" opacity="0.35">
              <line :x1="opticalCenterX - 20" :y1="opticalCenterY" :x2="opticalCenterX + 20" :y2="opticalCenterY" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3" />
              <line :x1="opticalCenterX" :y1="opticalCenterY - 20" :x2="opticalCenterX" :y2="opticalCenterY + 20" stroke="#94a3b8" stroke-width="1" stroke-dasharray="3 3" />
              <circle :cx="opticalCenterX" :cy="opticalCenterY" r="4" fill="none" stroke="#94a3b8" stroke-width="1" />
              <text :x="opticalCenterX + 8" :y="opticalCenterY - 8" fill="#94a3b8" font-size="10" font-family="monospace">
                OPTİK MERKEZ (640, 360)
              </text>
            </g>

            <!-- Vector Line Connecting Optical Center to Reticle -->
            <line
              v-if="aimOffsetY !== 0 || (!lockHorizontal && aimOffsetX !== 0)"
              :x1="opticalCenterX"
              :y1="opticalCenterY"
              :x2="crosshairX"
              :y2="crosshairY"
              stroke="#0284c7"
              stroke-width="1.2"
              stroke-dasharray="3 3"
              opacity="0.5"
            />

            <!-- LIVE AIM RETICLE (Cyan / Tactical NATO HUD) -->
            <g class="live-aim-reticle">
              <!-- Center Pip -->
              <circle :cx="crosshairX" :cy="crosshairY" r="2.5" fill="#38bdf8" />

              <!-- Crosshair Bars -->
              <line :x1="crosshairX - 32" :y1="crosshairY" :x2="crosshairX - 7" :y2="crosshairY" stroke="#38bdf8" stroke-width="2" />
              <line :x1="crosshairX + 7" :y1="crosshairY" :x2="crosshairX + 32" :y2="crosshairY" stroke="#38bdf8" stroke-width="2" />
              <line :x1="crosshairX" :y1="crosshairY - 32" :x2="crosshairX" :y2="crosshairY - 7" stroke="#38bdf8" stroke-width="2" />
              <line :x1="crosshairX" :y1="crosshairY + 7" :x2="crosshairX" :y2="crosshairY + 32" stroke="#38bdf8" stroke-width="2" />

              <!-- Outer Stadiametric Circle -->
              <circle :cx="crosshairX" :cy="crosshairY" r="22" fill="none" stroke="#38bdf8" stroke-width="1.2" opacity="0.75" />

              <!-- Pitch Graduation Ticks -->
              <line :x1="crosshairX - 6" :y1="crosshairY + 12" :x2="crosshairX + 6" :y2="crosshairY + 12" stroke="#38bdf8" stroke-width="1.2" />
              <line :x1="crosshairX - 10" :y1="crosshairY + 20" :x2="crosshairX + 10" :y2="crosshairY + 20" stroke="#38bdf8" stroke-width="1.5" />

              <!-- Reticle Coordinates Tag -->
              <rect :x="crosshairX + 14" :y="crosshairY + 10" width="128" height="22" rx="4" fill="rgba(2,12,23,0.85)" stroke="#38bdf8" stroke-width="1" />
              <text :x="crosshairX + 20" :y="crosshairY + 25" fill="#e0f2fe" font-size="11" font-weight="700" font-family="monospace">
                NİŞAN: {{ aimOffsetY > 0 ? `+${aimOffsetY}` : aimOffsetY }}px
              </text>
            </g>

            <!-- 1. FIRED RETICLE: USER REQUESTED "ateşe basınca orası kırmızı + olsun" -->
            <g v-if="firedAimPoint" class="fired-aim-marker">
              <!-- Pulsing Red Circle -->
              <circle :cx="firedAimPoint.x" :cy="firedAimPoint.y" r="22" fill="none" stroke="#ef4444" stroke-width="2" opacity="0.9" />
              <circle :cx="firedAimPoint.x" :cy="firedAimPoint.y" r="6" fill="#ef4444" opacity="0.4" />

              <!-- Prominent Bold Red Cross (+) -->
              <line :x1="firedAimPoint.x - 26" :y1="firedAimPoint.y" :x2="firedAimPoint.x + 26" :y2="firedAimPoint.y" stroke="#ef4444" stroke-width="3.5" stroke-linecap="round" />
              <line :x1="firedAimPoint.x" :y1="firedAimPoint.y - 26" :x2="firedAimPoint.x" :y2="firedAimPoint.y + 26" stroke="#ef4444" stroke-width="3.5" stroke-linecap="round" />

              <!-- Tactical Corner Brackets -->
              <path :d="`M ${firedAimPoint.x - 30} ${firedAimPoint.y - 14} L ${firedAimPoint.x - 30} ${firedAimPoint.y - 30} L ${firedAimPoint.x - 14} ${firedAimPoint.y - 30}`" fill="none" stroke="#ef4444" stroke-width="2" />
              <path :d="`M ${firedAimPoint.x + 30} ${firedAimPoint.y - 14} L ${firedAimPoint.x + 30} ${firedAimPoint.y - 30} L ${firedAimPoint.x + 14} ${firedAimPoint.y - 30}`" fill="none" stroke="#ef4444" stroke-width="2" />
              <path :d="`M ${firedAimPoint.x - 30} ${firedAimPoint.y + 14} L ${firedAimPoint.x - 30} ${firedAimPoint.y + 30} L ${firedAimPoint.x - 14} ${firedAimPoint.y + 30}`" fill="none" stroke="#ef4444" stroke-width="2" />
              <path :d="`M ${firedAimPoint.x + 30} ${firedAimPoint.y + 14} L ${firedAimPoint.x + 30} ${firedAimPoint.y + 30} L ${firedAimPoint.x + 14} ${firedAimPoint.y + 30}`" fill="none" stroke="#ef4444" stroke-width="2" />

              <!-- Red Badge Label -->
              <rect :x="firedAimPoint.x + 34" :y="firedAimPoint.y - 28" width="180" height="24" rx="4" fill="rgba(15, 23, 42, 0.95)" stroke="#ef4444" stroke-width="1.2" />
              <text :x="firedAimPoint.x + 42" :y="firedAimPoint.y - 12" fill="#fca5a5" font-size="11" font-weight="800" font-family="monospace">
                🔴 ATEŞ NOKTASI ({{ Math.round(firedAimPoint.x) }}, {{ Math.round(firedAimPoint.y) }})
              </text>
            </g>

            <!-- 2. IMPACT HOLE MARKER: USER REQUESTED "bir yere tıkla, tıklayayım" -->
            <g v-if="impactPoint" class="impact-hole-marker">
              <!-- Amber/Cyan Bullseye -->
              <circle :cx="impactPoint.x" :cy="impactPoint.y" r="24" fill="none" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="5 3" />
              <circle :cx="impactPoint.x" :cy="impactPoint.y" r="14" fill="none" stroke="#f59e0b" stroke-width="2.5" />
              <circle :cx="impactPoint.x" :cy="impactPoint.y" r="4.5" fill="#fde047" />

              <line :x1="impactPoint.x - 20" :y1="impactPoint.y" :x2="impactPoint.x - 8" :y2="impactPoint.y" stroke="#f59e0b" stroke-width="2" />
              <line :x1="impactPoint.x + 8" :y1="impactPoint.y" :x2="impactPoint.x + 20" :y2="impactPoint.y" stroke="#f59e0b" stroke-width="2" />
              <line :x1="impactPoint.x" :y1="impactPoint.y - 20" :x2="impactPoint.x" :y2="impactPoint.y - 8" stroke="#f59e0b" stroke-width="2" />
              <line :x1="impactPoint.x" :y1="impactPoint.y + 8" :x2="impactPoint.x" :y2="impactPoint.y + 20" stroke="#f59e0b" stroke-width="2" />

              <!-- Impact Coordinates Badge -->
              <rect :x="impactPoint.x + 26" :y="impactPoint.y + 12" width="180" height="24" rx="4" fill="rgba(15, 23, 42, 0.95)" stroke="#f59e0b" stroke-width="1.2" />
              <text :x="impactPoint.x + 34" :y="impactPoint.y + 28" fill="#fef08a" font-size="11" font-weight="800" font-family="monospace">
                🎯 VURUŞ DELİĞİ ({{ Math.round(impactPoint.x) }}, {{ Math.round(impactPoint.y) }})
              </text>
            </g>

            <!-- 3. DYNAMIC MEASUREMENT VECTOR CONNECTING RED + TO IMPACT HOLE -->
            <g v-if="firedAimPoint && impactPoint" class="measurement-vector">
              <line
                :x1="firedAimPoint.x"
                :y1="firedAimPoint.y"
                :x2="impactPoint.x"
                :y2="impactPoint.y"
                stroke="#f59e0b"
                stroke-width="2.2"
                stroke-dasharray="6 4"
              />
              <!-- Delta Offset Label Card -->
              <rect
                :x="(firedAimPoint.x + impactPoint.x) / 2 - 110"
                :y="(firedAimPoint.y + impactPoint.y) / 2 - 14"
                width="220"
                height="28"
                rx="6"
                fill="rgba(3, 14, 26, 0.95)"
                stroke="#f59e0b"
                stroke-width="1.5"
              />
              <text
                :x="(firedAimPoint.x + impactPoint.x) / 2"
                :y="(firedAimPoint.y + impactPoint.y) / 2 + 4"
                fill="#fef3c7"
                font-size="10.5"
                font-weight="900"
                font-family="monospace"
                text-anchor="middle"
              >
                ΔX: {{ shotDriftX >= 0 ? `+${shotDriftX}` : shotDriftX }}px ({{ shotDriftXCm >= 0 ? `+${shotDriftXCm}` : shotDriftXCm }}cm) · ΔY: {{ shotDriftY >= 0 ? `+${shotDriftY}` : shotDriftY }}px ({{ shotDriftYCm >= 0 ? `+${shotDriftYCm}` : shotDriftYCm }}cm)
              </text>
            </g>
          </svg>
        </div>

        <!-- Floating On-Screen Guidance Banner -->
        <div class="interactive-guide-pill" :class="`step-${currentStep}`">
          <div v-if="currentStep === 1" class="guide-content">
            <span class="guide-icon">🎯</span>
            <div>
              <strong>1. ADIM: HEDEFE NİŞAN ALIN & ATEŞ EDİN</strong>
              <p>Tareti hedef kağıdının merkezine doğrultun ve aşağıdaki <strong>[🔥 ATEŞ ET]</strong> butonuna (veya Space) basın.</p>
            </div>
          </div>
          <div v-else-if="currentStep === 2" class="guide-content">
            <span class="guide-icon animate-bounce">🔍</span>
            <div>
              <strong>2. ADIM: MERMİNİN VURDUĞU DELİĞE TIKLAYIN</strong>
              <p>Dürbün zoomu açıldı. Hedef kağıdında merminin bıraktığı deliğe mouse ile tıklayın.</p>
            </div>
          </div>
          <div v-else class="guide-content">
            <span class="guide-icon">✅</span>
            <div>
              <strong>3. ADIM: KALİBRASYON HESAPLANDI</strong>
              <p>Sapma değerlerini yandaki panelden inceleyin veya ince ayar yapıp <strong>[Kalibrasyonu Kaydet]</strong> butonuna basın.</p>
            </div>
          </div>
        </div>

        <!-- Floating Bottom-Left D-Pad Jog Controls -->
        <div class="floating-jog-pod">
          <div class="jog-pod-header">
            <span>TARET YÖNLENDİRME (JOG)</span>
          </div>
          <div class="jog-dpad-grid">
            <button type="button" class="jog-btn up" title="Yukarı Tilt" @click="emit('jog', 'tilt', 1, 1500)">
              <ArrowUp :size="16" />
            </button>
            <div class="jog-row">
              <button type="button" class="jog-btn left" title="Sola Pan" @click="emit('jog', 'pan', -1, 1500)">
                <ArrowLeft :size="16" />
              </button>
              <button type="button" class="jog-btn center" title="Nişangahı Sıfırla" @click="resetToCenter">
                <RotateCcw :size="14" />
              </button>
              <button type="button" class="jog-btn right" title="Sağa Pan" @click="emit('jog', 'pan', 1, 1500)">
                <ArrowRight :size="16" />
              </button>
            </div>
            <button type="button" class="jog-btn down" title="Aşağı Tilt" @click="emit('jog', 'tilt', -1, 1500)">
              <ArrowDown :size="16" />
            </button>
          </div>

          <!-- Big Fire Trigger Button -->
          <button
            type="button"
            class="btn-fire-trigger"
            :disabled="props.magazineRemaining <= 0 || isSaving"
            @click="handleFire"
          >
            <Zap :size="18" />
            <span>{{ hasFired ? 'TEKRAR ATEŞ ET (TEST)' : '🔥 TEST ATIŞI YAP (SPACE)' }}</span>
          </button>
        </div>
      </section>

      <!-- Right HUD Telemetry & Calibration Sidebar -->
      <aside class="calibration-telemetry-sidebar">
        <!-- Telemetry Card: User requested "+- yatayda şu kadar pixel +- dikeyde desin" -->
        <div class="telemetry-card">
          <div class="card-header">
            <span class="card-title">SAPMA & OFSET ANALİZİ</span>
            <span class="text-xs text-cyan-400 font-mono">{{ effectiveDistance }}m İstasyon</span>
          </div>

          <!-- Real-Time Offset Display -->
          <div class="metric-highlight-grid">
            <div class="metric-box">
              <span class="metric-title">DİKEY SAPMA (PITCH / DÜŞME)</span>
              <div class="metric-main-val" :class="aimOffsetY !== 0 ? 'text-amber-400' : 'text-slate-300'">
                {{ aimOffsetY > 0 ? `+${aimOffsetY}` : aimOffsetY }} <span class="unit">px</span>
              </div>
              <div class="metric-sub-val">
                Fiziksel Düşme: <strong>{{ dropCm > 0 ? `+${dropCm}` : dropCm }} cm</strong>
              </div>
            </div>

            <div class="metric-box">
              <span class="metric-title">YATAY SAPMA (YAW / SAPMA)</span>
              <div class="metric-main-val" :class="aimOffsetX !== 0 ? 'text-cyan-400' : 'text-slate-300'">
                {{ aimOffsetX > 0 ? `+${aimOffsetX}` : aimOffsetX }} <span class="unit">px</span>
              </div>
              <div class="metric-sub-val">
                Fiziksel Sapma: <strong>{{ windageCm > 0 ? `+${windageCm}` : windageCm }} cm</strong>
              </div>
            </div>
          </div>

          <!-- Relative Delta to Initial Calibration -->
          <div v-if="hasFired && impactPoint" class="drift-summary-box">
            <div class="drift-header">
              <Sparkles :size="14" class="text-amber-400" />
              <strong>MEVCUT KALİBRASYONA GÖRE FARK:</strong>
            </div>
            <div class="drift-row">
              <span>Yatay Değişim (ΔX):</span>
              <strong :class="diffXFromInitial !== 0 ? 'text-cyan-300' : 'text-slate-400'">
                {{ diffXFromInitial >= 0 ? `+${diffXFromInitial}` : diffXFromInitial }} px ({{ diffXCm >= 0 ? `+${diffXCm}` : diffXCm }} cm)
              </strong>
            </div>
            <div class="drift-row">
              <span>Dikey Değişim (ΔY):</span>
              <strong :class="diffYFromInitial !== 0 ? 'text-amber-300' : 'text-slate-400'">
                {{ diffYFromInitial >= 0 ? `+${diffYFromInitial}` : diffYFromInitial }} px ({{ diffYCm >= 0 ? `+${diffYCm}` : diffYCm }} cm)
              </strong>
            </div>
          </div>
        </div>

        <!-- Horizontal Lock Toggle -->
        <div class="control-section-card">
          <label class="toggle-control-label">
            <input v-model="lockHorizontal" type="checkbox" />
            <div class="toggle-text">
              <div class="toggle-title">
                <component :is="lockHorizontal ? Lock : Unlock" :size="14" :class="lockHorizontal ? 'text-emerald-400' : 'text-amber-400'" />
                <span>Yatay Sapmayı Kilitle (0 px)</span>
              </div>
              <small class="toggle-desc">
                Merminin sağa/sola sapmadığı durumlarda yatay ekseni 0'da sabit tutar.
              </small>
            </div>
          </label>
        </div>

        <!-- Micro-Adjustment Precision Steppers -->
        <div class="control-section-card">
          <div class="stepper-title-row">
            <span class="stepper-heading">DİKEY İNCE AYAR (YÜKSEKLİK)</span>
            <span class="stepper-val font-mono">{{ aimOffsetY }} px</span>
          </div>
          <div class="stepper-btn-grid">
            <button type="button" class="stepper-btn" @click="adjustOffsetY(-5)">-5 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetY(-1)">-1 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetY(1)">+1 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetY(5)">+5 px</button>
          </div>
        </div>

        <div v-if="!lockHorizontal" class="control-section-card">
          <div class="stepper-title-row">
            <span class="stepper-heading">YATAY İNCE AYAR (SAPMA)</span>
            <span class="stepper-val font-mono">{{ aimOffsetX }} px</span>
          </div>
          <div class="stepper-btn-grid">
            <button type="button" class="stepper-btn" @click="adjustOffsetX(-5)">-5 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetX(-1)">-1 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetX(1)">+1 px</button>
            <button type="button" class="stepper-btn" @click="adjustOffsetX(5)">+5 px</button>
          </div>
        </div>

        <!-- Magazine & Hardware Status -->
        <div class="magazine-status-pill">
          <span>ŞARJÖR:</span>
          <strong>{{ props.magazineRemaining }} / {{ props.magazineCapacity }} ADET</strong>
          <span class="sensor-scale-tag">1 px ≈ {{ mmPerPx.toFixed(1) }} mm</span>
        </div>

        <!-- Retest Button -->
        <button
          v-if="hasFired"
          type="button"
          class="btn-retest-action"
          @click="handleRetest"
        >
          <RotateCcw :size="15" />
          <span>İşaretleri Temizle & Yeni Atış Yap</span>
        </button>

        <!-- Error & Success Feedback Banners -->
        <div v-if="errorMessage" class="feedback-banner error">
          <AlertTriangle :size="16" />
          <span>{{ errorMessage }}</span>
        </div>

        <div v-if="saveSuccess" class="feedback-banner success">
          <CheckCircle2 :size="16" />
          <span>Yeni Sıfırlama Başarıyla Kaydedildi! Kokpite dönülüyor…</span>
        </div>

        <!-- Primary Action: Save & Return to Cockpit (User requested: "kaydet diyeyim dönsün kokpite yeni kalibre halinde") -->
        <div class="sidebar-action-footer">
          <button
            type="button"
            class="btn-save-and-exit"
            :disabled="isSaving"
            @click="saveCalibration"
          >
            <Save :size="18" />
            <span>{{ isSaving ? 'Kaydediliyor…' : '💾 SIFIRLAMAYI KAYDET & KOKPİTE DÖN' }}</span>
          </button>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.boresight-fullscreen-container {
  position: fixed;
  inset: 0;
  z-index: 150;
  width: 100vw;
  height: 100vh;
  background: #020617;
  color: #f1f5f9;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  user-select: none;
}

/* Top HUD Navigation Bar */
.hud-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: rgba(3, 14, 26, 0.95);
  border-bottom: 1px solid rgba(56, 189, 248, 0.25);
  backdrop-filter: blur(10px);
  z-index: 10;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.brand-badge {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-title {
  font-size: 0.88rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  color: #f0f9ff;
}

.hud-tag {
  font-size: 0.65rem;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(56, 189, 248, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
}

.distance-pill-group {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-left: 12px;
  border-left: 1px solid rgba(148, 163, 184, 0.2);
}

.distance-label {
  font-size: 0.68rem;
  font-weight: 800;
  color: #94a3b8;
}

.dist-pill {
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 700;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.25);
  color: #cbd5e1;
  cursor: pointer;
  transition: all 0.15s;
}

.dist-pill.active {
  background: rgba(8, 74, 99, 0.6);
  border-color: #38bdf8;
  color: #38bdf8;
  box-shadow: 0 0 10px rgba(56, 189, 248, 0.25);
}

.custom-dist-input {
  width: 60px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(2, 6, 23, 0.8);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #fff;
  font-size: 0.75rem;
  font-family: monospace;
}

/* Center Flow Indicator */
.topbar-center {
  display: flex;
  align-items: center;
}

.step-flow {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(15, 23, 42, 0.75);
  padding: 5px 14px;
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.15);
}

.flow-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.75rem;
  font-weight: 700;
  color: #64748b;
  transition: all 0.2s;
}

.flow-item.current {
  color: #38bdf8;
}

.flow-item.done {
  color: #34d399;
}

.flow-num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 0.65rem;
  border: 1px solid currentColor;
}

.flow-sep {
  color: #475569;
  font-size: 0.7rem;
}

/* Topbar Right Controls */
.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.zoom-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  background: rgba(15, 23, 42, 0.75);
  padding: 3px 6px;
  border-radius: 8px;
  border: 1px solid rgba(56, 189, 248, 0.25);
}

.zoom-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 800;
  background: rgba(8, 47, 73, 0.5);
  border: 1px solid rgba(56, 189, 248, 0.3);
  color: #bae6fd;
  cursor: pointer;
  transition: all 0.15s;
}

.zoom-btn.active {
  background: rgba(14, 116, 144, 0.8);
  border-color: #38bdf8;
  color: #fff;
  box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
}

.zoom-presets {
  display: flex;
  align-items: center;
  gap: 2px;
}

.zoom-chip {
  padding: 3px 7px;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 800;
  background: transparent;
  border: none;
  color: #94a3b8;
  cursor: pointer;
}

.zoom-chip.active {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}

.hud-close-btn {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.25);
  color: #cbd5e1;
  padding: 6px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
}

.hud-close-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: #ef4444;
  color: #ef4444;
}

/* Main Layout Area */
.lab-main-area {
  display: grid;
  grid-template-columns: 1fr 370px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Camera Stage */
.camera-viewport-stage {
  position: relative;
  background: #000;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.fire-flash-overlay {
  position: absolute;
  inset: 0;
  background: rgba(254, 240, 138, 0.35);
  pointer-events: none;
  z-index: 20;
  animation: flash-fade 0.18s ease-out forwards;
}

@keyframes flash-fade {
  from { opacity: 1; }
  to { opacity: 0; }
}

.camera-zoom-surface {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.camera-stream-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.camera-svg-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: auto;
}

.camera-svg-overlay.cursor-crosshair {
  cursor: crosshair;
}

/* Floating Guidance Banner */
.interactive-guide-pill {
  position: absolute;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(2, 14, 28, 0.92);
  border: 1px solid rgba(56, 189, 248, 0.4);
  border-radius: 12px;
  padding: 10px 20px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(8px);
  pointer-events: none;
  z-index: 10;
  max-width: 650px;
}

.interactive-guide-pill.step-2 {
  border-color: rgba(245, 158, 11, 0.6);
  background: rgba(20, 15, 2, 0.94);
}

.interactive-guide-pill.step-3 {
  border-color: rgba(52, 211, 153, 0.6);
  background: rgba(2, 24, 18, 0.94);
}

.guide-content {
  display: flex;
  align-items: center;
  gap: 12px;
}

.guide-icon {
  font-size: 1.4rem;
}

.guide-content strong {
  display: block;
  font-size: 0.82rem;
  letter-spacing: 0.05em;
  color: #f8fafc;
}

.guide-content p {
  margin: 2px 0 0;
  font-size: 0.74rem;
  color: #94a3b8;
}

/* Floating Bottom-Left Jog Pod */
.floating-jog-pod {
  position: absolute;
  bottom: 20px;
  left: 20px;
  background: rgba(4, 16, 28, 0.88);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 14px;
  padding: 12px;
  backdrop-filter: blur(10px);
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
  z-index: 10;
}

.jog-pod-header {
  font-size: 0.62rem;
  font-weight: 900;
  letter-spacing: 0.1em;
  color: #7dd3fc;
  text-align: center;
}

.jog-dpad-grid {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.jog-row {
  display: flex;
  gap: 4px;
}

.jog-btn {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.25);
  color: #e2e8f0;
  cursor: pointer;
  transition: all 0.15s;
}

.jog-btn:hover {
  background: rgba(8, 74, 99, 0.7);
  border-color: #38bdf8;
  color: #38bdf8;
  transform: scale(1.05);
}

.jog-btn:active {
  transform: scale(0.95);
}

.btn-fire-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 0.8rem;
  font-weight: 900;
  letter-spacing: 0.05em;
  background: linear-gradient(135deg, #ef4444, #b91c1c);
  border: 1px solid rgba(248, 113, 113, 0.6);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 0 20px rgba(239, 68, 68, 0.4);
  transition: all 0.2s;
}

.btn-fire-trigger:hover:not(:disabled) {
  background: linear-gradient(135deg, #f87171, #dc2626);
  box-shadow: 0 0 28px rgba(239, 68, 68, 0.6);
  transform: translateY(-1px);
}

.btn-fire-trigger:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

/* Right Sidebar */
.calibration-telemetry-sidebar {
  background: rgba(4, 13, 24, 0.95);
  border-left: 1px solid rgba(56, 189, 248, 0.2);
  padding: 18px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.telemetry-card {
  background: rgba(8, 24, 40, 0.7);
  border: 1px solid rgba(56, 189, 248, 0.25);
  border-radius: 12px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-size: 0.68rem;
  font-weight: 900;
  letter-spacing: 0.12em;
  color: #7dd3fc;
}

.metric-highlight-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.metric-box {
  background: rgba(2, 6, 23, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.15);
  border-radius: 8px;
  padding: 10px;
}

.metric-title {
  font-size: 0.6rem;
  font-weight: 800;
  color: #94a3b8;
  display: block;
}

.metric-main-val {
  font-size: 1.35rem;
  font-weight: 900;
  font-family: monospace;
  margin: 4px 0 2px;
}

.metric-main-val .unit {
  font-size: 0.75rem;
  font-weight: 700;
  opacity: 0.7;
}

.metric-sub-val {
  font-size: 0.65rem;
  color: #cbd5e1;
}

.drift-summary-box {
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-radius: 8px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.drift-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.68rem;
  color: #fde047;
  margin-bottom: 2px;
}

.drift-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.72rem;
}

/* Control Section Cards */
.control-section-card {
  background: rgba(8, 24, 40, 0.5);
  border: 1px solid rgba(148, 163, 184, 0.15);
  border-radius: 10px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.toggle-control-label {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
}

.toggle-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.toggle-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  font-weight: 800;
  color: #f1f5f9;
}

.toggle-desc {
  font-size: 0.66rem;
  color: #94a3b8;
  line-height: 1.3;
}

.stepper-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.stepper-heading {
  font-size: 0.65rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  color: #7dd3fc;
}

.stepper-val {
  font-size: 0.75rem;
  color: #e0f2fe;
}

.stepper-btn-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}

.stepper-btn {
  padding: 6px 2px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 800;
  font-family: monospace;
  background: rgba(15, 23, 42, 0.7);
  border: 1px solid rgba(148, 163, 184, 0.2);
  color: #e2e8f0;
  cursor: pointer;
  transition: all 0.15s;
}

.stepper-btn:hover {
  background: rgba(8, 74, 99, 0.6);
  border-color: #38bdf8;
  color: #38bdf8;
}

.magazine-status-pill {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(2, 6, 23, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.15);
  font-size: 0.68rem;
  color: #94a3b8;
}

.magazine-status-pill strong {
  color: #38bdf8;
}

.sensor-scale-tag {
  font-size: 0.62rem;
  color: #64748b;
}

.btn-retest-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 0.74rem;
  font-weight: 700;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.25);
  color: #cbd5e1;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-retest-action:hover {
  background: rgba(8, 47, 73, 0.5);
  border-color: #38bdf8;
  color: #38bdf8;
}

.feedback-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-radius: 8px;
  font-size: 0.72rem;
  font-weight: 700;
}

.feedback-banner.error {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #fca5a5;
}

.feedback-banner.success {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #6ee7b7;
}

.sidebar-action-footer {
  margin-top: auto;
  padding-top: 10px;
}

.btn-save-and-exit {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 13px 16px;
  border-radius: 12px;
  font-size: 0.86rem;
  font-weight: 900;
  letter-spacing: 0.04em;
  background: linear-gradient(135deg, #059669, #047857);
  border: 1px solid rgba(52, 211, 153, 0.5);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 0 25px rgba(16, 185, 129, 0.35);
  transition: all 0.2s;
}

.btn-save-and-exit:hover:not(:disabled) {
  background: linear-gradient(135deg, #10b981, #059669);
  box-shadow: 0 0 35px rgba(16, 185, 129, 0.55);
  transform: translateY(-1px);
}

.btn-save-and-exit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
