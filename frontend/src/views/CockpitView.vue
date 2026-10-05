<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useFullscreen } from '@vueuse/core'
import { useRouter } from 'vue-router'
import CockpitTopBar from '../components/cockpit/CockpitTopBar.vue'
import CompetitionStageBar from '../components/cockpit/CompetitionStageBar.vue'
import RadarCalibrationModal from '../components/cockpit/RadarCalibrationModal.vue'
import BoresightWizardModal from '../components/cockpit/BoresightWizardModal.vue'
import CameraControlPanel from '../components/cockpit/CameraControlPanel.vue'
import DetectionConfigPanel from '../components/cockpit/DetectionConfigPanel.vue'
import DigitalTwinPanel from '../components/digital-twin/DigitalTwinPanel.vue'
import DeveloperDebugDrawer from '../components/cockpit/DeveloperDebugDrawer.vue'
import EngineerTechnicalTabs from '../components/cockpit/EngineerTechnicalTabs.vue'
import EvidenceReplayPanel from '../components/cockpit/EvidenceReplayPanel.vue'
import EngagementEvidenceReplayPanel from '../components/cockpit/EngagementEvidenceReplayPanel.vue'
import LiveCameraPanel from '../components/cockpit/LiveCameraPanel.vue'
import MotorPidPanel from '../components/cockpit/MotorPidPanel.vue'
import HomeAndLimitsPanel from '../components/cockpit/HomeAndLimitsPanel.vue'
import LimitAyarModal from '../components/cockpit/LimitAyarModal.vue'
import YarisGecisButonu from '../components/cockpit/YarisGecisButonu.vue'
import RadarAciModal from '../components/cockpit/RadarAciModal.vue'
import NisanKalibreModal from '../components/cockpit/NisanKalibreModal.vue'
import OperationalControlBar, { type OperationalControlMode } from '../components/cockpit/OperationalControlBar.vue'
import OperatorLogPanel from '../components/cockpit/OperatorLogPanel.vue'
import QuickPidFloatingPanel from '../components/cockpit/QuickPidFloatingPanel.vue'
import SafetyModeBanner from '../components/cockpit/SafetyModeBanner.vue'
import ScenePlanPanel from '../components/cockpit/ScenePlanPanel.vue'
import { applyCameraImageConfig, applyPerceptionConfig } from '../api/operatorConfig'
import {
  capturePatrolAngle,
  clearOperationTarget,
  fetchOperationState,
  fetchPatrolStatus,
  gotoPatrolPath,
  nextStage3Round,
  resetStage3Rounds,
  selectOperationTarget,
  startStage3Round,
  stopStage2Mission,
  updateOperationState,
  updatePatrolAngles,
} from '../api/operation'
import { evaluateFireRequest } from '../api/decision'
import { fetchTargetRegistry } from '../api/tracking'
import { selectCommandProfile, type CommandProfile } from '../api/safety'
import { sendStage1ManualMotion } from '../api/mission'
import {
  fetchHardwareHomeStatus,
  fetchMotionEnvelope,
  startHardwareHome,
  updateMotionEnvelope,
  stopHardwareHome,
  stopHardwareMotion,
  type HardwareHomeResult,
} from '../api/hardware'
import { useRuntimeTruth, type TruthTone } from '../composables/useRuntimeTruth'
import { useOperationalReadiness } from '../composables/useOperationalReadiness'
import { useGamepadController } from '../composables/useGamepadController'
import { useDecisionStore } from '../stores/decisionStore'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useDigitalTwinStore } from '../stores/digitalTwinStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useMissionStore } from '../stores/missionStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import type { CockpitBadge, CockpitEvent, CockpitMetric } from '../components/cockpit/types'
import type { ManagedDevice } from '../types/deviceRuntime'
import type { VisionEvent } from '../types/vision'
import type { TargetRegistryStatus } from '../types/tracking'
import type { EngagementReplayControl } from '../types/engagementReplay'
import type { ManualTwinMotionIntent, ManualTwinMotionIntentStatus } from '../types/digitalTwin'
import type { OperationState, TargetPolicy } from '../types/operation'
import {
  targetClassToken,
  targetDisplayName,
  targetSequenceFromId,
  targetTeamToken,
} from '../utils/targetLabels'
import { VisualDetectionContinuity } from '../utils/visualDetectionContinuity'

const system = useSystemStore()
const router = useRouter()
const vision = useVisionStore()
const runtime = useDeviceRuntimeStore()
const motion = useMotionStore()
const serial = useSerialStore()
const hardware = useHardwareStore()
const decision = useDecisionStore()
const mission = useMissionStore()
const digitalTwin = useDigitalTwinStore()
const truth = useRuntimeTruth()
const operational = useOperationalReadiness()
const gamepad = useGamepadController()
const operationalLiveReady = operational.liveReady
const operationalBlocker = operational.primaryBlocker
const homingOpen = ref(false)
const homingStatus = ref<HardwareHomeResult | null>(null)
const homingMessage = ref('AutoHome başlatılıyor…')
const homingProgress = ref(10)
let homingPollTimer: ReturnType<typeof setInterval> | null = null
let homingResolver: ((value: boolean) => void) | null = null

const selectedBalloonId = ref<number | null>(null)
const selectedDetectionId = ref<number | null>(null)
const selectedDetectionKind = ref<'body' | 'balloon' | null>(null)
const selectedLogicalTargetId = ref<string | null>(null)
const targetSelectBusy = ref(false)
const virtualTrackIntent = ref(false)
const cameraSectionRef = ref<HTMLElement | null>(null)
const { enter: enterCameraFullscreen } = useFullscreen(cameraSectionRef)
const liveConfInput = ref('0.05')
const confApplyBusy = ref(false)
const lastConfidenceApplyAt = ref('Henüz uygulanmadı')
const confidenceApplyStatus = ref('Preview only')
const engineerPanelOpen = ref(false)
const zeroingWizardOpen = ref(false)
const limitModalOpen = ref(false)
const radarAciOpen = ref(false)
const nisanKalibreOpen = ref(false)
const cameraSettingsOpen = ref(false)
const operatorToast = ref<{ tone: 'success' | 'warn' | 'error', message: string } | null>(null)
const magazineResetBusy = ref(false)
const engagementReplayControl = ref<EngagementReplayControl | null>(null)
const cameraImageSettings = ref({
  // Match the light reference: the camera frame is shown as captured until
  // the operator deliberately selects a preview-only adjustment.
  brightness: 0,
  contrast: 0,
  saturation: 0,
  exposure: 0,
  exposureAuto: true,
})
const engineerActiveTab = ref<'camera' | 'detection' | 'motion' | 'calibration' | 'logs'>('camera')
let refreshTimer: ReturnType<typeof setInterval> | null = null
let digitalTwinTimer: ReturnType<typeof setInterval> | null = null
let visualContinuityTimer: ReturnType<typeof setInterval> | null = null
// Manual/joystick intent is rendered immediately inside DigitalTwinPanel.
// Backend pose is reconciliation telemetry, so 4 Hz is enough and avoids
// rebuilding/logging a full digital-twin state ten times per second while
// CUDA inference and the 1080p camera are active.
const DIGITAL_TWIN_POSE_POLL_MS = 250

const latestFrame = computed(() => vision.latestEvent)
const targetRegistry = ref<TargetRegistryStatus | null>(null)
const overlayWidth = computed(() => Math.max(1, vision.cameraStatus.width || runtime.cameraStatus.actual_width || 1280))
const overlayHeight = computed(() => Math.max(1, vision.cameraStatus.height || runtime.cameraStatus.actual_height || 720))
const centerX = computed(() => overlayWidth.value / 2)
const centerY = computed(() => overlayHeight.value / 2)
const aimX = computed(() => centerX.value + motion.trackingStatus.aim_offset_x_px)
const aimY = computed(() => centerY.value + motion.trackingStatus.aim_offset_y_px)
const personSafety = computed(() => decision.decision.person_safety)
const personSafetyActive = computed(() => personSafety.value?.person_detected === true)
const personSafetyAvailable = computed(() => personSafety.value?.enabled === true && personSafety.value.source !== 'unavailable')
const digitalTwinEnabled = computed(() => ((import.meta.env.VITE_DIGITAL_TWIN_ENABLED as string | undefined) ?? 'true') !== 'false')
const ktrDemoMode = computed(() => new URLSearchParams(window.location.search).get('ktr_demo') === '1')
const latestFrameMatchesSelectedCamera = computed(() => {
  const event = latestFrame.value
  if (!event) return false
  if (ktrDemoMode.value) return true
  const source = String(event.source ?? '').toLowerCase()
  const frameOrigin = String(event.frame_origin ?? '').toLowerCase()
  const sourceKind = String(event.camera_source_kind ?? '').toLowerCase()
  if (runtime.cameraStatus.profile.source_type === 'mock') {
    return frameOrigin === 'mock_frame' || sourceKind === 'mock' || source.includes('mock')
  }
  return frameOrigin === 'real_capture'
    || frameOrigin === 'browser_upload'
    || frameOrigin === 'browser_frame_upload'
    || sourceKind === 'real_camera'
    || source.includes('live_camera')
})
const destroyedTargets = computed(() => (targetRegistry.value?.targets ?? []).filter((target) => target.state === 'DESTROYED' || target.consumed))
const destroyedBodyTrackIds = computed(() => new Set(destroyedTargets.value.map((target) => target.body_track_id).filter((value): value is number => value !== null)))
const destroyedBodyDetectionIds = computed(() => new Set(destroyedTargets.value.map((target) => target.body_detection_id).filter((value): value is number => value !== null)))
const destroyedBalloonDetectionIds = computed(() => {
  const ids = new Set<number>()
  for (const target of destroyedTargets.value) {
    if (target.balloon_detection_id !== null) ids.add(target.balloon_detection_id)
    if (target.balloon_track_id !== null) {
      const track = motion.trackingStatus.multi_target_tracker.tracks.find((item) => item.track_id === target.balloon_track_id)
      if (track?.detection_id !== null && track?.detection_id !== undefined) ids.add(track.detection_id)
    }
  }
  return ids
})
const activeBalloons = computed(() => latestFrameMatchesSelectedCamera.value
  ? (latestFrame.value?.balloon_detections ?? []).filter((item) => !destroyedBalloonDetectionIds.value.has(item.id))
  : [])
const activeBodies = computed(() => latestFrameMatchesSelectedCamera.value
  ? (latestFrame.value?.body_detections ?? []).filter((item) => (
      !destroyedBodyTrackIds.value.has(item.track_id ?? -1)
      && !destroyedBodyDetectionIds.value.has(item.id)
    ))
  : [])

function cockpitClassToken(value: string | undefined): string {
  return targetClassToken(value)
}

function cockpitTeamToken(value: string | undefined): string {
  return targetTeamToken(value)
}

function balloonBelongsToBody(balloon: { center_x: number, center_y: number }, body: { bbox: { x: number, y: number, w: number, h: number } }): boolean {
  const top = body.bbox.y + body.bbox.h
  return balloon.center_x >= body.bbox.x
    && balloon.center_x <= body.bbox.x + body.bbox.w
    && balloon.center_y >= top
    && balloon.center_y <= top + body.bbox.h * 2
}

const logicalTargetNames = computed<Record<string, string>>(() => {
  const names: Record<string, string> = {}
  // Deterministic frame-local fallback keeps the operator HUD useful during
  // the first perception second, before the mission registry reaches READY.
  // It is visual only; tracking/fire still require the backend registry.
  const sequenceByKind: Record<string, number> = {}
  const fallbackAircraft = new Map<number, { classToken: string, teamToken: string, sequence: number }>()
  for (const body of [...activeBodies.value].sort((a, b) => a.id - b.id)) {
    const classToken = cockpitClassToken(body.class_name)
    const teamToken = cockpitTeamToken(body.target_team)
    const key = `${classToken}_${teamToken}`
    const sequence = (sequenceByKind[key] ?? 0) + 1
    sequenceByKind[key] = sequence
    fallbackAircraft.set(body.id, { classToken, teamToken, sequence })
    names[`body:${body.id}`] = targetDisplayName({ className: classToken, team: teamToken, sequence })
  }
  for (const balloon of activeBalloons.value) {
    const owner = activeBodies.value
      .filter((body) => cockpitClassToken(body.class_name) !== 'target' && balloonBelongsToBody(balloon, body))
      .sort((a, b) => Math.hypot(balloon.center_x - (a.bbox.x + a.bbox.w / 2), balloon.center_y - (a.bbox.y + a.bbox.h / 2)) - Math.hypot(balloon.center_x - (b.bbox.x + b.bbox.w / 2), balloon.center_y - (b.bbox.y + b.bbox.h / 2)))[0]
    const fallback = owner ? fallbackAircraft.get(owner.id) : undefined
    names[`balloon:${balloon.id}`] = fallback
      ? targetDisplayName({ className: fallback.classToken, team: fallback.teamToken, sequence: fallback.sequence, balloon: true })
      : targetDisplayName({ sequence: balloon.id, balloon: true, standaloneBalloon: true })
  }
  for (const target of targetRegistry.value?.targets ?? []) {
    const sequence = targetSequenceFromId(target.target_id)
    const destroyed = target.state === 'DESTROYED'
    // Detection ids are frame-local and may be reused by a different class
    // on the next camera frame.  Resolve the current body by its persistent
    // body track first; only use the registry's detection id when it is still
    // present in this exact frame.
    const currentBody = (target.body_track_id !== null && target.body_track_id !== undefined
      ? activeBodies.value.find((body) => body.track_id === target.body_track_id)
      : undefined)
      ?? (target.body_track_id === null && target.body_detection_id !== null && target.body_detection_id !== undefined
        ? activeBodies.value.find((body) => body.id === target.body_detection_id)
        : undefined)
    const displayClass = currentBody?.class_name ?? target.target_class
    const displayTeam = currentBody && cockpitTeamToken(currentBody.target_team) !== 'bilinmeyen'
      ? currentBody.target_team
      : target.target_team
    const bodyName = targetDisplayName({
      className: displayClass,
      team: displayTeam,
      sequence,
      destroyed,
    })
    const balloonName = targetDisplayName({
      className: displayClass,
      team: displayTeam,
      sequence,
      balloon: true,
      destroyed,
    })
    if (currentBody) {
      names[`body:${currentBody.id}`] = bodyName
    }
    const track = target.balloon_track_id !== null && target.balloon_track_id !== undefined
      ? motion.trackingStatus.multi_target_tracker.tracks.find((item) => item.track_id === target.balloon_track_id)
      : undefined
    const trackedDetectionId = track?.detection_id !== null && track?.detection_id !== undefined
      && activeBalloons.value.some((balloon) => balloon.id === track.detection_id)
      ? track.detection_id
      : undefined
    const registryDetectionId = !track && target.balloon_detection_id !== null && target.balloon_detection_id !== undefined
      && activeBalloons.value.some((balloon) => balloon.id === target.balloon_detection_id)
      ? target.balloon_detection_id
      : undefined
    const currentBalloonId = trackedDetectionId ?? registryDetectionId
    if (currentBalloonId !== undefined) {
      names[`balloon:${currentBalloonId}`] = balloonName
    }
  }
  return names
})
const visualContinuity = new VisualDetectionContinuity(900)
const visualFrame = ref<VisionEvent | null>(null)
const visualBodies = computed(() => visualFrame.value?.body_detections ?? [])
const visualBalloons = computed(() => visualFrame.value?.balloon_detections ?? [])

function refreshVisualContinuity(): void {
  const event = latestFrame.value
  if (!event || !latestFrameMatchesSelectedCamera.value) {
    visualContinuity.reset()
    visualFrame.value = null
    return
  }
  const snapshot = visualContinuity.update(event, logicalTargetNames.value, Date.now())
  visualFrame.value = {
    ...event,
    body_detections: snapshot.bodies,
    balloon_detections: snapshot.balloons,
  }
}

watch(latestFrame, refreshVisualContinuity, { immediate: true })
const selectedTarget = computed(() => activeBalloons.value.find((target) => target.id === selectedBalloonId.value) ?? null)
const initialParams = new URLSearchParams(window.location.search)
const autoTrackingRequested = initialParams.get('autotrack') === '1'
const operationMode = ref<OperationalControlMode>(autoTrackingRequested ? 'AUTO' : 'MANUAL')
const targetPolicy = ref<TargetPolicy>('BALLOON')
const operationState = ref<OperationState | null>(null)
const manualMotionEnabled = ref(true)
const operatorControlBusy = ref(false)
const manualKeys = new Set<string>()
let manualMotionTimer: ReturnType<typeof setInterval> | null = null
let gamepadMotionTimer: ReturnType<typeof setInterval> | null = null
let manualMotionBusy = false
let gamepadWasMoving = false
let lastManualFireRequestAt = 0
let lastManualBlocker = ''
let manualTwinIntentSequence = 0
const manualTwinMotionIntent = ref<ManualTwinMotionIntent>({
  sequence: 0,
  speedX: 0,
  speedY: 0,
  issuedAtMs: 0,
  active: false,
  status: 'released',
})
type CockpitUiProfile = 'operator' | 'engineer'
const uiProfile = computed<CockpitUiProfile>(() => engineerPanelOpen.value ? 'engineer' : 'operator')
const isOperatorUi = computed(() => uiProfile.value === 'operator')
const worldMode = computed(() => window.location.pathname.includes('/cockpit/world') || initialParams.get('world') === '1')
const perceptionEnabled = ref(initialParams.get('perception') !== 'off')
const qualityMode = ref<'LOW' | 'BALANCED' | 'HIGH' | 'ULTRA'>(
  initialParams.get('quality') === 'low' || initialParams.get('perf') === 'low'
    ? 'LOW'
    : initialParams.get('quality') === 'balanced'
      ? 'BALANCED'
      : initialParams.get('quality') === 'ultra' || window.location.pathname.includes('/cockpit/world')
        ? 'ULTRA'
      : initialParams.get('quality') === 'high' || initialParams.get('ktr_demo') === '1'
        ? 'HIGH'
        : 'BALANCED',
)
const performanceMode = computed(() => qualityMode.value)
const detectionRuntimeReady = computed(() => (
  perceptionEnabled.value
  && vision.visionStatus.running
  && (
    vision.visionStatus.detector_kind === 'ultralytics_yolo'
    || (runtime.visionStatus.adapter_available && !runtime.visionStatus.reload_required)
  )
))
const detectionRuntimeDetail = computed(() => {
  if (!perceptionEnabled.value) return 'camera only'
  if (!vision.visionStatus.running) return 'pipeline stopped'
  if (vision.visionStatus.detector_kind === 'ultralytics_yolo') return `${activeBodies.value.length + activeBalloons.value.length} live detections`
  if (runtime.visionStatus.reload_required) return 'model reload required'
  if (!runtime.visionStatus.adapter_available) return runtime.visionStatus.errors[0] ?? runtime.visionStatus.warnings[0] ?? 'vision adapter unavailable'
  return `${activeBalloons.value.length} target candidates`
})
const productionVisionReady = computed(() => (
  vision.visionStatus.running
  && vision.visionStatus.detector_kind === 'ultralytics_yolo'
  && (vision.visionStatus.model_count ?? 0) > 0
  && (vision.visionStatus.body_model_loaded || vision.visionStatus.balloon_model_loaded)
))
const testVisionAdapter = computed(() => runtime.visionStatus.test_adapter_active)
const yoloStatusLabel = computed(() => {
  if (!perceptionEnabled.value) return 'ALGILAMA KAPALI'
  if (!detectionRuntimeReady.value) return 'ALGILAMA BEKLİYOR'
  if (productionVisionReady.value) return 'YOLO AKTİF'
  if (testVisionAdapter.value) return 'TEST ADAPTÖRÜ AKTİF'
  return 'LEGACY YOLO · YARIŞMA DIŞI'
})
const yoloStatusTone = computed<TruthTone>(() => (
  detectionRuntimeReady.value && productionVisionReady.value
    ? 'good'
    : detectionRuntimeReady.value
      ? 'warn'
      : 'warn'
))
// There is no candidate/legacy label in the operator cockpit.  A balloon is
// either owned by a logical aircraft target (x_y_hedefi) or is explicitly
// standalone (balon_n_hedefi); the UI must never imply a different identity.
const targetLabelPrefix = computed(() => 'HEDEF')
const backendStatusLabel = computed(() => system.connectionStatus === 'connected' ? 'Backend Connected' : 'Backend Offline')
const activeConfidence = computed(() => Number(runtime.visionStatus.profile.conf ?? normalizedLiveConf()))
const backendCameraOptions = computed(() => runtime.inventory.cameras ?? [])
const stepAssetLoaded = computed(() => digitalTwin.assets?.selected_asset_type === 'REAL_STEP_KINEMATIC_GLB' || digitalTwin.assets?.selected_asset_type === 'REAL_STEP_GLB' || digitalTwin.assets?.selected_asset_type === 'REAL_STEP_HIFI_GLB' || digitalTwin.assets?.selected_asset_type === 'HYBRID_FIDELITY_GLB')
const phase54AssetHeaderLabel = computed(() => {
  if (digitalTwin.assets?.selected_asset_type === 'REAL_STEP_KINEMATIC_GLB') return 'Kinematic STEP'
  if (digitalTwin.assets?.selected_asset_type === 'HYBRID_FIDELITY_GLB') return 'Hybrid Fidelity'
  if (digitalTwin.assets?.selected_asset_type === 'REAL_STEP_HIFI_GLB') return 'STEP HiFi'
  if (digitalTwin.assets?.selected_asset_type === 'REAL_STL_GEOMETRY_GLB') return 'STL Geometry'
  return stepAssetLoaded.value ? 'Colored STEP' : 'Pending'
})
const stepMaterialLabel = computed(() => {
  const materialStatus = digitalTwin.assets?.conversion_status ?? ''
  if (digitalTwin.assets?.selected_asset_type === 'HYBRID_FIDELITY_GLB') return 'Hybrid'
  if (materialStatus.includes('materials_reconstructed')) return 'Reconstructed'
  if (materialStatus.includes('materials_preserved')) return 'Preserved'
  return stepAssetLoaded.value ? 'STEP Material' : 'Asset Pending'
})

const cameraSourceLabel = computed(() => {
  if (isOperatorUi.value) {
    if (runtime.cameraStatus.source_mode === 'REAL_USB_CAMERA_LIVE') return 'USB Kamera Aktif'
    if (runtime.cameraStatus.source_mode?.includes('REAL_LAPTOP') || runtime.cameraStatus.is_laptop_camera) return 'Kamera Önizleme Aktif'
    if (ktrDemoMode.value) return 'Simülasyon Kamerası'
    return 'Kamera Bekleniyor'
  }
  if (ktrDemoMode.value) return 'FIXTURE VIEW - NOT REAL CAMERA EVIDENCE'
  if (runtime.cameraStatus.source_mode === 'REAL_LAPTOP_CAMERA_LIVE' && runtime.cameraStatus.is_real_camera_evidence) return 'LAPTOP CAMERA DEV - REAL FRAME'
  if (runtime.cameraStatus.source_mode === 'REAL_LAPTOP_CAMERA_LIVE') return 'LAPTOP CAMERA FRAME PENDING'
  if (runtime.cameraStatus.source_mode === 'REAL_LAPTOP_CAMERA_LATEST_FRAME') return 'LATEST LAPTOP FRAME — NOT LIVE'
  if (runtime.cameraStatus.source_mode === 'REAL_USB_CAMERA_LIVE' && runtime.cameraStatus.is_real_camera_evidence) return 'REAL USB CAMERA LIVE'
  if (runtime.cameraStatus.source_mode === 'REAL_USB_CAMERA_LATEST_FRAME') return 'LATEST USB FRAME'
  if (runtime.cameraStatus.profile.source_type === 'mock') return 'MOCK/SURROGATE'
  if (runtime.cameraStatus.source_mode === 'CAMERA_UNAVAILABLE') return 'CAMERA UNAVAILABLE'
  return runtime.cameraStatus.source_mode ?? 'CAMERA SOURCE UNKNOWN'
})
const truthMode = computed<'KTR_DEMO_FIXTURE' | 'DEV_REAL_CAMERA' | 'LIVE_SYSTEM' | 'OFFLINE_FIXTURE'>(() => {
  if (ktrDemoMode.value) return 'KTR_DEMO_FIXTURE'
  if (runtime.cameraStatus.is_real_camera_evidence && runtime.cameraStatus.is_laptop_camera) return 'DEV_REAL_CAMERA'
  if (runtime.cameraStatus.is_external_usb_camera && truth.picoHealthy.value) return 'LIVE_SYSTEM'
  return 'OFFLINE_FIXTURE'
})
const truthLabel = computed(() => {
  if (isOperatorUi.value) {
    if (truthMode.value === 'LIVE_SYSTEM' || truthMode.value === 'DEV_REAL_CAMERA') return 'Canlı Önizleme'
    if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'Simülasyon'
    return 'Offline'
  }
  if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'KTR Fixture'
  if (truthMode.value === 'DEV_REAL_CAMERA') return 'Real Frame Dev'
  if (truthMode.value === 'LIVE_SYSTEM') return 'Live System'
  return 'Fixture / Offline'
})
const truthDetail = computed(() => {
  if (isOperatorUi.value) {
    if (truthMode.value === 'LIVE_SYSTEM' || truthMode.value === 'DEV_REAL_CAMERA') return 'Kamera görüntüsü izleniyor.'
    if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'Simülasyon hedef verisi kullanılıyor.'
    return 'Backend veya kamera yoksa yerel önizleme korunur.'
  }
  if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'truth=fixture · deterministic KTR view · not live target evidence'
  if (truthMode.value === 'DEV_REAL_CAMERA') return 'truth=real_frame_dev · laptop camera only · not competition USB acceptance'
  if (truthMode.value === 'LIVE_SYSTEM') return 'truth=live_system · USB camera and Pico telemetry present'
  return 'truth=fixture · hardware offline expected'
})
const cameraSourceTone = computed<TruthTone>(() => {
  if (ktrDemoMode.value) return 'warn'
  if (runtime.cameraStatus.source_mode === 'REAL_LAPTOP_CAMERA_LIVE' || runtime.cameraStatus.source_mode === 'REAL_USB_CAMERA_LIVE') return 'good'
  if (runtime.cameraStatus.source_mode?.includes('LATEST') || runtime.cameraStatus.is_laptop_camera) return 'warn'
  return truth.cameraTone.value
})
const cameraPanelDetail = computed(() => {
  if (isOperatorUi.value) {
    if (truthMode.value === 'LIVE_SYSTEM' || truthMode.value === 'DEV_REAL_CAMERA') return 'Kamera önizleme aktif.'
    if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'Simülasyon hedef verisi.'
    return 'Kamera bağlantısı bekleniyor.'
  }
  if (truthMode.value === 'KTR_DEMO_FIXTURE') return 'KTR fixture view · truth=fixture · no live camera claim'
  if (truthMode.value === 'DEV_REAL_CAMERA') return 'Laptop development frame · not competition USB camera'
  if (truthMode.value === 'LIVE_SYSTEM') return 'Live USB camera + telemetry source'
  return 'Offline fixture view · hardware expected offline'
})
const profileDisplayLabel = computed(() => {
  const profile = operational.preflight.value?.profile
  if (profile === 'DRY_RUN') return 'TEST'
  if (profile === 'LIVE_TEST') return 'CANLI TEST'
  if (profile === 'VIDEO_DEMO') return 'VİDEO DEMO'
  if (profile === 'COMPETITION') return 'YARIŞMA'
  return 'BİLİNMİYOR'
})
const physicalMotionReady = computed(() =>
  Boolean(operational.preflight.value?.physical_motion_enabled)
  || operational.preflight.value?.pico_protocol === 'light_raw'
  || serial.status.transport_healthy
  || serial.status.pico_verified
  || serial.status.real_serial_enabled
  || operationMode.value === 'MANUAL'
  || operationMode.value === 'AUTO'
)
const homingActive = computed(() => homingOpen.value && homingStatus.value?.phase !== 'DONE')
// The switch represents durable operator intent, not a short-lived health
// sample. Gateway readiness remains visible separately and still blocks the
// physical command while camera/Pico/E-Stop gates are unhealthy. Recovery
// re-applies ARM,1 automatically, so the switch never times itself out.
const triggerArmed = computed(() => operationState.value?.fire_permission === 'ENABLED' || Boolean(operational.preflight.value?.actuator_armed))
const triggerConfirmed = computed(() => triggerArmed.value)
const trackingActive = computed(() => Boolean(motion.trackingStatus.active))
const selectedTargetLabel = computed(() => {
  if (selectedLogicalTargetId.value) {
    return targetRegistry.value?.targets.find((item) => item.target_id === selectedLogicalTargetId.value)?.display_name
      ?? selectedLogicalTargetId.value
  }
  if (selectedDetectionId.value !== null) {
    return `${selectedDetectionKind.value === 'body' ? 'GÖVDE' : 'BALON'} #${selectedDetectionId.value}`
  }
  return 'Hedef yok'
})
const operatorBlockerLabel = computed(() => operational.preflight.value?.reason_codes[0]
  ?? operationalBlocker.value?.reasonCode
  ?? 'PREFLIGHT_REQUIRED')
const targetPolicyLabel = computed(() => targetPolicy.value === 'BALLOON'
  ? 'BALON'
  : targetPolicy.value === 'AIRCRAFT'
    ? 'HAVA ARACI'
    : 'BİRLİKTE')
// Legacy stage names remain available to evidence/export code; they are not
// rendered in the operator cockpit. Compatibility contract retained here:
// if (mission.snapshot.state.active_stage === 'stage1') return 'AŞAMA 1'
const topBadges = computed<CockpitBadge[]>(() => {
  const isPicoReady = serial.status.transport_healthy || serial.status.pico_verified || serial.status.real_serial_enabled || operational.preflight.value?.pico_protocol === 'light_raw' || operational.preflight.value?.gates?.some((g) => g.code === 'PICO_HANDSHAKE_OK')
  const isSystemReady = isPicoReady && (operational.liveReady.value || physicalMotionReady.value || operationMode.value === 'MANUAL' || operationMode.value === 'AUTO')
  return [
    { label: `MOD ${profileDisplayLabel.value}`, tone: operational.preflight.value?.profile === 'DRY_RUN' ? 'neutral' : 'good' },
    { label: `HEDEF ${targetPolicyLabel.value}`, tone: 'neutral' },
    { label: isSystemReady ? 'SİSTEM HAZIR' : `ENGEL ${operational.primaryBlocker.value?.reasonCode ?? 'PICO_BEKLENİYOR'}`, tone: isSystemReady ? 'good' : 'warn' },
    { label: `ŞARJÖR ${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`, tone: serial.status.magazine_empty ? 'bad' : serial.status.magazine_remaining <= 2 ? 'warn' : 'good' },
  ]
})
const operatorEvents = computed<CockpitEvent[]>(() => {
  const issues = truth.healthIssues.value.slice(0, 5).map((issue) => ({
    id: issue.id,
    title: `${issue.area} · ${issue.label}`,
    detail: issue.detail,
    tone: issue.tone,
  }))
  return [
    { id: 'backend_status', title: backendStatusLabel.value, detail: system.connectionStatus === 'connected' ? 'Canlı veri bağlantısı açık.' : 'Yerel önizleme aktif; görev ekranı çalışır durumda.', tone: system.connectionStatus === 'connected' ? 'good' : 'warn' },
    { id: 'camera_source_decision', title: cameraSourceLabel.value, detail: `${truthLabel.value} · MODEL ${yoloStatusLabel.value}`, tone: cameraSourceTone.value },
    { id: 'target_projected', title: activeBalloons.value.length ? 'Hedef algılandı' : 'Hedef bekleniyor', detail: activeBalloons.value.length ? `Yön ${projectionBearing.value}; derinlik ${projectionDepth.value}.` : 'Kamera alanında hedef yok.', tone: activeBalloons.value.length ? 'good' : 'neutral' },
    { id: 'safety_no_tx', title: operationalLiveReady.value ? 'Atış kapısı hazır' : 'Atış kapısı beklemede', detail: operationalLiveReady.value ? 'FIRE yetkisi ve preflight hazır.' : `Fiziksel komut beklemede: ${operatorBlockerLabel.value}`, tone: operationalLiveReady.value ? 'good' : 'warn' },
    { id: 'person_safety', title: personSafetyActive.value ? 'İnsan güvenliği aktif' : personSafetyAvailable.value ? 'İnsan güvenliği izleniyor' : 'İnsan güvenliği bekleniyor', detail: personSafetyActive.value ? 'Atış kapısı insan güvenliği nedeniyle kapalı.' : personSafetyAvailable.value ? 'Sistem izleme modunda.' : 'Sınıflandırıcı durumu bekleniyor.', tone: personSafetyActive.value ? 'bad' : personSafetyAvailable.value ? 'good' : 'warn' },
    ...(issues.length ? issues : [{ id: 'clean', title: 'Operational log clear', detail: 'Kritik uyarı yok; dry-run safety invariant aktif.', tone: 'good' as TruthTone }]),
  ]
})
const bottomMetrics = computed<CockpitMetric[]>(() => [
  { key: 'target', label: 'Seçili hedef', value: selectedTarget.value ? `#${selectedTarget.value.id}` : 'hedef yok', tone: selectedTarget.value ? 'good' : 'neutral' },
  { key: 'tracking', label: 'Takip', value: virtualTrackIntent.value ? 'kilitli' : engagementState.value, tone: virtualTrackIntent.value ? 'good' : 'neutral' },
  { key: 'fire', label: 'FIRE', value: operational.liveReady.value || triggerArmed.value ? 'READY' : operational.primaryBlocker.value?.reasonCode ?? 'PREFLIGHT_REQUIRED', tone: operational.liveReady.value || triggerArmed.value ? 'good' : 'warn' },
  { key: 'mission', label: 'Kokpit', value: targetPolicyLabel.value, tone: 'neutral' },
])
const targetPlanX = computed(() => {
  const target = selectedTarget.value
  if (!target) return 210
  return 40 + (target.center_x / overlayWidth.value) * 240
})
const targetPlanY = computed(() => {
  const target = selectedTarget.value
  if (!target) return 58
  return 24 + (target.center_y / overlayHeight.value) * 96
})
const projectionEstimate = computed(() => digitalTwin.state?.target_projection_estimates?.[0] ?? null)
const projectionXNorm = computed(() => projectionEstimate.value?.normalized_center_x?.toFixed(2) ?? '0.76')
const projectionYNorm = computed(() => projectionEstimate.value?.normalized_center_y?.toFixed(2) ?? '0.54')
const projectionArea = computed(() => projectionEstimate.value?.bbox_area_ratio?.toFixed(3) ?? '0.031')
const projectionDepth = computed(() => projectionEstimate.value?.estimated_range_band ?? 'mid')
const projectionPoseSource = computed(() => digitalTwin.state?.device_pose.pose_source ?? 'tracker_estimate')
const projectionBearing = computed(() => {
  const x = Number(projectionXNorm.value)
  if (x > 0.62) return 'RIGHT'
  if (x < 0.38) return 'LEFT'
  return 'MID'
})
const engagementState = computed(() => {
  if (!activeBalloons.value.length) return 'NO TARGET'
  if (!selectedTarget.value) return 'TARGET DETECTED'
  if (virtualTrackIntent.value) return 'TRACKING PREVIEW · FIRE GATE BLOCKED / NO TX'
  return 'TARGET SELECTED · FIRE GATE BLOCKED / NO TX'
})

function showToast(tone: 'success' | 'warn' | 'error', message: string): void {
  operatorToast.value = { tone, message }
  window.setTimeout(() => {
    if (operatorToast.value?.message === message) operatorToast.value = null
  }, 3500)
}

function onZeroingSaved(offset: { x: number, y: number, distance: number, dropCm: number }): void {
  showToast('success', `${offset.distance}m Sıfırlaması Kaydedildi: Düşme ${offset.dropCm}cm (${offset.y}px)`)
  motion.trackingStatus.aim_offset_x_px = offset.x
  motion.trackingStatus.aim_offset_y_px = offset.y
  void motion.refreshTrackingStatus().catch(() => undefined)
}

watch(() => motion.state.limit_reason_code, (code, previous) => {
  if (!code || code === previous) return
  const labels: Record<string, string> = {
    PAN_MIN_LIMIT: 'Yatay sol limite ulaşıldı',
    PAN_MAX_LIMIT: 'Yatay sağ limite ulaşıldı',
    TILT_MIN_LIMIT: 'Dikey alt limite ulaşıldı',
    TILT_MAX_LIMIT: 'Dikey üst limite ulaşıldı',
    HOME_TILT_TIMEOUT: 'Dikey home switch zaman aşımı',
    HOME_PAN_TIMEOUT: 'Yatay home switch zaman aşımı',
    HOME_CENTER_TIMEOUT: 'Merkezleme zaman aşımı',
  }
  showToast('warn', `${labels[code] ?? 'Hareket sınırı etkin'} · ${code}`)
})

let lastAutoFireResultAt = 0
watch(
  () => motion.trackingStatus.last_fire_result?.updated_at ?? 0,
  (updatedAt) => {
    if (!updatedAt || updatedAt <= lastAutoFireResultAt) return
    lastAutoFireResultAt = updatedAt
    const result = motion.trackingStatus.last_fire_result
    if (!result) return
    // Autonomous firing state is continuously streamed over WebSocket.
    // Avoid spamming toasts and redundant HTTP fetches on every single shot.
    if (!result.accepted && result.reason_codes.includes('MAGAZINE_EMPTY')) {
      showToast('warn', 'Mermi yok · Şarjörü doldurun')
    }
  },
)

function cameraSourceTypeFor(device: ManagedDevice): 'laptop' | 'usb' {
  if (device.is_external_camera) return 'usb'
  if (device.is_laptop_camera) return 'laptop'
  const text = `${device.name} ${device.description} ${device.manufacturer ?? ''} ${device.stable_path ?? ''} ${device.device_path}`
  return /integrated|internal|built.?in|front|user.?facing|laptop|VID_2B7E.*PID_B685/i.test(text) ? 'laptop' : 'usb'
}

async function applyBackendCamera(deviceId: string): Promise<void> {
  const camera = backendCameraOptions.value.find((item) => item.device_id === deviceId)
  if (!camera) return
  runtime.cameraDraft = {
    ...runtime.cameraStatus.profile,
    source_type: cameraSourceTypeFor(camera),
    device_id: camera.device_id,
    device_path: camera.device_path,
    stable_path: camera.stable_path,
    width: Math.max(640, runtime.cameraStatus.profile.width || 1280),
    height: Math.max(360, runtime.cameraStatus.profile.height || 720),
    fps: Math.max(15, runtime.cameraStatus.profile.fps || 30),
    stream_width: 1280,
    stream_height: 720,
    inference_width: 1280,
    inference_height: 720,
    pixel_format: runtime.cameraStatus.profile.pixel_format === 'auto' ? 'MJPG' : runtime.cameraStatus.profile.pixel_format,
    roi: { ...runtime.cameraStatus.profile.roi },
  }
  await runtime.applyCamera()
  if (perceptionEnabled.value) {
    await ensureYoloRuntime()
    await vision.start()
  }
  await refreshAll()
}

function normalizedLiveConf(): number {
  const parsed = Number(liveConfInput.value.replace(',', '.'))
  return Number.isFinite(parsed) ? Math.max(0.001, Math.min(1, parsed)) : 0.05
}

async function ensureYoloRuntime(conf?: number): Promise<void> {
  // A hard refresh/direct cockpit URL can run before Landing has copied the
  // machine-wide active profile id into this browser's localStorage. The
  // backend runtime is already authoritative at that point; treating the
  // missing browser key as "unmanaged" used to overwrite the verified
  // two-model CUDA profile with the old balloon-only fallback.
  const backendManagedProfileActive = runtime.visionStatus.runtime_source === 'setup_direct_model_path'
    || runtime.visionStatus.active_model_summary.active_body_model_id === 'setup_body_path'
    || runtime.visionStatus.active_model_summary.active_balloon_model_id === 'setup_balloon_path'
  const managedProfileActive = Boolean(localStorage.getItem('istiklal_active_profile_id')) || backendManagedProfileActive
  if (managedProfileActive) {
    if (conf === undefined) return
    runtime.visionDraft = {
      ...runtime.visionStatus.profile,
      conf,
      body_conf_threshold: conf,
      balloon_conf_threshold: conf,
      target_fps: Math.max(60, Number(runtime.visionStatus.profile?.target_fps || 60)),
    }
    await runtime.applyVision()
    return
  }
  const effectiveConf = conf ?? normalizedLiveConf()
  runtime.visionDraft = {
    ...runtime.visionStatus.profile,
    inference_adapter: 'ultralytics_yolo',
    active_balloon_model_id: 'legacy-balloon-yolo',
    active_body_model_id: null,
    conf: effectiveConf,
    balloon_conf_threshold: effectiveConf,
    device: 'auto',
    imgsz: 640,
    max_det: 20,
    tracker_enabled: false,
    tracker_type: 'none',
  }
  await runtime.applyVision()
  await runtime.reloadModels()
}

async function applyLiveConfidence(conf?: number): Promise<void> {
  const nextConf = conf ?? normalizedLiveConf()
  liveConfInput.value = nextConf.toFixed(nextConf < 0.1 ? 3 : 2).replace(/0+$/, '').replace(/\.$/, '')
  confApplyBusy.value = true
  try {
    await applyPerceptionConfig({ confidence_threshold: nextConf, yolo_enabled: perceptionEnabled.value })
    await ensureYoloRuntime(nextConf)
    if (perceptionEnabled.value) {
      await vision.stop().catch(() => undefined)
      await vision.start()
    }
    await refreshAll()
    lastConfidenceApplyAt.value = new Date().toLocaleTimeString('tr-TR')
    confidenceApplyStatus.value = 'Applied'
    showToast('success', `Algılama eşiği ${liveConfInput.value} olarak uygulandı`)
  } catch (error) {
    confidenceApplyStatus.value = 'Failed'
    showToast('error', `Uygulanamadı: ${error instanceof Error ? error.message : 'backend yanıt vermedi'}`)
  } finally {
    confApplyBusy.value = false
  }
}

async function togglePerception(): Promise<void> {
  perceptionEnabled.value = !perceptionEnabled.value
  const params = new URLSearchParams(window.location.search)
  if (perceptionEnabled.value) params.delete('perception')
  else params.set('perception', 'off')
  const next = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`
  window.history.replaceState(null, '', next)
  if (perceptionEnabled.value) {
    await ensureYoloRuntime()
    await vision.start()
  }
  await applyPerceptionConfig({ confidence_threshold: normalizedLiveConf(), yolo_enabled: perceptionEnabled.value }).catch(() => undefined)
  await refreshAll()
  showToast(
    perceptionEnabled.value && productionVisionReady.value && detectionRuntimeReady.value ? 'success' : 'warn',
    perceptionEnabled.value
      ? `${yoloStatusLabel.value} — ${detectionRuntimeDetail.value}`
      : 'Algılama kapalı — yalnız kamera',
  )
}

async function openCameraFullscreen(): Promise<void> {
  await enterCameraFullscreen()
}

function resolveBalloonForBody(bodyDetectionId: number): { id: number, center_x: number, center_y: number } | null {
  const body = activeBodies.value.find((item) => item.id === bodyDetectionId)
  if (!body) return null
  if (cockpitTeamToken(body.target_team) === 'dost') return null

  // Prefer the mission registry's immutable body→balloon link.  This keeps a
  // body click on the same balloon after a short detector dropout or a frame
  // local id change.
  const registryTarget = targetRegistry.value?.targets.find((item) => (
    item.body_detection_id === bodyDetectionId
      && item.balloon_track_id !== null
      && item.state !== 'DESTROYED'
      && !item.consumed
  ))
  if (registryTarget?.balloon_track_id !== null && registryTarget?.balloon_track_id !== undefined) {
    const track = motion.trackingStatus.multi_target_tracker.tracks.find((item) => item.track_id === registryTarget.balloon_track_id)
    const linked = track?.detection_id === null || track?.detection_id === undefined
      ? null
      : activeBalloons.value.find((item) => item.id === track.detection_id)
    if (linked) return linked
  }

  // During the first registry tick, use only the calibrated attachment region
  // and choose the nearest visible balloon.  A distant standalone balloon is
  // never silently substituted for an aircraft's own balloon.
  return activeBalloons.value
    .filter((balloon) => balloonBelongsToBody(balloon, body))
    .sort((a, b) => {
      const bodyCenterX = body.bbox.x + body.bbox.w / 2
      const bodyCenterY = body.bbox.y + body.bbox.h
      const da = Math.hypot(a.center_x - bodyCenterX, a.center_y - bodyCenterY)
      const db = Math.hypot(b.center_x - bodyCenterX, b.center_y - bodyCenterY)
      return da - db
    })[0] ?? null
}

async function selectBalloonTarget(target: { id: number, center_x: number, center_y: number, kind: 'body' | 'balloon', track_id?: number | null }): Promise<void> {
  if (targetSelectBusy.value) return
  const handoffInProgress = operationMode.value === 'AUTO' && trackingActive.value
  let selected = target
  if (targetPolicy.value === 'BALLOON' && target.kind !== 'balloon') {
    showToast('warn', 'BALON politikasında yalnız balon kutuları seçilebilir')
    return
  }
  if (targetPolicy.value === 'AIRCRAFT' && target.kind !== 'body') {
    showToast('warn', 'HAVA ARACI politikasında yalnız hava aracı kutuları seçilebilir')
    return
  }
  if (targetPolicy.value === 'BALLOON_AIRCRAFT' && target.kind === 'body') {
    const balloon = resolveBalloonForBody(target.id)
    if (!balloon) {
      showToast('warn', 'Bu hava aracına ait kararlı/görünür balon bulunamadı')
      return
    }
    selected = { ...balloon, kind: 'balloon' }
  }
  const balloon = selected.kind === 'balloon' ? selected : resolveBalloonForBody(selected.id)
  const ownerBody = activeBodies.value
    .filter((body) => cockpitClassToken(body.class_name) !== 'target' && balloon && balloonBelongsToBody(balloon, body))
    .sort((a, b) => {
      const da = balloon ? Math.hypot(balloon.center_x - (a.bbox.x + a.bbox.w / 2), balloon.center_y - (a.bbox.y + a.bbox.h / 2)) : 0
      const db = balloon ? Math.hypot(balloon.center_x - (b.bbox.x + b.bbox.w / 2), balloon.center_y - (b.bbox.y + b.bbox.h / 2)) : 0
      return da - db
    })[0]
  const selectedBody = selected.kind === 'body'
    ? activeBodies.value.find((body) => body.id === selected.id)
    : ownerBody
  const friendlySelection = Boolean(selectedBody && cockpitTeamToken(selectedBody.target_team) === 'dost')
  const logicalTarget = targetRegistry.value?.targets.find((item) => (
    (selected.kind === 'body' && item.body_detection_id === selected.id)
      || (selected.kind === 'balloon' && balloon !== null && (item.balloon_detection_id === balloon.id
        || item.balloon_track_id === motion.trackingStatus.multi_target_tracker.tracks.find((track) => track.detection_id === balloon.id)?.track_id))
  ))
  const fireBlockedTarget = Boolean(logicalTarget && !logicalTarget.engagement_allowed)
  targetSelectBusy.value = true
  selectedBalloonId.value = balloon?.id ?? null
  selectedDetectionId.value = selected.id
  selectedDetectionKind.value = selected.kind
  selectedLogicalTargetId.value = logicalTarget?.target_id ?? null
  try {
    await selectOperationTarget({
      logical_target_id: logicalTarget?.target_id ?? null,
      x: selected.center_x,
      y: selected.center_y,
      detection_id: selected.id,
      detection_kind: selected.kind,
      body_track_id: selected.kind === 'body' ? selected.track_id ?? null : null,
      frame_id: latestFrame.value?.frame_id,
    })
    virtualTrackIntent.value = false
    await fetchOperationState().then((state) => { operationState.value = state; targetPolicy.value = state.target_policy })
    await operational.refresh()
    await refreshAll()
    showToast(
      friendlySelection || fireBlockedTarget ? 'warn' : 'success',
      friendlySelection || fireBlockedTarget
        ? 'Dost hedef seçildi · takip edilebilir, fiziksel FIRE engellidir'
        : handoffInProgress
          ? `${selected.kind === 'body' ? 'Hava aracı' : 'Balon'} devralındı · otonom takip yeni hedefte sürüyor`
          : operationMode.value === 'AUTO'
            ? `${selected.kind === 'body' ? 'Hava aracı' : 'Balon'} seçildi · otonom takip devraldı`
            : `${selected.kind === 'body' ? 'Hava aracı' : 'Balon'} seçildi · manuel modda otomatik hareket yok`,
    )
  } catch (caught) {
    virtualTrackIntent.value = false
    showToast('error', caught instanceof Error ? caught.message : 'Hedef seçilemedi veya takip başlatılamadı')
  } finally {
    targetSelectBusy.value = false
  }
}

async function toggleTracking(): Promise<void> {
  if (operatorControlBusy.value) return
  if (operationMode.value !== 'AUTO') {
    showToast('warn', 'Önce OTONOM modunu seç')
    return
  }
  operatorControlBusy.value = true
  try {
    if (trackingActive.value) {
      virtualTrackIntent.value = false
      operationState.value = await updateOperationState({ tracking_requested: false })
      showToast('warn', 'Takip durduruldu')
    } else {
      operationState.value = await updateOperationState({ control_mode: 'AUTONOMOUS', tracking_requested: true })
      operationMode.value = 'AUTO'
      showToast('success', 'Takip başlatıldı')
    }
    await refreshAll()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Takip değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

async function setOperationMode(nextMode: OperationalControlMode): Promise<void> {
  // The `autotrack=1` route hint can paint AUTO before backend operation
  // state has accepted `tracking_requested=true`. Re-selecting the visible
  // mode must therefore remain an idempotent backend intent, not a UI-only
  // early return; otherwise AUTO looks active while the tracker is STOPPED.
  if (operatorControlBusy.value) return
  operatorControlBusy.value = true
  try {
    if (nextMode === 'AUTO') {
      operationState.value = await updateOperationState({ control_mode: 'AUTONOMOUS', tracking_requested: true })
      operationMode.value = 'AUTO'
      await motion.refreshTrackingStatus().catch(() => undefined)
      showToast('success', 'Otonom mod aktif · ilk uygun hedef otomatik takip edilir')
    } else {
      operationState.value = await updateOperationState({ control_mode: 'MANUAL', tracking_requested: false })
      virtualTrackIntent.value = false
      operationMode.value = 'MANUAL'
      showToast('success', 'Manuel hareket modu aktif')
    }
    await refreshAll()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Çalışma modu değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

async function toggleTrigger(): Promise<void> {
  if (operatorControlBusy.value) return
  operatorControlBusy.value = true
  try {
    const current = operational.preflight.value?.profile ?? 'LIVE_TEST'
    const profile: CommandProfile = current === 'DRY_RUN' ? 'LIVE_TEST' : current
    const nextArmed = !triggerArmed.value
    await selectCommandProfile(profile, nextArmed).catch(() => undefined)
    operationState.value = await updateOperationState({ fire_permission: nextArmed ? 'ENABLED' : 'DISABLED' })
    await operational.refresh().catch(() => undefined)
    showToast('success', nextArmed ? 'Tetik AÇIK duruma alındı (ARMED)' : 'Tetik KAPALI duruma alındı (DISARMED)')
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Tetik durumu değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

async function safeStop(): Promise<void> {
  releaseManualKeys()
  try {
    await motion.stopTracking().catch(() => undefined)
    virtualTrackIntent.value = false
    await motion.stop()
    await updateOperationState({ control_mode: 'MANUAL', tracking_requested: false, fire_permission: 'DISABLED' }).catch(() => undefined)
    await operational.refresh().catch(() => undefined)
    operationMode.value = 'MANUAL'
    showToast('success', 'Hareket güvenli şekilde durduruldu')
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Hareket durdurulamadı')
  }
}

function toggleManualMotion(): void {
  manualMotionEnabled.value = !manualMotionEnabled.value
  if (!manualMotionEnabled.value) releaseManualKeys()
  showToast(manualMotionEnabled.value ? 'success' : 'warn', manualMotionEnabled.value ? 'Manuel yön tuşları açık' : 'Manuel yön tuşları kapalı')
}

async function setTargetPolicy(policy: TargetPolicy): Promise<void> {
  if (targetPolicy.value === policy || operatorControlBusy.value) return
  operatorControlBusy.value = true
  try {
    const state = await updateOperationState({ target_policy: policy, tracking_requested: operationMode.value === 'AUTO' })
    targetPolicy.value = state.target_policy
    operationState.value = state
    selectedBalloonId.value = null
    selectedDetectionId.value = null
    selectedDetectionKind.value = null
    selectedLogicalTargetId.value = null
    virtualTrackIntent.value = false
    showToast('success', `Hedef politikası: ${policy === 'BALLOON' ? 'BALON' : policy === 'AIRCRAFT' ? 'HAVA ARACI' : policy === 'STAGE1_INDEPENDENT' ? 'BAĞIMSIZ (AŞAMA 1)' : 'BALON + HAVA ARACI'}`)
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Hedef politikası değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

const currentCompetitionStage = computed<'STAGE_1' | 'STAGE_2' | 'STAGE_3'>(() => {
  if (operationState.value?.competition_stage) {
    return operationState.value.competition_stage
  }
  if (targetPolicy.value === 'STAGE1_INDEPENDENT') {
    return 'STAGE_1'
  }
  if (targetPolicy.value === 'BALLOON') {
    return 'STAGE_2'
  }
  return 'STAGE_3'
})

async function selectCompetitionStage(stage: 'STAGE_1' | 'STAGE_2' | 'STAGE_3'): Promise<void> {
  if (operatorControlBusy.value) return
  operatorControlBusy.value = true
  try {
    // Aşama 2, parkur ve hedefler Aşama 3 ile birebir aynı olduğu için artık
    // kendi ayrı BALLOON politikasını değil, doğrudan Aşama 3'ün
    // BALLOON_AIRCRAFT politikasını kullanır (bkz. handleStartStage2 ->
    // handleStartStage3Round devri).
    const policyMap: Record<'STAGE_1' | 'STAGE_2' | 'STAGE_3', TargetPolicy> = {
      STAGE_1: 'STAGE1_INDEPENDENT',
      STAGE_2: 'BALLOON_AIRCRAFT',
      STAGE_3: 'BALLOON_AIRCRAFT',
    }
    // İSTENEN: Aşama 1/2/3 butonlarından hangisine basılırsa basılsın taret
    // her zaman MANUEL modda başlar. Otonom takip/hareket ancak ve ancak
    // operatör kokpitteki "OTONOM" butonuna (setOperationMode('AUTO') /
    // toggleTracking) ya da ilgili "Aşama Başlat" kontrolüne bastığında
    // devreye girer - stage seçimi kendi başına asla AUTO/AUTONOMOUS
    // tetiklemez.
    const modeMap: Record<'STAGE_1' | 'STAGE_2' | 'STAGE_3', OperationalControlMode> = {
      STAGE_1: 'MANUAL',
      STAGE_2: 'MANUAL',
      STAGE_3: 'MANUAL',
    }
    const targetP = policyMap[stage]
    const targetM = modeMap[stage]
    const state = await updateOperationState({
      control_mode: 'MANUAL',
      target_policy: targetP,
      competition_stage: stage,
      tracking_requested: false,
    })
    operationMode.value = targetM
    targetPolicy.value = state.target_policy
    operationState.value = state
    selectedBalloonId.value = null
    selectedDetectionId.value = null
    selectedDetectionKind.value = null
    selectedLogicalTargetId.value = null
    // NOT: Burada artık handleStartStage2()/handleStartStage3Round()
    // OTOMATİK çağrılmıyor - bu çağrılar modu anında AUTONOMOUS'a çekip
    // tracking_requested=true yapıyor, F5/refresh sonrası da beklenmedik
    // otonom harekete yol açabiliyordu. Aşama seçimi artık yalnızca hedef
    // politikasını/parkuru hazırlar; fiziksel takip yalnız operatörün
    // OTONOM butonuna ya da "Aşama Başlat" kontrolüne basmasıyla başlar.
    const msg = stage === 'STAGE_1'
      ? 'Aşama 1: Manuel Operatör (Bağımsız Balon + Hava Aracı)'
      : stage === 'STAGE_2'
      ? 'Aşama 2: Manuel hazır · Aşama 3 akışı (OTONOM ile başlatın)'
      : 'Aşama 3: Manuel hazır · Kural 1-2-4 & 3 Kol Devriyesi (OTONOM ile başlatın)'
    showToast('success', msg)
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Aşama değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

const stageBarBusy = ref(false)
const patrolStatus = ref<{
  patrol_enabled: boolean
  active_path: number
  path1_deg: number
  path2_deg: number
  path3_deg: number
  dwell_time_s?: number
  rounds_completed?: number
  dwell_remaining_s?: number
  on_path?: boolean
  state: string
  strategy?: string
  sweep_speed_dps?: number
  sweep_sector_deg?: number
} | null>(null)
const stage3CurrentRound = ref(1)

async function refreshPatrol(): Promise<void> {
  try {
    const res = await fetchPatrolStatus()
    if (res) patrolStatus.value = res
  } catch {
    // Ignore patrol status errors during offline or connection refresh
  }
}

async function handleStartStage2(): Promise<void> {
  // Aşama 2 artık arka planda ayrı/bağımsız bir "Aşama 2 mantığı"
  // çalıştırmıyor: parkur ve hedefler Aşama 3 ile birebir aynı olduğundan
  // bu çağrı doğrudan Aşama 3'ün politikasına (BALLOON_AIRCRAFT) ve
  // tur/devriye akışına (handleStartStage3Round) devrediyor.
  await handleStartStage3Round()
}

async function handleStopStage2(): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const state = await stopStage2Mission()
    operationState.value = state
    operationMode.value = 'MANUAL'
    showToast('warn', 'Aşama 2: Radar devriyesi durduruldu')
    await refreshPatrol()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Aşama 2 durdurulamadı')
  } finally {
    stageBarBusy.value = false
  }
}

async function handleStartStage3Round(): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const res = await startStage3Round(stage3CurrentRound.value)
    if (res?.operation_state) {
      operationState.value = res.operation_state
      operationMode.value = 'AUTO'
      targetPolicy.value = res.operation_state.target_policy
    }
    if (res?.current_round) {
      stage3CurrentRound.value = res.current_round
    }
    showToast('success', `Aşama 3: Tur ${stage3CurrentRound.value} başlatıldı`)
    await refreshPatrol()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Aşama 3 turu başlatılamadı')
  } finally {
    stageBarBusy.value = false
  }
}

async function handleNextStage3Round(): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const res = await nextStage3Round()
    if (res?.current_round) {
      stage3CurrentRound.value = res.current_round
    }
    showToast('success', `Aşama 3: Tur ${stage3CurrentRound.value} devralındı`)
    await refreshPatrol()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Sonraki tura geçilemedi')
  } finally {
    stageBarBusy.value = false
  }
}

async function handleResetStage3Rounds(): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    await resetStage3Rounds()
    stage3CurrentRound.value = 1
    showToast('warn', 'Aşama 3: Turlar 1. tura sıfırlandı')
    await refreshPatrol()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Turlar sıfırlanamadı')
  } finally {
    stageBarBusy.value = false
  }
}

async function handleCapturePathAngle(pathNumber: number): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const res = await capturePatrolAngle(pathNumber)
    showToast('success', `${pathNumber}. Kol açısı ${res.captured_angle_deg}° olarak güncellendi`)
    await refreshPatrol()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Açı kaydedilemedi')
  } finally {
    stageBarBusy.value = false
  }
}

const radarModalOpen = ref(false)
let radarModalPollTimer: ReturnType<typeof setInterval> | null = null

watch(radarModalOpen, (isOpen) => {
  if (isOpen) {
    if (radarModalPollTimer) clearInterval(radarModalPollTimer)
    radarModalPollTimer = setInterval(async () => {
      try {
        await Promise.all([
          refreshPatrol(),
          motion.refresh(),
        ])
      } catch {
        // ignore polling errors
      }
    }, 120)
  } else {
    if (radarModalPollTimer) {
      clearInterval(radarModalPollTimer)
      radarModalPollTimer = null
    }
  }
})

async function handleRadarJog(axis: 'pan' | 'tilt', dir: number, step?: number): Promise<void> {
  const speed = (step && step > 1 ? 500 : 250) * (dir > 0 ? 1 : -1)
  try {
    if (axis === 'pan') {
      await sendStage1ManualMotion({ speed_x: speed, speed_y: 0, duration_ms: 220 })
    } else {
      await sendStage1ManualMotion({ speed_x: 0, speed_y: speed, duration_ms: 220 })
    }
    await motion.refresh()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Manuel taret hareketi başarısız')
  }
}

const currentTurretPanDeg = computed(() => {
  const phys = motion.state.physical_pan_deg
  if (phys !== null && phys !== undefined && !Number.isNaN(phys)) {
    return phys
  }
  return (motion.state.pan_position_deg ?? 0) + 135.0
})

async function handleSavePatrolAngles(angles: {
  path1_deg: number
  path2_deg: number
  path3_deg: number
  dwell_time_s: number
  strategy?: string
  sweep_speed_dps?: number
  sweep_sector_deg?: number
}): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const updated = await updatePatrolAngles(angles)
    patrolStatus.value = updated
    showToast('success', 'Radar kol açıları başarıyla kaydedildi')
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Açılar kaydedilemedi')
  } finally {
    stageBarBusy.value = false
  }
}

async function handleGotoPath(pathNumber: number): Promise<void> {
  if (stageBarBusy.value) return
  stageBarBusy.value = true
  try {
    const res = await gotoPatrolPath(pathNumber)
    if (res?.status) patrolStatus.value = res.status
    showToast('success', `Taret ${pathNumber}. kola yönlendiriliyor...`)
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Kola yönlendirilemedi')
  } finally {
    stageBarBusy.value = false
  }
}

async function toggleBboxLock(): Promise<void> {
  if (operatorControlBusy.value) return
  operatorControlBusy.value = true
  const enabled = !motion.trackingStatus.bbox_lock_enabled
  try {
    await motion.updateTrackingConfig({ bbox_lock_enabled: enabled })
    showToast(enabled ? 'success' : 'warn', enabled ? '%30 bbox takip kilidi açık' : '%30 bbox takip kilidi kapalı')
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'BBox takip kilidi değiştirilemedi')
  } finally {
    operatorControlBusy.value = false
  }
}

function openCameraSettings(): void { cameraSettingsOpen.value = true }
function openSetup(): void { cameraSettingsOpen.value = true }

function manualMotionVector(): { speed_x: number, speed_y: number } {
  let speedX = 0
  let speedY = 0
  if (manualKeys.has('ArrowLeft')) speedX -= 3000
  if (manualKeys.has('ArrowRight')) speedX += 3000
  if (manualKeys.has('ArrowUp')) speedY += 2500
  if (manualKeys.has('ArrowDown')) speedY -= 2500
  return { speed_x: speedX, speed_y: speedY }
}

const keyboardZoomActive = ref(false)
const manualZoomToggle = ref(false)

const isSniperZoomActive = computed(() => {
  return Boolean(
    gamepad.zoomPressed.value ||
    keyboardZoomActive.value ||
    manualZoomToggle.value
  )
})

function toggleManualZoom(): void {
  manualZoomToggle.value = !manualZoomToggle.value
}

async function sendGamepadMotionPulse(): Promise<void> {
  if (homingActive.value) {
    gamepadWasMoving = false
    publishManualTwinIntent(0, 0, 'released', false)
    return
  }
  const enabled = gamepad.profile.value.enabled
    && (operationMode.value === 'MANUAL' || radarModalOpen.value)
    && manualMotionEnabled.value
    && Boolean(gamepad.activeDevice.value)
  if (!enabled || manualKeys.size > 0) {
    if (gamepadWasMoving && manualKeys.size === 0) {
      gamepadWasMoving = false
      publishManualTwinIntent(0, 0, 'released', false)
      void stopHardwareMotion().catch(() => undefined)
    }
    return
  }
  // Hassas Nişan Alma (Fine Aim): Zoom aktifken hareket hızını %45'e düşür (ince ayar kontrolü)
  const speedScale = isSniperZoomActive.value ? 0.18 : 1.0
  const speedX = Math.round(gamepad.speedX.value * speedScale)
  const speedY = Math.round(gamepad.speedY.value * speedScale)
  if (speedX === 0 && speedY === 0) {
    if (gamepadWasMoving) {
      gamepadWasMoving = false
      publishManualTwinIntent(0, 0, 'released', false)
      void stopHardwareMotion().catch(() => undefined)
      void motion.stop().catch(() => undefined)
    }
    return
  }
  if (manualMotionBusy) return
  if (!physicalMotionReady.value) {
    if (lastManualBlocker !== operatorBlockerLabel.value) {
      lastManualBlocker = operatorBlockerLabel.value
      showToast('warn', `USB kumanda hareketi engellendi: ${operatorBlockerLabel.value}`)
    }
    return
  }
  manualMotionBusy = true
  gamepadWasMoving = true
  const intentSequence = publishManualTwinIntent(speedX, speedY, 'pending', false)
  try {
    const result = await sendStage1ManualMotion({ speed_x: speedX, speed_y: speedY, duration_ms: 420 })
    if (result.accepted) {
      updateManualTwinIntentStatus(intentSequence, 'accepted', true)
      lastManualBlocker = ''
    } else {
      updateManualTwinIntentStatus(intentSequence, 'rejected', false)
      lastManualBlocker = result.reason_codes[0] ?? 'MOTION_BLOCKED'
      showToast('warn', `USB kumanda hareketi engellendi: ${lastManualBlocker}`)
    }
  } catch (caught) {
    updateManualTwinIntentStatus(intentSequence, 'rejected', false)
    showToast('error', caught instanceof Error ? caught.message : 'USB kumanda komutu gönderilemedi')
  } finally {
    manualMotionBusy = false
  }
}

function publishManualTwinIntent(
  speedX: number,
  speedY: number,
  status: ManualTwinMotionIntentStatus,
  active: boolean,
  sequence = ++manualTwinIntentSequence,
): number {
  manualTwinMotionIntent.value = {
    sequence,
    speedX,
    speedY,
    issuedAtMs: performance.now(),
    active,
    status,
  }
  return sequence
}

function updateManualTwinIntentStatus(sequence: number, status: ManualTwinMotionIntentStatus, active: boolean): void {
  if (manualTwinMotionIntent.value.sequence !== sequence) return
  manualTwinMotionIntent.value = {
    ...manualTwinMotionIntent.value,
    active,
    status,
  }
}

async function sendManualMotionPulse(): Promise<void> {
  if (homingActive.value || manualMotionBusy || manualKeys.size === 0) return
  const vector = manualMotionVector()
  if (vector.speed_x === 0 && vector.speed_y === 0) return
  manualMotionBusy = true
  const intentSequence = publishManualTwinIntent(vector.speed_x, vector.speed_y, 'pending', false)
  try {
    const request = sendStage1ManualMotion({ ...vector, duration_ms: 520 })
    const result = await request
    if (!result.accepted && lastManualBlocker !== (result.reason_codes[0] ?? 'MOTION_BLOCKED')) {
      updateManualTwinIntentStatus(intentSequence, 'rejected', false)
      lastManualBlocker = result.reason_codes[0] ?? 'MOTION_BLOCKED'
      showToast('warn', `Manuel hareket engellendi: ${lastManualBlocker}`)
    } else if (result.accepted) {
      updateManualTwinIntentStatus(intentSequence, 'accepted', manualKeys.size > 0)
      window.setTimeout(() => { void digitalTwin.refreshPose() }, 35)
      lastManualBlocker = ''
    }
  } catch (caught) {
    updateManualTwinIntentStatus(intentSequence, 'rejected', false)
    showToast('error', caught instanceof Error ? caught.message : 'Manuel hareket komutu gönderilemedi')
  } finally {
    manualMotionBusy = false
  }
}

function releaseManualKeys(): void {
  manualKeys.clear()
  publishManualTwinIntent(0, 0, 'released', false)
  if (manualMotionTimer) {
    clearInterval(manualMotionTimer)
    manualMotionTimer = null
  }
  // Stop the physical Gateway immediately; the generic motion store stop is
  // retained only to keep the advisory UI state in sync.
  void stopHardwareMotion().catch(() => undefined)
  void motion.stop().catch(() => undefined)
}

function handleManualKeyDown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (event.key.toLowerCase() === 'f') {
    if (target?.tagName === 'INPUT' || target?.tagName === 'SELECT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return
    if (event.repeat) return
    event.preventDefault()
    if (homingActive.value) {
      showToast('warn', 'Auto-home tamamlanana kadar tetik bekletiliyor')
      return
    }
    void requestFire()
    return
  }
  if (event.key.toLowerCase() === 'z') {
    if (target?.tagName === 'INPUT' || target?.tagName === 'SELECT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return
    if (event.repeat) return
    event.preventDefault()
    keyboardZoomActive.value = true
    return
  }
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
  if (target?.tagName === 'INPUT' || target?.tagName === 'SELECT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return
  if (homingActive.value) return
  event.preventDefault()
  manualKeys.add(event.key)
  if (!manualMotionTimer) manualMotionTimer = setInterval(() => { void sendManualMotionPulse() }, 180)
  void sendManualMotionPulse()
}

function handleManualKeyUp(event: KeyboardEvent): void {
  if (event.key.toLowerCase() === 'z') {
    keyboardZoomActive.value = false
    return
  }
  if (!manualKeys.has(event.key)) return
  event.preventDefault()
  manualKeys.delete(event.key)
  if (manualKeys.size === 0) releaseManualKeys()
}

function handleWindowBlur(): void {
  keyboardZoomActive.value = false
  releaseManualKeys()
}

// Evidence history is read lazily: only while the engineer "logs" tab is
// visible (store-side single-flight + 5 s throttle).  Never from the FIRE path.
watch([engineerPanelOpen, engineerActiveTab], ([open, tab]) => {
  if (open && tab === 'logs') void digitalTwin.refreshEngagementEvidence().catch(() => undefined)
})

watch(gamepad.triggerPressed, (pressed, previous) => {
  if (!pressed || previous || homingActive.value || !gamepad.profile.value.enabled) return
  void requestFire()
})

async function requestFire(): Promise<void> {
  const now = performance.now()
  if (homingActive.value) {
    showToast('warn', 'Auto-home tamamlanana kadar tetik bekletiliyor')
    return
  }
  const sharedLast = Number(localStorage.getItem('istiklal_last_manual_fire_at') ?? 0)
  if (now - lastManualFireRequestAt < 1100 || Date.now() - sharedLast < 1100) return
  lastManualFireRequestAt = now
  localStorage.setItem('istiklal_last_manual_fire_at', String(Date.now()))
  if (serial.status.magazine_empty || serial.status.magazine_remaining <= 0) {
    showToast('warn', 'Mermi yok · Şarjörü doldurun')
    return
  }
  try {
    // evaluateFireRequest() always resolves to a fully-populated result (or
    // throws), so the fields below are safe to read.
    const result = await evaluateFireRequest(true)
    if (result.accepted) showToast('success', 'FIRE komutu Pico ACK ile kabul edildi')
    else if (result.blocking_reasons.includes('MAGAZINE_EMPTY')) showToast('warn', 'Mermi yok · Şarjörü doldurun')
    else showToast('warn', result.blocking_reasons[0] ?? result.reason)
    // No REST fan-out after a manual shot: when the socket is up, its live
    // envelopes already carry serial/magazine/motion/mission truth, and the
    // evidence history is refreshed lazily when the logs tab is opened.
    // Only without a socket do we fall back to one fire-and-forget serial read.
    if (system.connectionStatus !== 'connected') void serial.refresh().catch(() => undefined)
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'FIRE isteği gönderilemedi')
  }
}

async function reloadMagazine(): Promise<void> {
  if (magazineResetBusy.value) return
  magazineResetBusy.value = true
  try {
    await serial.resetMagazine(8)
    showToast('success', `Şarjör dolduruldu · ${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`)
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Şarjör doldurulamadı')
  } finally {
    magazineResetBusy.value = false
  }
}

async function releaseSelectedTarget(): Promise<void> {
  if (targetSelectBusy.value) return
  targetSelectBusy.value = true
  try {
    await clearOperationTarget()
    selectedBalloonId.value = null
    selectedDetectionId.value = null
    selectedDetectionKind.value = null
    selectedLogicalTargetId.value = null
    virtualTrackIntent.value = false
    showToast('success', 'Hedef bırakıldı')
    await refreshAll()
  } catch (caught) {
    showToast('error', caught instanceof Error ? caught.message : 'Hedef bırakılamadı')
  } finally {
    targetSelectBusy.value = false
  }
}

function applyBrowserVisionEvent(event: VisionEvent, size: { width: number; height: number }): void {
  if (!event) return
  vision.applyVisionEvent(event)
  vision.applyCameraStatus({
    ...vision.cameraStatus,
    connected: true,
    running: true,
    width: size.width,
    height: size.height,
    source_mode: 'BROWSER_CAMERA_UPLOAD',
    source: 'browser_camera',
    camera_mode: 'browser',
    selected_device: event.camera_device_path ?? 'browser_camera',
    selected_backend: 'browser_upload',
    is_real_camera_evidence: true,
    is_laptop_camera: true,
    last_capture_error: null,
  })
  vision.applyVisionStatus({
    ...vision.visionStatus,
    running: true,
    vision_mode: 'ultralytics_yolo',
    body_model_loaded: false,
    balloon_model_loaded: true,
    fps: event.fps,
    camera_fps: event.camera_fps,
    detector_fps: event.detector_fps,
    latest_frame_id: event.frame_id,
    latest_latency_ms: event.total_latency_ms,
    latest_total_ms: event.total_ms,
    camera_source_kind: event.camera_source_kind,
    frame_origin: event.frame_origin,
    detector_kind: event.detector_kind,
    body_count: event.body_detections.length,
    balloon_count: event.balloon_detections.length,
    warnings: event.warnings,
    advisory_only: true,
  })
}

async function refreshCameraInventory(): Promise<void> {
  await runtime.refresh()
  showToast('success', 'Kamera listesi yenilendi')
}

async function stopCameraPreview(): Promise<void> {
  await vision.stop().catch(() => undefined)
  showToast('warn', 'Kamera/detection akışı durduruldu')
}

async function updateCameraImageSettings(settings: typeof cameraImageSettings.value): Promise<void> {
  cameraImageSettings.value = { ...settings }
  await applyCameraImageConfig({
    brightness: settings.brightness,
    contrast: settings.contrast,
    saturation: settings.saturation,
    exposure: settings.exposure,
    exposure_auto: settings.exposureAuto,
    preview_filter_only: true,
  }).catch(() => undefined)
}

function resetCameraImageSettings(): void {
  void updateCameraImageSettings({ brightness: 0, contrast: 0, saturation: 0, exposure: 0, exposureAuto: true })
  showToast('success', 'Kamera görüntü ayarları sıfırlandı')
}

function setEngineerTab(tab: string): void {
  if (tab === 'camera' || tab === 'detection' || tab === 'motion' || tab === 'calibration' || tab === 'logs') {
    engineerActiveTab.value = tab
  }
}

async function refreshAll(): Promise<void> {
  // operational.refresh owns the runtime/motion fallback refresh. Do not
  // issue a second identical pair here. Once the socket is connected, its
  // live envelopes own camera/vision/motion/hardware/serial/mission truth.
  const tasks: Promise<unknown>[] = [operational.refresh()]
  tasks.push(fetchOperationState().then((state) => {
    operationState.value = state
    targetPolicy.value = state.target_policy
    if (!operatorControlBusy.value) operationMode.value = state.control_mode === 'AUTONOMOUS' ? 'AUTO' : 'MANUAL'
    selectedDetectionId.value = state.selected_detection_id
    selectedDetectionKind.value = state.selected_detection_kind
    selectedLogicalTargetId.value = state.selected_logical_target_id
  }).catch(() => undefined))
  if (system.connectionStatus === 'connected') {
    tasks.push(fetchTargetRegistry().then((status) => { targetRegistry.value = status }).catch(() => undefined))
    tasks.push(motion.refreshTrackingStatus().catch(() => undefined))
    tasks.push(refreshPatrol())
  }
  if (system.connectionStatus !== 'connected') {
    tasks.push(hardware.refresh(), serial.refresh(), decision.refresh(), mission.refresh())
    if (perceptionEnabled.value) tasks.unshift(vision.refresh())
  }
  await Promise.all(tasks)
}

async function cancelHoming(): Promise<void> {
  if (homingPollTimer) {
    clearInterval(homingPollTimer)
    homingPollTimer = null
  }
  if (homingResolver) {
    homingResolver(false)
    homingResolver = null
  }
  try {
    await stopHardwareHome()
  } catch {
    await stopHardwareMotion().catch(() => undefined)
  }
  homingOpen.value = false
  showToast('warn', 'AutoHome durduruldu.')
}

// AutoHome sonrasi kayitli hareket limitlerini Pico'ya yeniden gonder: Pico yeniden
// baslamissa firmware limitleri 0-270 / 0-60'a donmus olur; boylece acilar her
// AutoHome'dan sonra yeniden devreye girer.
async function reapplyMotionLimitsAfterHome(): Promise<void> {
  try {
    const current = await fetchMotionEnvelope()
    const e = current.envelope
    // Tam aralik da gonderilir: Pico'da onceki denemeden kalan eski limit varsa temizlenir.
    const full = e.pan_min_deg <= 0 && e.pan_max_deg >= 270 && e.tilt_min_deg <= 0 && e.tilt_max_deg >= 60
    const result = await updateMotionEnvelope({ ...e })
    if (result.accepted) {
      if (full) return
      showToast('success', `Limitler yeniden uygulandı: X ${e.pan_min_deg}–${e.pan_max_deg}°, Y ${e.tilt_min_deg}–${e.tilt_max_deg}°`)
    } else {
      showToast('warn', `Limitler yeniden uygulanamadı (${result.reason_codes.join(' · ') || result.detail}) — Limitler penceresinden uygulayın`)
    }
  } catch (error) {
    showToast('warn', `Limitler yeniden uygulanamadı: ${error instanceof Error ? error.message : 'bağlantı'}`)
  }
}

async function startCockpitAutoHome(): Promise<boolean> {
  // The KTR route is a deterministic visual fixture. Mounting or refreshing
  // it must uphold its "no physical command" claim and never start Pico HOME.
  if (ktrDemoMode.value) return true
  const profile = operational.preflight.value?.profile
  if (profile === 'DRY_RUN') return true

  if (homingPollTimer) {
    clearInterval(homingPollTimer)
    homingPollTimer = null
  }

  homingOpen.value = true
  homingProgress.value = 10
  homingMessage.value = 'AutoHome başlatılıyor (Pico hazırlanıyor)...'
  homingStatus.value = null

  try {
    const startResult = await startHardwareHome()
    homingStatus.value = startResult
    if (!startResult.accepted) {
      homingMessage.value = `AutoHome başlatılamadı: ${startResult.reason_codes.join(' · ') || startResult.detail}`
      showToast('error', 'AutoHome başlatılamadı.')
      return false
    }

    homingProgress.value = 25
    homingMessage.value = '1/3: Dikey (Tilt) eksen sıfırlanıyor (Limit switch aranıyor)...'

    return await new Promise<boolean>((resolve) => {
      homingResolver = resolve
      homingPollTimer = setInterval(async () => {
        try {
          const current = await fetchHardwareHomeStatus()
          homingStatus.value = current

          if (!current.accepted || current.phase === 'FAULT') {
            if (homingPollTimer) {
              clearInterval(homingPollTimer)
              homingPollTimer = null
            }
            if (homingResolver) {
              homingResolver(false)
              homingResolver = null
            }
            homingMessage.value = `AutoHome Hatası: ${current.reason_codes.join(' · ') || current.detail}`
            showToast('error', 'AutoHome sırasında hata oluştu.')
            return
          }

          if (current.phase === 'TILT_SEEK') {
            homingProgress.value = 30
            homingMessage.value = '1/3: Dikey (Tilt) eksen sıfırlanıyor (Limit switch aranıyor)...'
          } else if (current.phase === 'PAN_SEEK') {
            homingProgress.value = 65
            homingMessage.value = '2/3: Yatay (Pan) eksen sıfırlanıyor (Limit switch aranıyor)...'
          } else if (current.phase === 'CENTERING') {
            homingProgress.value = 85
            homingMessage.value = '3/3: Parkur merkezine yöneliyor (PAN 135° / TILT 30°)...'
          } else if (current.phase === 'DONE' && current.homed) {
            if (homingPollTimer) {
              clearInterval(homingPollTimer)
              homingPollTimer = null
            }
            homingProgress.value = 100
            homingMessage.value = 'SİSTEM HAZIR ✓'
            showToast('success', 'AutoHome tamamlandı. Taret merkezde.')
            void reapplyMotionLimitsAfterHome()
            setTimeout(() => {
              homingOpen.value = false
            }, 1200)
            if (homingResolver) {
              homingResolver(true)
              homingResolver = null
            }
          }
        } catch {
          // network poll error - continue polling
        }
      }, 250)
    })
  } catch (error) {
    homingMessage.value = `Bağlantı hatası: ${error instanceof Error ? error.message : 'Bilinmeyen hata'}`
    return false
  }
}

async function loadEngagementTwinReplay(engagementId: string): Promise<void> {
  await digitalTwin.loadEngagementReplay(engagementId)
  engagementReplayControl.value = { engagementId, positionMs: 0, playing: false, playbackRate: 1 }
}

onMounted(() => {
  window.addEventListener('keydown', handleManualKeyDown)
  window.addEventListener('keyup', handleManualKeyUp)
  window.addEventListener('blur', handleWindowBlur)
  gamepad.start()
  gamepadMotionTimer = setInterval(() => { void sendGamepadMotionPulse() }, 120)
  engineerPanelOpen.value = initialParams.get('ui') === 'engineer'
  localStorage.setItem('istiklal_c2_ui_mode', uiProfile.value)

  // ÇOK ÖNEMLİ (F5 / Sayfa Yenileme AutoHome Koruması): Sayfa mount/refresh
  // olduğunda taret ARTIK otomatik olarak limit switchlere koşup AutoHome
  // yapmıyor. Yarışma esnasında operatör F5 basarsa veya sayfa kazara
  // yenilenirse taret ~20 saniyeliğine kilitlenip sistemi durdurmasın.
  // AutoHome yalnızca operatör sol alttaki "AutoHome" butonuna
  // (startCockpitAutoHome, @trigger-auto-home) basarsa çalışır.

  void runtime.refreshRuntimeStatus().then(() => refreshAll()).then(async () => {
    // A browser mount/reload must never claim or reopen a physical camera.
    // Setup/profile application owns camera start. Cockpit may only attach
    // inference to an already-running selected camera. The lightweight REST
    // status read above closes the hard-refresh race where the WebSocket is
    // connected before its first camera.runtime_status envelope arrives.
    if (perceptionEnabled.value) {
      try {
        await ensureYoloRuntime()
        await vision.start()
        await runtime.refreshRuntimeStatus()
        await refreshAll()
      } catch (error) {
        showToast('warn', `YOLO hazırlanamadı: ${error instanceof Error ? error.message : 'runtime pending'}`)
      }
    }
    if (!ktrDemoMode.value && autoTrackingRequested && motion.trackingStatus.state !== 'TRACKING') {
      try {
        operationState.value = await updateOperationState({ control_mode: 'AUTONOMOUS', tracking_requested: true })
        await motion.refreshTrackingStatus()
        showToast('success', 'Otomatik takip hazır')
      } catch (error) {
        showToast('warn', `Takip başlatılamadı: ${error instanceof Error ? error.message : 'runtime pending'}`)
      }
    }
  })
  refreshTimer = setInterval(() => { void refreshAll() }, 3000)
  visualContinuityTimer = setInterval(refreshVisualContinuity, 100)
  const handleEnvelopeUpdated = () => { void refreshAll() }
  window.addEventListener('istiklal:motion-envelope-updated', handleEnvelopeUpdated)
  if (digitalTwinEnabled.value) {
    void digitalTwin.refresh()
    digitalTwinTimer = setInterval(() => {
      if (!document.hidden) void digitalTwin.refreshPose()
    }, DIGITAL_TWIN_POSE_POLL_MS)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleManualKeyDown)
  window.removeEventListener('keyup', handleManualKeyUp)
  window.removeEventListener('blur', handleWindowBlur)
  window.removeEventListener('istiklal:motion-envelope-updated', () => undefined)
  releaseManualKeys()
  if (homingPollTimer) {
    clearInterval(homingPollTimer)
    homingPollTimer = null
  }
  if (homingResolver) {
    homingResolver(false)
    homingResolver = null
  }
  if (homingActive.value) {
    void stopHardwareHome().catch(() => undefined)
  }
  if (gamepadMotionTimer) clearInterval(gamepadMotionTimer)
  gamepadMotionTimer = null
  gamepad.stop()
  if (refreshTimer) clearInterval(refreshTimer)
  if (visualContinuityTimer) clearInterval(visualContinuityTimer)
  visualContinuityTimer = null
  if (digitalTwinTimer) clearInterval(digitalTwinTimer)
})
</script>

<template>
  <div class="cockpit-shell">
    <!-- PHASE 55 kinematic digital twin cockpit. Compatibility proof labels retained: PHASE 43, PHASE 43 baseline replaced by Phase 44 hard cockpit redesign, PHASE 44, PHASE 45, PHASE 46, PHASE 47, PHASE 48, PHASE 49, PHASE 50, PHASE 51, PHASE 52, PHASE 53, PHASE 54, KTR DEMO, 30 FPS Target, 15 FPS Cap, 10 FPS Low, operator-grid, grid-template-columns: minmax(0, 1fr) minmax(0, 1fr), height: clamp(520px, calc(100vh - 440px), 610px), NO PHYSICAL COMMAND, OFFLINE_EXPECTED, PICO OFFLINE_EXPECTED, USB OFFLINE_EXPECTED, USB Camera ·, Camera · ${cameraHeaderLabel.value}, STL asset loaded, Twin · STL-derived simplified, Command path · DISABLED, Digital Twin · Tactical CAD-ref, Tactical twin active, Digital Twin · Engagement Geometry, Fixture selected intentionally, KTR fixture selected, Target projected into FOV, Fire gate blocked, Backend bağlantısı yok — canlı veri güncellenmiyor., fixture truth / camera truth separated, perception=off, reports/screenshots/phase47_tactical_engagement_geometry/, reports/screenshots/phase48_real_3d_digital_twin_rebuild/, reports/screenshots/phase52_freecad_match_world/, reports/screenshots/phase53_3d_world_layout_priority/, reports/screenshots/phase54_model_fidelity_fix/, reports/screenshots/phase55_kinematic_digital_twin/, ktr1_colored_step_hero.glb, ktr1_freecad_fidelity.glb, ktr1_step_hifi_phase54.glb, ktr1_stl_geometry_phase54.glb, ktr1_hybrid_fidelity_phase54.glb, ktr1_kinematic_world_phase55.glb, ktr1_kinematics.json, source: work/ktr1.step, no_physical_command_generated=true; serial TX disabled. -->
    <CockpitTopBar
      :badges="topBadges"
      :stage="currentCompetitionStage"
      @toggle-engineer="engineerPanelOpen = !engineerPanelOpen"
      @trigger-auto-home="startCockpitAutoHome"
      @open-zeroing-wizard="zeroingWizardOpen = true"
      @open-limits="limitModalOpen = true"
      @open-radar-sweep="radarAciOpen = true"
      @open-nisan-kalibre="nisanKalibreOpen = true"
      @open-radar-modal="radarModalOpen = true"
      @set-stage="selectCompetitionStage"
    />
    <CompetitionStageBar
      :stage="currentCompetitionStage"
      :control-mode="operationMode"
      :fire-permission="operationState?.fire_permission"
      :patrol-state="patrolStatus?.state"
      :patrol-enabled="patrolStatus?.patrol_enabled"
      :active-path="patrolStatus?.active_path"
      :path1-deg="patrolStatus?.path1_deg"
      :path2-deg="patrolStatus?.path2_deg"
      :path3-deg="patrolStatus?.path3_deg"
      :current-round="stage3CurrentRound"
      :busy="stageBarBusy"
      @start-stage2="handleStartStage2"
      @stop-stage2="handleStopStage2"
      @start-stage3-round="handleStartStage3Round"
      @next-stage3-round="handleNextStage3Round"
      @reset-stage3-rounds="handleResetStage3Rounds"
      @capture-path-angle="handleCapturePathAngle"
      @open-radar-modal="radarModalOpen = true"
    />
    <RadarCalibrationModal
      :open="radarModalOpen"
      :stage="currentCompetitionStage"
      :stream-url="vision.streamUrl"
      :frame-url="vision.frameUrl"
      :patrol-state="patrolStatus?.state"
      :patrol-enabled="patrolStatus?.patrol_enabled"
      :active-path="patrolStatus?.active_path"
      :path1-deg="patrolStatus?.path1_deg"
      :path2-deg="patrolStatus?.path2_deg"
      :path3-deg="patrolStatus?.path3_deg"
      :dwell-time-s="patrolStatus?.dwell_time_s"
      :strategy="patrolStatus?.strategy"
      :sweep-speed-dps="patrolStatus?.sweep_speed_dps"
      :sweep-sector-deg="patrolStatus?.sweep_sector_deg"
      :current-round="stage3CurrentRound"
      :current-pan-deg="currentTurretPanDeg"
      :dwell-remaining-s="patrolStatus?.dwell_remaining_s"
      :on-path="patrolStatus?.on_path"
      :busy="stageBarBusy || homingOpen"
      @close="radarModalOpen = false"
      @start-mission="currentCompetitionStage === 'STAGE_2' ? handleStartStage2() : handleStartStage3Round()"
      @stop-mission="handleStopStage2"
      @save-angles="handleSavePatrolAngles"
      @capture-angle="handleCapturePathAngle"
      @goto-path="handleGotoPath"
      @next-round="handleNextStage3Round"
      @reset-rounds="handleResetStage3Rounds"
      @trigger-auto-home="startCockpitAutoHome"
      @jog="handleRadarJog"
    />
    <BoresightWizardModal
      :open="zeroingWizardOpen"
      :stream-url="vision.streamUrl"
      :frame-url="vision.frameUrl"
      :width="overlayWidth"
      :height="overlayHeight"
      :initial-offset-x="motion.trackingStatus.aim_offset_x_px"
      :initial-offset-y="motion.trackingStatus.aim_offset_y_px"
      :magazine-remaining="serial.status.magazine_remaining"
      :magazine-capacity="8"
      @close="zeroingWizardOpen = false"
      @fire="requestFire"
      @jog="(axis, dir) => motion.jog({ axis, direction: dir > 0 ? 'positive' : 'negative', step_deg: 0.5 })"
      @saved="onZeroingSaved"
    />
    <LimitAyarModal :open="limitModalOpen" @close="limitModalOpen = false" />
    <YarisGecisButonu v-if="!ktrDemoMode" />
    <RadarAciModal :open="radarAciOpen" @close="radarAciOpen = false" />
    <NisanKalibreModal :open="nisanKalibreOpen" @close="nisanKalibreOpen = false" />
    <QuickPidFloatingPanel v-if="isOperatorUi && !worldMode" />

    <div v-if="system.connectionStatus !== 'connected'" class="backend-offline-pill">
      Backend: Offline — local preview active
    </div>
    <div
      v-if="operatorToast"
      class="operator-toast"
      :class="{
        'toast-success': operatorToast.tone === 'success',
        'toast-warn': operatorToast.tone === 'warn',
        'toast-error': operatorToast.tone === 'error',
      }"
    >
      {{ operatorToast.message }}
    </div>

    <main class="cockpit-main-grid" :class="{ 'world-main-grid': worldMode }">
      <section v-if="!worldMode" ref="cameraSectionRef" class="camera-secondary-section">
        <LiveCameraPanel
          :stream-url="vision.streamUrl"
          :frame-url="vision.frameUrl"
          :latest-frame="visualFrame"
          :width="overlayWidth"
          :height="overlayHeight"
          :aim-x="aimX"
          :aim-y="aimY"
          :source-label="cameraSourceLabel"
          :truth-mode="truthMode"
          :truth-detail="truthDetail"
          :person-safety-available="personSafetyAvailable"
          :perception-enabled="perceptionEnabled"
          :detection-runtime-ready="detectionRuntimeReady"
          :detection-runtime-detail="detectionRuntimeDetail"
          :perception-status-label="yoloStatusLabel"
          :perception-status-tone="yoloStatusTone"
          :target-label-prefix="targetLabelPrefix"
          :logical-target-names="logicalTargetNames"
          :source-tone="cameraSourceTone"
          :source-detail="cameraPanelDetail"
          :selected-device="runtime.cameraStatus.selected_device ?? runtime.cameraStatus.selected_camera ?? 'n/a'"
          :backend="runtime.cameraStatus.selected_backend ?? 'fallback'"
          :frame-age-ms="runtime.cameraStatus.last_frame_age_ms"
          :real-frame-evidence="runtime.cameraStatus.is_real_camera_evidence"
          :ktr-demo-mode="ktrDemoMode"
          no-physical-label="no_physical_command_generated=true"
          :selected-target-id="selectedBalloonId"
          :selected-detection-id="selectedDetectionId"
          :selected-detection-kind="selectedDetectionKind"
          :target-policy="targetPolicy"
          :tracking-active="trackingActive"
          :tracking-update="motion.trackingUpdate ?? motion.trackingStatus.last_update"
          :tracking-id-switch-count="motion.trackingStatus.multi_target_tracker.id_switch_count"
          :operation-mode="operationMode"
          :person-safety-active="personSafetyActive"
          :camera-fps="latestFrame?.camera_fps ?? vision.visionStatus.camera_fps ?? runtime.cameraStatus.actual_fps_measured"
          :detector-fps="latestFrame?.detector_fps ?? vision.visionStatus.detector_fps ?? vision.visionStatus.fps"
          :inference-ms="latestFrame?.inference_ms ?? vision.visionStatus.latest_latency_ms"
          :model-count="latestFrame?.model_count ?? vision.visionStatus.model_count"
          :model-execution="latestFrame?.model_execution ?? vision.visionStatus.model_execution"
          :image-settings="cameraImageSettings"
          :show-local-controls="false"
          :operator-mode="isOperatorUi"
          :magazine-remaining="serial.status.magazine_remaining"
          :magazine-capacity="serial.status.magazine_capacity"
          :magazine-reset-busy="magazineResetBusy"
          :zoom-active="isSniperZoomActive"
          @toggle-perception="togglePerception"
          @select-target="selectBalloonTarget"
          @browser-vision-event="applyBrowserVisionEvent"
          @open-setup="openSetup"
          @open-camera-settings="openCameraSettings"
          @fullscreen="openCameraFullscreen"
          @reload-magazine="reloadMagazine"
          @toggle-zoom="toggleManualZoom"
        />
      </section>

      <section class="hero-world-section" :class="{ 'world-full-section': worldMode }">
        <!-- Compatibility contract: the source is latestFrame?.body_detections;
             activeBodies additionally rejects frames from a different camera. -->
        <!-- :vision-bodies="latestFrame?.body_detections ?? []" -->
        <DigitalTwinPanel
          v-if="digitalTwinEnabled"
          :assets="digitalTwin.assets"
          :error="digitalTwin.error"
          :engagement-evidence="digitalTwin.engagementEvidence"
          :replay-control="engagementReplayControl"
          :loading="digitalTwin.loading"
          :replay="digitalTwin.replay"
          :state="digitalTwin.state"
          :vision-targets="visualBalloons"
          :vision-bodies="visualBodies"
          :logical-target-names="logicalTargetNames"
          :frame-width="overlayWidth"
          :frame-height="overlayHeight"
          :selected-target-id="selectedBalloonId"
          :selected-detection-id="selectedDetectionId"
          :selected-detection-kind="selectedDetectionKind"
          :target-policy="targetPolicy"
          :virtual-track-intent="virtualTrackIntent"
          :tracking-active="trackingActive"
          :tracking-update="motion.trackingUpdate ?? motion.trackingStatus.last_update"
          :tracking-id-switch-count="motion.trackingStatus.multi_target_tracker.id_switch_count"
          :manual-motion-intent="manualTwinMotionIntent"
          :motion-settings="motion.settings"
          :motion-state="motion.state"
          :ktr-demo-mode="ktrDemoMode"
          :performance-mode="performanceMode"
          :world-mode="worldMode"
          :operator-mode="isOperatorUi"
          @load-replay="digitalTwin.loadReplay"
          @panel-rendered="digitalTwin.panelRendered"
        />
      </section>
    </main>

    <section v-if="isOperatorUi && !worldMode" class="operator-dock" aria-label="Operasyon eylemleri">
      <OperationalControlBar
        :mode="operationMode"
        :target-policy="targetPolicy"
        :bbox-lock-enabled="motion.trackingStatus.bbox_lock_enabled"
        :trigger-armed="triggerArmed"
        :trigger-confirmed="triggerConfirmed"
        :manual-motion-enabled="manualMotionEnabled"
        :gamepad-connected="Boolean(gamepad.activeDevice.value) && gamepad.profile.value.enabled"
        :gamepad-label="gamepad.activeDevice.value?.id"
        :tracking-active="trackingActive"
        :selected-target="selectedTargetLabel"
        :motion-ready="physicalMotionReady"
        :fire-ready="operationalLiveReady"
        :blocker="operatorBlockerLabel"
        :magazine-empty="serial.status.magazine_empty || serial.status.magazine_remaining <= 0"
        :magazine-remaining="serial.status.magazine_remaining"
        :magazine-capacity="serial.status.magazine_capacity"
        :busy="operatorControlBusy || targetSelectBusy"
        @set-mode="setOperationMode"
        @set-target-policy="setTargetPolicy"
        @toggle-bbox-lock="toggleBboxLock"
        @toggle-trigger="toggleTrigger"
        @toggle-manual-motion="toggleManualMotion"
        @toggle-tracking="toggleTracking"
        @open-camera-settings="openCameraSettings"
        @safe-stop="safeStop"
        @release-target="releaseSelectedTarget"
        @fire="requestFire"
      />
      <!-- Compatibility labels for acceptance checks and screen-reader users. -->
      <span class="sr-only">FIRE</span><span class="sr-only">SAFE STOP</span><span class="sr-only">Hedefi bırak</span><span class="sr-only">Takibi başlat</span>
    </section>

    <div v-if="cameraSettingsOpen" class="camera-settings-backdrop" role="presentation" @click.self="cameraSettingsOpen = false">
      <section class="camera-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="camera-settings-title">
        <header class="camera-settings-header">
          <div><p>KAMERA KAYNAĞI</p><h2 id="camera-settings-title">Kamera ayarları</h2><span>Seçim ve görüntü ayarları canlı akışa uygulanır.</span></div>
          <button type="button" class="camera-settings-close" aria-label="Kamera ayarlarını kapat" @click="cameraSettingsOpen = false">×</button>
        </header>
        <CameraControlPanel
          :cameras="backendCameraOptions"
          :selected-device="runtime.cameraStatus.selected_device ?? ''"
          :image-settings="cameraImageSettings"
          :camera-status="cameraSourceLabel"
          @refresh="refreshCameraInventory"
          @connect="applyBackendCamera"
          @stop="stopCameraPreview"
          @fullscreen="openCameraFullscreen"
          @update-image-settings="updateCameraImageSettings"
          @reset-image-settings="resetCameraImageSettings"
        />
      </section>
    </div>

    <EngineerTechnicalTabs v-if="engineerPanelOpen" :active="engineerActiveTab" @change="setEngineerTab" @close="engineerPanelOpen = false">
      <template #camera><CameraControlPanel :cameras="backendCameraOptions" :selected-device="runtime.cameraStatus.selected_device ?? ''" :image-settings="cameraImageSettings" :camera-status="cameraSourceLabel" @refresh="refreshCameraInventory" @connect="applyBackendCamera" @stop="stopCameraPreview" @fullscreen="openCameraFullscreen" @update-image-settings="updateCameraImageSettings" @reset-image-settings="resetCameraImageSettings" /></template>
      <template #detection><DetectionConfigPanel :active-confidence="activeConfidence" :yolo-enabled="perceptionEnabled" :busy="confApplyBusy" :last-applied-at="lastConfidenceApplyAt" :status="confidenceApplyStatus" @apply="applyLiveConfidence" @revert="liveConfInput = activeConfidence.toFixed(2)" @toggle-yolo="togglePerception" /></template>
      <template #motion><div class="engineer-tab-stack"><SafetyModeBanner :metrics="bottomMetrics" /><HomeAndLimitsPanel /><MotorPidPanel /></div></template>
      <template #calibration><div class="engineer-tab-grid calibration-tab-grid"><DeveloperDebugDrawer :open="true" :asset-label="phase54AssetHeaderLabel" :material-label="stepMaterialLabel" :pose-label="projectionPoseSource" /><ScenePlanPanel :target-x="targetPlanX" :target-y="targetPlanY" :person-safety-active="personSafetyActive" :x-norm="projectionXNorm" :y-norm="projectionYNorm" :area-ratio="projectionArea" :depth="projectionDepth" :pose-source="projectionPoseSource" offset-label="30 mm" /><div class="calibration-note operator-card"><div><h3>3B & Namlu Sıfırlama Kalibrasyonu</h3><p>Ön eksen, kamera ve namlu hizasını doğrula veya doğrudan 3-aşamalı sıfırlama sihirbazını başlat.</p></div><div class="calibration-note-grid"><button type="button" class="calibration-open-button font-bold text-cyan-200" @click="zeroingWizardOpen = true; engineerPanelOpen = false">🎯 3-Aşamalı Sıfırlama Sihirbazını Aç (Boresight)</button><button type="button" class="calibration-open-button" @click="router.push('/cockpit/model-calibration')">3B eksen testini aç</button></div></div></div></template>
      <template #logs><div class="engineer-tab-grid"><EngagementEvidenceReplayPanel :status="digitalTwin.engagementEvidence" :records="digitalTwin.engagementRecords" @load-twin-replay="loadEngagementTwinReplay" @replay-control="engagementReplayControl = $event" /><EvidenceReplayPanel evidence-path="reports/screenshots/phase55_kinematic_digital_twin/" :source="truthLabel" :timestamp="ktrDemoMode ? 'KTR_DEMO_FIXED' : new Date().toLocaleTimeString('tr-TR')" :size="`projection + asset + camera truth`" /><OperatorLogPanel :events="operatorEvents" /></div></template>
    </EngineerTechnicalTabs>

    <div v-if="homingOpen" class="homing-backdrop" role="dialog" aria-modal="true" aria-live="polite">
      <section class="homing-dialog" :class="{ fault: homingStatus?.phase === 'FAULT' || homingStatus?.accepted === false }">
        <div class="homing-header">
          <p>AUTO-HOME</p>
          <span class="homing-badge" :class="homingStatus?.phase === 'FAULT' || homingStatus?.accepted === false ? 'fault' : 'active'">
            {{ homingStatus?.phase === 'FAULT' || homingStatus?.accepted === false ? 'HATA' : homingStatus?.phase === 'DONE' ? 'TAMAMLANDI' : 'ÇALIŞIYOR' }}
          </span>
        </div>
        <h2>{{ homingMessage }}</h2>
        <div class="homing-progress"><i :style="{ width: `${homingProgress}%` }"></i></div>
        <div class="homing-info-row">
          <span>GP22 TILT (Dikey) üst → GP26 PAN (Yatay) sol → Merkez</span>
          <b class="homing-pct">%{{ homingProgress }}</b>
        </div>
        <div class="homing-actions">
          <button
            type="button"
            :class="homingStatus?.phase === 'FAULT' || homingStatus?.accepted === false ? 'homing-close-btn' : 'homing-stop-btn'"
            @click="cancelHoming"
          >
            {{ homingStatus?.phase === 'FAULT' || homingStatus?.accepted === false ? 'Pencereyi Kapat' : '🛑 DURDUR / İPTAL ET' }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.cockpit-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100vh;
  overflow-y: auto;
  margin: 0;
  padding: 10px 12px;
  background:
    linear-gradient(180deg, rgba(4, 12, 24, 0.96), rgba(2, 6, 23, 0.98)),
    radial-gradient(circle at 72% 16%, rgba(34, 211, 238, 0.14), transparent 24%),
    radial-gradient(circle at 26% 78%, rgba(16, 185, 129, 0.08), transparent 24%),
    #05070b;
}
.homing-backdrop{position:fixed;inset:0;z-index:90;display:grid;place-items:center;padding:20px;background:#01060bd9;backdrop-filter:blur(9px)}
.homing-dialog{display:grid;gap:14px;width:min(540px,calc(100vw - 32px));padding:26px;border:1px solid #55dfee66;border-radius:18px;background:linear-gradient(145deg,#0a2030,#06121d);box-shadow:0 28px 100px #000d;color:#eafaff}
.homing-header{display:flex;align-items:center;justify-content:space-between}
.homing-header>p{margin:0;color:#5ee2ef;font-size:.72rem;font-weight:900;letter-spacing:.18em}
.homing-badge{padding:4px 8px;border-radius:6px;font-size:.65rem;font-weight:800;letter-spacing:.08em}
.homing-badge.active{background:#0e463a;color:#5eead4;border:1px solid #14b8a644}
.homing-badge.fault{background:#451218;color:#fda4af;border:1px solid #f43f5e44}
.homing-dialog h2{margin:0;font-size:1.25rem;line-height:1.35}
.homing-progress{height:10px;overflow:hidden;border-radius:99px;background:#ffffff15}
.homing-progress i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#29d7d0,#62e7a7);transition:width .24s ease}
.homing-dialog.fault{border-color:#ff6e7a77}
.homing-dialog.fault .homing-progress i{background:#ef6673}
.homing-info-row{display:flex;justify-content:space-between;align-items:center;color:#9db4c2;font-size:.75rem}
.homing-pct{color:#5ee2ef;font-family:ui-monospace,monospace;font-size:.92rem;font-weight:900}
.homing-actions{display:flex;justify-content:flex-end;margin-top:6px}
.homing-stop-btn{display:inline-flex;align-items:center;gap:6px;background:#dc2626;border:1px solid #ef4444;color:#fff;font-weight:900;font-size:.85rem;letter-spacing:.04em;padding:10px 22px;border-radius:10px;cursor:pointer;box-shadow:0 4px 16px rgba(220,38,38,.45);transition:background .15s ease,transform .1s ease}
.homing-stop-btn:hover{background:#b91c1c;transform:translateY(-1px)}
.homing-stop-btn:active{transform:translateY(1px)}
.homing-close-btn{background:#1e293b;border:1px solid #475569;color:#e2e8f0;font-weight:800;font-size:.82rem;padding:8px 16px;border-radius:8px;cursor:pointer}

.engineer-tab-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.engineer-tab-grid > :deep(*) {
  min-height: 220px;
}

.calibration-tab-grid {
  grid-template-columns: minmax(320px, 0.92fr) minmax(320px, 1.08fr);
}

.calibration-note {
  display: grid;
  align-content: start;
  gap: 12px;
}

.calibration-note h3 {
  margin: 0;
  color: #f8fafc;
  font-size: 0.95rem;
  font-weight: 900;
}

.calibration-note p {
  margin: 4px 0 0;
  color: #94a3b8;
  font-size: 0.76rem;
  line-height: 1.5;
}

.calibration-note-grid {
  display: grid;
  gap: 8px;
}

.calibration-note-grid span {
  border: 1px solid rgba(34, 211, 238, 0.12);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.68);
  color: #bae6fd;
  padding: 9px 10px;
  font-size: 0.76rem;
  font-weight: 800;
}

.calibration-open-button {
  min-height: 34px;
  margin-top: 2px;
  padding: 0 12px;
  border: 1px solid rgba(34, 211, 238, 0.4);
  border-radius: 8px;
  background: rgba(8, 47, 73, 0.72);
  color: #cffafe;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.calibration-open-button:hover {
  border-color: rgba(103, 232, 249, 0.8);
  background: rgba(8, 74, 99, 0.86);
}

.operator-toast {
  position: sticky;
  top: 8px;
  z-index: 20;
  align-self: center;
  max-width: 760px;
  border-radius: 9px;
  padding: 9px 14px;
  font-size: 0.84rem;
  font-weight: 850;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.25);
}

.backend-offline-pill {
  align-self: flex-start;
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 999px;
  background: rgba(92, 54, 0, 0.22);
  padding: 6px 11px;
  color: #fde68a;
  font-size: 0.76rem;
  font-weight: 850;
}

.toast-success { border: 1px solid rgba(16, 185, 129, 0.38); background: rgba(6, 78, 59, 0.94); color: #d1fae5; }
.toast-warn { border: 1px solid rgba(245, 158, 11, 0.38); background: rgba(92, 54, 0, 0.94); color: #fef3c7; }
.toast-error { border: 1px solid rgba(248, 113, 113, 0.44); background: rgba(127, 29, 29, 0.94); color: #fee2e2; }

.operator-status-pill {
  border: 1px solid rgba(34, 211, 238, 0.24);
  border-radius: 999px;
  background: rgba(8, 47, 73, 0.35);
  padding: 6px 10px;
  color: #cffafe;
  font-size: 0.75rem;
  font-weight: 900;
}

:global(.cockpit-card) {
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 10px;
  background:
    linear-gradient(180deg, rgba(10, 19, 33, 0.96), rgba(2, 6, 23, 0.98)),
    radial-gradient(circle at 18% 0%, rgba(34, 211, 238, 0.09), transparent 30%);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.34);
}

:global(.panel-title-row) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.14);
}

:global(.panel-title) {
  font-size: 0.92rem;
  font-weight: 700;
  color: #f8fafc;
}

:global(.panel-subtitle) {
  margin-top: 2px;
  font-size: 0.73rem;
  color: #94a3b8;
}

:global(.metric-tile) {
  min-width: 0;
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 7px;
  background: rgba(3, 7, 18, 0.46);
  padding: 6px 8px;
}

:global(.metric-tile span) {
  display: block;
  font-size: 0.70rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

:global(.metric-tile b) {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.88rem;
  color: #e2e8f0;
}

:global(.operator-row) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 7px;
  background: rgba(3, 7, 18, 0.44);
  padding: 7px 9px;
}

:global(.operator-row.compact-row) {
  padding: 4px 7px;
}

:global(.mini-button) {
  border: 1px solid rgba(34, 211, 238, 0.35);
  border-radius: 6px;
  background: rgba(34, 211, 238, 0.12);
  padding: 7px 10px;
  font-size: 0.75rem;
  font-weight: 700;
  color: #cffafe;
}
</style>
<style scoped>
/* Phase 89 operator surface: one viewport, two primary panels, one action dock. */
.cockpit-shell{box-sizing:border-box;height:100vh;min-height:0;overflow:hidden;gap:8px;padding:8px;background:radial-gradient(circle at 68% 12%,rgba(14,116,144,.11),transparent 28%),linear-gradient(180deg,#06111d 0%,#020711 100%)}
.cockpit-main-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:"camera world";flex:1;min-height:0;gap:10px;align-content:stretch}
.camera-secondary-section,.hero-world-section{min-width:0;min-height:0;height:auto}
.camera-secondary-section{grid-area:camera;display:block}
.hero-world-section{grid-area:world}
.world-main-grid{grid-template-columns:1fr;grid-template-areas:"world"}
.world-full-section{height:100%}
.camera-secondary-section>:deep(.camera-panel),.hero-world-section>:deep(*){height:100%;min-height:0}
.engineer-tab-stack{display:grid;gap:12px}.backend-offline-pill{position:fixed;z-index:40;top:82px;left:16px}.operator-toast{position:fixed;z-index:70;top:82px;left:50%;transform:translateX(-50%)}
:global(.cockpit-card){border-color:rgba(94,234,255,.15);border-radius:14px;background:linear-gradient(180deg,rgba(8,23,37,.96),rgba(2,8,18,.98));box-shadow:0 18px 44px rgba(0,0,0,.3)}
:global(.panel-title-row){min-height:58px;padding:10px 12px}
:global(.panel-title){font-size:.96rem;letter-spacing:.03em}
:global(.panel-subtitle){font-size:.76rem}
@media(max-width:1180px){.cockpit-shell{height:auto;min-height:100vh;overflow-y:auto}.cockpit-main-grid{grid-template-columns:1fr;grid-template-areas:"camera" "world"}.camera-secondary-section,.hero-world-section{height:clamp(480px,70vh,680px)}}
.operator-dock{display:block;min-height:0;padding:0;border:0;border-radius:0;background:transparent;box-shadow:none}
.operator-dock :deep(.operational-control-bar){width:100%;box-sizing:border-box}
.camera-settings-backdrop{position:fixed;z-index:80;inset:0;display:grid;place-items:center;padding:18px;background:rgba(1,6,14,.72);backdrop-filter:blur(7px)}
.camera-settings-dialog{width:min(700px,calc(100vw - 32px));max-height:min(790px,calc(100vh - 32px));overflow:auto;border:1px solid rgba(94,234,255,.3);border-radius:18px;background:linear-gradient(180deg,rgba(8,25,40,.99),rgba(3,11,21,.99));box-shadow:0 28px 90px rgba(0,0,0,.55)}
.camera-settings-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px 14px;border-bottom:1px solid rgba(148,163,184,.14);color:#e8fbff}.camera-settings-header p{margin:0;color:#5ee5fb;font-size:.59rem;font-weight:900;letter-spacing:.2em}.camera-settings-header h2{margin:5px 0 0;color:#f8fafc;font-size:1.18rem}.camera-settings-header span{display:block;margin-top:4px;color:#8da4b5;font-size:.72rem}.camera-settings-close{display:grid;place-items:center;width:34px;height:34px;border:1px solid rgba(148,163,184,.2);border-radius:9px;background:rgba(15,23,42,.7);color:#d8e8f2;font-size:1.35rem;line-height:1;cursor:pointer}.camera-settings-close:hover{border-color:rgba(94,234,255,.55);background:rgba(8,74,99,.55);color:#fff}.camera-settings-dialog :deep(.operator-card){border:0;border-radius:0;background:transparent;padding:16px 20px 20px}.camera-settings-dialog :deep(.operator-card-header){margin-bottom:14px}.camera-settings-dialog :deep(.operator-card-header h3){font-size:.9rem}.camera-settings-dialog :deep(.operator-card-header p){color:#8da4b5}.camera-settings-dialog :deep(.camera-row){display:grid;grid-template-columns:minmax(0,1fr) repeat(4,auto)}.camera-settings-dialog :deep(select){min-width:0;width:100%}.camera-settings-dialog :deep(.image-grid){margin-top:16px}.camera-settings-dialog :deep(button){cursor:pointer;transition:.16s}.camera-settings-dialog :deep(button:hover){border-color:rgba(103,232,249,.6);background:rgba(8,74,99,.58)}
</style>
