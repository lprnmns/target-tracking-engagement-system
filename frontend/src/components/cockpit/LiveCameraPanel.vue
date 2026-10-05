<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Maximize2, RefreshCw, Settings2, ZoomIn } from '@lucide/vue'
import StatusBadge from '../shared/StatusBadge.vue'
import type { TruthTone } from '../../composables/useRuntimeTruth'
import type { SelectedDetectionKind, TargetPolicy } from '../../types/operation'
import type { CockpitTargetVerdict, VisionEvent } from '../../types/vision'
import type { TrackingUpdate } from '../../types/tracking'
import {
  targetClassToken,
  targetDisplayDescription,
  targetDisplayName,
  targetTeamToken,
} from '../../utils/targetLabels'

const props = defineProps<{
  streamUrl: string
  frameUrl: string
  latestFrame: VisionEvent | null
  width: number
  height: number
  aimX: number
  aimY: number
  sourceLabel: string
  truthMode: string
  truthDetail: string
  sourceTone: TruthTone
  sourceDetail: string
  selectedDevice: string
  backend: string
  frameAgeMs: number | null
  realFrameEvidence: boolean
  ktrDemoMode: boolean
  noPhysicalLabel: string
  selectedTargetId: number | null
  selectedDetectionId?: number | null
  selectedDetectionKind?: SelectedDetectionKind | null
  targetPolicy?: TargetPolicy
  trackingActive?: boolean
  trackingUpdate?: TrackingUpdate | null
  trackingIdSwitchCount?: number
  operationMode?: 'MANUAL' | 'AUTO'
  personSafetyAvailable: boolean
  personSafetyActive: boolean
  cameraFps?: number | null
  detectorFps?: number | null
  inferenceMs?: number | null
  modelCount?: number
  modelExecution?: string
  perceptionEnabled: boolean
  detectionRuntimeReady?: boolean
  detectionRuntimeDetail?: string
  perceptionStatusLabel?: string
  perceptionStatusTone?: TruthTone
  targetLabelPrefix?: string
  logicalTargetNames?: Record<string, string>
  operatorMode?: boolean
  showLocalControls?: boolean
  magazineRemaining?: number
  magazineCapacity?: number
  magazineResetBusy?: boolean
  zoomActive?: boolean
  zoomScale?: number
  imageSettings?: {
    brightness: number
    contrast: number
    saturation: number
    exposure: number
    exposureAuto: boolean
  }
}>()

const emit = defineEmits<{
  selectTarget: [target: { id: number, center_x: number, center_y: number, kind: 'body' | 'balloon', track_id?: number | null, confidence?: number, bbox?: { x: number, y: number, w: number, h: number }, class_name?: string, target_team?: string }]
  togglePerception: []
  browserVisionEvent: [event: VisionEvent, size: { width: number, height: number }]
  openSetup: []
  openCameraSettings: []
  fullscreen: []
  reloadMagazine: []
  toggleZoom: []
}>()

const localVideoRef = ref<HTMLVideoElement | null>(null)
const browserCameraDevices = ref<Array<{ deviceId: string, label: string }>>([])
const selectedBrowserDeviceId = ref('')
const browserCameraStream = ref<MediaStream | null>(null)
const browserCameraError = ref<string | null>(null)
const browserCameraStatus = ref('Browser camera not started')
const backendFrameObjectUrl = ref<string | null>(null)
const backendFrameError = ref<string | null>(null)
const backendStreamConnected = ref(false)
const backendStreamNonce = ref(0)
const backendStreamError = ref<string | null>(null)
const backendFrameFallback = ref(false)
let backendFrameTimer: ReturnType<typeof window.setTimeout> | null = null
let backendFrameRequest: AbortController | null = null
let backendStreamRetryTimer: ReturnType<typeof window.setTimeout> | null = null
let backendStreamConnectTimer: ReturnType<typeof window.setTimeout> | null = null
let browserInferenceTimer: ReturnType<typeof window.setInterval> | null = null
let browserInferenceBusy = false

const viewBox = computed(() => `0 0 ${Math.max(props.width, 1)} ${Math.max(props.height, 1)}`)
// This is the physical aim point, not merely the optical image centre.  The
// backend uses the same offsets for autonomous tracking/FIRE, so the blue
// circle and arrows show where the turret will actually settle.
const centerX = computed(() => props.aimX)
const centerY = computed(() => props.aimY)
const opticalCenterX = computed(() => props.width / 2)
const opticalCenterY = computed(() => props.height / 2)
const hasAimOffset = computed(() => Math.abs(centerX.value - opticalCenterX.value) > 0.5 || Math.abs(centerY.value - opticalCenterY.value) > 0.5)
type OverlayDetection = {
  id: number
  kind: 'body' | 'balloon'
  confidence: number
  bbox: { x: number, y: number, w: number, h: number }
  center_x: number
  center_y: number
  source: string
  class_name?: string
  target_team?: string
  track_id?: number | null
  logical_name?: string
  visual_track_key: string
  visual_live: boolean
  visual_age_ms: number
  verdict?: CockpitTargetVerdict
}

const detections = computed<OverlayDetection[]>(() => {
  if (!(props.perceptionEnabled || props.ktrDemoMode) || !props.latestFrame) return []
  const frame = props.latestFrame
  const verdictMap = new Map<string, CockpitTargetVerdict>()
  if (frame.target_verdicts) {
    for (const v of frame.target_verdicts) {
      if (v.detection_id !== null && v.detection_id !== undefined) {
        verdictMap.set(`${v.kind}:${v.detection_id}`, v)
      }
    }
  }
  const bodies: OverlayDetection[] = frame.body_detections.map((body) => ({
    id: body.id,
    kind: 'body',
    confidence: body.confidence,
    bbox: body.bbox,
    center_x: body.bbox.x + body.bbox.w / 2,
    center_y: body.bbox.y + body.bbox.h / 2,
    source: body.source,
    class_name: body.class_name,
    target_team: body.target_team,
    track_id: body.track_id,
    logical_name: body.visual_logical_name ?? props.logicalTargetNames?.[`body:${body.id}`],
    visual_track_key: body.visual_track_key ?? `body:${body.track_id ?? body.id}`,
    visual_live: body.visual_live !== false,
    visual_age_ms: body.visual_age_ms ?? 0,
    verdict: verdictMap.get(`body:${body.id}`),
  }))
  const balloons: OverlayDetection[] = frame.balloon_detections.map((balloon) => ({
    id: balloon.id,
    kind: 'balloon',
    confidence: balloon.confidence,
    bbox: balloon.bbox,
    center_x: balloon.center_x,
    center_y: balloon.center_y,
    source: balloon.source,
    logical_name: balloon.visual_logical_name ?? props.logicalTargetNames?.[`balloon:${balloon.id}`],
    visual_track_key: balloon.visual_track_key ?? `balloon:${balloon.id}`,
    visual_live: balloon.visual_live !== false,
    visual_age_ms: balloon.visual_age_ms ?? 0,
    verdict: verdictMap.get(`balloon:${balloon.id}`),
  }))
  return [...bodies, ...balloons]
})
const detectionFrameWidth = computed(() => Math.max(1, props.latestFrame?.frame_width ?? props.width))
const detectionFrameHeight = computed(() => Math.max(1, props.latestFrame?.frame_height ?? props.height))
const detectionScaleX = computed(() => props.width / detectionFrameWidth.value)
const detectionScaleY = computed(() => props.height / detectionFrameHeight.value)
const trackingDiagnostic = computed(() => props.trackingUpdate ?? null)
const trackingLockZone = computed(() => {
  const update = trackingDiagnostic.value
  if (!update?.bbox_lock_enabled || update.lock_zone_center_x === null || update.lock_zone_center_x === undefined || update.lock_zone_center_y === null || update.lock_zone_center_y === undefined) return null
  const width = update.lock_zone_width * detectionScaleX.value
  const height = update.lock_zone_height * detectionScaleY.value
  return {
    x: update.lock_zone_center_x * detectionScaleX.value - width / 2,
    y: update.lock_zone_center_y * detectionScaleY.value - height / 2,
    width,
    height,
  }
})
const trackingMeasurementLabel = computed(() => {
  if (trackingDiagnostic.value?.measurement_status === 'NEW') return 'FRESH MEASUREMENT'
  if (trackingDiagnostic.value?.measurement_status === 'REUSED') return 'HELD MEASUREMENT'
  return trackingDiagnostic.value?.measurement_status ?? 'MISSING'
})
const trackedCenterX = computed(() => {
  const value = trackingDiagnostic.value?.target_center_x
  return value === null || value === undefined ? null : value * detectionScaleX.value
})
const trackedCenterY = computed(() => {
  const value = trackingDiagnostic.value?.target_center_y
  return value === null || value === undefined ? null : value * detectionScaleY.value
})
const trackedImageCenterX = computed(() => (trackingDiagnostic.value?.frame_center_x ?? centerX.value) * detectionScaleX.value)
const trackedImageCenterY = computed(() => (trackingDiagnostic.value?.frame_center_y ?? centerY.value) * detectionScaleY.value)
const trackingAgeLabel = computed(() => {
  const age = trackingDiagnostic.value?.selected_track_last_seen_age_ms
  return age === null || age === undefined ? '—' : `${Math.round(age)} ms`
})
const browserPreviewActive = computed(() => !!browserCameraStream.value && !props.ktrDemoMode)
const backendCameraConfigured = computed(() => {
  const device = props.selectedDevice.trim().toLowerCase()
  const backend = props.backend.trim().toLowerCase()
  return !['', 'n/a', 'mock', 'none'].includes(device) && backend !== 'released'
})
const backendFrameActive = computed(() => (props.realFrameEvidence || backendCameraConfigured.value) && !props.ktrDemoMode && !browserPreviewActive.value)
const serverAlignedOverlayActive = computed(() => backendFrameActive.value && !browserPreviewActive.value)
// Waiting for `realFrameEvidence` creates a deadlock: the first current frame
// is what makes that evidence true, so a configured backend must be allowed
// to request the stream before the first status refresh.
const liveFrameVisible = computed(() => browserPreviewActive.value || backendFrameActive.value)
const showCameraImage = computed(() => liveFrameVisible.value)
const backendStreamUrl = computed(() => {
  // The backend burns detections into the exact immutable frame consumed by
  // YOLO. A plain current-camera MJPEG plus a separately polled SVG bbox is an
  // unjoinable pair and visibly trails whenever the camera moves.
  const alignedStreamUrl = props.streamUrl.replace('/stream.mjpg', '/stream-overlay.mjpg')
  const separator = alignedStreamUrl.includes('?') ? '&' : '?'
  return `${alignedStreamUrl}${separator}cockpit_session=${backendStreamNonce.value}`
})
const backendAlignedFrameUrl = computed(() => props.frameUrl.replace('/frame.jpg', '/frame-overlay.jpg'))
const evidenceTruth = computed(() => browserPreviewActive.value ? 'real_frame_dev' : props.realFrameEvidence && !props.ktrDemoMode ? 'real_frame' : 'fixture')
const displaySourceLabel = computed(() => {
  if (props.ktrDemoMode) return props.operatorMode ? 'SİMÜLASYON AKTİF' : 'KTR Fixture - Not Live Target'
  if (browserPreviewActive.value) return props.operatorMode ? 'KAMERA ÖNİZLEME AKTİF' : 'LAPTOP CAMERA DEV - BROWSER PREVIEW'
  if (props.realFrameEvidence) return props.operatorMode ? 'CANLI KAMERA AKTİF' : 'LAPTOP CAMERA DEV - REAL FRAME'
  return props.operatorMode ? 'KAMERA BEKLENİYOR' : 'FIXTURE VIEW - NOT REAL CAMERA EVIDENCE'
})
const displayTone = computed<TruthTone>(() => liveFrameVisible.value ? 'good' : 'warn')
const cleanTruth = computed(() => props.truthMode === 'DEV_REAL_CAMERA' ? 'real frame dev' : props.truthMode === 'LIVE_SYSTEM' ? 'live system' : 'fixture')
const cleanSource = computed(() => props.ktrDemoMode ? 'KTR fixture' : browserPreviewActive.value ? 'Laptop browser' : props.realFrameEvidence ? 'Laptop dev' : 'Offline fixture')
const fallbackPerceptionStatusLabel = computed(() => props.perceptionEnabled ? 'Algılama aktif' : 'Algılama kapalı')
const perceptionStatusLabel = computed(() => props.perceptionStatusLabel ?? fallbackPerceptionStatusLabel.value)
const perceptionTone = computed<TruthTone>(() => props.perceptionStatusTone ?? (props.perceptionEnabled && props.detectionRuntimeReady !== false ? 'good' : 'warn'))
// Runtime status is the authoritative rolling measurement. A hard refresh can
// leave the last frame object stale for one WebSocket cycle, so preferring it
// made the HUD show an old 1-6 FPS value while the backend was already back at
// 25+ FPS. Frame-local values remain a fallback for isolated preview modes.
const measuredCameraFps = computed(() => props.cameraFps ?? props.latestFrame?.camera_fps ?? null)
const measuredDetectorFps = computed(() => props.detectorFps ?? props.latestFrame?.detector_fps ?? null)
const measuredInferenceMs = computed(() => props.latestFrame?.inference_ms ?? props.inferenceMs ?? null)
const measuredModelCount = computed(() => props.latestFrame?.model_count ?? props.modelCount ?? 0)
const measuredModelExecution = computed(() => props.latestFrame?.model_execution ?? props.modelExecution ?? 'none')
const magazineCapacity = computed(() => Math.max(1, Math.round(props.magazineCapacity ?? 8)))
const magazineRemaining = computed(() => Math.max(0, Math.min(magazineCapacity.value, Math.round(props.magazineRemaining ?? magazineCapacity.value))))
const magazineEmpty = computed(() => magazineRemaining.value <= 0)
const magazineLow = computed(() => !magazineEmpty.value && magazineRemaining.value <= Math.max(1, Math.ceil(magazineCapacity.value * 0.25)))
const magazineTone = computed(() => magazineEmpty.value ? 'empty' : magazineLow.value ? 'low' : 'ready')
function metricNumber(value: number | null): string { return value === null || !Number.isFinite(value) ? '—' : value.toFixed(1) }
const selectedBrowserDeviceLabel = computed(() => browserCameraDevices.value.find((device) => device.deviceId === selectedBrowserDeviceId.value)?.label ?? 'Laptop camera')
const cameraFilterStyle = computed(() => {
  const settings = props.imageSettings ?? { brightness: 0, contrast: 0, saturation: 0, exposure: 0, exposureAuto: true }
  const exposureBoost = settings.exposureAuto ? 0 : settings.exposure * 0.35
  const brightness = Math.max(0.25, Math.min(1.9, 1 + (settings.brightness + exposureBoost) / 100))
  const contrast = Math.max(0.25, Math.min(2.1, 1 + settings.contrast / 100))
  const saturation = Math.max(0, Math.min(2.2, 1 + settings.saturation / 100))
  return {
    filter: `brightness(${brightness.toFixed(2)}) contrast(${contrast.toFixed(2)}) saturate(${saturation.toFixed(2)})`,
  }
})

function labelX(target: OverlayDetection): number {
  return Math.max(18, Math.min(boxX(target), props.width - 360))
}

function labelY(target: OverlayDetection): number {
  const above = boxY(target) - 9
  if (above > 22) return above
  return Math.min(props.height - 18, boxY(target) + boxH(target) + 22)
}

function machineClassToken(value: string | undefined): string { return targetClassToken(value) }

function targetLabel(target: OverlayDetection): string {
  if (target.verdict?.label_tr) {
    return target.verdict.label_tr
  }
  const continuity = target.visual_live ? '' : ` · SON GÖRÜLME ${Math.max(0, target.visual_age_ms)}ms`
  if (target.kind === 'body') {
    const fallback = target.logical_name ?? targetDisplayName({ className: machineClassToken(target.class_name), team: targetTeamToken(target.target_team), sequence: target.id })
    return `${fallback} | ${Math.round(target.confidence * 100)}% | HAVA ARACI${continuity}`
  }
  const logicalName = target.logical_name ?? targetDisplayName({ sequence: target.id, balloon: true, className: 'balloon', team: 'enemy', standaloneBalloon: true })
  return `${logicalName} | ${Math.round(target.confidence * 100)}% | ${/_dost_\d+_balon/i.test(logicalName) ? 'DOST BALONU' : 'HEDEF BALONU'}${continuity}`
}

function showTargetDecoration(target: OverlayDetection): boolean {
  if (props.targetPolicy === 'BALLOON_AIRCRAFT' && target.kind === 'balloon') {
    return true
  }
  return true
}

function targetStroke(target: OverlayDetection): string {
  if (target.verdict) {
    if (target.verdict.verdict_state === 'FIRE_AUTHORIZED') return '#22c55e'
    if (target.verdict.verdict_state === 'RANGE_WAIT') return '#f59e0b'
    if (target.verdict.verdict_state === 'FRIEND_LOCKED') return '#3b82f6'
    if (target.verdict.verdict_state === 'CLASSIFYING') return '#a855f7'
  }
  if (target.kind === 'body') return target.target_team === 'enemy' ? '#ef4444' : '#3b82f6'
  return /_dost_\d+_balon/i.test(target.logical_name ?? '') ? '#22c55e' : props.selectedTargetId === target.id ? '#22c55e' : '#f59e0b'
}

function targetFill(target: OverlayDetection): string {
  if (target.verdict) {
    if (target.verdict.verdict_state === 'FIRE_AUTHORIZED') return 'rgba(34,197,94,0.12)'
    if (target.verdict.verdict_state === 'RANGE_WAIT') return 'rgba(245,158,11,0.12)'
    if (target.verdict.verdict_state === 'FRIEND_LOCKED') return 'rgba(59,130,246,0.12)'
    if (target.verdict.verdict_state === 'CLASSIFYING') return 'rgba(168,85,247,0.12)'
  }
  return target.kind === 'body'
    ? target.target_team === 'enemy' ? 'rgba(239,68,68,0.10)' : 'rgba(59,130,246,0.10)'
    : 'rgba(245,158,11,0.09)'
}

function targetSelectable(target: OverlayDetection): boolean {
  // A held bbox is evidence for visual continuity only. It can never produce
  // a target selection or flow into tracking/motion/FIRE.
  if (!target.visual_live) return false
  const policy = props.targetPolicy ?? 'BALLOON'
  if (policy === 'BALLOON') return target.kind === 'balloon'
  // Enemy bodies are acquired automatically; an operator may explicitly
  // click any aircraft for observation/tracking. Gateway FIRE still rejects
  // friend/unknown targets independently.
  if (policy === 'AIRCRAFT') return target.kind === 'body'
  return true
}

function targetSelected(target: OverlayDetection): boolean {
  if (props.selectedDetectionId !== null && props.selectedDetectionId !== undefined && props.selectedDetectionKind) {
    return target.id === props.selectedDetectionId && target.kind === props.selectedDetectionKind
  }
  return target.kind === 'balloon' && props.selectedTargetId === target.id
}

function boxX(target: OverlayDetection): number { return target.bbox.x * detectionScaleX.value }
function boxY(target: OverlayDetection): number { return target.bbox.y * detectionScaleY.value }
function boxW(target: OverlayDetection): number { return target.bbox.w * detectionScaleX.value }
function boxH(target: OverlayDetection): number { return target.bbox.h * detectionScaleY.value }
function targetCenterX(target: OverlayDetection): number { return target.center_x * detectionScaleX.value }
function targetCenterY(target: OverlayDetection): number { return target.center_y * detectionScaleY.value }

async function refreshBrowserCameras(): Promise<void> {
  browserCameraError.value = null
  if (!navigator.mediaDevices?.enumerateDevices) {
    browserCameraError.value = 'Browser camera API unavailable'
    browserCameraStatus.value = 'Browser camera API unavailable'
    return
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    browserCameraDevices.value = devices
      .filter((device) => device.kind === 'videoinput')
      .map((device, index) => ({
        deviceId: device.deviceId,
        label: device.label || `Camera ${index + 1}`,
      }))
    if (!selectedBrowserDeviceId.value && browserCameraDevices.value[0]) {
      selectedBrowserDeviceId.value = browserCameraDevices.value[0].deviceId
    }
    browserCameraStatus.value = browserCameraDevices.value.length
      ? `${browserCameraDevices.value.length} camera detected`
      : 'No browser camera detected'
  } catch (error) {
    browserCameraError.value = error instanceof Error ? error.message : String(error)
    browserCameraStatus.value = 'Camera enumeration failed'
  }
}

function stopBrowserCamera(): void {
  browserCameraStream.value?.getTracks().forEach((track) => track.stop())
  browserCameraStream.value = null
  if (localVideoRef.value) localVideoRef.value.srcObject = null
  browserCameraStatus.value = 'Browser camera stopped'
}

async function submitBrowserFrameForYolo(): Promise<void> {
  if (!props.perceptionEnabled || !browserPreviewActive.value || browserInferenceBusy) return
  const video = localVideoRef.value
  if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return
  browserInferenceBusy = true
  try {
    const targetWidth = video.videoWidth
    const targetHeight = video.videoHeight
    const canvas = document.createElement('canvas')
    canvas.width = targetWidth
    canvas.height = targetHeight
    const context = canvas.getContext('2d')
    if (!context) return
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    const imageBase64 = canvas.toDataURL('image/jpeg', 0.92)
    const response = await fetch('/api/vision/browser-frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64: imageBase64, width: canvas.width, height: canvas.height, device_label: selectedBrowserDeviceLabel.value }),
    })
    if (!response.ok) throw new Error(`BROWSER_FRAME_HTTP_${response.status}`)
    const event = await response.json() as VisionEvent
    emit('browserVisionEvent', event, { width: canvas.width, height: canvas.height })
    browserCameraStatus.value = `Vision frame processed: ${event.balloon_detections.length} target candidate`
  } catch (error) {
    browserCameraError.value = error instanceof Error ? error.message : String(error)
  } finally {
    browserInferenceBusy = false
  }
}

function startBrowserInferenceLoop(): void {
  if (browserInferenceTimer !== null) return
  browserInferenceTimer = window.setInterval(() => { void submitBrowserFrameForYolo() }, 650)
}

function stopBrowserInferenceLoop(): void {
  if (browserInferenceTimer === null) return
  window.clearInterval(browserInferenceTimer)
  browserInferenceTimer = null
}

function stopBackendFrameLoop(): void {
  if (backendFrameTimer !== null) window.clearTimeout(backendFrameTimer)
  backendFrameTimer = null
  backendFrameRequest?.abort()
  backendFrameRequest = null
}

function releaseBackendFrameUrl(): void {
  if (backendFrameObjectUrl.value) URL.revokeObjectURL(backendFrameObjectUrl.value)
  backendFrameObjectUrl.value = null
}

// Kept as a diagnostic fallback for old embedding pages. The cockpit itself
// uses the streaming path below; it never aborts a healthy request on a timer.
async function refreshBackendFrame(): Promise<void> {
  if (!backendFrameFallback.value || !backendFrameActive.value || browserPreviewActive.value || backendFrameRequest) return
  const controller = new AbortController()
  backendFrameRequest = controller
  try {
    const separator = backendAlignedFrameUrl.value.includes('?') ? '&' : '?'
    const response = await fetch(`${backendAlignedFrameUrl.value}${separator}t=${Date.now()}`, { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error(`CAMERA_FRAME_HTTP_${response.status}`)
    const blob = await response.blob()
    if (!blob.type.startsWith('image/')) throw new Error('CAMERA_FRAME_INVALID_CONTENT')
    const nextUrl = URL.createObjectURL(blob)
    const previousUrl = backendFrameObjectUrl.value
    backendFrameObjectUrl.value = nextUrl
    if (previousUrl) URL.revokeObjectURL(previousUrl)
    backendStreamConnected.value = true
    backendStreamError.value = null
    backendFrameError.value = null
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) backendFrameError.value = error instanceof Error ? error.message : 'CAMERA_FRAME_FETCH_FAILED'
  } finally {
    if (backendFrameRequest === controller) backendFrameRequest = null
    if (backendFrameFallback.value && backendFrameActive.value && !browserPreviewActive.value) {
      backendFrameTimer = window.setTimeout(() => { void refreshBackendFrame() }, 50)
    }
  }
}
// Retain the single-frame diagnostic entry point for older integrations;
// normal cockpit rendering intentionally uses the MJPEG stream.
void refreshBackendFrame

function stopBackendStream(): void {
  if (backendStreamRetryTimer !== null) window.clearTimeout(backendStreamRetryTimer)
  if (backendStreamConnectTimer !== null) window.clearTimeout(backendStreamConnectTimer)
  backendStreamRetryTimer = null
  backendStreamConnectTimer = null
  backendStreamConnected.value = false
  backendFrameFallback.value = false
}

function startBackendStream(): void {
  stopBackendFrameLoop()
  stopBackendStream()
  if (!backendFrameActive.value || browserPreviewActive.value) return
  backendStreamError.value = null
  backendStreamNonce.value += 1
  // Some Chromium/Windows combinations render an MJPEG response but never
  // dispatch the img load event; others leave the element black after a
  // route handoff while detections continue over WebSocket.  Setup's camera
  // proves the worker is healthy, so fall back to the same current-frame
  // endpoint if the stream has not positively connected shortly after mount.
  backendStreamConnectTimer = window.setTimeout(() => {
    backendStreamConnectTimer = null
    if (!backendStreamConnected.value && backendFrameActive.value) {
      backendFrameFallback.value = true
      backendStreamError.value = 'Canlı kare modu etkin'
      void refreshBackendFrame()
    }
  }, 900)
}

function onBackendStreamLoad(): void {
  if (backendStreamConnectTimer !== null) window.clearTimeout(backendStreamConnectTimer)
  backendStreamConnectTimer = null
  backendStreamConnected.value = true
  backendFrameFallback.value = false
  stopBackendFrameLoop()
  backendStreamError.value = null
  backendFrameError.value = null
}

function onBackendStreamError(): void {
  backendStreamConnected.value = false
  backendFrameFallback.value = true
  backendStreamError.value = 'Canlı kare modu etkin'
  void refreshBackendFrame()
  if (backendStreamRetryTimer !== null || !backendFrameActive.value || browserPreviewActive.value) return
  backendStreamRetryTimer = window.setTimeout(() => {
    backendStreamRetryTimer = null
    if (backendFrameActive.value && !browserPreviewActive.value) backendStreamNonce.value += 1
  }, 500)
}

async function startBrowserCamera(): Promise<void> {
  if (props.ktrDemoMode) return
  browserCameraError.value = null
  if (!navigator.mediaDevices?.getUserMedia) {
    browserCameraError.value = 'Browser camera API unavailable'
    browserCameraStatus.value = 'Browser camera API unavailable'
    return
  }
  stopBrowserCamera()
  try {
    const video: MediaTrackConstraints = {
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { ideal: 30, max: 30 },
    }
    if (selectedBrowserDeviceId.value) video.deviceId = { exact: selectedBrowserDeviceId.value }
    const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false })
    browserCameraStream.value = stream
    await nextTick()
    if (localVideoRef.value) {
      localVideoRef.value.srcObject = stream
      await localVideoRef.value.play().catch(() => undefined)
    }
    await refreshBrowserCameras()
    browserCameraStatus.value = `Connected: ${selectedBrowserDeviceLabel.value}`
  } catch (error) {
    browserCameraError.value = error instanceof Error ? error.message : String(error)
    browserCameraStatus.value = 'Browser camera connection failed'
  }
}

watch(selectedBrowserDeviceId, () => {
  if (browserCameraStream.value) void startBrowserCamera()
})

watch([backendFrameActive, browserPreviewActive, () => props.streamUrl], () => {
  if (backendFrameActive.value && !browserPreviewActive.value) startBackendStream()
  else {
    stopBackendFrameLoop()
    stopBackendStream()
    releaseBackendFrameUrl()
  }
})

const cameraContainerRef = ref<HTMLElement | null>(null)
const zoomVideoRef = ref<HTMLVideoElement | null>(null)
const containerRect = ref<{ width: number, height: number }>({ width: 0, height: 0 })

function updateContainerRect(): void {
  if (!cameraContainerRef.value) return
  containerRect.value = {
    width: cameraContainerRef.value.clientWidth,
    height: cameraContainerRef.value.clientHeight,
  }
}

let resizeObserver: ResizeObserver | null = null

const SCOPE_DIAMETER = 320
const DEFAULT_ZOOM_SCALE = 2.5

const scopeGeometry = computed(() => {
  const cw = containerRect.value.width || props.width
  const ch = containerRect.value.height || props.height
  const pw = props.width || 1280
  const ph = props.height || 720
  const ar = pw / ph
  const containerAr = cw / (ch || 1)

  let renderedW = cw
  let renderedH = ch
  let offsetX = 0
  let offsetY = 0

  if (containerAr > ar) {
    renderedH = ch
    renderedW = ch * ar
    offsetX = (cw - renderedW) / 2
    offsetY = 0
  } else {
    renderedW = cw
    renderedH = cw / ar
    offsetX = 0
    offsetY = (ch - renderedH) / 2
  }

  const normAimX = Math.min(1, Math.max(0, props.aimX / pw))
  const normAimY = Math.min(1, Math.max(0, props.aimY / ph))

  const screenAimX = offsetX + normAimX * renderedW
  const screenAimY = offsetY + normAimY * renderedH

  return {
    cw,
    ch,
    renderedW,
    renderedH,
    offsetX,
    offsetY,
    screenAimX,
    screenAimY,
    normAimX,
    normAimY,
  }
})

const scopeContainerStyle = computed(() => {
  const g = scopeGeometry.value
  return {
    width: `${SCOPE_DIAMETER}px`,
    height: `${SCOPE_DIAMETER}px`,
    left: `${Math.round(g.screenAimX)}px`,
    top: `${Math.round(g.screenAimY)}px`,
    transform: 'translate(-50%, -50%)',
  }
})

const sniperMediaStyle = computed(() => {
  const g = scopeGeometry.value
  const mag = props.zoomScale ?? DEFAULT_ZOOM_SCALE
  const zoomedW = g.renderedW * mag
  const zoomedH = g.renderedH * mag
  const centerRadius = SCOPE_DIAMETER / 2
  const left = centerRadius - g.normAimX * zoomedW
  const top = centerRadius - g.normAimY * zoomedH

  return {
    width: `${Math.round(zoomedW)}px`,
    height: `${Math.round(zoomedH)}px`,
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    filter: cameraFilterStyle.value.filter,
  }
})

watch([() => props.zoomActive, browserCameraStream], () => {
  if (props.zoomActive && zoomVideoRef.value && browserCameraStream.value) {
    zoomVideoRef.value.srcObject = browserCameraStream.value
    void zoomVideoRef.value.play().catch(() => undefined)
  }
})

onMounted(() => {
  // Browser getUserMedia is an explicit engineering fallback only. Starting
  // it automatically races the profile-owned backend capture for the same
  // camera and can make a healthy UVC stream appear stale.
  void refreshBrowserCameras()
  startBrowserInferenceLoop()
  startBackendStream()
  if (cameraContainerRef.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      updateContainerRect()
    })
    resizeObserver.observe(cameraContainerRef.value)
    updateContainerRect()
  }
})

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  stopBrowserInferenceLoop()
  stopBackendFrameLoop()
  stopBackendStream()
  releaseBackendFrameUrl()
  stopBrowserCamera()
})
</script>

<template>
  <section class="cockpit-card camera-panel flex min-w-0 flex-col overflow-hidden">
    <div class="panel-title-row">
      <div class="min-w-0">
        <h2 class="panel-title">CANLI KAMERA</h2>
        <p class="panel-subtitle truncate">{{ props.sourceDetail }}</p>
      </div>
      <div class="flex flex-wrap justify-end gap-2">
        <StatusBadge :label="displaySourceLabel" :tone="displayTone" />
        <StatusBadge :label="props.operatorMode && props.ktrDemoMode ? 'DEMO ALGILAMA' : perceptionStatusLabel" :tone="perceptionTone" />
        <StatusBadge v-if="!props.operatorMode" :label="`Truth: ${props.truthMode === 'KTR_DEMO_FIXTURE' ? 'fixture' : props.truthMode === 'DEV_REAL_CAMERA' ? 'real_frame_dev' : props.truthMode === 'LIVE_SYSTEM' ? 'live_system' : 'fixture'}`" tone="neutral" />
        <span class="sr-only">KTR DEMO FIXTURE - NOT LIVE TARGET · truth=fixture · evidence_truth=fixture · Real camera path preserved separately</span>
        <StatusBadge v-if="!props.operatorMode" :label="`${props.width}x${props.height}`" tone="neutral" />
        <button
          class="header-icon-button"
          :class="{ 'border-cyan-400 text-cyan-400 bg-cyan-950/60 shadow-[0_0_12px_rgba(56,189,248,0.5)]': props.zoomActive }"
          type="button"
          :title="props.zoomActive ? 'Dürbün zoomu kapat (Z / Kumanda Buton 1)' : '2.5X Hassas Dürbün Zoom aç (Z / Kumanda Buton 1)'"
          @click="emit('toggleZoom')"
        >
          <ZoomIn :size="16" />
        </button>
        <button v-if="props.operatorMode" class="header-icon-button" type="button" title="Kamera ayarları" @click="emit('openCameraSettings')"><Settings2 :size="16" /></button>
        <button class="header-icon-button" type="button" title="Tam ekran" @click="emit('fullscreen')"><Maximize2 :size="16" /></button>
      </div>
    </div>

    <div v-if="!props.ktrDemoMode && props.showLocalControls !== false" class="camera-select-strip">
      <label>
        Camera
        <select v-model="selectedBrowserDeviceId">
          <option v-if="!browserCameraDevices.length" value="">No browser camera listed</option>
          <option v-for="device in browserCameraDevices" :key="device.deviceId" :value="device.deviceId">
            {{ device.label }}
          </option>
        </select>
      </label>
      <button type="button" @click="startBrowserCamera">Connect Laptop Cam</button>
      <button type="button" @click="refreshBrowserCameras">Refresh List</button>
      <button type="button" @click="stopBrowserCamera">Stop</button>
      <span :class="browserCameraError ? 'text-amber-200' : 'text-cyan-100'">{{ browserCameraError ?? browserCameraStatus }}</span>
      <span class="sr-only">Browser camera preview is local development evidence only; not competition USB acceptance; no physical command generated.</span>
    </div>

    <div ref="cameraContainerRef" class="relative min-h-0 flex-1 bg-black">
      <!-- The SVG viewBox preserves the native camera aspect ratio. The
           pixels must use the same contain geometry; object-cover crops the
           image while leaving detection coordinates uncropped and makes
           boxes appear above/right of their targets. -->
      <video v-if="browserPreviewActive" ref="localVideoRef" class="camera-live-frame h-full w-full object-contain" :style="cameraFilterStyle" autoplay muted playsinline />
      <img v-else-if="backendFrameActive && backendFrameFallback && backendFrameObjectUrl" :src="backendFrameObjectUrl" class="camera-live-frame h-full w-full object-contain" :style="cameraFilterStyle" alt="Canlı kamera karesi" decoding="async" />
      <img v-else-if="backendFrameActive" :src="backendStreamUrl" class="camera-live-frame h-full w-full object-contain" :style="cameraFilterStyle" alt="Canlı kamera akışı" decoding="async" fetchpriority="high" @load="onBackendStreamLoad" @error="onBackendStreamError" />
      <div v-if="backendFrameActive && !backendStreamConnected && backendStreamError" class="absolute left-4 top-4 z-10 rounded-md border border-amber-300/40 bg-black/78 px-3 py-2 text-sm font-semibold text-amber-100">{{ backendStreamError }}</div>
      <div v-if="!liveFrameVisible" class="absolute inset-0 bg-[radial-gradient(circle_at_54%_42%,rgba(14,165,233,0.18),transparent_30%),radial-gradient(circle_at_50%_100%,rgba(22,163,74,0.16),transparent_26%),linear-gradient(180deg,#071426_0%,#06131a_52%,#04120d_100%)]"></div>

      <div
        v-if="!showCameraImage"
        class="absolute left-4 top-4 z-10 max-w-[52%] rounded-md border border-amber-300/40 bg-black/78 px-3 py-2 text-xs font-semibold text-amber-100"
      >
        {{ displaySourceLabel }}
        <div class="mt-1 text-[11px] text-amber-200">{{ props.truthDetail }}</div>
      </div>

      <!-- Sniper Scope Dairesel Büyüteç (Zoom Lens) -->
      <div
        v-if="props.zoomActive"
        class="sniper-scope-container"
        :style="scopeContainerStyle"
      >
        <div class="sniper-scope-lens">
          <img
            v-if="backendFrameActive && !browserPreviewActive"
            :src="backendFrameFallback && backendFrameObjectUrl ? backendFrameObjectUrl : backendStreamUrl"
            class="sniper-scope-media"
            :style="sniperMediaStyle"
            alt="Dürbün zoom karesi"
          />
          <video
            v-else-if="browserPreviewActive"
            ref="zoomVideoRef"
            class="sniper-scope-media"
            :style="sniperMediaStyle"
            autoplay
            muted
            playsinline
          />
          <div class="sniper-scope-glass"></div>
          <svg class="sniper-scope-reticle" viewBox="-160 -160 320 320">
            <!-- Dış bilezikler -->
            <circle cx="0" cy="0" r="158" fill="none" stroke="#38bdf8" stroke-width="2.5" />
            <circle cx="0" cy="0" r="152" fill="none" stroke="rgba(56, 189, 248, 0.4)" stroke-width="1.2" stroke-dasharray="4 4" />
            <circle cx="0" cy="0" r="42" fill="none" stroke="rgba(56, 189, 248, 0.55)" stroke-width="1" />
            <!-- Kalibre sarı nişangah çizgileri -->
            <line x1="-155" y1="0" x2="-10" y2="0" stroke="#f59e0b" stroke-width="2" />
            <line x1="10" y1="0" x2="155" y2="0" stroke="#f59e0b" stroke-width="2" />
            <line x1="0" y1="-155" x2="0" y2="-10" stroke="#f59e0b" stroke-width="2" />
            <line x1="0" y1="10" x2="0" y2="155" stroke="#f59e0b" stroke-width="2" />
            <!-- Kalibre sarı hedef merkezi ve halkası -->
            <circle cx="0" cy="0" r="9" fill="none" stroke="#f59e0b" stroke-width="1.8" />
            <circle cx="0" cy="0" r="2.8" fill="#fde047" />
            <!-- Mil-Dot işaretçileri -->
            <circle cx="-30" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="-60" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="-90" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="-120" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="30" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="60" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="90" cy="0" r="1.6" fill="#38bdf8" />
            <circle cx="120" cy="0" r="1.6" fill="#38bdf8" />
            <circle cy="-30" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="-60" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="-90" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="-120" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="30" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="60" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="90" cx="0" r="1.6" fill="#38bdf8" />
            <circle cy="120" cx="0" r="1.6" fill="#38bdf8" />
            <!-- Üst kuzey/yön işareti -->
            <line x1="0" y1="-155" x2="0" y2="-138" stroke="#f43f5e" stroke-width="2.5" />
          </svg>
          <div class="sniper-scope-badge top-badge">2.5X HASSAS NİŞANGÂH</div>
          <div class="sniper-scope-badge bottom-badge">HASSAS MOD (%45 HIZ)</div>
        </div>
      </div>

      <svg class="absolute inset-0 h-full w-full" :viewBox="viewBox">
        <rect v-if="props.ktrDemoMode" x="0" y="0" :width="props.width" :height="props.height" fill="rgba(14,165,233,0.035)" />

        <!-- Optical Center Faint Reference (shown if boresight is offset) -->
        <g v-if="hasAimOffset" class="optical-axis-reference" opacity="0.4">
          <line :x1="opticalCenterX - 12" :y1="opticalCenterY" :x2="opticalCenterX + 12" :y2="opticalCenterY" stroke="#94a3b8" stroke-width="1" stroke-dasharray="2 2" />
          <line :x1="opticalCenterX" :y1="opticalCenterY - 12" :x2="opticalCenterX" :y2="opticalCenterY + 12" stroke="#94a3b8" stroke-width="1" stroke-dasharray="2 2" />
          <circle :cx="opticalCenterX" :cy="opticalCenterY" r="2.5" fill="#94a3b8" />
          <text :x="opticalCenterX + 6" :y="opticalCenterY - 6" fill="#94a3b8" font-size="9" font-family="monospace">OPTİK</text>
        </g>

        <!-- Kalibre Edilmiş Sarı Namlu Nişangâhı (Ballistics Boresight Reticle) -->
        <g class="nato-hud-reticle">
          <!-- Kalibre Vuruş Merkezi -->
          <circle :cx="centerX" :cy="centerY" r="3" fill="#fde047" />
          <circle :cx="centerX" :cy="centerY" r="9" fill="none" stroke="#f59e0b" stroke-width="1.8" />

          <!-- Sarı Çapraz Nişangâh Çizgileri -->
          <line :x1="centerX - 30" :y1="centerY" :x2="centerX - 9" :y2="centerY" stroke="#f59e0b" stroke-width="2.2" />
          <line :x1="centerX + 9" :y1="centerY" :x2="centerX + 30" :y2="centerY" stroke="#f59e0b" stroke-width="2.2" />
          <line :x1="centerX" :y1="centerY - 30" :x2="centerX" :y2="centerY - 9" stroke="#f59e0b" stroke-width="2.2" />
          <line :x1="centerX" :y1="centerY + 9" :x2="centerX" :y2="centerY + 30" stroke="#f59e0b" stroke-width="2.2" />

          <!-- Dış Halka -->
          <circle :cx="centerX" :cy="centerY" r="26" fill="none" stroke="#38bdf8" stroke-width="1.2" opacity="0.75" />

          <!-- Dikey Yükseliş Çizgileri -->
          <line :x1="centerX - 5" :y1="centerY + 14" :x2="centerX + 5" :y2="centerY + 14" stroke="#f59e0b" stroke-width="1.4" opacity="0.85" />
          <line :x1="centerX - 7" :y1="centerY + 22" :x2="centerX + 7" :y2="centerY + 22" stroke="#f59e0b" stroke-width="1.6" opacity="0.9" />

          <!-- Zero Offset Rozeti -->
          <g v-if="hasAimOffset" opacity="0.95">
            <rect :x="centerX + 14" :y="centerY + 8" width="114" height="20" rx="4" fill="rgba(3,14,26,0.88)" stroke="#f59e0b" stroke-width="1" />
            <text :x="centerX + 20" :y="centerY + 22" fill="#fde047" font-size="10" font-weight="800" font-family="monospace">
              NAMLU: {{ Math.round(centerY - opticalCenterY) > 0 ? `+${Math.round(centerY - opticalCenterY)}` : Math.round(centerY - opticalCenterY) }}px
            </text>
          </g>
        </g>

        <!-- Tracking Vector in AUTO mode -->
        <g v-if="props.operationMode === 'AUTO' && trackedCenterX !== null && trackedCenterY !== null && !serverAlignedOverlayActive" class="tracking-vector-overlay">
          <line :x1="trackedImageCenterX" :y1="trackedImageCenterY" :x2="trackedCenterX" :y2="trackedCenterY" stroke="#a7f3d0" stroke-width="2" stroke-dasharray="8 6" opacity="0.82" />
          <circle :cx="trackedCenterX" :cy="trackedCenterY" r="10" fill="rgba(16,185,129,.15)" stroke="#6ee7b7" stroke-width="2.4" />
          <circle :cx="trackedImageCenterX" :cy="trackedImageCenterY" r="4" fill="#67e8f9" />
        </g>

        <g v-if="props.personSafetyActive">
          <rect :x="props.width * 0.18" :y="props.height * 0.16" :width="props.width * 0.64" :height="props.height * 0.68" fill="rgba(239,68,68,0.11)" stroke="#ef4444" stroke-width="3" stroke-dasharray="12 10" />
          <text :x="props.width * 0.2" :y="props.height * 0.2" fill="#fecaca" font-size="17" font-weight="800">PERSON SAFETY / NO-GO</text>
        </g>

        <g
          v-for="target in detections"
          :key="target.visual_track_key"
          :class="targetSelectable(target) ? 'cursor-pointer' : 'cursor-not-allowed'"
          :opacity="serverAlignedOverlayActive ? 0 : target.visual_live ? (targetSelectable(target) ? 1 : 0.38) : 0.42"
          @click.stop="targetSelectable(target) && emit('selectTarget', target)"
        >
          <rect :x="boxX(target)" :y="boxY(target)" :width="boxW(target)" :height="boxH(target)" :fill="targetFill(target)" :stroke="targetStroke(target)" :stroke-width="targetSelected(target) ? 5 : 3" :stroke-dasharray="target.visual_live ? undefined : '12 9'" />
          <rect
            v-if="targetSelected(target) && trackingDiagnostic?.bbox_lock_enabled !== false"
            :x="trackingLockZone?.x ?? boxX(target) + boxW(target) * 0.35"
            :y="trackingLockZone?.y ?? boxY(target) + boxH(target) * 0.35"
            :width="trackingLockZone?.width ?? boxW(target) * 0.3"
            :height="trackingLockZone?.height ?? boxH(target) * 0.3"
            fill="rgba(34,211,238,0.04)"
            :stroke="props.trackingActive ? '#67e8f9' : 'rgba(103,232,249,.65)'"
            stroke-width="2"
            stroke-dasharray="8 6"
          />
          <circle v-if="showTargetDecoration(target)" :cx="targetCenterX(target)" :cy="targetCenterY(target)" r="6" :fill="target.kind === 'body' ? targetStroke(target) : '#fde047'" />
          <circle v-if="showTargetDecoration(target)" :cx="targetCenterX(target)" :cy="targetCenterY(target)" :r="Math.max(14, Math.min(boxW(target), boxH(target)) / 4)" fill="none" :stroke="target.kind === 'body' ? targetStroke(target) : '#ec4899'" stroke-width="2" stroke-dasharray="5 5" />
          <rect v-if="showTargetDecoration(target)" :x="labelX(target) - 6" :y="labelY(target) - 18" width="350" :height="props.operatorMode ? 27 : 42" rx="5" fill="rgba(0,0,0,0.72)" stroke="rgba(245,158,11,0.55)" />
          <text v-if="showTargetDecoration(target)" :x="labelX(target)" :y="labelY(target)" :fill="target.kind === 'body' ? '#fecaca' : '#fde68a'" font-size="13" font-weight="800" textLength="325" lengthAdjust="spacingAndGlyphs">
            {{ targetLabel(target) }}
          </text>
          <text v-if="!props.operatorMode && showTargetDecoration(target)" :x="labelX(target)" :y="labelY(target) + 17" fill="#bfdbfe" font-size="11" font-weight="700" textLength="290" lengthAdjust="spacingAndGlyphs">
            {{ target.kind === 'body' ? targetDisplayDescription({ className: target.class_name, team: target.target_team }) : 'Hedef Balonu' }} · x_norm={{ (target.center_x / detectionFrameWidth).toFixed(2) }} · y_norm={{ (target.center_y / detectionFrameHeight).toFixed(2) }}
          </text>
        </g>
      </svg>

      <div v-if="!props.operatorMode" class="absolute right-4 top-4 z-10 max-w-[270px] rounded-md border border-cyan-300/24 bg-black/58 px-3 py-2 text-xs font-semibold text-cyan-100">
        {{ props.operatorMode ? (browserPreviewActive || backendFrameActive ? 'Kamera önizlemesi aktif' : 'Kamera bekleniyor') : props.ktrDemoMode ? 'KTR fixture view · no live target claim' : browserPreviewActive ? 'Browser laptop camera · local dev only' : backendFrameActive ? 'Laptop dev frame · not USB acceptance' : 'Offline fixture view' }}
        <div class="mt-1 text-[11px]" :class="props.perceptionEnabled ? 'text-emerald-200' : 'text-amber-200'">{{ perceptionStatusLabel }}</div>
        <div v-if="props.perceptionEnabled && props.detectionRuntimeReady === false && props.detectionRuntimeDetail" class="mt-1 text-[11px] text-amber-200">{{ props.detectionRuntimeDetail }}</div>
      </div>

      <div v-if="liveFrameVisible" class="absolute left-4 top-4 z-10 rounded-md border border-emerald-300/25 bg-black/65 px-3 py-2.5 font-mono text-[11px] text-emerald-100 shadow-lg">
        <div class="mb-1 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">LIVE PERFORMANCE</div>
        <div class="grid grid-cols-2 gap-x-4 gap-y-1">
          <span>CAM</span><b>{{ metricNumber(measuredCameraFps) }} FPS</b>
          <span>DETECT</span><b>{{ metricNumber(measuredDetectorFps) }} FPS</b>
          <span>LATENCY</span><b>{{ metricNumber(measuredInferenceMs) }} ms</b>
          <span>MODELS</span><b>{{ measuredModelCount }} · {{ measuredModelExecution }}</b>
        </div>
      </div>

      <div v-if="props.operationMode === 'AUTO'" class="tracking-diagnostics" role="status" aria-live="polite">
        <div class="tracking-diagnostics-title"><span>AUTO TRACK</span><b>{{ trackingDiagnostic?.state ?? (props.trackingActive ? 'SEARCHING' : 'PAUSED') }}</b></div>
        <div class="tracking-diagnostics-grid">
          <span>MOD</span><b>{{ trackingDiagnostic?.controller_mode ?? 'GENERAL' }}</b>
          <span>IMAGE</span><b>{{ Math.round(trackingDiagnostic?.frame_center_x ?? centerX) }}, {{ Math.round(trackingDiagnostic?.frame_center_y ?? centerY) }}</b>
          <span>OBJECT</span><b>{{ trackingDiagnostic?.target_center_x === null || trackingDiagnostic?.target_center_x === undefined ? '—' : `${Math.round(trackingDiagnostic.target_center_x)}, ${Math.round(trackingDiagnostic.target_center_y ?? 0)}` }}</b>
          <span>ERROR</span><b>{{ Math.round(trackingDiagnostic?.error_x_px ?? 0) }} / {{ Math.round(trackingDiagnostic?.error_y_px ?? 0) }} px</b>
          <span>TRACK ID</span><b>{{ trackingDiagnostic?.selected_track_id ?? '—' }}</b>
          <span>AGE</span><b>{{ trackingAgeLabel }} · {{ trackingDiagnostic?.selected_track_age_frames ?? 0 }}f</b>
          <span>CONF</span><b>{{ trackingDiagnostic?.selected_target_confidence === null || trackingDiagnostic?.selected_target_confidence === undefined ? '—' : `${Math.round(trackingDiagnostic.selected_target_confidence * 100)}%` }}</b>
          <span>INPUT</span><b>{{ trackingMeasurementLabel }}</b>
          <span>MOMENTUM</span><b>{{ trackingDiagnostic?.momentum_lock_active ? `${Math.round(trackingDiagnostic.target_velocity_x_px_s)} / ${Math.round(trackingDiagnostic.target_velocity_y_px_s)} px/s` : 'DURAĞAN' }}</b>
          <span>ID SWITCH</span><b>{{ props.trackingIdSwitchCount ?? 0 }}</b>
        </div>
        <div v-if="trackingDiagnostic?.selected_track_occluded" class="tracking-occluded">KISA KAYIP · KİMLİK KORUNUYOR · AKTÜASYON YOK</div>
      </div>

      <div class="magazine-hud" :class="[`magazine-${magazineTone}`, { 'operator-magazine': props.operatorMode }]" role="status" :aria-label="`Şarjör ${magazineRemaining}/${magazineCapacity}`">
        <div class="magazine-hud-header">
          <div class="magazine-title-block"><span class="magazine-kicker">ŞARJÖR</span><strong>{{ magazineRemaining }}/{{ magazineCapacity }}</strong></div>
          <button type="button" class="magazine-reload-button" :disabled="props.magazineResetBusy" @click.stop="emit('reloadMagazine')"><RefreshCw :size="12" :class="{ 'animate-spin': props.magazineResetBusy }" />{{ props.magazineResetBusy ? 'DOLDURULUYOR' : 'DOLDUR' }}</button>
        </div>
        <div class="magazine-rounds" aria-hidden="true"><span v-for="round in magazineCapacity" :key="round" class="magazine-round" :class="{ spent: round > magazineRemaining }"></span></div>
        <div class="magazine-hud-footer"><span>{{ magazineEmpty ? 'MERMİ YOK · FIRE ENGELLİ' : magazineLow ? 'SON MERMİLER' : 'ATIŞ HAZIR' }}</span><span>8'Lİ</span></div>
      </div>

      <div v-if="!props.operatorMode" class="absolute right-4 top-[86px] z-10 w-[142px] rounded-md border border-cyan-300/18 bg-black/55 p-2 font-mono text-[10px] text-cyan-100">
        <div class="mb-1 text-[9px] font-bold uppercase tracking-[0.16em] text-slate-400">HUD TELEMETRY</div>
        <div class="flex justify-between"><span>FOV</span><b>78/48</b></div>
        <div class="flex justify-between"><span>SRC</span><b>{{ cleanSource }}</b></div>
        <div class="flex justify-between"><span>TRUTH</span><b>{{ cleanTruth }}</b></div>
        <div class="flex justify-between"><span>YOLO</span><b>{{ props.perceptionEnabled ? 'ON' : 'OFF' }}</b></div>
        <div class="flex justify-between"><span>CONF</span><b>{{ detections[0] ? Math.round(detections[0].confidence * 100) : 0 }}%</b></div>
        <div class="flex justify-between"><span>SAFETY</span><b>{{ props.personSafetyActive ? 'BLOCK' : props.personSafetyAvailable ? 'MON' : 'N/A' }}</b></div>
        <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800"><div class="h-full w-2/3 bg-cyan-300"></div></div>
      </div>

      <div v-if="!props.operatorMode" class="absolute bottom-4 left-4 flex max-w-[calc(100%-2rem)] flex-wrap gap-2 rounded-md border border-cyan-300/16 bg-black/58 p-2 font-mono text-[10px] text-cyan-100">
        <span>source: {{ cleanSource }}</span>
        <span>truth: {{ evidenceTruth }}</span>
        <span>{{ props.perceptionEnabled ? 'YOLO ON' : 'YOLO OFF - camera only' }}</span>
        <span>age: {{ props.frameAgeMs ?? 'n/a' }}ms</span>
        <span>stream: {{ backendStreamConnected ? 'live' : backendStreamError ?? 'connecting' }}</span>
        <span>person check: {{ props.personSafetyActive ? 'blocked' : props.personSafetyAvailable ? 'monitored' : 'N/A' }}</span>
        <span class="sr-only">person_check={{ props.personSafetyActive ? 'blocked' : props.personSafetyAvailable ? 'monitored' : 'unavailable' }}</span>
        <span>no physical command</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.camera-panel {
  box-shadow: 0 0 42px rgba(34, 211, 238, 0.08), inset 0 0 0 1px rgba(34, 211, 238, 0.04);
}
.tracking-diagnostics{position:absolute;z-index:13;left:16px;bottom:16px;width:min(260px,calc(100% - 2rem));border:1px solid rgba(110,231,183,.34);border-radius:10px;background:rgba(2,10,18,.84);box-shadow:0 12px 34px rgba(0,0,0,.34);padding:9px 10px;color:#d1fae5;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.64rem;backdrop-filter:blur(7px)}
.tracking-diagnostics-title{display:flex;justify-content:space-between;gap:8px;margin-bottom:6px;color:#6ee7b7;font-weight:900;letter-spacing:.12em}.tracking-diagnostics-title b{color:#e0f2fe}.tracking-diagnostics-grid{display:grid;grid-template-columns:auto 1fr;gap:3px 10px}.tracking-diagnostics-grid span{color:#7893a3}.tracking-diagnostics-grid b{text-align:right;color:#dffbff}.tracking-occluded{margin-top:6px;border-top:1px solid rgba(251,191,36,.22);padding-top:5px;color:#fde68a;font-weight:800}

.camera-panel :deep(img),
.camera-panel :deep(video),
.camera-panel svg {
  display: block;
}

.header-icon-button{display:grid;place-items:center;width:31px;height:31px;border:1px solid rgba(103,232,249,.24);border-radius:9px;background:rgba(8,47,73,.48);color:#c9f7ff;cursor:pointer;transition:.16s}.header-icon-button:hover{border-color:rgba(103,232,249,.58);background:rgba(8,74,99,.64);transform:translateY(-1px)}

.camera-select-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid rgba(103, 232, 249, 0.1);
  border-bottom: 1px solid rgba(103, 232, 249, 0.1);
  background: rgba(2, 6, 23, 0.72);
  padding: 8px 12px;
  font-size: 0.72rem;
  color: #cbd5e1;
}

.camera-select-strip label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #67e8f9;
}

.camera-select-strip select {
  min-width: 220px;
  max-width: min(420px, 70vw);
  border: 1px solid rgba(103, 232, 249, 0.24);
  border-radius: 7px;
  background: rgba(15, 23, 42, 0.92);
  padding: 6px 8px;
  color: #f8fafc;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: none;
}

.camera-select-strip button {
  border: 1px solid rgba(103, 232, 249, 0.26);
  border-radius: 7px;
  background: rgba(8, 47, 73, 0.56);
  padding: 6px 9px;
  color: #cffafe;
  font-weight: 800;
}

.magazine-hud{position:absolute;z-index:12;top:92px;left:16px;width:min(226px,calc(100% - 2rem));border:1px solid rgba(103,232,249,.28);border-radius:11px;background:linear-gradient(145deg,rgba(3,18,31,.92),rgba(2,8,18,.84));box-shadow:0 12px 28px rgba(0,0,0,.28),inset 0 0 18px rgba(34,211,238,.04);padding:8px 9px 7px;color:#d9faff;backdrop-filter:blur(7px)}
.magazine-hud.operator-magazine{top:14px;right:14px;left:auto;width:min(214px,calc(100% - 2rem))}
.magazine-hud.magazine-low{border-color:rgba(251,191,36,.46)}.magazine-hud.magazine-empty{border-color:rgba(248,113,113,.62);background:linear-gradient(145deg,rgba(69,10,10,.88),rgba(20,6,12,.9));box-shadow:0 12px 28px rgba(127,29,29,.24),inset 0 0 18px rgba(248,113,113,.07)}
.magazine-hud-header,.magazine-hud-footer{display:flex;align-items:center;justify-content:space-between;gap:8px}.magazine-title-block{display:flex;align-items:baseline;gap:8px}.magazine-kicker{color:#8da9b9;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.61rem;font-weight:900;letter-spacing:.16em}.magazine-title-block strong{color:#dffbff;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86rem;letter-spacing:.05em}.magazine-empty .magazine-title-block strong,.magazine-empty .magazine-hud-footer span:first-child{color:#fecaca}.magazine-low .magazine-title-block strong,.magazine-low .magazine-hud-footer span:first-child{color:#fde68a}
.magazine-reload-button{display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(103,232,249,.25);border-radius:6px;background:rgba(8,47,73,.5);padding:4px 6px;color:#c9f7ff;font-size:.58rem;font-weight:900;letter-spacing:.06em;cursor:pointer}.magazine-reload-button:hover{border-color:rgba(103,232,249,.62);background:rgba(8,74,99,.66);transform:translateY(-1px)}.magazine-reload-button:disabled{cursor:wait;opacity:.65}.magazine-rounds{display:flex;align-items:flex-end;justify-content:flex-start;gap:6px;margin:8px 0 6px}.magazine-round{position:relative;display:block;width:15px;height:24px;flex:none;border:1px solid rgba(134,239,172,.44);border-radius:8px 8px 3px 3px;background:linear-gradient(90deg,#15803d 0 22%,#86efac 23% 68%,#22c55e 69%);box-shadow:0 0 8px rgba(34,197,94,.24)}.magazine-round:before{position:absolute;top:-3px;left:3px;width:7px;height:5px;border-radius:50% 50% 2px 2px;background:#bbf7d0;content:''}.magazine-round.spent{border-color:rgba(148,163,184,.14);background:rgba(51,65,85,.36);box-shadow:none}.magazine-round.spent:before{background:rgba(100,116,139,.4)}.magazine-empty .magazine-round:not(.spent){border-color:rgba(252,165,165,.4);background:linear-gradient(180deg,#fca5a5,#ef4444)}.magazine-hud-footer{color:#8da9b9;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.58rem;font-weight:800;letter-spacing:.06em}.magazine-hud-footer span:last-child{color:#dffbff}

/* Sniper Scope Dairesel Büyüteç (Zoom Lens) Stilleri */
.sniper-scope-container {
  position: absolute;
  pointer-events: none;
  z-index: 25;
  border-radius: 50%;
  box-shadow:
    0 0 0 2.5px #38bdf8,
    0 0 30px rgba(56, 189, 248, 0.45),
    inset 0 0 40px rgba(0, 0, 0, 0.85);
  overflow: hidden;
  backdrop-filter: contrast(1.1) brightness(1.05);
}

.sniper-scope-lens {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  overflow: hidden;
}

.sniper-scope-media {
  position: absolute;
  pointer-events: none;
  max-width: none !important;
  max-height: none !important;
}

.sniper-scope-glass {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  pointer-events: none;
  background: radial-gradient(circle, transparent 50%, rgba(6, 18, 30, 0.35) 80%, rgba(0, 0, 0, 0.75) 100%);
}

.sniper-scope-reticle {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.sniper-scope-badge {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  padding: 2px 10px;
  background: rgba(3, 14, 26, 0.92);
  border: 1px solid rgba(56, 189, 248, 0.6);
  border-radius: 9999px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #38bdf8;
  white-space: nowrap;
  pointer-events: none;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.75);
}

.sniper-scope-badge.top-badge {
  top: 14px;
}

.sniper-scope-badge.bottom-badge {
  bottom: 14px;
  font-size: 9px;
  color: #6ee7b7;
  border-color: rgba(110, 231, 183, 0.5);
}
</style>
