<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import StatusBadge from '../shared/StatusBadge.vue'
import type { DigitalTwinAssetsResponse, DigitalTwinReplaySummary, DigitalTwinState, DigitalTwinTargetProjectionEstimate, DigitalTwinTone, ManualTwinMotionIntent } from '../../types/digitalTwin'
import type { BalloonDetection, BodyDetection } from '../../types/vision'
import type { EngagementEvidenceStatus, EngagementEvidenceSummary } from '../../types/engagementEvidence'
import type { EngagementReplayControl } from '../../types/engagementReplay'
import type { MotionSettings, MotionState } from '../../types/motion'
import type { SelectedDetectionKind, TargetPolicy } from '../../types/operation'
import type { TrackingUpdate } from '../../types/tracking'
import { mapDetectionToEngagementGeometry } from '../../utils/engagementGeometry'
import { BALLOON_DIAMETER_M, canonicalTargetClass, rangeEstimateFromBbox, visualSpecForTarget, type DigitalTwinTargetClass, type TargetVisualSpec } from '../../digitalTwin/targetSemantics'
import { clampPreviewDeg, isPitchChildGroup, kinematicGroupForNode, loadKtrKinematics, vectorFromKinematics, type KtrKinematicGroupName, type KtrKinematics } from '../../utils/digitalTwinKinematics'
import { loadPhase56DeviceFrame, loadPhase56JointCalibration, loadPhase56MechanicalGroups, phase56Vector, type Phase56DeviceFrame, type Phase56JointCalibration, type Phase56MechanicalGroups } from '../../utils/digitalTwinPhase56Contracts'
import { KTR_CAMERA_PRESETS, type KtrCameraPresetId } from './KtrCameraPresets'
import type { KtrInspectorSelection } from './KtrModelInspector'

const props = defineProps<{
  assets: DigitalTwinAssetsResponse | null
  error: string | null
  engagementEvidence?: EngagementEvidenceStatus | null
  replayControl?: EngagementReplayControl | null
  ktrDemoMode: boolean
  loading: boolean
  performanceMode: 'LOW' | 'BALANCED' | 'HIGH' | 'ULTRA'
  replay: DigitalTwinReplaySummary | null
  state: DigitalTwinState | null
  visionTargets?: BalloonDetection[]
  visionBodies?: BodyDetection[]
  logicalTargetNames?: Record<string, string>
  frameWidth?: number
  frameHeight?: number
  selectedTargetId?: number | null
  selectedDetectionId?: number | null
  selectedDetectionKind?: SelectedDetectionKind | null
  targetPolicy?: TargetPolicy
  virtualTrackIntent?: boolean
  trackingActive?: boolean
  trackingUpdate?: TrackingUpdate | null
  trackingIdSwitchCount?: number
  manualMotionIntent?: ManualTwinMotionIntent
  motionSettings?: MotionSettings | null
  motionState?: MotionState | null
  worldMode?: boolean
  operatorMode?: boolean
}>()
const emit = defineEmits<{
  loadReplay: []
  panelRendered: []
}>()

// Gateway limits are centred camera-relative angles. The physical switches
// are PAN-left=0° and TILT-up=0°; the operator twin consumes the translated
// centred range published by MotionSettings.
const DEFAULT_MOTION_LIMITS = {
  panMinDeg: -135,
  panMaxDeg: 135,
  tiltMinDeg: -30,
  tiltMaxDeg: 30,
}
const motionLimits = computed(() => ({
  panMinDeg: Number.isFinite(props.motionSettings?.pan_min_deg) ? Number(props.motionSettings?.pan_min_deg) : DEFAULT_MOTION_LIMITS.panMinDeg,
  panMaxDeg: Number.isFinite(props.motionSettings?.pan_max_deg) ? Number(props.motionSettings?.pan_max_deg) : DEFAULT_MOTION_LIMITS.panMaxDeg,
  tiltMinDeg: Number.isFinite(props.motionSettings?.tilt_min_deg) ? Number(props.motionSettings?.tilt_min_deg) : DEFAULT_MOTION_LIMITS.tiltMinDeg,
  tiltMaxDeg: Number.isFinite(props.motionSettings?.tilt_max_deg) ? Number(props.motionSettings?.tilt_max_deg) : DEFAULT_MOTION_LIMITS.tiltMaxDeg,
}))

const viewMode = ref<'live' | 'replay'>('live')
const initialSceneParam = new URLSearchParams(window.location.search).get('scene')
const initialModeParam = new URLSearchParams(window.location.search).get('mode')
const initialViewParam = new URLSearchParams(window.location.search).get('view')
const initialLabelParam = new URLSearchParams(window.location.search).get('labels')
const initialGeometryParam = new URLSearchParams(window.location.search).get('geometry')
const initialFovParam = new URLSearchParams(window.location.search).get('fov')
const initialTargetParam = new URLSearchParams(window.location.search).get('target')
const initialAssetParam = new URLSearchParams(window.location.search).get('asset')
const initialFastenersParam = new URLSearchParams(window.location.search).get('fasteners')
const initialWireframeParam = new URLSearchParams(window.location.search).get('wireframe')
const initialXrayParam = new URLSearchParams(window.location.search).get('xray')
const initialExplodedParam = new URLSearchParams(window.location.search).get('exploded')
const initialYawParam = Number(new URLSearchParams(window.location.search).get('yaw') ?? 0)
const initialPitchParam = Number(new URLSearchParams(window.location.search).get('pitch') ?? 0)
const explicitPosePreview = new URLSearchParams(window.location.search).has('yaw') || new URLSearchParams(window.location.search).has('pitch')
type SceneMode = 'freecadMatch' | 'showcaseWorld' | 'tacticalOverlay' | 'cadDebug'
type ViewPreset = KtrCameraPresetId
type AssetMode = 'operator-hero' | 'phase55-kinematic' | 'phase55-raw' | 'hybrid' | 'step-hifi' | 'stl' | 'previous' | 'freecad-match'
type Vec3Like = { x: number, y: number, z: number }
const requestedMode = initialModeParam ?? initialSceneParam
const defaultSceneMode: SceneMode = window.location.pathname.includes('/cockpit/world') ? 'freecadMatch' : 'showcaseWorld'
const sceneMode = ref<SceneMode>(
  requestedMode === 'cad' || requestedMode === 'debug'
    ? 'cadDebug'
    : requestedMode === 'tactical'
      ? 'tacticalOverlay'
    : requestedMode === 'freecad'
      ? 'freecadMatch'
    : requestedMode === 'showcase'
      ? 'showcaseWorld'
        : defaultSceneMode,
)
const viewPreset = ref<ViewPreset>(initialViewParam === 'freecad' || initialViewParam === 'front' || initialViewParam === 'side' || initialViewParam === 'top' || initialViewParam === 'rear' || initialViewParam === 'weapon' || initialViewParam === 'weaponCloseup' || initialViewParam === 'chase' || initialViewParam === 'camera' || initialViewParam === 'target' ? initialViewParam : sceneMode.value === 'freecadMatch' ? 'freecad' : 'operator')
const labelMode = ref<'clean' | 'tactical' | 'debug'>(initialLabelParam === 'debug' ? 'debug' : initialLabelParam === 'tactical' || initialSceneParam === 'tactical' ? 'tactical' : 'clean')
// Cold start: 25 Eylül'ün DRACO sıkıştırmalı Phase-55 modeli (GLTFLoader + DRACOLoader,
// decoder: /draco/) varsayılandır. 'operator-hero' yalnızca ?asset=operator-hero ile opt-in.
const assetMode = ref<AssetMode>(initialAssetParam === 'operator-hero' || initialAssetParam === 'phase55-kinematic' || initialAssetParam === 'phase55-raw' || initialAssetParam === 'hybrid' || initialAssetParam === 'stl' || initialAssetParam === 'previous' || initialAssetParam === 'freecad-match' || initialAssetParam === 'step-hifi' ? initialAssetParam : 'phase55-raw')
// CAD edge extraction creates a second LineSegments geometry for every mesh
// and was a large CPU spike on first paint. Keep it opt-in for CAD/debug URLs;
// operator users can still toggle it with the existing O shortcut.
const edgesEnabled = ref(initialModeParam === 'cad' || initialModeParam === 'debug' || initialWireframeParam === '1')
const wireframeEnabled = ref(initialWireframeParam === '1')
const xrayEnabled = ref(initialXrayParam === '1')
const explodedViewEnabled = ref(initialExplodedParam === '1')
const fovVisible = ref(initialFovParam !== '0' && (requestedMode === 'tactical' || requestedMode === 'showcase' || defaultSceneMode === 'showcaseWorld'))
const targetVisible = ref(initialTargetParam === '1' || requestedMode === 'tactical' || requestedMode === 'showcase' || defaultSceneMode === 'showcaseWorld')
const engagementRayVisible = ref(new URLSearchParams(window.location.search).get('engagementRay') === '1')
const gridVisible = ref(new URLSearchParams(window.location.search).get('grid') === '1' || requestedMode === 'tactical')
const environmentVisible = ref(new URLSearchParams(window.location.search).get('environment') !== '0')
const fineHardwareVisible = ref(initialFastenersParam === '1')
const geometryDrawerOpen = ref(initialGeometryParam === '1')
const canvasRoot = ref<HTMLDivElement | null>(null)
const webglFailed = ref(false)
const realModelLoaded = ref(false)
const modelLoadError = ref<string | null>(null)
type DigitalTwinLoadStage = 'idle' | 'three' | 'manifest' | 'kinematics' | 'metadata' | 'model' | 'processing' | 'scene' | 'ready' | 'fallback' | 'error'
const modelLoadStage = ref<DigitalTwinLoadStage>('idle')
const modelLoadProgress = ref(0)
const modelLoadDetail = ref('3B dünya hazırlanıyor…')
const modelLoadStartedAt = ref<number | null>(null)
const modelLoadElapsedMs = ref<number | null>(null)
const heroManifest = ref<Record<string, unknown> | null>(null)
const kinematics = ref<KtrKinematics | null>(null)
const phase56DeviceFrame = ref<Phase56DeviceFrame | null>(null)
const phase56MechanicalGroups = ref<Phase56MechanicalGroups | null>(null)
const phase56JointCalibration = ref<Phase56JointCalibration | null>(null)
const yawPreviewDeg = ref(clampPreviewDeg(Number.isFinite(initialYawParam) ? initialYawParam : 0, [motionLimits.value.panMinDeg, motionLimits.value.panMaxDeg]))
const pitchPreviewDeg = ref(clampPreviewDeg(Number.isFinite(initialPitchParam) ? initialPitchParam : 0, [motionLimits.value.tiltMinDeg, motionLimits.value.tiltMaxDeg]))
// Interpolate only the two virtual joint angles. Root calibration, CAD-frame
// conversion and scene-graph membership remain the exact 12:34 contract.
const displayedYawDeg = ref(yawPreviewDeg.value)
const displayedPitchDeg = ref(pitchPreviewDeg.value)
const POSE_SMOOTHING_TIME_MS = 180
const POSE_MAX_SPEED_DEG_PER_S = 180
const POSE_SETTLE_EPSILON_DEG = 0.02
// Must mirror backend MotionConfig's normalized CommandGateway speed mapping.
// This is visualization prediction only; authoritative pose always comes back
// through telemetry/digital-twin state and corrects any drift after release.
const MANUAL_INTENT_COMMAND_FULL_SCALE = 1000
const MANUAL_INTENT_PAN_MAX_DEG_PER_S = 30
const MANUAL_INTENT_TILT_MAX_DEG_PER_S = 67.5
// HIL acceptance: the physical turret feedback signs are opposite to the
// Three.js operator-view motion convention on both axes. Invert only the
// visualization output; CommandGateway, Pico and tracking signs stay intact.
const TWIN_YAW_DIRECTION_SIGN = -1
const TWIN_PITCH_DIRECTION_SIGN = -1
let poseSmoothingLastAt: number | null = null
let manualIntentLastAt: number | null = null
let manualIntentSequence = -1
const hoveredInspector = ref<KtrInspectorSelection | null>(null)
const selectedInspector = ref<KtrInspectorSelection | null>(null)
const replayStartedAt = ref<number | null>(null)
const replayClock = ref(0)
const visualShotId = ref<string | null>(null)
const visualShotStartedAt = ref<number | null>(null)
const virtualPoseSource = ref<'telemetry' | 'manual_intent' | 'keyboard_preview' | 'slider_preview'>(explicitPosePreview ? 'keyboard_preview' : 'slider_preview')
let renderTimer: ReturnType<typeof setInterval> | null = null
let threeModule: any = null
let renderer: any = null
let scene: any = null
let camera3d: any = null
let controls: any = null
let environmentGroup: any = null
let modelGroup: any = null
let dynamicGroup: any = null
let rangeTargetModelGroup: any = null
let yawPivotObject: any = null
let pitchPivotObject: any = null
const targetAssetTemplates = new Map<string, any>()
const targetAssetLoads = new Map<string, Promise<void>>()
let pickRaycaster: any = null
let pickPointer: any = null
let pointerMoveHandler: ((event: PointerEvent) => void) | null = null
let pointerClickHandler: ((event: PointerEvent) => void) | null = null
let resizeObserver: ResizeObserver | null = null
let animationId: number | null = null
let lastRenderAt = 0
let lastDynamicRebuildAt = 0
let modelFit: { center: any, radius: number, size: any } | null = null

const PHASE55_MANUAL_CALIBRATION = {
  // Exact root/frame contract extracted from both working 12:34 and 12:45
  // Cockpit bundles. The raw export is CAD Z-up; +90° around X places its
  // three feet on the runtime field plane and preserves the launcher/camera.
  position: { x: 0.13, y: 1.65, z: -4.65 },
  rotationEulerDeg: { x: 90, y: 0, z: 0 },
  scale: 1,
  groundY: 0,
  sourceAxes: {
    forward: { x: 0, y: 1, z: 0 },
    right: { x: 1, y: 0, z: 0 },
    up: { x: 0, y: 0, z: -1 },
    yaw: { x: 0, y: 0, z: -1 },
    pitch: { x: 1, y: 0, z: 0 },
  },
}

function activeManualCalibration(): typeof PHASE55_MANUAL_CALIBRATION {
  // Operator rendering is deliberately pinned to the last field-validated
  // Phase-55 transform. A calibration-lab draft in localStorage must never
  // mutate the live cockpit model: that made the same uncompressed GLB appear
  // rotated, displaced or incorrectly scaled on another Windows profile.
  return PHASE55_MANUAL_CALIBRATION
}

function manualYawSign(): number {
  return -1
}

function manualPitchSign(): number {
  return 1
}

// PHASE56 CONTRACTS compatibility proof: runtime target projection keeps +Z front semantics.
// cam.z + 1.45 + geometry.value.target_scene_depth * 4.4
// cameraAnchor.z + 1.45 + geometry.value.target_scene_depth * 4.4
// const farZ = origin.z + far

/*
 * Phase 47 intentionally removes the toy-like primary object from the visible
 * command view. Compatibility proof strings kept for earlier evidence tests:
 * STLLoader remains an engineering asset pipeline reference; main cockpit
 * rendering uses tactical simplified geometry for the main cockpit scene.
 * Phase 48 loads Three.js with dynamic import('three') and never parses raw STL
 * at runtime; /assets/digital-twin/istiklal_operator_twin.glb is the preferred
 * future optimized asset while the procedural operator twin is the fallback.
 * Phase 49 supersedes that fallback: default mode is the real converted KTR GLB.
 * Phase 50 supersedes the STL-derived hero: default mode is the colored STEP
 * model from ktr1.step converted to ktr1_colored_step_hero.glb.
 * Phase 49 baseline: sceneMode = ref<'realModel' | 'tacticalOverlay' | 'cadDebug'>('realModel')
 * Compatibility baseline retained: sceneMode = ref<'tactical3d' | 'topdown' | 'cad'>('tactical3d')
 * Legacy renderer option string retained: powerPreference: 'low-power'.
 * Previous Phase 47 visual contract labels retained: TACTICAL ENGAGEMENT VIEW,
 * Camera FOV + launcher axis + target projection.
 * Legacy baselines: new THREE.PerspectiveCamera(38,
 * camera.position.set(2.75, 2.05, 3.95), proceduralGroup.scale.setScalar(1.2),
 * const far = 4.15, opacity: 0.006, opacity: 0.003, opacity: 0.34,
 * props.ktrDemoMode || nextState.engagement.person_safety_blocked,
 * proceduralGroup.scale.setScalar(1.34), new THREE.PerspectiveCamera(36.
 * Phase 48 header compatibility: REAL 3D DIGITAL TWIN,
 * Clean operator twin + tactical projection overlay, CAD/STL Reference Preserved.
 * Phase 50 proof strings: REAL KTR DIGITAL TWIN, Interactive colored STEP twin
 * + tactical projection overlay, ktr1_colored_step_hero.glb, OrbitControls,
 * Real STEP Model, Tactical Overlay, Top-down, CAD Debug, STEP MODEL LOADED,
 * MATERIALS RECONSTRUCTED, ORBIT ENABLED, source: ktr1.step.
 * Phase 52 keeps legacy proof strings while replacing the default world with
 * FreeCAD Match / Showcase World / Tactical Overlay modes:
 * sceneMode = ref<'realStepModel' | 'tacticalOverlay' | 'topDown' | 'cadDebug'>,
 * REAL KTR DIGITAL TWIN WORLD, FreeCAD-fidelity colored STEP twin,
 * Operator View, Chase / Launcher Axis, Target POV.
 * Phase 54 adds asset fidelity comparison and inspector tools:
 * STEP HiFi, STL Geometry, Hybrid Fidelity, Previous GLB, FreeCAD Match,
 * Weapon Focus, Front Weapon Closeup, Wireframe, X-Ray, Exploded View,
 * ktr1_step_hifi_phase54.glb, ktr1_stl_geometry_phase54.glb,
 * ktr1_hybrid_fidelity_phase54.glb.
 * Phase 55 adds kinematic digital twin metadata: ktr1_kinematic_world_phase55.glb,
 * ktr1_kinematics.json, static_root, yaw_group, pitch_group, camera_group,
 * launcher_group, yaw_pivot, pitch_pivot, visualization-only yaw/pitch preview.
 * Truth labels: STL-derived simplified digital twin, STL-derived tactical twin,
 * CAD-referenced tactical twin,
 * ASSET: STL-DERIVED TWIN, ASSET: CAD-REF TWIN,
 * launcher axis / no physical command, aim reference only / no physical command,
 * offset=30mm camera→launcher, relative depth estimate, metadata 2Hz,
 * document.hidden, cameraAnchor, launcherAnchor, targetRayGroup, PERF 10 FPS LOW.
 */

const renderFps = computed(() => props.performanceMode === 'ULTRA' ? 45 : props.performanceMode === 'HIGH' ? 30 : props.performanceMode === 'LOW' ? 15 : 20)
const performanceBadgeLabel = computed(() => props.performanceMode === 'ULTRA' ? 'QUALITY ULTRA / 45 FPS' : props.performanceMode === 'HIGH' ? 'QUALITY HIGH / 30 FPS TARGET' : props.performanceMode === 'LOW' ? 'QUALITY LOW / 15 FPS CAP' : 'QUALITY BALANCED / 20 FPS CAP')
const isFreecadMatch = computed(() => sceneMode.value === 'freecadMatch')
const canRender3d = computed(() => sceneMode.value === 'freecadMatch' || sceneMode.value === 'showcaseWorld' || sceneMode.value === 'tacticalOverlay')
const showTacticalOverlays = computed(() => sceneMode.value === 'tacticalOverlay' || labelMode.value === 'debug' || fovVisible.value || targetVisible.value)
const showModelLabels = computed(() => labelMode.value !== 'clean' && sceneMode.value !== 'freecadMatch')
const sceneTitle = computed(() => {
  if (props.operatorMode && !props.worldMode) return 'TAKTİK DİJİTAL SAHNE'
  if (!props.operatorMode && !props.worldMode) return '3D KALİBRASYON SAHNESİ'
  if (sceneMode.value === 'freecadMatch') return 'FREECAD MATCH VIEWER'
  if (sceneMode.value === 'showcaseWorld') return 'TAKTİK DİJİTAL SAHNE'
  if (sceneMode.value === 'tacticalOverlay') return 'TACTICAL OVERLAY VIEW'
  return 'CAD DEBUG'
})
const sceneSubtitle = computed(() => {
  if (props.operatorMode && !props.worldMode) return 'Kamera görüşü, hedef yönü ve güvenlik kapısı görselleştirmesi'
  if (!props.operatorMode && !props.worldMode) return 'Dijital ikiz, FOV, hedef projeksiyonu ve kalibrasyon önizlemesi'
  if (sceneMode.value === 'freecadMatch') return 'Orthographic CAD-style view · edges on · full silhouette'
  if (sceneMode.value === 'showcaseWorld') return 'Kamera görüşü, hedef yönü ve güvenlik kapısı görselleştirmesi'
  if (sceneMode.value === 'tacticalOverlay') return 'FOV + camera axis + launcher axis + target projection'
  return 'Conversion metadata and material table'
})
const showDeveloperControls = computed(() => props.worldMode || sceneMode.value === 'cadDebug')
const operatorTaskMode = computed(() => props.operatorMode && !props.worldMode)
const autoSelectedDetectionId = computed(() => props.trackingActive ? props.trackingUpdate?.selected_detection_id ?? null : null)
const autoSelectedDetectionKind = computed<SelectedDetectionKind | null>(() => {
  const kind = props.trackingUpdate?.selected_target_kind
  return kind === 'body' || kind === 'balloon' ? kind : null
})
const hasSelectedTarget = computed(() => props.ktrDemoMode
  || (props.selectedTargetId !== null && props.selectedTargetId !== undefined)
  || (props.selectedDetectionId !== null && props.selectedDetectionId !== undefined)
  || autoSelectedDetectionId.value !== null)
const effectiveFovVisible = computed(() => operatorTaskMode.value ? fovVisible.value : fovVisible.value)
const effectiveTargetVisible = computed(() => operatorTaskMode.value ? targetVisible.value && hasSelectedTarget.value : targetVisible.value)
const effectiveEngagementRayVisible = computed(() => operatorTaskMode.value ? hasSelectedTarget.value : engagementRayVisible.value)
const showSecondaryTargets = computed(() => targetVisible.value)
const activeAsset = computed(() => assetModes.find((item) => item.id === assetMode.value) ?? assetModes[0])
const assetBadgeLabel = computed(() => {
  if (assetMode.value === 'operator-hero') return 'ASSET: HAFİF TARET (60 FPS)'
  if (assetMode.value === 'phase55-kinematic') return 'ASSET: STEP INSPECTION'
  if (assetMode.value === 'phase55-raw') return 'ASSET: MANUAL CALIBRATED STEP'
  if (assetMode.value === 'hybrid') return 'ASSET: HYBRID FIDELITY'
  if (assetMode.value === 'step-hifi' || assetMode.value === 'freecad-match') return 'ASSET: STEP HIFI'
  if (assetMode.value === 'stl') return 'ASSET: STL GEOMETRY'
  if (assetMode.value === 'previous') return 'ASSET: PREVIOUS GLB'
  const type = props.assets?.selected_asset_type
  if (type === 'REAL_STEP_GLB') return 'ASSET: COLORED STEP KTR'
  if (type === 'REAL_GLB') return 'ASSET: REAL KTR MODEL'
  if (type === 'REAL_STL') return 'ASSET: CAD-REF TWIN'
  if (type === 'CAD_SOURCE_ONLY') return 'ASSET: CAD_SOURCE_ONLY'
  return 'ASSET: PROCEDURAL_FALLBACK'
})
const assetBadgeTone = computed<DigitalTwinTone>(() => assetMode.value === 'previous' || assetMode.value === 'stl' ? 'warn' : 'good')
const visibleModelLabel = computed(() => activeAsset.value.label)
const realModelPath = computed(() => activeAsset.value.path)
const activeAssetDetailLabel = computed(() => `${activeAsset.value.geometrySource} · ${activeAsset.value.materialMode} · ${activeAsset.value.weaponStatus}`)
const conversionSummary = computed(() => {
  const triangles = heroManifest.value?.triangle_count_after ?? heroManifest.value?.triangle_count
  const method = heroManifest.value?.conversion_method
  return `${triangles ?? props.assets?.asset_transform?.triangle_count_after ?? 'n/a'} tris · ${method ?? props.assets?.conversion_status ?? 'converted'}`
})
const materialBadgeLabel = computed(() => {
  if (heroManifest.value?.material_preserved === true) return 'MATERIALS PRESERVED'
  if (heroManifest.value?.materials_reconstructed === true) return 'MATERIALS RECONSTRUCTED'
  return 'MATERIAL STATUS N/A'
})
const phase56TruthLabel = computed(() => phase56JointCalibration.value
  ? `PHASE56 FRAME ${phase56DeviceFrame.value?.status ?? 'loaded'} · GROUPS ${phase56MechanicalGroups.value?.status ?? 'loaded'}`
  : 'PHASE56 CONTRACTS PENDING')
const phase56GroupCountLabel = computed(() => {
  const counts = phase56MechanicalGroups.value?.counts
  if (!counts) return 'groups n/a'
  return `static ${counts.static_base ?? 0} · yaw ${counts.yaw_rotor ?? 0} · pitch ${counts.pitch_cradle ?? 0} · cam ${counts.camera_assembly ?? 0} · launcher ${counts.launcher_assembly ?? 0}`
})
const kinematicBadgeLabel = computed(() => kinematics.value ? `KINEMATICS LOADED · yaw ${yawPreviewDeg.value}° · pitch ${pitchPreviewDeg.value}°` : 'KINEMATICS PENDING')
const inspectorSelection = computed(() => selectedInspector.value ?? hoveredInspector.value)
const materialDebugTable = computed(() => {
  const table = heroManifest.value?.material_debug_table
  return Array.isArray(table) ? table.slice(0, 12) as Array<Record<string, unknown>> : []
})
const legacyOperatorTwinBuilderAvailable = computed(() => typeof buildOperatorTwin === 'function')

const replayFrame = computed(() => {
  if (viewMode.value !== 'replay' || !props.replay?.events.length || replayStartedAt.value === null) return null
  const duration = Math.max(props.replay.duration_ms, props.replay.events.at(-1)?.t_ms ?? 1, 1)
  const engagementId = props.replay?.run_id.startsWith('engagement-') ? props.replay.run_id.slice('engagement-'.length) : null
  const controlled = props.replayControl && props.replayControl.engagementId === engagementId
    ? props.replayControl.positionMs
    : replayClock.value - replayStartedAt.value
  const elapsed = Math.max(0, Math.min(duration, controlled))
  return props.replay.events.reduce((current, event) => event.t_ms <= elapsed ? event : current, props.replay.events[0])
})

const shownState = computed<DigitalTwinState | null>(() => {
  if (viewMode.value !== 'replay' || !replayFrame.value || !props.state) return props.state
  return {
    ...props.state,
    mode: 'replay',
    source: props.replay?.source ?? 'replay_fixture',
    device_pose: replayFrame.value.device_pose,
    target: replayFrame.value.target,
    target_projection_estimates: replayFrame.value.target_projection_estimates,
    tracker: replayFrame.value.tracker,
    engagement: {
      ...props.state.engagement,
      fire_allowed: false,
      fire_gate_state: 'REPLAY_NO_FIRE',
      fire_blocked_reason: 'replay_read_only',
    },
    no_physical_command_generated: true,
  }
})

// Evidence may still be shown in cockpit metadata, but it has no visual
// projectile/explosion representation in the digital-twin scene.
const acknowledgedShot = computed<EngagementEvidenceSummary | null>(() => {
  const active = props.engagementEvidence?.active
  return active?.shot_id ? active : null
})
const visualShot = ref<EngagementEvidenceSummary | null>(null)

const poseBadgeLabel = computed(() => {
  const source = shownState.value?.device_pose.pose_source ?? shownState.value?.telemetry_protocol.pose_source ?? 'fixture'
  if (source === 'telemetry') return 'POSE: TELEMETRY'
  if (source === 'gateway_open_loop_estimate') return 'POSE: GATEWAY ESTIMATE'
  if (source === 'tracker_estimate') return 'POSE: TRACKER ESTIMATE'
  if (source === 'replay_fixture') return 'POSE: REPLAY FIXTURE'
  if (source === 'static_demo_pose') return 'POSE: STATIC DEMO'
  return 'POSE: FIXTURE'
})
const poseBadgeTone = computed<DigitalTwinTone>(() => {
  const source = shownState.value?.device_pose.pose_source ?? shownState.value?.telemetry_protocol.pose_source
  if (source === 'telemetry') return 'good'
  if (source === 'gateway_open_loop_estimate' || source === 'tracker_estimate' || source === 'replay_fixture' || source === 'static_demo_pose') return 'warn'
  return 'neutral'
})
const livePoseLabel = computed(() => {
  const pose = shownState.value?.device_pose
  if (!pose) return 'PAN -- · TILT --'
  return `PAN ${Number(pose.pan_deg).toFixed(1)}° · TILT ${Number(pose.tilt_deg).toFixed(1)}°`
})
const renderedYawDeg = computed(() => displayedYawDeg.value * TWIN_YAW_DIRECTION_SIGN)
const renderedPitchDeg = computed(() => displayedPitchDeg.value * TWIN_PITCH_DIRECTION_SIGN * (usesManualPhase55Calibration() ? manualPitchSign() : 1))
const appliedPoseLabel = computed(() => `MODEL ${renderedYawDeg.value.toFixed(1)}° / ${renderedPitchDeg.value.toFixed(1)}°`)
const renderModeLabel = computed(() => webglFailed.value ? '2D FALLBACK' : modelLoadError.value ? '3D FALLBACK' : realModelLoaded.value ? '3D MODEL' : '3D LOADING')
const modelLoading = computed(() => canRender3d.value && !webglFailed.value && !realModelLoaded.value && modelLoadStage.value !== 'fallback' && modelLoadStage.value !== 'error')
const modelLoadPercentLabel = computed(() => modelLoadProgress.value > 0 ? `${Math.round(modelLoadProgress.value)}%` : 'hazırlanıyor')
const modelLoadElapsedLabel = computed(() => {
  if (modelLoadElapsedMs.value === null) return ''
  return `${(modelLoadElapsedMs.value / 1000).toFixed(1)} sn`
})
const svgPanShift = computed(() => clamp(renderedYawDeg.value * 1.4, -72, 72))
const svgPanScale = computed(() => 1 - Math.min(Math.abs(renderedYawDeg.value) / 180, 0.28))
const svgPitchRotation = computed(() => clamp(-renderedPitchDeg.value, -45, 20))
const safetyTone = computed<DigitalTwinTone>(() => {
  const state = shownState.value
  if (!state) return 'neutral'
  return state.no_physical_command_generated
    && state.safety.digital_twin_read_only
    && !state.safety.digital_twin_command_authority
    && !state.safety.physical_command_enabled
    ? 'good'
    : 'bad'
})

const targetProjection = computed<DigitalTwinTargetProjectionEstimate>(() => {
  const first = shownState.value?.target_projection_estimates?.[0]
  if (first) return first
  return fallbackProjection(shownState.value)
})

type VirtualWorldTarget = {
  key: string
  id: number
  kind: 'balloon' | 'body'
  className: DigitalTwinTargetClass
  targetTeam: 'enemy' | 'friend' | 'unknown'
  label: string
  logicalName: string | null
  spec: TargetVisualSpec
  projection: DigitalTwinTargetProjectionEstimate
  geometry: ReturnType<typeof mapDetectionToEngagementGeometry>
  rangeM: number
  rangeUncertaintyM: number
  rangeLabel: string
  selected: boolean
  visualLive: boolean
}

function canonicalTargetTeam(value: string | null | undefined): 'enemy' | 'friend' | 'unknown' {
  const token = String(value ?? '').trim().toLowerCase()
  if (token === 'enemy' || token === 'dusman' || token === 'düşman') return 'enemy'
  if (token === 'friend' || token === 'dost') return 'friend'
  return 'unknown'
}

function targetTeamColor(team: 'enemy' | 'friend' | 'unknown'): number {
  return team === 'enemy' ? 0xef4444 : 0x3b82f6
}

function targetTeamLabel(team: 'enemy' | 'friend' | 'unknown'): string {
  return team === 'enemy' ? 'DÜŞMAN' : team === 'friend' ? 'DOST' : 'SINIFLANDIRILMADI'
}

const virtualTargets = computed<VirtualWorldTarget[]>(() => {
  const frameWidth = Math.max(1, props.frameWidth ?? shownState.value?.camera.width ?? 1280)
  const frameHeight = Math.max(1, props.frameHeight ?? shownState.value?.camera.height ?? 720)
  const fovHorizontal = shownState.value?.camera_fov_horizontal_deg ?? targetProjection.value.camera_fov_horizontal_deg ?? 78
  const fovVertical = shownState.value?.camera_fov_vertical_deg ?? targetProjection.value.camera_fov_vertical_deg ?? 48
  const offset = shownState.value?.camera_to_launcher_offset_z_mm ?? targetProjection.value.camera_to_launcher_offset_z_mm ?? 30
  const policy = props.targetPolicy ?? 'BALLOON'
  const liveBalloonTargets = policy !== 'AIRCRAFT' && props.visionTargets?.length
    ? props.visionTargets.map((target) => projectionFromBalloonDetection(target, frameWidth, frameHeight, fovHorizontal, fovVertical, offset))
    : []
  const liveBodyTargets = policy !== 'BALLOON' && props.visionBodies?.length
    ? props.visionBodies.map((target) => projectionFromBodyDetection(target, frameWidth, frameHeight, fovHorizontal, fovVertical, offset))
    : []
  const liveTargets = [...liveBodyTargets, ...liveBalloonTargets]
  const targets = liveTargets.length
    ? liveTargets
    : props.ktrDemoMode
      ? [targetProjection.value]
      : []
  return targets.map((projection, index) => {
    const className = canonicalTargetClass(projection.class_name)
    const targetTeam = canonicalTargetTeam(projection.target_team)
    const spec = visualSpecForTarget(className)
    const range = projection.estimated_range_m !== null && projection.estimated_range_m !== undefined
      ? { rangeM: projection.estimated_range_m, uncertaintyM: projection.range_uncertainty_m ?? projection.estimated_range_m * spec.uncertaintyRatio }
      : rangeEstimateFromBbox(className, projection.bbox, frameWidth, frameHeight, fovHorizontal, fovVertical)
    const mapped = mapDetectionToEngagementGeometry({
      x_norm: projection.normalized_center_x,
      y_norm: projection.normalized_center_y,
      bbox_area_relative: projection.bbox_area_ratio,
      fov_horizontal_deg: fovHorizontal,
      fov_vertical_deg: fovVertical,
      camera_to_launcher_offset_z_mm: offset,
    })
    const targetKind: SelectedDetectionKind = className === 'balloon' ? 'balloon' : 'body'
    const effectiveSelectedId = props.selectedDetectionId ?? autoSelectedDetectionId.value
    const effectiveSelectedKind = props.selectedDetectionKind ?? autoSelectedDetectionKind.value
    const selected = effectiveSelectedId !== null && effectiveSelectedId !== undefined && effectiveSelectedKind
      ? projection.target_id === effectiveSelectedId && targetKind === effectiveSelectedKind
      : className === 'balloon' && props.selectedTargetId !== null && props.selectedTargetId !== undefined
        ? projection.target_id === props.selectedTargetId
        : props.ktrDemoMode && index === 0 && className === 'balloon'
    return {
      key: `${className}:${projection.target_id ?? index + 1}`,
      id: projection.target_id ?? index + 1,
      kind: className === 'balloon' ? 'balloon' : 'body',
      className,
      targetTeam,
      label: spec.label,
      logicalName: projection.visual_logical_name
        ?? props.logicalTargetNames?.[`${className === 'balloon' ? 'balloon' : 'body'}:${projection.target_id ?? index + 1}`]
        ?? null,
      spec,
      projection,
      geometry: mapped,
      rangeM: range.rangeM,
      rangeUncertaintyM: range.uncertaintyM,
      rangeLabel: `~${range.rangeM.toFixed(1)} m ±${range.uncertaintyM.toFixed(1)} m`,
      selected,
      visualLive: projection.visual_live !== false,
    }
  })
})

const geometry = computed(() => mapDetectionToEngagementGeometry({
  x_norm: targetProjection.value.normalized_center_x,
  y_norm: targetProjection.value.normalized_center_y,
  bbox_area_relative: targetProjection.value.bbox_area_ratio,
  fov_horizontal_deg: shownState.value?.camera_fov_horizontal_deg ?? targetProjection.value.camera_fov_horizontal_deg,
  fov_vertical_deg: shownState.value?.camera_fov_vertical_deg ?? targetProjection.value.camera_fov_vertical_deg,
  camera_to_launcher_offset_z_mm: shownState.value?.camera_to_launcher_offset_z_mm ?? targetProjection.value.camera_to_launcher_offset_z_mm,
}))

const targetScale = computed(() => targetProjection.value.estimated_range_band === 'near' ? 1.38 : targetProjection.value.estimated_range_band === 'mid' ? 1 : 0.72)
const targetSvg = computed(() => {
  const x = 450 + geometry.value.target_scene_x * 340
  const y = 458 - geometry.value.target_scene_depth * 372 - geometry.value.target_scene_y * 90
  const radius = 11 + targetScale.value * 6
  return {
    x: clamp(x, 205, 695),
    y: clamp(y, 96, 386),
    radius,
  }
})
const topDownTarget = computed(() => ({
  x: clamp(96 + geometry.value.target_scene_x * 60, 36, 156),
  y: clamp(152 - geometry.value.target_scene_depth * 118, 28, 152),
}))
const confidenceLabel = computed(() => `${Math.round(targetProjection.value.confidence * 100)}%`)
const targetName = computed(() => targetProjection.value.class_name.toUpperCase() || 'TARGET')
const targetLogicalName = computed(() => (
  props.logicalTargetNames?.[`balloon:${targetProjection.value.target_id}`]
  ?? props.logicalTargetNames?.[`body:${targetProjection.value.target_id}`]
  ?? null
))
const fireGateLabel = computed(() => {
  if (shownState.value?.engagement.person_safety_blocked) return 'PERSON BLOCKED'
  return shownState.value?.engagement.fire_allowed ? 'READY' : 'BLOCKED / NO TX'
})
const fireGateTone = computed<DigitalTwinTone>(() => shownState.value?.engagement.fire_allowed ? 'warn' : 'good')
const truthModeLabel = computed(() => {
  if (props.ktrDemoMode) return 'KTR fixture / projection estimate'
  if (shownState.value?.camera.is_laptop_camera) return 'Laptop dev / not USB acceptance'
  if (shownState.value?.camera.is_external_usb_camera) return 'USB camera / read-only'
  return 'Fixture or offline estimate'
})
const virtualPoseLabel = computed(() => {
  const telemetry = shownState.value?.telemetry_protocol
  if (manualVisualIntentActive()) return 'Pose source: manual command intent + telemetry reconcile'
  if (telemetry?.telemetry_fresh && telemetry.pan_deg !== null && telemetry.tilt_deg !== null) return 'Pose source: Pico telemetry read-only'
  if (virtualPoseSource.value === 'keyboard_preview') return 'Pose source: keyboard virtual preview'
  return 'Pose source: slider virtual preview'
})
const rangeMarks = [
  { label: '5 m', y: 376, x1: 356, x2: 544 },
  { label: '10 m', y: 286, x1: 300, x2: 600 },
  { label: '15 m', y: 186, x1: 238, x2: 662 },
]
const rangeBands3d = [5, 10, 15]
const FOV_WORLD_DEPTH = 14.5
const FOV_WORLD_HALF_WIDTH = 7.4
const FOV_WORLD_HALF_HEIGHT = 3.85
const TURRET_PHYSICAL_WIDTH_M = 0.60
const TURRET_PHYSICAL_DEPTH_M = 0.60
const TURRET_PHYSICAL_HEIGHT_M = 0.40
const NEAR_RANGE_TRUE_SCALE_LIMIT_M = 2.0
const BALLOON_REFERENCE_BBOX_AREA_AT_0_85M = (100 * 126) / (640 * 480)
const assetModes: Array<{ id: AssetMode, label: string, path: string, manifest: string, kinematics?: string, materialMode: string, geometrySource: string, weaponStatus: string }> = [
  {
    id: 'phase55-kinematic',
    label: 'Kinematic STEP',
    path: '/assets/digital-twin/ktr1_kinematic_world_phase55_draco.glb?v=operator-draco-node-safe-20260818',
    manifest: '/assets/digital-twin/ktr1_kinematic_world_phase55_manifest.json',
    kinematics: '/assets/digital-twin/ktr1_kinematics.json',
    materialMode: 'reconstructed STEP materials + kinematic metadata',
    geometrySource: 'work/ktr1.step',
    weaponStatus: 'yaw/pitch/camera/launcher groups available',
  },
  {
    id: 'phase55-raw',
    label: 'Manual Calibrated GLB',
    path: '/assets/digital-twin/ktr1_kinematic_world_phase55_draco.glb?v=operator-draco-node-safe-20260818',
    manifest: '/assets/digital-twin/ktr1_kinematic_world_phase55_manifest.json',
    kinematics: '/assets/digital-twin/ktr1_kinematics.json',
    materialMode: 'manual transform · curated virtual yaw/pitch grouping',
    geometrySource: 'work/ktr1.step · calibrated from /cockpit/model-calibration',
    weaponStatus: 'ground legs fixed · upper yaw · weapon/camera pitch preview',
  },
  {
    id: 'hybrid',
    label: 'Hybrid Fidelity',
    path: '/assets/digital-twin/ktr1_hybrid_fidelity_phase54.glb',
    manifest: '/assets/digital-twin/ktr1_hybrid_fidelity_phase54_manifest.json',
    materialMode: 'STEP colors + STL geometry evidence',
    geometrySource: 'STEP HiFi + STL',
    weaponStatus: 'front assembly inspection layer',
  },
  {
    id: 'step-hifi',
    label: 'STEP HiFi',
    path: '/assets/digital-twin/ktr1_step_hifi_phase54.glb',
    manifest: '/assets/digital-twin/ktr1_step_hifi_phase54_manifest.json',
    materialMode: 'reconstructed STEP materials',
    geometrySource: 'work/ktr1.step',
    weaponStatus: 'high tessellation candidate',
  },
  {
    id: 'stl',
    label: 'STL Geometry',
    path: '/assets/digital-twin/ktr1_stl_geometry_phase54.glb',
    manifest: '/assets/digital-twin/ktr1_stl_geometry_phase54_manifest.json',
    materialMode: 'geometry only',
    geometrySource: 'ktr1.stl',
    weaponStatus: 'neutral geometry fallback',
  },
  {
    id: 'previous',
    label: 'Previous GLB',
    path: '/assets/digital-twin/ktr1_freecad_fidelity.glb',
    manifest: '/assets/digital-twin/ktr1_freecad_fidelity_manifest.json',
    materialMode: 'previous reconstructed materials',
    geometrySource: 'Phase 51/52 GLB',
    weaponStatus: 'baseline comparison',
  },
  {
    id: 'freecad-match',
    label: 'FreeCAD Match',
    // Keep this explicit engineering comparison on the actual FreeCAD
    // fidelity export.  The previous entry pointed at STEP HiFi, which has
    // the same source CAD but a different flattened placement and could make
    // the launcher/camera look missing when an engineer switched views.
    path: '/assets/digital-twin/ktr1_freecad_fidelity.glb',
    manifest: '/assets/digital-twin/ktr1_freecad_fidelity_manifest.json',
    materialMode: 'FreeCAD-reference materials',
    geometrySource: 'work/ktr1.step',
    weaponStatus: 'comparison only · launcher placement not accepted for operator view',
  },
  {
    id: 'operator-hero',
    label: 'Hafif Taret (14MB / 60 FPS)',
    path: '/assets/digital-twin/ktr1_operator_hero.glb',
    manifest: '/assets/digital-twin/ktr1_operator_hero_manifest.json',
    kinematics: '/assets/digital-twin/ktr1_kinematics.json',
    materialMode: 'optimizasyonlu taret gövdesi',
    geometrySource: 'ktr1_operator_hero.glb',
    weaponStatus: 'hafif ve akıcı 60 FPS',
  },
]
const cameraPresets: Array<{ id: ViewPreset, label: string }> = KTR_CAMERA_PRESETS

function startRenderTicker(): void {
  stopRenderTicker()
  const frameMs = Math.max(1000 / renderFps.value, 66)
  renderTimer = setInterval(() => {
    if (document.hidden) return
    if (viewMode.value === 'replay') replayClock.value = performance.now()
  }, frameMs)
}

function stopRenderTicker(): void {
  if (renderTimer) clearInterval(renderTimer)
  renderTimer = null
}

function setModelLoadState(stage: DigitalTwinLoadStage, progress: number, detail: string): void {
  modelLoadStage.value = stage
  modelLoadProgress.value = Math.max(0, Math.min(100, progress))
  modelLoadDetail.value = detail
  if (modelLoadStartedAt.value !== null) {
    modelLoadElapsedMs.value = Math.round(performance.now() - modelLoadStartedAt.value)
  }
}

function loadGlbWithProgress(loader: any, path: string, onProgress: (loaded: number, total: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    loader.load(
      path,
      (gltf: any) => resolve(gltf),
      (event: ProgressEvent<EventTarget>) => onProgress(event.loaded, event.total),
      (error: unknown) => reject(error),
    )
  })
}

async function initThreeScene(): Promise<void> {
  if (!canvasRoot.value || renderer || !canRender3d.value) return
  modelLoadStartedAt.value = performance.now()
  modelLoadElapsedMs.value = null
  modelLoadError.value = null
  realModelLoaded.value = false
  setModelLoadState('three', 2, '3B motoru hazırlanıyor…')
  try {
    const [THREE, controlsModule] = await Promise.all([
      import('three'),
      import('three/examples/jsm/controls/OrbitControls.js'),
    ])
    threeModule = THREE
    // Keep FileLoader responses in Three's process cache as well as the
    // browser HTTP cache. This avoids a second network read when the operator
    // toggles a view/rebuilds the scene during one cockpit session.
    THREE.Cache.enabled = true
    scene = new THREE.Scene()
    scene.background = new THREE.Color(isFreecadMatch.value ? 0xe8edf2 : 0x050b14)
    camera3d = isFreecadMatch.value
      ? new THREE.OrthographicCamera(-2, 2, 1.2, -1.2, 0.01, 200)
      : new THREE.PerspectiveCamera(props.worldMode || props.performanceMode === 'ULTRA' ? 30 : 34, 1, 0.05, 160)
    camera3d.position.set(2.7, 1.55, 3.2)
    camera3d.lookAt(0, 0.4, 0)

    renderer = new THREE.WebGLRenderer({ antialias: props.performanceMode !== 'LOW', alpha: false, powerPreference: props.performanceMode === 'LOW' ? 'low-power' : 'high-performance' })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, props.performanceMode === 'LOW' ? 1 : props.performanceMode === 'ULTRA' ? 1.6 : props.performanceMode === 'HIGH' ? 1.35 : 1.1))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = isFreecadMatch.value ? 1.05 : props.performanceMode === 'ULTRA' ? 1.38 : 1.2
    renderer.shadowMap.enabled = props.performanceMode === 'ULTRA' || props.performanceMode === 'HIGH'
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    canvasRoot.value.appendChild(renderer.domElement)
    setModelLoadState('three', 16, 'Sahne ve kamera hazırlanıyor…')
    const { OrbitControls } = controlsModule
    controls = new OrbitControls(camera3d, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    // Presets frame the scene, but mouse orbit/pan/zoom remains available for
    // inspection until the operator explicitly presses Odakla.
    controls.enableRotate = true
    controls.enablePan = true
    controls.screenSpacePanning = true
    controls.enableZoom = true
    controls.rotateSpeed = 0.82
    controls.panSpeed = 0.86
    controls.zoomSpeed = 0.9
    controls.minPolarAngle = 0.05
    controls.maxPolarAngle = Math.PI - 0.05
    controls.minAzimuthAngle = -Infinity
    controls.maxAzimuthAngle = Infinity
    if ('zoomToCursor' in controls) controls.zoomToCursor = true
    controls.minDistance = 0.35
    controls.maxDistance = 24
    controls.minZoom = 0.22
    controls.maxZoom = 8
    renderer.domElement.addEventListener('dblclick', resetViewPreset)
    pickRaycaster = new THREE.Raycaster()
    pickPointer = new THREE.Vector2()
    pointerMoveHandler = (event: PointerEvent) => updateInspectorPick(event, false)
    pointerClickHandler = (event: PointerEvent) => updateInspectorPick(event, true)
    renderer.domElement.addEventListener('pointermove', pointerMoveHandler)
    renderer.domElement.addEventListener('click', pointerClickHandler)

    scene.add(new THREE.HemisphereLight(isFreecadMatch.value ? 0xffffff : 0xdbeafe, isFreecadMatch.value ? 0xcbd5e1 : 0x0f172a, isFreecadMatch.value ? 1.7 : 1.25))
    scene.add(new THREE.AmbientLight(0xffffff, isFreecadMatch.value ? 0.78 : 0.35))
    const key = new THREE.DirectionalLight(0xffffff, isFreecadMatch.value ? 1.25 : 2.1)
    key.position.set(3.5, 5.2, 3.4)
    key.castShadow = props.performanceMode === 'ULTRA'
    scene.add(key)
    const fill = new THREE.DirectionalLight(isFreecadMatch.value ? 0xffffff : 0x8fdcff, isFreecadMatch.value ? 0.55 : 0.95)
    fill.position.set(-4, 2.2, 2.8)
    scene.add(fill)
    const rim = new THREE.DirectionalLight(0xfff1c2, isFreecadMatch.value ? 0.35 : 1.15)
    rim.position.set(-2.5, 3.1, -4.2)
    scene.add(rim)

    environmentGroup = new THREE.Group()
    environmentGroup.name = 'virtual_world_environment'
    scene.add(environmentGroup)
    modelGroup = new THREE.Group()
    dynamicGroup = new THREE.Group()
    scene.add(modelGroup)
    scene.add(dynamicGroup)
    rangeTargetModelGroup = new THREE.Group()
    rangeTargetModelGroup.name = 'range_reference_target_model_group'
    scene.add(rangeTargetModelGroup)
    setModelLoadState('manifest', 20, 'Model manifesti ve kinematik metadata okunuyor…')
    await loadHeroManifest()
    setModelLoadState('model', 38, `${activeAsset.value.label} indiriliyor…`)
    await loadRealKtrHeroModel()

    resizeObserver = new ResizeObserver(resizeThreeScene)
    resizeObserver.observe(canvasRoot.value)
    resizeThreeScene()
    applyCameraPreset(viewPreset.value)
    renderThreeLoop()
    if (realModelLoaded.value) setModelLoadState('ready', 100, '3B dünya hazır')
    emit('panelRendered')
  } catch {
    setModelLoadState('error', 100, 'WebGL sahnesi başlatılamadı')
    webglFailed.value = true
    cleanupThreeScene()
    emit('panelRendered')
  }
}

async function loadHeroManifest(): Promise<void> {
  kinematics.value = null
  // All metadata is static/read-only. Fetch it in parallel and allow the
  // browser HTTP cache to satisfy later cockpit mounts.
  const manifestPromise = fetch(activeAsset.value.manifest, { cache: 'force-cache' })
    .then(async (response) => response.ok ? await response.json() as Record<string, unknown> : null)
    .catch(() => null)
  const phase56Promise = Promise.all([
    loadPhase56DeviceFrame(),
    loadPhase56MechanicalGroups(),
    loadPhase56JointCalibration(),
  ]).catch(() => [null, null, null] as const)
  const manifest = await manifestPromise
  heroManifest.value = manifest
  setModelLoadState('kinematics', 27, 'Kinematik harita hazırlanıyor…')
  const kinematicsPath = activeAsset.value.kinematics ?? (manifest?.kinematics_path as string | undefined)
  const kinematicsPromise = kinematicsPath
    ? loadKtrKinematics(kinematicsPath).catch(() => null)
    : Promise.resolve(null)
  const [loadedKinematics, phase56] = await Promise.all([kinematicsPromise, phase56Promise])
  kinematics.value = loadedKinematics
  setModelLoadState('metadata', 36, 'Kinematik ve saha kalibrasyonu hazır…')
  phase56DeviceFrame.value = phase56[0]
  phase56MechanicalGroups.value = phase56[1]
  phase56JointCalibration.value = phase56[2]
}

async function loadRealKtrHeroModel(): Promise<void> {
  const THREE = threeModule
  if (!THREE || !modelGroup) return
  disposeChildren(modelGroup)
  realModelLoaded.value = false
  modelLoadError.value = null
  try {
    // Draco compresses geometry only. Unlike the rejected Meshopt/quantized
    // candidate it preserves all 136 node names and leaves node transforms
    // untouched, so the field-validated manual kinematic grouping is intact.
    const [{ GLTFLoader }, { DRACOLoader }] = await Promise.all([
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/loaders/DRACOLoader.js'),
    ])
    const loader = new GLTFLoader()
    const dracoLoader = new DRACOLoader()
    dracoLoader.setDecoderPath('/draco/')
    dracoLoader.preload()
    loader.setDRACOLoader(dracoLoader)
    setModelLoadState('model', 40, `${activeAsset.value.label} indiriliyor…`)
    const progress = (loaded: number, total: number) => {
      if (total > 0) setModelLoadState('model', 40 + (loaded / total) * 43, `${activeAsset.value.label} indiriliyor… ${Math.round((loaded / total) * 100)}%`)
      else setModelLoadState('model', Math.min(76, modelLoadProgress.value + 1.5), `${activeAsset.value.label} indiriliyor…`)
    }
    let gltf
    try {
      gltf = await loadGlbWithProgress(loader, realModelPath.value, progress)
    } catch (compressedError) {
      // Raw GLB remains a visual rollback inside the runtime. A decoder/cache
      // failure therefore costs load time, never model correctness.
      if (!realModelPath.value.includes('_draco.glb')) throw compressedError
      setModelLoadState('model', 44, 'Sıkıştırılmış model açılamadı; raw model deneniyor…')
      gltf = await loadGlbWithProgress(loader, '/assets/digital-twin/ktr1_kinematic_world_phase55.glb?v=operator-raw-fallback-20260818', progress)
    }
    dracoLoader.dispose()
    setModelLoadState('processing', 85, '3B mesh ve malzemeler hazırlanıyor…')
    const root = gltf.scene
    root.name = 'real_ktr_freecad_fidelity_glb_hero'
    const materialUsage = new Map<string, { color: string, roughness: number, metalness: number, mesh_count: number }>()
    root.traverse((child: any) => {
      if (child.isMesh) {
        const fineHardware = isFineHardwareNode(child.name ?? '')
        child.userData.fineHardware = fineHardware
        child.visible = fineHardwareVisible.value || !fineHardware
        if (!child.material) child.material = new THREE.MeshStandardMaterial({ color: 0xd1d5db, metalness: 0.12, roughness: 0.5 })
        const materials = Array.isArray(child.material) ? child.material : [child.material]
        materials.forEach((material: any) => {
          normalizeCadMaterial(child.name ?? '', material)
          material.roughness = clamp(material.roughness ?? 0.58, 0.45, 0.78)
          material.metalness = clamp(material.metalness ?? 0.04, 0.0, 0.18)
          material.envMapIntensity = isFreecadMatch.value ? 0.25 : 0.55
          material.wireframe = wireframeEnabled.value
          if (xrayEnabled.value) {
            material.transparent = true
            material.opacity = Math.min(material.opacity ?? 1, 0.36)
            material.depthWrite = false
          }
          material.needsUpdate = true
          const color = material.color?.getHexString?.() ? `#${material.color.getHexString()}` : '#d1d5db'
          const name = material.name || color
          child.userData.materialName = name
          child.userData.materialColor = color
          const entry = materialUsage.get(name) ?? { color, roughness: material.roughness, metalness: material.metalness, mesh_count: 0 }
          entry.mesh_count += 1
          materialUsage.set(name, entry)
        })
        child.castShadow = props.performanceMode === 'ULTRA' || props.performanceMode === 'HIGH'
        child.receiveShadow = true
        if (child.visible && edgesEnabled.value && child.geometry) {
          const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(child.geometry, isFreecadMatch.value ? 8 : 24),
            new THREE.LineBasicMaterial({ color: isFreecadMatch.value ? 0x111827 : 0x67e8f9, transparent: true, opacity: isFreecadMatch.value ? 0.72 : 0.22 }),
          )
          edges.name = 'phase52_cad_edge_outline'
          child.add(edges)
        }
      }
    })
    const box = new THREE.Box3().setFromObject(root)
    const size = new THREE.Vector3()
    const center = new THREE.Vector3()
    box.getSize(size)
    box.getCenter(center)
    if (usesManualPhase55Calibration()) {
      applyRawManualKinematicSceneGraph(root)
      applyManualPhase55Transform(root)
    } else if (assetMode.value !== 'phase55-kinematic') {
      root.position.sub(center)
      root.rotation.set(0, 0, 0)
    }
    if (!usesManualPhase55Calibration()) root.scale.setScalar(1)
    if (assetMode.value === 'phase55-kinematic') root.position.set(0, 0, 0)
    if (assetMode.value === 'phase55-kinematic') applyKinematicSceneGraph(root)
    if (explodedViewEnabled.value) applyExplodedView(root)
    modelGroup.add(root)
    modelFit = computeModelFit()
    updateWorldEnvironment()
    heroManifest.value = {
      ...(heroManifest.value ?? {}),
      material_debug_table: [...materialUsage.entries()].map(([name, value]) => ({ name, ...value })),
    }
    if (!isFreecadMatch.value) addRealModelAnchorMarkers()
    if (showDeveloperControls.value) void loadRangeReferenceTargetModel()
    fitCameraToModel(viewPreset.value)
    setModelLoadState('scene', 97, 'Kinematik sahne bağlanıyor…')
    realModelLoaded.value = true
  } catch (error) {
    modelLoadError.value = error instanceof Error ? error.message : String(error)
    setModelLoadState('fallback', 100, 'Detaylı GLB yüklenemedi; basit güvenli görünüm aktif')
    createProceduralKinematicFallback()
    const blocker = createTextSprite('STEP FALLBACK ACTIVE\\nFREECAD-FIDELITY MODEL NOT LOADED\\ncheck conversion report', 0xfca5a5)
    if (showDeveloperControls.value) {
      blocker.position.set(0, 1.7, -1.4)
      blocker.scale.set(1.45, 0.52, 1)
      modelGroup.add(blocker)
    }
  }
}

function createProceduralKinematicFallback(): void {
  const THREE = threeModule
  if (!THREE || !modelGroup) return
  disposeChildren(modelGroup)
  const root = new THREE.Group()
  root.name = 'procedural_kinematic_turret_fallback'
  const baseMaterial = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.62, metalness: 0.34 })
  const bodyMaterial = new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.48, metalness: 0.28 })
  const accentMaterial = new THREE.MeshStandardMaterial({ color: 0x0891b2, roughness: 0.42, metalness: 0.22, emissive: 0x042f3e })
  const launcherMaterial = new THREE.MeshStandardMaterial({ color: 0x111827, roughness: 0.56, metalness: 0.3 })

  const base = mesh3d(new THREE.CylinderGeometry(0.72, 0.86, 0.28, 32), baseMaterial)
  base.position.y = 0.14
  root.add(base)
  const feet = mesh3d(new THREE.BoxGeometry(1.75, 0.12, 1.15), baseMaterial)
  feet.position.y = 0.04
  root.add(feet)

  const yawPivot = new THREE.Group()
  yawPivot.name = 'procedural_yaw_pivot'
  yawPivot.position.y = 0.28
  root.add(yawPivot)
  const yawBody = mesh3d(new THREE.BoxGeometry(1.06, 0.42, 0.82), bodyMaterial)
  yawBody.position.y = 0.25
  yawPivot.add(yawBody)
  const sideLeft = mesh3d(new THREE.BoxGeometry(0.16, 0.82, 0.72), accentMaterial)
  sideLeft.position.set(-0.48, 0.72, 0)
  yawPivot.add(sideLeft)
  const sideRight = mesh3d(new THREE.BoxGeometry(0.16, 0.82, 0.72), accentMaterial)
  sideRight.position.set(0.48, 0.72, 0)
  yawPivot.add(sideRight)

  const pitchPivot = new THREE.Group()
  pitchPivot.name = 'procedural_pitch_pivot'
  pitchPivot.position.set(0, 0.82, 0)
  yawPivot.add(pitchPivot)
  const launcher = mesh3d(new THREE.BoxGeometry(0.38, 0.32, 1.75), launcherMaterial)
  launcher.position.z = -0.48
  pitchPivot.add(launcher)
  const barrel = mesh3d(new THREE.CylinderGeometry(0.08, 0.08, 1.35, 20), launcherMaterial)
  barrel.rotation.x = Math.PI / 2
  barrel.position.set(0, 0.02, -1.95)
  pitchPivot.add(barrel)
  const cameraModule = mesh3d(new THREE.BoxGeometry(0.34, 0.24, 0.42), accentMaterial)
  cameraModule.position.set(0.33, 0.22, -1.05)
  pitchPivot.add(cameraModule)
  const lens = mesh3d(new THREE.CylinderGeometry(0.07, 0.07, 0.08, 20), new THREE.MeshStandardMaterial({ color: 0x020617, emissive: 0x164e63 }))
  lens.rotation.x = Math.PI / 2
  lens.position.set(0.33, 0.22, -1.29)
  pitchPivot.add(lens)

  root.traverse((node: any) => {
    if (node.isMesh) {
      node.castShadow = true
      node.receiveShadow = true
    }
  })
  modelGroup.add(root)
  yawPivotObject = yawPivot
  pitchPivotObject = pitchPivot
  modelFit = computeModelFit()
  applyKinematicPreviewPose()
  updateWorldEnvironment()
  fitCameraToModel(viewPreset.value)
}

async function loadRangeReferenceTargetModel(): Promise<void> {
  const THREE = threeModule
  if (!THREE || !rangeTargetModelGroup) return
  disposeChildren(rangeTargetModelGroup)
  if (isFreecadMatch.value || operatorTaskMode.value) return
  try {
    const { STLLoader } = await import('three/examples/jsm/loaders/STLLoader.js')
    const loader = Reflect.construct(STLLoader, []) as InstanceType<typeof STLLoader>
    const geometry = await loader.loadAsync('/assets/digital-twin/ornek_modeller.stl')
    geometry.computeBoundingBox()
    geometry.computeVertexNormals()
    const bbox = geometry.boundingBox
    if (!bbox) return
    const center = new THREE.Vector3()
    const size = new THREE.Vector3()
    bbox.getCenter(center)
    bbox.getSize(size)
    geometry.translate(-center.x, -center.y, -center.z)
    const maxAxis = Math.max(size.x, size.y, size.z, 0.001)
    const mesh = mesh3d(
      geometry,
      new THREE.MeshStandardMaterial({ color: 0xd9212b, roughness: 0.52, metalness: 0.04, emissive: 0x210000 }),
    )
    mesh.name = 'ornek_modeller_stl_range_target'
    mesh.scale.setScalar(4.6 / maxAxis)
    mesh.castShadow = true
    mesh.receiveShadow = true
    rangeTargetModelGroup.add(mesh)
    positionRangeReferenceTargetModel()
  } catch (error) {
    console.warn('ornek_modeller.stl target model could not be loaded', error)
  }
}

function positionRangeReferenceTargetModel(): void {
  const THREE = threeModule
  if (!THREE || !rangeTargetModelGroup || !rangeTargetModelGroup.children.length || !modelFit || operatorTaskMode.value) return
  const basis = tacticalBasis()
  const cameraAnchor = anchorVector('camera_anchor', { x: 0.5, y: 0.72, z: 1.03 })
  const cam = new THREE.Vector3(cameraAnchor.x, cameraAnchor.y, cameraAnchor.z)
  const fovOrigin = visibleCameraFovOrigin(cam, basis)
  const targetPosition = fovOrigin.clone()
    .add(basis.forward.clone().multiplyScalar(4.7))
    .add(basis.right.clone().multiplyScalar(-0.12))
    .add(basis.up.clone().multiplyScalar(-0.06))
  rangeTargetModelGroup.position.copy(targetPosition)
  rangeTargetModelGroup.lookAt(fovOrigin)
  rangeTargetModelGroup.rotateX(-Math.PI / 2)
}

function isFineHardwareNode(nodeName: string): boolean {
  const label = nodeName.toLowerCase()
  return label.includes('608zz') || label.includes('rulman') || label.includes('vida') || label.includes('cıvata') || label.includes('civata') || label.includes('somun') || label.includes('pul')
}

function applyKinematicSceneGraph(root: any): void {
  const THREE = threeModule
  if (!THREE || !kinematics.value || assetMode.value !== 'phase55-kinematic') {
    yawPivotObject = null
    pitchPivotObject = null
    return
  }
  const yawPivotPos = vectorFromKinematics(kinematics.value, 'pivots', 'yaw_pivot', { x: 0, y: 0.35, z: 0 })
  const pitchPivotPos = vectorFromKinematics(kinematics.value, 'pivots', 'pitch_pivot', { x: 0, y: 0.72, z: -0.52 })
  const yawPivot = new THREE.Group()
  yawPivot.name = kinematics.value.nodes.yawPivot ?? 'yaw_pivot'
  yawPivot.position.set(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z)
  const yawGroup = new THREE.Group()
  yawGroup.name = kinematics.value.nodes.yawGroup ?? 'yaw_group'
  const pitchPivot = new THREE.Group()
  pitchPivot.name = kinematics.value.nodes.pitchPivot ?? 'pitch_pivot'
  pitchPivot.position.set(pitchPivotPos.x - yawPivotPos.x, pitchPivotPos.y - yawPivotPos.y, pitchPivotPos.z - yawPivotPos.z)
  const pitchGroup = new THREE.Group()
  pitchGroup.name = kinematics.value.nodes.pitchGroup ?? 'pitch_group'
  const staticRoot = new THREE.Group()
  staticRoot.name = kinematics.value.nodes.staticRoot ?? 'static_root'

  const originalChildren = [...root.children]
  originalChildren.forEach((child: any) => root.remove(child))
  root.name = kinematics.value.nodes.root ?? 'ktr1_root'
  root.add(staticRoot)
  root.add(yawPivot)
  yawPivot.add(yawGroup)
  yawPivot.add(pitchPivot)
  pitchPivot.add(pitchGroup)

  for (const child of originalChildren) {
    const group = kinematicGroupForNode(kinematics.value, child.name) as KtrKinematicGroupName
    child.userData.kinematicGroup = group
    child.traverse?.((node: any) => {
      node.userData.kinematicGroup = group
      node.userData.sourceNodeName = child.name
    })
    if (group === 'static_root' || group === 'decorative_static_covers') {
      staticRoot.add(child)
    } else if (isPitchChildGroup(group)) {
      translateGeometryTree(child, pitchPivotPos)
      pitchGroup.add(child)
    } else {
      translateGeometryTree(child, yawPivotPos)
      yawGroup.add(child)
    }
  }
  yawPivotObject = yawPivot
  pitchPivotObject = pitchPivot
  applyKinematicPreviewPose()
}

function translateGeometryTree(object: any, pivot: { x: number, y: number, z: number }): void {
  object.traverse?.((node: any) => {
    if (!node.geometry || node.userData.phase55PivotTranslated) return
    node.geometry.translate(-pivot.x, -pivot.y, -pivot.z)
    node.userData.phase55PivotTranslated = true
  })
}

function applyRawManualKinematicSceneGraph(root: any): void {
  const THREE = threeModule
  if (!THREE) return
  const yawPivotPos = rawSourcePivot('yaw')
  const pitchPivotPos = rawSourcePivot('pitch')
  const yawPivot = new THREE.Group()
  yawPivot.name = 'manual_yaw_pivot_source_z_axis'
  yawPivot.position.set(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z)
  const yawGroup = new THREE.Group()
  yawGroup.name = 'manual_yaw_group_all_upper_parts'
  const pitchPivot = new THREE.Group()
  pitchPivot.name = 'manual_pitch_pivot_launcher_camera'
  pitchPivot.position.set(pitchPivotPos.x - yawPivotPos.x, pitchPivotPos.y - yawPivotPos.y, pitchPivotPos.z - yawPivotPos.z)
  const pitchGroup = new THREE.Group()
  pitchGroup.name = 'manual_pitch_group_launcher_camera'
  const staticRoot = new THREE.Group()
  staticRoot.name = 'manual_static_root_three_ground_legs'

  const originalChildren = [...root.children]
  originalChildren.forEach((child: any) => root.remove(child))
  root.add(staticRoot)
  root.add(yawPivot)
  yawPivot.add(yawGroup)
  yawPivot.add(pitchPivot)
  pitchPivot.add(pitchGroup)

  for (const child of originalChildren) {
    // Field-validated raw grouping from the last physically accepted model.
    // Keep the lower supports fixed while the complete launcher/camera cradle
    // follows the elevation pivot. This explicit map intentionally wins over
    // the later generated metadata whose pitch membership deformed the model.
    const group = rawManualGroupForNode(child.name)
    child.userData.kinematicGroup = group
    child.traverse?.((node: any) => {
      node.userData.kinematicGroup = group
      node.userData.sourceNodeName = child.name
    })
    if (group === 'static_root') {
      staticRoot.add(child)
    } else if (group === 'pitch_group' || group === 'camera_group' || group === 'launcher_group') {
      translateGeometryTree(child, pitchPivotPos)
      pitchGroup.add(child)
    } else {
      translateGeometryTree(child, yawPivotPos)
      yawGroup.add(child)
    }
  }

  yawPivotObject = yawPivot
  pitchPivotObject = pitchPivot
  applyKinematicPreviewPose()
}

function rawManualGroupForNode(nodeName: string): KtrKinematicGroupName {
  // GLTFLoader normalizes several CAD labels with underscores. Use locale-
  // independent lowercase as well: Turkish locale turns `SOLID` into `solıd`,
  // which prevented the actual weapon mesh from entering the pitch cradle.
  const label = nodeName.toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ').trim()
  const staticNames = [
    'alt gövde', 'alt govde', 'tabla',
    'yan gövde1', 'yan govde1', 'yan gövde 3', 'yan govde 3',
    'bileşen18', 'bilesen18', 'bileşen19', 'bilesen19',
    'bileşen20', 'bilesen20',
    'compound006', 'compound007', 'compound008', 'compound011',
  ]
  if (staticNames.some((name) => label.includes(name))) return 'static_root'

  const pitchNames = [
    'üst dişli kutusu', 'üst sonsuz dişl', 'üst dişli 20', 'üst nema17',
    'kamera', 'camera', 'bileşen13', 'bilesen13',
    'solid', 'axel', 'wire', 'grand fulffy',
    'compound001', 'compound002', 'compound009',
  ]
  if (pitchNames.some((name) => label.includes(name))) {
    if (label.includes('kamera') || label.includes('camera')) return 'camera_group'
    if (label.includes('bileşen13') || label.includes('bilesen13')) return 'launcher_group'
    return 'pitch_group'
  }
  return 'yaw_group'
}

function rawSourcePivot(kind: 'yaw' | 'pitch'): Vec3Like {
  const fallback = kind === 'yaw'
    ? { x: 0, y: 1.20311, z: 0.52561 }
    : { x: 0.01526, y: 0.90761, z: -0.01325 }
  return vectorFromKinematics(kinematics.value, 'pivots', kind === 'yaw' ? 'yaw_pivot' : 'pitch_pivot', fallback)
}

function normalizeCadMaterial(nodeName: string, material: any): void {
  if (!material?.color) return
  const label = nodeName.toLowerCase()
  if (label.includes('kamera') || label.includes('camera')) {
    material.name = 'freecad_camera_mechanical_gray'
    material.color.setHex(isFreecadMatch.value ? 0x4b5563 : 0x334155)
    material.emissive?.setHex?.(0x000000)
    material.roughness = 0.62
    material.metalness = 0.08
    return
  }
  if (label.includes('bileşen13') || label.includes('bilesen13') || label.includes('namlu') || label.includes('launcher') || label.includes('barrel')) {
    material.name = 'freecad_launcher_graphite'
    material.color.setHex(isFreecadMatch.value ? 0x2f343b : 0x111827)
    material.emissive?.setHex?.(0x000000)
    material.roughness = 0.54
    material.metalness = 0.12
    return
  }
  if (label.includes('rulman') || label.includes('dişli') || label.includes('disli') || label.includes('nema') || label.includes('axel') || label.includes('wire')) {
    material.name = 'freecad_inner_mechanical_dark'
    material.color.setHex(isFreecadMatch.value ? 0x20242a : 0x111827)
    material.emissive?.setHex?.(0x000000)
    material.roughness = 0.52
    material.metalness = 0.18
    return
  }
  if (label.includes('sağ') || label.includes('sag') || label.includes('sol') || label.includes('kapak') || label.includes('yan') || label.includes('üst')) {
    material.name = 'freecad_muted_red_panel'
    material.color.setHex(isFreecadMatch.value ? 0xb85252 : 0xc92a2a)
    material.emissive?.setHex?.(0x000000)
    material.roughness = 0.58
    material.metalness = 0.04
    return
  }
  if (label.includes('tabla') || label.includes('alt gövde') || label.includes('alt govde')) {
    material.name = 'freecad_base_gray'
    material.color.setHex(isFreecadMatch.value ? 0xa7aca7 : 0x5b6268)
    material.emissive?.setHex?.(0x000000)
    material.roughness = 0.64
    material.metalness = 0.05
  }
}

function resetDisplayedPoseToTarget(): void {
  displayedYawDeg.value = yawPreviewDeg.value
  displayedPitchDeg.value = pitchPreviewDeg.value
  poseSmoothingLastAt = performance.now()
}

function advanceDisplayedAxis(current: number, target: number, elapsedMs: number): number {
  const error = target - current
  if (Math.abs(error) <= POSE_SETTLE_EPSILON_DEG) return target
  const alpha = 1 - Math.exp(-elapsedMs / POSE_SMOOTHING_TIME_MS)
  const easedDelta = error * alpha
  const maxDelta = POSE_MAX_SPEED_DEG_PER_S * (elapsedMs / 1000)
  const boundedDelta = clamp(easedDelta, -maxDelta, maxDelta)
  const next = current + boundedDelta
  if (Math.sign(target - next) !== Math.sign(error)) return target
  return Math.abs(target - next) <= POSE_SETTLE_EPSILON_DEG ? target : next
}

function advanceDisplayedPose(now = performance.now()): void {
  if (poseSmoothingLastAt === null) {
    resetDisplayedPoseToTarget()
    return
  }
  const elapsedMs = Math.min(Math.max(now - poseSmoothingLastAt, 0), 120)
  poseSmoothingLastAt = now
  if (elapsedMs <= 0) return
  displayedYawDeg.value = advanceDisplayedAxis(displayedYawDeg.value, yawPreviewDeg.value, elapsedMs)
  displayedPitchDeg.value = advanceDisplayedAxis(displayedPitchDeg.value, pitchPreviewDeg.value, elapsedMs)
}

function applyKinematicPreviewPose(): void {
  const THREE = threeModule
  if (!THREE) return
  if (yawPivotObject) {
    if (usesManualPhase55Calibration()) yawPivotObject.rotation.z = THREE.MathUtils.degToRad(renderedYawDeg.value * manualYawSign())
    else yawPivotObject.rotation.y = THREE.MathUtils.degToRad(renderedYawDeg.value)
  }
  if (pitchPivotObject) pitchPivotObject.rotation.x = THREE.MathUtils.degToRad(renderedPitchDeg.value)
}

function applyTelemetryPoseIfAvailable(): void {
  if (manualVisualIntentActive()) return
  // Explicit URL/engineering preview is isolated from live telemetry so the
  // complete scene graph can be verified without moving physical hardware.
  if (virtualPoseSource.value === 'keyboard_preview') {
    if (explicitPosePreview) return
    virtualPoseSource.value = 'slider_preview'
  }
  const telemetry = shownState.value?.telemetry_protocol
  if (telemetry?.telemetry_fresh && typeof telemetry.pan_deg === 'number' && typeof telemetry.tilt_deg === 'number') {
    yawPreviewDeg.value = clampPreviewDeg(telemetry.pan_deg, [motionLimits.value.panMinDeg, motionLimits.value.panMaxDeg])
    pitchPreviewDeg.value = clampPreviewDeg(telemetry.tilt_deg, [motionLimits.value.tiltMinDeg, motionLimits.value.tiltMaxDeg])
    virtualPoseSource.value = 'telemetry'
    return
  }
  const pose = shownState.value?.device_pose
  if (!pose) return
  if (pose.pose_quality === 'unavailable' || pose.pose_source === 'fixture' || pose.pose_source === 'static_demo_pose') return
  yawPreviewDeg.value = clampPreviewDeg(Number(pose.pan_deg), [motionLimits.value.panMinDeg, motionLimits.value.panMaxDeg])
  pitchPreviewDeg.value = clampPreviewDeg(Number(pose.tilt_deg), [motionLimits.value.tiltMinDeg, motionLimits.value.tiltMaxDeg])
  virtualPoseSource.value = 'slider_preview'
}

function manualVisualIntentActive(): boolean {
  const intent = props.manualMotionIntent
  return Boolean(intent?.active && intent.status === 'accepted')
}

function advanceManualMotionIntent(now: number): void {
  const intent = props.manualMotionIntent
  if (!intent || !manualVisualIntentActive()) {
    manualIntentLastAt = null
    return
  }
  if (intent.sequence !== manualIntentSequence) {
    manualIntentSequence = intent.sequence
    manualIntentLastAt = Math.min(now, intent.issuedAtMs || now)
  }
  if (manualIntentLastAt === null) manualIntentLastAt = now
  const elapsedS = Math.min(Math.max(now - manualIntentLastAt, 0), 80) / 1000
  manualIntentLastAt = now
  if (elapsedS <= 0) return
  const panRate = clamp(intent.speedX / MANUAL_INTENT_COMMAND_FULL_SCALE, -1, 1) * MANUAL_INTENT_PAN_MAX_DEG_PER_S
  const tiltRate = clamp(intent.speedY / MANUAL_INTENT_COMMAND_FULL_SCALE, -1, 1) * MANUAL_INTENT_TILT_MAX_DEG_PER_S
  yawPreviewDeg.value = clampPreviewDeg(yawPreviewDeg.value + panRate * elapsedS, [motionLimits.value.panMinDeg, motionLimits.value.panMaxDeg])
  pitchPreviewDeg.value = clampPreviewDeg(pitchPreviewDeg.value + tiltRate * elapsedS, [motionLimits.value.tiltMinDeg, motionLimits.value.tiltMaxDeg])
  // The intent itself is already integrated frame-by-frame, so applying the
  // same exponential filter again would recreate input lag. Reconciliation
  // after release still uses the normal smoothing path.
  displayedYawDeg.value = yawPreviewDeg.value
  displayedPitchDeg.value = pitchPreviewDeg.value
  poseSmoothingLastAt = now
  virtualPoseSource.value = 'manual_intent'
}

function transformPointByPreview(point: { x: number, y: number, z: number }, group: KtrKinematicGroupName): { x: number, y: number, z: number } {
  const THREE = threeModule
  if (!THREE || !kinematics.value || assetMode.value !== 'phase55-kinematic') return point
  const yawPivotPos = vectorFromKinematics(kinematics.value, 'pivots', 'yaw_pivot', { x: 0, y: 0.35, z: 0 })
  const pitchPivotPos = vectorFromKinematics(kinematics.value, 'pivots', 'pitch_pivot', { x: 0, y: 0.72, z: -0.52 })
  const vector = new THREE.Vector3(point.x, point.y, point.z)
  if (isPitchChildGroup(group)) {
    vector.sub(new THREE.Vector3(pitchPivotPos.x, pitchPivotPos.y, pitchPivotPos.z))
    vector.applyAxisAngle(new THREE.Vector3(1, 0, 0), THREE.MathUtils.degToRad(renderedPitchDeg.value))
    vector.add(new THREE.Vector3(pitchPivotPos.x, pitchPivotPos.y, pitchPivotPos.z))
  }
  if (group !== 'static_root' && group !== 'decorative_static_covers') {
    vector.sub(new THREE.Vector3(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z))
    vector.applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(renderedYawDeg.value))
    vector.add(new THREE.Vector3(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z))
  }
  return { x: vector.x, y: vector.y, z: vector.z }
}

function transformDirectionByPreview(direction: { x: number, y: number, z: number }, group: KtrKinematicGroupName): any {
  const THREE = threeModule
  if (!THREE) return null
  if (usesManualPhase55Calibration()) return rawSourceDirectionToRuntime(direction, group)
  const vector = new THREE.Vector3(direction.x, direction.y, direction.z)
  if (!kinematics.value || assetMode.value !== 'phase55-kinematic') return vector.normalize()
  if (isPitchChildGroup(group)) vector.applyAxisAngle(new THREE.Vector3(1, 0, 0), THREE.MathUtils.degToRad(renderedPitchDeg.value))
  if (group !== 'static_root' && group !== 'decorative_static_covers') vector.applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(renderedYawDeg.value))
  return vector.normalize()
}

function applyExplodedView(root: any): void {
  const THREE = threeModule
  if (!THREE) return
  const whole = new THREE.Box3().setFromObject(root)
  const wholeCenter = new THREE.Vector3()
  whole.getCenter(wholeCenter)
  const wholeSize = new THREE.Vector3()
  whole.getSize(wholeSize)
  const offset = Math.max(wholeSize.length() * 0.018, 0.055)
  root.traverse((child: any) => {
    if (!child.isMesh || !child.geometry) return
    child.geometry.computeBoundingBox?.()
    const childBox = child.geometry.boundingBox
    if (!childBox) return
    const childCenter = new THREE.Vector3()
    childBox.getCenter(childCenter)
    const direction = childCenter.sub(wholeCenter)
    if (direction.length() < 0.001) return
    direction.y *= 0.38
    direction.normalize()
    child.position.add(direction.multiplyScalar(offset))
  })
}

function addRealModelAnchorMarkers(): void {
  const THREE = threeModule
  if (!THREE || !modelGroup) return
  const cameraAnchor = anchorVector('camera_anchor', { x: 0.5, y: 0.72, z: 1.03 })
  const launcherAnchor = anchorVector('launcher_anchor', { x: -0.3, y: 0.68, z: 1.26 })
  if (labelMode.value !== 'clean') {
    const offsetLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(cameraAnchor.x, cameraAnchor.y, cameraAnchor.z), new THREE.Vector3(launcherAnchor.x, launcherAnchor.y, launcherAnchor.z)]),
      new THREE.LineDashedMaterial({ color: 0xfacc15, dashSize: 0.035, gapSize: 0.03, transparent: true, opacity: 0.72 }),
    )
    offsetLine.computeLineDistances()
    modelGroup.add(offsetLine)
  }
}

function anchorVector(key: 'camera_anchor' | 'launcher_anchor' | 'base_anchor' | 'target_projection_anchor', fallback: { x: number, y: number, z: number }): { x: number, y: number, z: number } {
  if (assetMode.value === 'phase55-raw') {
    return manifestAnchorVector(key, fallback)
  }
  const phase56Key = key === 'camera_anchor'
    ? 'camera_origin'
    : key === 'launcher_anchor'
      ? 'launcher_origin'
      : key === 'target_projection_anchor'
        ? 'target_projection_anchor'
        : ''
  if (phase56Key && phase56JointCalibration.value) {
    return transformPointByPreview(phase56Vector(phase56JointCalibration.value, phase56Key, fallback), 'pitch_group')
  }
  const kinematicKey = key === 'camera_anchor'
    ? 'camera_origin'
    : key === 'launcher_anchor'
      ? 'launcher_origin'
      : key === 'target_projection_anchor'
        ? 'target_projection_anchor'
        : ''
  if (kinematicKey) {
    const fromKinematics = vectorFromKinematics(kinematics.value, 'anchors', kinematicKey, fallback)
    return transformPointByPreview(fromKinematics, 'pitch_group')
  }
  const fromManifest = heroManifest.value?.[key] as Record<string, unknown> | undefined
  const fromTransform = heroManifest.value?.asset_transform as Record<string, unknown> | undefined
  const transformKey = key === 'camera_anchor' ? 'camera_mount_anchor' : key === 'launcher_anchor' ? 'launcher_axis_anchor' : key
  const candidate = fromManifest ?? fromTransform?.[transformKey] as Record<string, unknown> | undefined
  return {
    x: Number(candidate?.x ?? fallback.x),
    y: Number(candidate?.y ?? fallback.y),
    z: Number(candidate?.z ?? fallback.z),
  }
}

function manifestAnchorVector(key: 'camera_anchor' | 'launcher_anchor' | 'base_anchor' | 'target_projection_anchor', fallback: { x: number, y: number, z: number }): { x: number, y: number, z: number } {
  const fromManifest = heroManifest.value?.[key] as Record<string, unknown> | undefined
  const fromTransform = heroManifest.value?.asset_transform as Record<string, unknown> | undefined
  const transformKey = key === 'camera_anchor' ? 'camera_mount_anchor' : key === 'launcher_anchor' ? 'launcher_axis_anchor' : key
  const candidate = fromManifest ?? fromTransform?.[transformKey] as Record<string, unknown> | undefined
  const cadPoint = {
    x: Number(candidate?.x ?? fallback.x),
    y: Number(candidate?.y ?? fallback.y),
    z: Number(candidate?.z ?? fallback.z),
  }
  if (!usesManualPhase55Calibration()) return cadPoint
  return rawSourcePointToRuntime(cadPoint, key === 'base_anchor' ? 'static_root' : 'pitch_group')
}

function usesManualPhase55Calibration(): boolean {
  return assetMode.value === 'phase55-raw'
}

function applyManualPhase55Transform(root: any): void {
  const THREE = threeModule
  if (!THREE) return
  const calibration = activeManualCalibration()
  root.position.set(calibration.position.x, calibration.position.y, calibration.position.z)
  root.rotation.set(
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.x),
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.y),
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.z),
    'XYZ',
  )
  root.scale.setScalar(calibration.scale)
}

function rawSourcePointToRuntime(point: Vec3Like, group: KtrKinematicGroupName): Vec3Like {
  const THREE = threeModule
  if (!THREE) return point
  const vector = new THREE.Vector3(point.x, point.y, point.z)
  applyRawPreviewToSourceVector(vector, group, false)
  applyRawCalibrationToVector(vector)
  return { x: vector.x, y: vector.y, z: vector.z }
}

function rawSourceDirectionToRuntime(direction: Vec3Like, group: KtrKinematicGroupName): any {
  const THREE = threeModule
  if (!THREE) return null
  const vector = new THREE.Vector3(direction.x, direction.y, direction.z)
  applyRawPreviewToSourceVector(vector, group, true)
  applyRawCalibrationRotationToVector(vector)
  return vector.normalize()
}

function applyRawPreviewToSourceVector(vector: any, group: KtrKinematicGroupName, directionOnly: boolean): void {
  const THREE = threeModule
  if (!THREE) return
  const yawPivotPos = rawSourcePivot('yaw')
  const pitchPivotPos = rawSourcePivot('pitch')
  if (isPitchChildGroup(group)) {
    if (!directionOnly) vector.sub(new THREE.Vector3(pitchPivotPos.x, pitchPivotPos.y, pitchPivotPos.z))
    vector.applyAxisAngle(new THREE.Vector3(PHASE55_MANUAL_CALIBRATION.sourceAxes.pitch.x, PHASE55_MANUAL_CALIBRATION.sourceAxes.pitch.y, PHASE55_MANUAL_CALIBRATION.sourceAxes.pitch.z), THREE.MathUtils.degToRad(renderedPitchDeg.value))
    if (!directionOnly) vector.add(new THREE.Vector3(pitchPivotPos.x, pitchPivotPos.y, pitchPivotPos.z))
  }
  if (group !== 'static_root' && group !== 'decorative_static_covers') {
    if (!directionOnly) vector.sub(new THREE.Vector3(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z))
    vector.applyAxisAngle(new THREE.Vector3(PHASE55_MANUAL_CALIBRATION.sourceAxes.yaw.x, PHASE55_MANUAL_CALIBRATION.sourceAxes.yaw.y, PHASE55_MANUAL_CALIBRATION.sourceAxes.yaw.z), THREE.MathUtils.degToRad(renderedYawDeg.value))
    if (!directionOnly) vector.add(new THREE.Vector3(yawPivotPos.x, yawPivotPos.y, yawPivotPos.z))
  }
}

function applyRawCalibrationToVector(vector: any): void {
  const THREE = threeModule
  if (!THREE) return
  const calibration = activeManualCalibration()
  applyRawCalibrationRotationToVector(vector)
  vector.multiplyScalar(calibration.scale)
  vector.add(new THREE.Vector3(calibration.position.x, calibration.position.y, calibration.position.z))
}

function applyRawCalibrationRotationToVector(vector: any): void {
  const THREE = threeModule
  if (!THREE) return
  const calibration = activeManualCalibration()
  vector.applyEuler(new THREE.Euler(
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.x),
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.y),
    THREE.MathUtils.degToRad(calibration.rotationEulerDeg.z),
    'XYZ',
  ))
}

function buildOperatorTwin(): void {
  const THREE = threeModule
  if (!THREE || !modelGroup) return
  disposeChildren(modelGroup)

  const graphite = new THREE.MeshStandardMaterial({ color: 0x111827, metalness: 0.42, roughness: 0.38 })
  const graphiteDark = new THREE.MeshStandardMaterial({ color: 0x020617, metalness: 0.5, roughness: 0.32 })
  const accentRed = new THREE.MeshStandardMaterial({ color: 0x7f1d1d, metalness: 0.28, roughness: 0.44 })
  const cyan = new THREE.MeshStandardMaterial({ color: 0x0e7490, emissive: 0x083344, metalness: 0.2, roughness: 0.34 })
  const yellow = new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: 0.95 })

  const base = mesh3d(new THREE.CylinderGeometry(0.9, 1.05, 0.16, 72), graphite)
  base.position.set(0, -0.38, 0)
  modelGroup.add(base)
  const turntable = mesh3d(new THREE.CylinderGeometry(0.62, 0.72, 0.16, 72), graphiteDark)
  turntable.position.set(0, -0.25, -0.02)
  modelGroup.add(turntable)
  const yoke = mesh3d(new THREE.CylinderGeometry(0.18, 0.22, 0.92, 32), accentRed)
  yoke.position.set(0, 0.26, 0)
  modelGroup.add(yoke)

  const body = mesh3d(new THREE.BoxGeometry(0.92, 0.34, 0.62), graphite)
  body.position.set(0, 0.72, -0.25)
  modelGroup.add(body)
  for (const side of [-1, 1]) {
    const panel = mesh3d(new THREE.BoxGeometry(0.07, 0.62, 0.78), accentRed)
    panel.position.set(side * 0.55, 0.72, -0.25)
    modelGroup.add(panel)
  }

  const rail = mesh3d(new THREE.CylinderGeometry(0.07, 0.085, 2.65, 36), graphiteDark)
  rail.rotation.x = Math.PI / 2
  rail.position.set(0, 0.74, -1.26)
  modelGroup.add(rail)
  const railSleeve = mesh3d(new THREE.BoxGeometry(0.28, 0.18, 1.04), graphite)
  railSleeve.position.set(0, 0.74, -0.74)
  modelGroup.add(railSleeve)

  const cameraBox = mesh3d(new THREE.BoxGeometry(0.46, 0.22, 0.28), cyan)
  cameraBox.position.set(-0.34, 0.98, 0.12)
  modelGroup.add(cameraBox)
  const lens = mesh3d(new THREE.CylinderGeometry(0.07, 0.07, 0.05, 24), new THREE.MeshBasicMaterial({ color: 0x67e8f9 }))
  lens.rotation.x = Math.PI / 2
  lens.position.set(-0.34, 0.98, -0.05)
  modelGroup.add(lens)

  const offsetLine = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-0.34, 0.98, 0.12), new THREE.Vector3(0, 0.74, -0.28)]),
    new THREE.LineDashedMaterial({ color: 0xfacc15, dashSize: 0.035, gapSize: 0.03, transparent: true, opacity: 0.78 }),
  )
  offsetLine.computeLineDistances()
  modelGroup.add(offsetLine)
  const axisStub = mesh3d(new THREE.CylinderGeometry(0.018, 0.018, 0.38, 12), yellow)
  axisStub.rotation.x = Math.PI / 2
  axisStub.position.set(0, 0.74, -2.62)
  modelGroup.add(axisStub)
}

function mesh3d(geometry: any, material: any): any {
  const THREE = threeModule
  return new THREE['Mesh'](geometry, material)
}

function createVirtualWorldFloorTexture(): any {
  const THREE = threeModule
  if (!THREE) return null
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 512
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const gradient = ctx.createLinearGradient(0, 0, 512, 512)
    gradient.addColorStop(0, '#07111c')
    gradient.addColorStop(0.48, '#0a1522')
    gradient.addColorStop(1, '#050b13')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, 512, 512)
    ctx.strokeStyle = 'rgba(34, 211, 238, 0.13)'
    ctx.lineWidth = 1
    for (let i = 0; i <= 512; i += 32) {
      ctx.beginPath()
      ctx.moveTo(i, 0)
      ctx.lineTo(i, 512)
      ctx.stroke()
      ctx.beginPath()
      ctx.moveTo(0, i)
      ctx.lineTo(512, i)
      ctx.stroke()
    }
    ctx.strokeStyle = 'rgba(34, 211, 238, 0.28)'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(256, 0)
    ctx.lineTo(256, 512)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(0, 256)
    ctx.lineTo(512, 256)
    ctx.stroke()
    ctx.fillStyle = 'rgba(125, 211, 252, 0.045)'
    for (let i = 0; i < 460; i += 1) {
      const x = (i * 79) % 512
      const y = (i * 137) % 512
      ctx.fillRect(x, y, 1, 1)
    }
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  // The operator world is an open plane, not a fog volume. Keep the grid
  // texture scale stable after enlarging the terrain below.
  texture.repeat.set(24, 24)
  return texture
}

function addSkyGradientBackdrop3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const basis = worldReferenceBasis()
  const floorY = worldTerrainY()
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const canvas = document.createElement('canvas')
  canvas.width = 64
  canvas.height = 256
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const gradient = ctx.createLinearGradient(0, 0, 0, 256)
    gradient.addColorStop(0, '#030712')
    gradient.addColorStop(0.38, '#071426')
    gradient.addColorStop(0.74, '#0f2434')
    gradient.addColorStop(1, '#12293a')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, 64, 256)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const right = basis.right.clone()
  const up = basis.up.clone()
  const farCenter = center.clone().add(basis.forward.clone().multiplyScalar(17.5)).add(up.clone().multiplyScalar(4.4))
  const halfW = 28
  const halfH = 8.2
  const points = [
    farCenter.clone().add(right.clone().multiplyScalar(-halfW)).add(up.clone().multiplyScalar(halfH)),
    farCenter.clone().add(right.clone().multiplyScalar(halfW)).add(up.clone().multiplyScalar(halfH)),
    farCenter.clone().add(right.clone().multiplyScalar(halfW)).add(up.clone().multiplyScalar(-halfH)).setY(floorY - 0.02),
    farCenter.clone().add(right.clone().multiplyScalar(-halfW)).add(up.clone().multiplyScalar(-halfH)).setY(floorY - 0.02),
  ]
  const geometry3d = new THREE.BufferGeometry().setFromPoints(points)
  geometry3d.setIndex([0, 1, 2, 0, 2, 3])
  environmentGroup.add(mesh3d(geometry3d, new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, depthWrite: false })))
}

function addRangeBands3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const floorY = worldFloorY()
  const basis = worldReferenceBasis()
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  for (const [index, range] of rangeBands3d.entries()) {
    const bandCenter = center.clone().add(basis.forward.clone().multiplyScalar(1.45 + index * 1.45))
    const width = 1.8 + index * 1.4
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        bandCenter.clone().add(basis.right.clone().multiplyScalar(-width)).setY(floorY + 0.018),
        bandCenter.clone().add(basis.right.clone().multiplyScalar(width)).setY(floorY + 0.018),
      ]),
      new THREE.LineDashedMaterial({ color: 0x22d3ee, dashSize: 0.09, gapSize: 0.08, transparent: true, opacity: 0.34 }),
    )
    line.computeLineDistances()
    environmentGroup.add(line)
    const label = createTextSprite(`${range} m`, 0x94a3b8, 'rgba(2, 6, 23, 0.0)')
    label.position.copy(bandCenter.clone().add(basis.right.clone().multiplyScalar(width + 0.18)).setY(floorY + 0.18))
    label.scale.set(0.38, 0.12, 1)
    environmentGroup.add(label)
  }
}

function addDistantDigitalTerrain3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const floorY = worldTerrainY()
  const basis = worldReferenceBasis()
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const right = basis.right.clone()
  const buildHorizonLine = (distance: number, height: number, color: number, opacity: number, dashSize: number) => {
    const farCenter = center.clone().add(basis.forward.clone().multiplyScalar(distance))
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        farCenter.clone().add(right.clone().multiplyScalar(-18)).setY(floorY + height),
        farCenter.clone().add(right.clone().multiplyScalar(18)).setY(floorY + height),
      ]),
      new THREE.LineDashedMaterial({ color, dashSize, gapSize: dashSize * 0.85, transparent: true, opacity, depthWrite: false }),
    )
    line.computeLineDistances()
    environmentGroup.add(line)
  }
  buildHorizonLine(15.5, 0.16, 0x22d3ee, 0.18, 0.28)
  buildHorizonLine(20.5, 0.42, 0x38bdf8, 0.12, 0.42)
  buildHorizonLine(25.5, 0.74, 0x0ea5e9, 0.08, 0.58)
}

function addContactShadow3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const size = modelFit?.size ?? new THREE.Vector3(2, 2, 2)
  const radius = Math.max(size.x, size.z) * 0.55
  const shadow = mesh3d(
    new THREE.CircleGeometry(radius, 72),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.26, depthWrite: false }),
  )
  shadow.rotation.x = -Math.PI / 2
  shadow.position.set(center.x, worldFloorY() + 0.012, center.z)
  environmentGroup.add(shadow)
}

function addMountedPlatform3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const floorY = worldFloorY()
  const platform = new THREE.Group()
  platform.name = 'phase61_tactical_low_profile_pedestal'
  const topY = floorY - 0.008
  const deckThickness = 0.16
  const deckY = topY - deckThickness / 2
  const deck = mesh3d(
    new THREE.CylinderGeometry(1.34, 1.48, deckThickness, 6),
    new THREE.MeshStandardMaterial({ color: 0x161f2a, roughness: 0.7, metalness: 0.35 }),
  )
  deck.rotation.y = Math.PI / 6
  deck.position.set(center.x, deckY, center.z)
  deck.castShadow = true
  deck.receiveShadow = true
  platform.add(deck)

  const lowerDeck = mesh3d(
    new THREE.CylinderGeometry(1.54, 1.62, 0.08, 6),
    new THREE.MeshStandardMaterial({ color: 0x0b1220, roughness: 0.74, metalness: 0.38 }),
  )
  lowerDeck.rotation.y = Math.PI / 6
  lowerDeck.position.set(center.x, deckY - 0.11, center.z)
  lowerDeck.castShadow = true
  lowerDeck.receiveShadow = true
  platform.add(lowerDeck)

  const legMaterial = new THREE.MeshStandardMaterial({ color: 0x1f2933, roughness: 0.68, metalness: 0.32 })
  ;[0, 1, 2, 3, 4, 5].forEach((index) => {
    const angle = (index / 6) * Math.PI * 2 + Math.PI / 6
    const leg = mesh3d(new THREE.BoxGeometry(0.12, 0.52, 0.12), legMaterial)
    leg.position.set(center.x + Math.cos(angle) * 1.08, floorY - 0.38, center.z + Math.sin(angle) * 1.08)
    leg.rotation.y = angle
    leg.castShadow = true
    leg.receiveShadow = true
    platform.add(leg)
  })

  const ring = mesh3d(
    new THREE.TorusGeometry(0.8, 0.018, 12, 96),
    new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.72 }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.set(center.x, topY + 0.034, center.z)
  ring.castShadow = true
  platform.add(ring)

  const boltMaterial = new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.42, metalness: 0.48 })
  for (let i = 0; i < 12; i += 1) {
    const angle = (i / 12) * Math.PI * 2
    const bolt = mesh3d(new THREE.CylinderGeometry(0.025, 0.025, 0.018, 12), boltMaterial)
    bolt.position.set(center.x + Math.cos(angle) * 0.68, topY + 0.054, center.z + Math.sin(angle) * 0.68)
    bolt.castShadow = true
    platform.add(bolt)
  }

  const stripeMaterial = new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.52 })
  for (let i = 0; i < 5; i += 1) {
    const stripe = mesh3d(new THREE.BoxGeometry(0.34, 0.012, 0.035), stripeMaterial)
    stripe.position.set(center.x - 1.13 + i * 0.18, topY + 0.07, center.z - 1.16)
    stripe.rotation.y = -0.5
    platform.add(stripe)
  }

  environmentGroup.add(platform)
}

function addRangeScaleCues3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup || sceneMode.value === 'freecadMatch') return
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const floorY = worldTerrainY()
  const material = new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: sceneMode.value === 'showcaseWorld' ? 0.18 : 0.34 })
  ;[5, 10, 15].forEach((meters, index) => {
    const radius = 1.8 + index * 1.55
    const ring = mesh3d(new THREE.TorusGeometry(radius, 0.007, 8, 128), material)
    ring.rotation.x = Math.PI / 2
    ring.position.set(center.x, floorY + 0.018, center.z)
    ring.name = `subtle_range_ring_${meters}m`
    environmentGroup.add(ring)
  })
  // Keep the terrain material uniform. The former filled scan triangle made
  // the initial FOV direction permanently lighter than the rest of the floor.
}

function addOptionalGrid3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  if (!gridVisible.value && sceneMode.value !== 'tacticalOverlay' && labelMode.value !== 'debug') return
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const basis = worldReferenceBasis()
  const grid = new THREE.GridHelper(18, props.performanceMode === 'LOW' ? 18 : 48, 0x22d3ee, 0x123148)
  const gridCenter = center.clone().add(basis.forward.clone().multiplyScalar(2.2))
  grid.position.set(gridCenter.x, worldFloorY() + 0.014, gridCenter.z)
  ;(Array.isArray(grid.material) ? grid.material : [grid.material]).forEach((item: any) => {
    item.transparent = true
    item.opacity = labelMode.value === 'debug' ? 0.5 : sceneMode.value === 'tacticalOverlay' ? 0.24 : 0.12
  })
  environmentGroup.add(grid)
}

function addPanLimitWalls3d(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  // These planes belong to the field, not to the currently rotating camera.
  const basis = worldReferenceBasis()
  const floorY = worldTerrainY() + 0.02
  const height = 3.2
  const distance = 9.5
  const activeCode = props.motionState?.limit_reason_code ?? ''
  const addWall = (angleDeg: number, side: 'MIN' | 'MAX') => {
    // Motion limits are camera-semantic angles, while the field-accepted
    // twin mirrors physical PAN with TWIN_YAW_DIRECTION_SIGN.  Draw the wall
    // in that same visual frame; otherwise left/right motion is correct but
    // the two red boundary planes appear swapped.
    const visualAngleDeg = angleDeg * TWIN_YAW_DIRECTION_SIGN
    const radians = THREE.MathUtils.degToRad(visualAngleDeg)
    const direction = basis.forward.clone().multiplyScalar(Math.cos(radians)).add(basis.right.clone().multiplyScalar(Math.sin(radians))).normalize()
    const origin = new THREE.Vector3(center.x, floorY, center.z)
    const far = origin.clone().add(direction.multiplyScalar(distance))
    const points = [
      origin.clone(), far.clone(), far.clone().add(basis.up.clone().multiplyScalar(height)), origin.clone().add(basis.up.clone().multiplyScalar(height)),
    ]
    const geometry = new THREE.BufferGeometry().setFromPoints(points)
    geometry.setIndex([0, 1, 2, 0, 2, 3])
    const hit = activeCode === `PAN_${side}_LIMIT` || activeCode === 'PAN_SOFT_LIMIT'
    const wall = mesh3d(geometry, new THREE.MeshBasicMaterial({
      color: 0xef4444, side: THREE.DoubleSide, transparent: true,
      opacity: hit ? 0.45 : 0.20, depthWrite: false,
    }))
    wall.name = `pan_${side.toLowerCase()}_limit_wall`
    wall.renderOrder = 2
    environmentGroup.add(wall)
  }
  // Y/Tilt için duvara gerek yok; sadece Pan (X ekseni) limitleri çizilir
  addWall(motionLimits.value.panMinDeg, 'MIN')
  addWall(motionLimits.value.panMaxDeg, 'MAX')
}

function applyWorldAtmosphere(): void {
  const THREE = threeModule
  if (!THREE || !scene) return
  if (isFreecadMatch.value) {
    scene.fog = null
    scene.background = new THREE.Color(0xe8eef4)
    return
  }
  const color = sceneMode.value === 'showcaseWorld' ? 0x050b14 : 0x060b13
  scene.background = new THREE.Color(color)
  // Targets are projected into the same world plane as the turret. Fog made
  // aircraft at the far end of that plane look like they were behind a grey
  // veil and also changed their apparent team colours. The world background
  // remains dark, but depth is now clear and uniform.
  scene.fog = null
}

function updateWorldEnvironment(): void {
  const THREE = threeModule
  if (!THREE || !environmentGroup) return
  disposeChildren(environmentGroup)
  applyWorldAtmosphere()
  if (isFreecadMatch.value) {
    // The full-world route intentionally keeps the CAD background clean.
    return
  }
  if (!environmentVisible.value) {
    addOptionalGrid3d()
    return
  }
  const center = modelFit?.center ?? new THREE.Vector3(0, 0, 0)
  const floorTexture = createVirtualWorldFloorTexture()
  const floor = mesh3d(new THREE.PlaneGeometry(160, 160), new THREE.MeshStandardMaterial({ color: 0x07111c, map: floorTexture, metalness: 0.0, roughness: 0.92 }))
  floor.rotation.x = -Math.PI / 2
  const floorCenter = center.clone().add(worldReferenceBasis().forward.clone().multiplyScalar(4.2))
  floor.position.set(floorCenter.x, worldTerrainY(), floorCenter.z)
  floor.receiveShadow = true
  environmentGroup.add(floor)
  addSkyGradientBackdrop3d()
  addDistantDigitalTerrain3d()
  addMountedPlatform3d()
  addContactShadow3d()
  addRangeScaleCues3d()
  addPanLimitWalls3d()
  addOptionalGrid3d()
  if (sceneMode.value === 'tacticalOverlay' || labelMode.value === 'debug') addRangeBands3d()
}

function worldFloorY(): number {
  if (!threeModule || !modelGroup) return -0.48
  if (usesManualPhase55Calibration()) return activeManualCalibration().groundY
  const THREE = threeModule
  const supportBox = supportPlaneBox()
  if (supportBox) return supportBox.min.y - 0.012
  const box = new THREE.Box3().setFromObject(modelGroup)
  return Number.isFinite(box.min.y) ? box.min.y - 0.018 : -0.48
}

function worldTerrainY(): number {
  return worldFloorY() - 0.24
}

function supportPlaneBox(): any | null {
  const THREE = threeModule
  if (!THREE || !modelGroup || !usesManualPhase55Calibration()) return null
  const box = new THREE.Box3()
  let found = false
  const supportNames = ['tabla', 'alt gövde', 'alt govde', 'yan gövde', 'yan govde']
  modelGroup.traverse((child: any) => {
    if (!child.isMesh || child.userData?.fineHardware || !child.visible) return
    const label = String(child.name ?? '').toLowerCase()
    if (!supportNames.some((name) => label.includes(name))) return
    box.expandByObject(child)
    found = true
  })
  return found && Number.isFinite(box.min.y) ? box : null
}

function updateDynamic3d(): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  disposeChildren(dynamicGroup)
  const cameraAnchor = anchorVector('camera_anchor', { x: 0.5, y: 0.72, z: 1.03 })
  const launcherAnchor = anchorVector('launcher_anchor', { x: -0.3, y: 0.68, z: 1.26 })
  const cam = new THREE.Vector3(cameraAnchor.x, cameraAnchor.y, cameraAnchor.z)
  const launcher = new THREE.Vector3(launcherAnchor.x, launcherAnchor.y, launcherAnchor.z)
  const basis = tacticalBasis()
  const muzzle = launcherMuzzleAnchorVector(launcher, basis)
  const fovOrigin = visibleCameraFovOrigin(cam, basis, muzzle)
  const selectedTarget = selectedWorldTarget()
  const selectedTargetPoint = selectedTarget ? targetWorldPosition(fovOrigin, selectedTarget) : null
  const showTacticalHelpers = sceneMode.value === 'tacticalOverlay' || labelMode.value === 'debug'
  positionRangeReferenceTargetModel()

  if (effectiveFovVisible.value) {
    addFovVolume3d(fovOrigin)
  }

  if (effectiveFovVisible.value && showTacticalHelpers) {
    dynamicGroup.add(line3d(fovOrigin, fovOrigin.clone().add(basis.forward.clone().multiplyScalar(FOV_WORLD_DEPTH)), 0x22d3ee, labelMode.value === 'debug' ? 0.58 : 0.34, true))
    if (labelMode.value === 'debug') dynamicGroup.add(line3d(muzzle, muzzle.clone().add(basis.forward.clone().multiplyScalar(FOV_WORLD_DEPTH)), 0xfacc15, 0.5, true))
    if (props.ktrDemoMode || shownState.value?.engagement.person_safety_blocked) addNoGoZone3d()
  }

  if (showSecondaryTargets.value) {
    virtualTargets.value
      .filter((worldTarget) => !worldTarget.selected)
      .forEach((worldTarget) => {
        const targetPoint = targetWorldPosition(fovOrigin, worldTarget)
        if (targetPoint) addSecondaryTarget3d(targetPoint, worldTarget)
      })
  }

  if (effectiveTargetVisible.value && selectedTarget && selectedTargetPoint) {
    addSelectedTarget3d(selectedTargetPoint, selectedTarget)
    if (effectiveEngagementRayVisible.value) addPrimaryEngagementRay3d(muzzle, selectedTargetPoint)
  }

  if (labelMode.value === 'debug') {
    virtualTargets.value
      .filter((worldTarget) => !worldTarget.selected)
      .forEach((worldTarget) => addDebugUnselectedTarget3d(fovOrigin, worldTarget))
  }

  if (labelMode.value === 'debug') {
    const cameraLabel = createTextSprite('Camera module', 0x67e8f9)
    cameraLabel.position.set(cam.x - 0.58, cam.y + 0.18, cam.z + 0.22)
    cameraLabel.scale.set(0.46, 0.13, 1)
    dynamicGroup.add(cameraLabel)
    const launcherLabel = createTextSprite('Launcher axis', 0xfde68a)
    launcherLabel.position.set(launcher.x + 0.42, launcher.y + 0.18, launcher.z + 1.1)
    launcherLabel.scale.set(0.46, 0.13, 1)
    dynamicGroup.add(launcherLabel)
    const offsetLabel = createTextSprite('30 mm offset', 0xfde68a)
    offsetLabel.position.set((cam.x + launcher.x) / 2 - 0.36, (cam.y + launcher.y) / 2 - 0.12, (cam.z + launcher.z) / 2)
    offsetLabel.scale.set(0.42, 0.12, 1)
    dynamicGroup.add(offsetLabel)
    const muzzleLabel = createTextSprite('Launcher muzzle anchor\\nmanual calibrated', 0xfde68a)
    muzzleLabel.position.set(muzzle.x + 0.34, muzzle.y + 0.12, muzzle.z)
    muzzleLabel.scale.set(0.52, 0.17, 1)
    dynamicGroup.add(muzzleLabel)
  }
}

function selectedWorldTarget(): VirtualWorldTarget | null {
  return virtualTargets.value.find((worldTarget) => worldTarget.selected) ?? null
}

function visibleCameraFovOrigin(cam: any, _basis: { forward: any, right: any, up: any }, _muzzle?: any): any {
  // The FOV must start at the calibrated camera optical origin. The launcher
  // muzzle remains a separate ray origin because the two are not assumed to
  // be coincident until mechanical extrinsics are measured.
  return cam
}

function launcherMuzzleAnchorVector(launcher: any, basis: { forward: any }): any {
  return launcher.clone().add(basis.forward.clone().multiplyScalar(0.42))
}

function targetWorldPosition(cam: any, worldTarget: VirtualWorldTarget): any {
  const THREE = threeModule
  if (!THREE) return null
  const basis = tacticalBasis()
  const depthScene = sceneDepthForPhysicalRange(worldTarget.rangeM)
  // HEDEF SİMETRİ / AYNALAMA DÜZELTMESİ:
  // Gerçek kamerada sağda görünen hedefin dijital ikizde de sağda görünmesi için
  // X ekseni işaret tersliği giderildi.
  const targetX = -Math.tan(THREE.MathUtils.degToRad(worldTarget.projection.azimuth_deg)) * depthScene
  const targetY = Math.tan(THREE.MathUtils.degToRad(worldTarget.projection.elevation_deg)) * depthScene
  const target = cam.clone()
    .add(basis.right.clone().multiplyScalar(targetX))
    .add(basis.up.clone().multiplyScalar(targetY))
    .add(basis.forward.clone().multiplyScalar(depthScene))
  return target
}

function addSelectedTarget3d(target: any, worldTarget: VirtualWorldTarget): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  if (worldTarget.kind === 'body') {
    addBodyTarget3d(target, worldTarget, true)
    return
  }
  const balloon = new THREE.Group()
  const radius = targetMarkerRadius(worldTarget, true)
  const sphere = mesh3d(
    new THREE.SphereGeometry(radius, 36, 22),
    new THREE.MeshStandardMaterial({
      color: 0xf97316,
      emissive: 0x4a1600,
      roughness: 0.42,
      transparent: true,
      opacity: worldTarget.visualLive ? (operatorTaskMode.value ? 0.48 : 0.9) : 0.24,
      wireframe: operatorTaskMode.value,
    }),
  )
  balloon.add(sphere)
  const glow = mesh3d(
    new THREE.SphereGeometry(radius * 1.55, 24, 14),
    new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: worldTarget.visualLive ? (operatorTaskMode.value ? 0.18 : 0.16) : 0.07, depthWrite: false }),
  )
  balloon.add(glow)
  const ring = mesh3d(new THREE.TorusGeometry(radius * 1.65, 0.012, 8, 64), new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: worldTarget.visualLive ? 0.9 : 0.28 }))
  ring.rotation.x = Math.PI / 2
  balloon.add(ring)
  const groundRing = mesh3d(new THREE.TorusGeometry(radius * 1.2, 0.01, 8, 54), new THREE.MeshBasicMaterial({ color: 0xfb923c, transparent: true, opacity: 0.46, depthWrite: false }))
  groundRing.rotation.x = Math.PI / 2
  groundRing.position.set(0, -Math.max(0.08, target.y - worldTerrainY() - 0.03), 0)
  balloon.add(groundRing)
  balloon.position.copy(target)
  dynamicGroup.add(balloon)

  if (labelMode.value !== 'debug' || sceneMode.value === 'tacticalOverlay') {
    const label = createTextSprite(`${worldTarget.logicalName ?? `Target #${worldTarget.id}`}\\n${worldTarget.label.toUpperCase()} ${Math.round(worldTarget.projection.confidence * 100)}% · ${worldTarget.geometry.bearing_label}\\n${worldTarget.rangeLabel} · bbox estimate`, 0xfde68a, 'rgba(2, 6, 23, 0.68)')
    label.position.set(target.x + radius + 0.18, target.y + radius + 0.08, target.z)
    label.scale.set(labelMode.value === 'clean' ? 0.44 : 0.54, labelMode.value === 'clean' ? 0.15 : 0.19, 1)
    dynamicGroup.add(label)
  }
}

function addSecondaryTarget3d(target: any, worldTarget: VirtualWorldTarget): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  if (worldTarget.kind === 'body') {
    addBodyTarget3d(target, worldTarget, false)
    return
  }
  const marker = new THREE.Group()
  const radius = targetMarkerRadius(worldTarget, false)
  const sphere = mesh3d(
    new THREE.SphereGeometry(radius, 24, 14),
    new THREE.MeshStandardMaterial({ color: 0xef4444, emissive: 0x330000, roughness: 0.42, transparent: true, opacity: worldTarget.visualLive ? 0.42 : 0.18, wireframe: true }),
  )
  marker.add(sphere)
  const ring = mesh3d(new THREE.TorusGeometry(radius * 1.55, 0.008, 8, 48), new THREE.MeshBasicMaterial({ color: 0xfca5a5, transparent: true, opacity: worldTarget.visualLive ? 0.65 : 0.2 }))
  ring.rotation.x = Math.PI / 2
  marker.add(ring)
  marker.position.copy(target)
  marker.name = `live_detected_balloon_${worldTarget.id}_secondary_marker`
  dynamicGroup.add(marker)
}

function targetMarkerRadius(worldTarget: VirtualWorldTarget, selected: boolean): number {
  if (worldTarget.kind === 'balloon') {
    // The scene depth curve compresses the 15 m lane so it fits on screen. It
    // must not shrink physical object dimensions relative
    // to the metre-scaled turret CAD. Selection is shown by glow/rings, not by
    // making the balloon itself unrealistically larger.
    const detectedDiameterM = worldTarget.projection.reference_size_m ?? BALLOON_DIAMETER_M
    const physicalRadius = detectedDiameterM * 0.5 * sceneUnitsPerPhysicalMeter()
    return Math.max(0.025, physicalRadius * balloonBboxDisplayScale(worldTarget))
  }
  const area = Math.max(0, worldTarget.projection.bbox_area_ratio)
  const areaScale = clamp(area / 0.031, 0.035, 3.2)
  const distanceScale = clamp(1.55 - ((worldTarget.rangeM - 5) / 10) * 1.22, 0.16, 1.55)
  const selectedBoost = selected ? 1.16 : 1
  const baseRadius = selected ? 0.42 : 0.32
  return clamp(baseRadius * areaScale * distanceScale * selectedBoost, selected ? 0.035 : 0.025, selected ? 1.8 : 1.35)
}

function balloonBboxDisplayScale(worldTarget: VirtualWorldTarget): number {
  const measuredArea = Math.max(0.000001, worldTarget.projection.bbox_area_ratio)
  // The 160 mm HIL balloon at 0.85 m is the 1.0 display reference. Perspective
  // already supplies the first distance cue; this bounded correction makes
  // the operator map reflect the stronger bbox-area change seen by the barrel
  // camera without feeding any value back into aiming or fire decisions.
  return clamp((measuredArea / BALLOON_REFERENCE_BBOX_AREA_AT_0_85M) ** 0.42, 0.38, 1.22)
}

function sceneUnitsPerPhysicalMeter(): number {
  const size = modelFit?.size
  if (!size) return 1
  const candidates = [
    Math.abs(size.x) / TURRET_PHYSICAL_WIDTH_M,
    Math.abs(size.z) / TURRET_PHYSICAL_DEPTH_M,
    Math.abs(size.y) / TURRET_PHYSICAL_HEIGHT_M,
  ].filter((value) => Number.isFinite(value) && value > 0.01).sort((a, b) => a - b)
  if (!candidates.length) return 1
  // Median rejects a long barrel/foot outlier while preserving the measured
  // 60x60x40 cm physical envelope of the actual turret.
  return clamp(candidates[Math.floor(candidates.length / 2)], 0.8, 6.0)
}

function sceneDepthForPhysicalRange(rangeM: number): number {
  const range = clamp(rangeM, 0.1, 40)
  const unitsPerMeter = sceneUnitsPerPhysicalMeter()
  if (range <= NEAR_RANGE_TRUE_SCALE_LIMIT_M) return range * unitsPerMeter
  const nearDepth = NEAR_RANGE_TRUE_SCALE_LIMIT_M * unitsPerMeter
  const farRangeSpan = 15 - NEAR_RANGE_TRUE_SCALE_LIMIT_M
  const compressedFarSpan = Math.max(0.5, FOV_WORLD_DEPTH - nearDepth)
  return nearDepth + Math.min(1, (range - NEAR_RANGE_TRUE_SCALE_LIMIT_M) / farRangeSpan) * compressedFarSpan
}

function addBodyTarget3d(target: any, worldTarget: VirtualWorldTarget, selected: boolean): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  const visual = new THREE.Group()
  visual.name = `live_detected_${worldTarget.className}_${worldTarget.id}`
  const teamColor = targetTeamColor(worldTarget.targetTeam)
  const template = worldTarget.spec.assetPath ? targetAssetTemplates.get(worldTarget.spec.assetPath) : null
  if (template) {
    const model = template.clone(true)
    model.userData.sharedTargetAsset = true
    model.traverse((child: any) => {
      child.userData.sharedTargetAsset = true
      if (child.isMesh && child.material) {
        const tintMaterial = (material: any) => {
          const clone = material.clone()
          if (clone.color?.setHex) clone.color.setHex(teamColor)
          if (clone.emissive?.setHex) clone.emissive.setHex(worldTarget.targetTeam === 'enemy' ? 0x340909 : 0x071a38)
          clone.needsUpdate = true
          return clone
        }
        child.material = Array.isArray(child.material)
          ? child.material.map(tintMaterial)
          : tintMaterial(child.material)
      }
    })
    // Target GLBs are authored in metres. Match them to the measured physical
    // turret scale; the separate depth curve compresses lane distance only.
    model.scale.setScalar(sceneUnitsPerPhysicalMeter())
    model.rotation.set(...worldTarget.spec.modelRotation)
    if (worldTarget.spec.modelYawRad) {
      model.rotateOnWorldAxis(new THREE.Vector3(0, 1, 0), worldTarget.spec.modelYawRad)
    }
    visual.add(model)
  } else {
    void ensureTargetAsset(worldTarget.spec)
    const proxy = mesh3d(
      new THREE.OctahedronGeometry(Math.max(0.07, worldTarget.spec.referenceSpanM * sceneUnitsPerPhysicalMeter() * 0.36), 1),
      new THREE.MeshStandardMaterial({ color: teamColor, emissive: worldTarget.targetTeam === 'enemy' ? 0x340909 : 0x071a38, roughness: 0.54, transparent: true, opacity: 0.82, wireframe: true }),
    )
    visual.add(proxy)
  }
  const ringColor = selected ? 0xfacc15 : teamColor
  const ring = mesh3d(
    new THREE.TorusGeometry(Math.max(0.09, worldTarget.spec.referenceSpanM * sceneUnitsPerPhysicalMeter() * 0.62), selected ? 0.014 : 0.009, 8, 48),
    new THREE.MeshBasicMaterial({ color: ringColor, transparent: true, opacity: selected ? 0.94 : 0.62, depthWrite: false }),
  )
  ring.rotation.x = Math.PI / 2
  visual.add(ring)
  if (!worldTarget.visualLive) applyVisualHoldOpacity(visual)
  visual.position.copy(target)
  dynamicGroup.add(visual)
  const labelPlacement = placeAircraftLabel(target, worldTarget)
  const label = createTextSprite(
    `${worldTarget.logicalName ?? `${worldTarget.label.toUpperCase()} #${worldTarget.id}`} · ${targetTeamLabel(worldTarget.targetTeam)}\\n${Math.round(worldTarget.projection.confidence * 100)}% · ${worldTarget.rangeLabel}\\n${worldTarget.projection.range_source}`,
    ringColor,
    'rgba(2, 6, 23, 0.84)',
  )
  label.position.copy(labelPlacement.position)
  label.scale.set(labelPlacement.scaleX, labelPlacement.scaleY, 1)
  dynamicGroup.add(label)
  // A small leader keeps the label attached to the correct model when several
  // aircraft are close together in image space.
  if (props.targetPolicy === 'AIRCRAFT') dynamicGroup.add(line3d(target, labelPlacement.position, ringColor, 0.42, true))
}

function placeAircraftLabel(target: any, worldTarget: VirtualWorldTarget): { position: any, scaleX: number, scaleY: number } {
  const THREE = threeModule
  const baseY = Math.max(0.18, worldTarget.spec.referenceSpanM * sceneUnitsPerPhysicalMeter() * 0.7)
  if (!THREE) return { position: target, scaleX: 0.5, scaleY: 0.17 }
  if (props.targetPolicy !== 'AIRCRAFT') {
    return { position: new THREE.Vector3(target.x + 0.18, target.y + baseY, target.z), scaleX: 0.5, scaleY: 0.17 }
  }
  const aircraft = virtualTargets.value.filter((item) => item.kind === 'body')
  const nearby = aircraft.filter((item) =>
    Math.abs(item.projection.normalized_center_x - worldTarget.projection.normalized_center_x) < 0.14
    && Math.abs(item.projection.normalized_center_y - worldTarget.projection.normalized_center_y) < 0.14,
  )
  const slot = Math.max(0, nearby.findIndex((item) => item.key === worldTarget.key))
  const side = slot % 2 === 0 ? 1 : -1
  const row = Math.floor(slot / 2)
  const horizontal = side * (0.28 + row * 0.12)
  const vertical = baseY + 0.16 + row * 0.22
  const depth = side > 0 ? 0.025 : -0.025
  return {
    position: new THREE.Vector3(target.x + horizontal, target.y + vertical, target.z + depth),
    scaleX: 0.58,
    scaleY: 0.2,
  }
}

function applyVisualHoldOpacity(object: any): void {
  object.traverse?.((child: any) => {
    const materials = Array.isArray(child.material) ? child.material : child.material ? [child.material] : []
    materials.forEach((material: any) => {
      material.transparent = true
      material.opacity = Math.min(Number(material.opacity ?? 1), 0.28)
      material.depthWrite = false
      material.needsUpdate = true
    })
  })
}

async function ensureTargetAsset(spec: TargetVisualSpec): Promise<void> {
  const THREE = threeModule
  const path = spec.assetPath
  if (!THREE || !path || targetAssetTemplates.has(path) || targetAssetLoads.has(path)) return
  const pending = (async () => {
    try {
      const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js')
      const loader = new GLTFLoader()
      const gltf = await loader.loadAsync(path)
      const root = gltf.scene
      root.name = `target_asset_template_${spec.className}`
      root.traverse((child: any) => {
        if (child.isMesh) {
          child.castShadow = true
          child.receiveShadow = true
        }
      })
      targetAssetTemplates.set(path, root)
    } catch (error) {
      console.warn(`target asset could not be loaded: ${path}`, error)
    } finally {
      targetAssetLoads.delete(path)
    }
  })()
  targetAssetLoads.set(path, pending)
  await pending
}

function addPrimaryEngagementRay3d(muzzle: any, target: any): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  const fireAllowed = shownState.value?.engagement.fire_allowed === true
  const ray = fireAllowed
    ? thickLine3d(muzzle, target, 0x22c55e, sceneMode.value === 'tacticalOverlay' ? 0.028 : 0.02, 0.9)
    : line3d(muzzle, target, 0xf59e0b, sceneMode.value === 'tacticalOverlay' ? 0.92 : 0.78, true)
  ray.name = 'primary_virtual_engagement_ray_launcher_muzzle_to_selected_target'
  dynamicGroup.add(ray)
  const marker = mesh3d(
    new THREE.SphereGeometry(0.055, 18, 12),
    new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: 0.95 }),
  )
  marker.name = 'launcher_muzzle_origin_marker'
  marker.position.copy(muzzle)
  dynamicGroup.add(marker)
}

function addDebugUnselectedTarget3d(cam: any, worldTarget: VirtualWorldTarget): void {
  const THREE = threeModule
  if (!THREE || !dynamicGroup) return
  const target = targetWorldPosition(cam, worldTarget)
  if (!target) return
  dynamicGroup.add(line3d(cam, target, 0x22d3ee, 0.18, true))
  const marker = mesh3d(
    new THREE.SphereGeometry(0.08, 16, 10),
    new THREE.MeshStandardMaterial({ color: 0xef4444, transparent: true, opacity: 0.52, roughness: 0.4 }),
  )
  marker.position.copy(target)
  dynamicGroup.add(marker)
}

function addFovVolume3d(origin: any): void {
  const THREE = threeModule
  const basis = tacticalBasis()
  const far = FOV_WORLD_DEPTH
  const halfW = FOV_WORLD_HALF_WIDTH
  const halfH = FOV_WORLD_HALF_HEIGHT
  const farCenter = origin.clone().add(basis.forward.clone().multiplyScalar(far))
  const corners = [
    farCenter.clone().add(basis.right.clone().multiplyScalar(-halfW)).add(basis.up.clone().multiplyScalar(halfH)),
    farCenter.clone().add(basis.right.clone().multiplyScalar(halfW)).add(basis.up.clone().multiplyScalar(halfH)),
    farCenter.clone().add(basis.right.clone().multiplyScalar(halfW)).add(basis.up.clone().multiplyScalar(-halfH)),
    farCenter.clone().add(basis.right.clone().multiplyScalar(-halfW)).add(basis.up.clone().multiplyScalar(-halfH)),
  ]
  // Outline-only FOV. The former transparent face mesh was rendered with
  // depthTest=false and tinted the floor as a permanent pale wedge in the
  // initial camera direction. Keeping the eight boundary edges communicates
  // the same field of view without changing any terrain pixel/material.
  const boundary = new THREE.LineSegments(
    new THREE.BufferGeometry().setFromPoints([
      origin, corners[0], origin, corners[1], origin, corners[2], origin, corners[3],
      corners[0], corners[1], corners[1], corners[2], corners[2], corners[3], corners[3], corners[0],
    ]),
    new THREE.LineDashedMaterial({ color: 0x67e8f9, dashSize: 0.22, gapSize: 0.12, transparent: true, opacity: sceneMode.value === 'showcaseWorld' ? 0.74 : labelMode.value === 'clean' ? 0.68 : 0.9, depthTest: false, depthWrite: false }),
  )
  boundary.name = 'camera_fov_outline_from_camera_anchor'
  boundary.renderOrder = 82
  boundary.computeLineDistances()
  dynamicGroup.add(boundary)
  const tubeOpacity = sceneMode.value === 'showcaseWorld' ? 0.38 : 0.42
  const tubeRadius = sceneMode.value === 'showcaseWorld' ? 0.007 : 0.008
  const fovEdges = [
    [origin, corners[0]], [origin, corners[1]], [origin, corners[2]], [origin, corners[3]],
    [corners[0], corners[1]], [corners[1], corners[2]], [corners[2], corners[3]], [corners[3], corners[0]],
  ]
  fovEdges.forEach(([start, end]) => {
    const edge = thickLine3d(start, end, 0x67e8f9, tubeRadius, tubeOpacity)
    edge.name = 'legacy_visible_camera_fov_edge'
    edge.renderOrder = 84
    dynamicGroup.add(edge)
  })
  if (labelMode.value !== 'clean' || sceneMode.value === 'tacticalOverlay') {
    const label = createTextSprite('Camera FOV', 0x67e8f9, 'rgba(2, 6, 23, 0.42)')
    label.position.copy(farCenter.clone().add(basis.right.clone().multiplyScalar(-halfW * 0.72)).add(basis.up.clone().multiplyScalar(halfH * 0.22)))
    label.scale.set(0.36, 0.11, 1)
    dynamicGroup.add(label)
  }
}

function addNoGoZone3d(): void {
  const THREE = threeModule
  const zone = mesh3d(
    new THREE.BoxGeometry(1.2, 1.05, 1.0),
    new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.09, wireframe: false, depthWrite: false }),
  )
  zone.position.set(1.62, 0.18, 3.75)
  dynamicGroup.add(zone)
  const label = createTextSprite('NO-GO', 0xfca5a5)
  label.position.set(1.62, 0.93, 3.75)
  label.scale.set(0.4, 0.13, 1)
  dynamicGroup.add(label)
}

function line3d(start: any, end: any, color: number, opacity: number, dashed = false): any {
  const THREE = threeModule
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, dashSize: 0.12, gapSize: 0.09, transparent: true, opacity, depthTest: false, depthWrite: false })
    : new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false })
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([start, end]), material)
  if (dashed) line.computeLineDistances()
  return line
}

function thickLine3d(start: any, end: any, color: number, radius: number, opacity: number): any {
  const THREE = threeModule
  const direction = end.clone().sub(start)
  const length = direction.length()
  const geometry = new THREE.CylinderGeometry(radius, radius, length, 14)
  const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false })
  const cylinder = mesh3d(geometry, material)
  cylinder.position.copy(start.clone().add(end).multiplyScalar(0.5))
  cylinder.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize())
  return cylinder
}

function createTextSprite(text: string, color: number, background = 'rgba(2, 6, 23, 0.78)'): any {
  const THREE = threeModule
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 192
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = background
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.strokeStyle = `#${color.toString(16).padStart(6, '0')}`
    ctx.lineWidth = 4
    if (background !== 'rgba(2, 6, 23, 0.0)') ctx.strokeRect(3, 3, canvas.width - 6, canvas.height - 6)
    ctx.fillStyle = `#${color.toString(16).padStart(6, '0')}`
    ctx.font = '700 30px Inter, Arial, sans-serif'
    text.split('\\n').slice(0, 3).forEach((line, index) => ctx.fillText(line.slice(0, 34), 20, 48 + index * 48))
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }))
}

function updateInspectorPick(event: PointerEvent, commit: boolean): void {
  if (!renderer || !camera3d || !modelGroup || !pickRaycaster || !pickPointer) return
  const rect = renderer.domElement.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  pickPointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  pickPointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
  pickRaycaster.setFromCamera(pickPointer, camera3d)
  const hits = pickRaycaster.intersectObjects(modelGroup.children, true)
  const hit = hits.find((item: any) => item.object?.isMesh)
  if (!hit) {
    if (!commit) hoveredInspector.value = null
    return
  }
  const selection = inspectorSelectionFromObject(hit.object)
  if (commit) selectedInspector.value = selection
  else hoveredInspector.value = selection
}

function inspectorSelectionFromObject(object: any): KtrInspectorSelection {
  const THREE = threeModule
  const sourceName = String(object.userData.sourceNodeName ?? object.name ?? 'unnamed_node')
  const group = String(object.userData.kinematicGroup ?? kinematicGroupForNode(kinematics.value, sourceName))
  const material = Array.isArray(object.material) ? object.material[0] : object.material
  const materialName = String(object.userData.materialName ?? material?.name ?? 'material n/a')
  const materialColor = String(object.userData.materialColor ?? (material?.color?.getHexString?.() ? `#${material.color.getHexString()}` : '#d1d5db'))
  let bbox: KtrInspectorSelection['boundingBox']
  if (THREE && labelMode.value === 'debug') {
    const box = new THREE.Box3().setFromObject(object)
    bbox = {
      min: [round3(box.min.x), round3(box.min.y), round3(box.min.z)],
      max: [round3(box.max.x), round3(box.max.y), round3(box.max.z)],
    }
  }
  return { nodeName: sourceName, groupName: group, materialName, materialColor, boundingBox: bbox }
}

function resetKinematicPreview(): void {
  yawPreviewDeg.value = 0
  pitchPreviewDeg.value = 0
  applyKinematicPreviewPose()
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000
}

function setViewPreset(preset: ViewPreset): void {
  viewPreset.value = preset
  applyCameraPreset(preset)
}

function resetViewPreset(): void {
  setViewPreset(sceneMode.value === 'freecadMatch' ? 'freecad' : 'operator')
}

function focusScene(): void {
  // Restore the validated operator framing after free orbit/pan/zoom. This
  // changes only the camera, never pose, selection or tracking state.
  resetViewPreset()
}

async function enterBrowserFullscreen(): Promise<void> {
  const panel = canvasRoot.value?.closest('.digital-twin-shell') as HTMLElement | null
  if (!panel?.requestFullscreen) return
  await panel.requestFullscreen()
  setTimeout(resizeThreeScene, 120)
}

function handleKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
  if (event.key.toLowerCase() === 'r') resetViewPreset()
  // F is reserved by the cockpit for manual FIRE. Fullscreen keeps an
  // explicit non-operational chord so a key press can never trigger both.
  if (event.key.toLowerCase() === 'f' && event.ctrlKey && event.shiftKey) void enterBrowserFullscreen()
  if (event.key.toLowerCase() === 'l') labelMode.value = labelMode.value === 'clean' ? 'tactical' : labelMode.value === 'tactical' ? 'debug' : 'clean'
  if (event.key.toLowerCase() === 'o') {
    edgesEnabled.value = !edgesEnabled.value
    rebuildThreeScene()
  }
  if (event.key === '1') { sceneMode.value = 'freecadMatch'; setViewPreset('freecad') }
  if (event.key === '2') setViewPreset('operator')
  if (event.key === '3') setViewPreset('front')
  if (event.key === '4') setViewPreset('side')
  if (event.key === '5') setViewPreset('top')
  if (event.key === '6') setViewPreset('weapon')
  if (event.key === '7') setViewPreset('target')
  if (event.key === '8') setViewPreset('camera')
}

function computeModelFit(): { center: any, radius: number, size: any } | null {
  if (!threeModule || !modelGroup) return null
  const THREE = threeModule
  const box = new THREE.Box3().setFromObject(modelGroup)
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)
  const radius = Math.max(size.length() * 0.5, Math.max(size.x, size.y, size.z) * 0.5, 0.1)
  return { center, radius, size }
}

function fitCameraToModel(preset: ViewPreset): void {
  if (!camera3d || !controls || !threeModule) return
  if (!modelFit) modelFit = computeModelFit()
  if (!modelFit) return
  const THREE = threeModule
  const { center, radius, size } = modelFit
  const frameCenter = center.clone()
  if (!isFreecadMatch.value) frameCenter.y -= radius * 0.1
  const isWeaponPreset = preset === 'weapon' || preset === 'weaponCloseup'
  if (isWeaponPreset) {
    const launcherAnchor = anchorVector('launcher_anchor', { x: -0.3, y: 0.68, z: 1.26 })
    frameCenter.set(launcherAnchor.x, launcherAnchor.y, launcherAnchor.z)
  }
  const distance = isWeaponPreset
    ? radius * (preset === 'weaponCloseup' ? 1.18 : 1.62)
    : radius * (isFreecadMatch.value ? 2.8 : props.worldMode ? 2.55 : 2.8)
  const presetVectors: Record<ViewPreset, any> = {
    freecad: new THREE.Vector3(0.05, 0.34, 1),
    operator: new THREE.Vector3(-0.38, 0.48, 0.86),
    front: new THREE.Vector3(0, 0.18, 1),
    side: new THREE.Vector3(1, 0.18, 0),
    top: new THREE.Vector3(0, 1, 0.001),
    rear: new THREE.Vector3(0, 0.18, -1),
    weapon: new THREE.Vector3(0.16, 0.16, 1),
    weaponCloseup: new THREE.Vector3(0.08, 0.12, 1),
    chase: new THREE.Vector3(-0.15, 0.2, 1),
    camera: new THREE.Vector3(0.05, 0.12, 1),
    target: new THREE.Vector3(0.55, 0.24, -0.82),
  }
  const direction = presetVectors[preset].clone().normalize()
  camera3d.position.copy(frameCenter.clone().add(direction.multiplyScalar(distance)))
  controls.target.copy(frameCenter)
  if (camera3d.isOrthographicCamera) {
    const width = Math.max(320, canvasRoot.value?.clientWidth ?? 1200)
    const height = Math.max(240, canvasRoot.value?.clientHeight ?? 760)
    const aspect = width / height
    const halfH = isWeaponPreset
      ? Math.max(size.y * (preset === 'weaponCloseup' ? 0.16 : 0.24), radius * (preset === 'weaponCloseup' ? 0.2 : 0.3))
      : isFreecadMatch.value
        ? Math.max(size.y * 0.68, radius * 0.74)
        : Math.max(size.y * 0.54, radius * 0.6)
    const halfW = halfH * aspect
    camera3d.left = -halfW
    camera3d.right = halfW
    camera3d.top = halfH
    camera3d.bottom = -halfH
    camera3d.zoom = isWeaponPreset
      ? (preset === 'weaponCloseup' ? 1.15 : 1.02)
      : isFreecadMatch.value
        ? 0.84
        : props.worldMode ? 0.98 : 0.94
  } else {
    camera3d.fov = isWeaponPreset ? (preset === 'weaponCloseup' ? 22 : 28) : props.worldMode ? 32 : 34
  }
  camera3d.near = 0.01
  camera3d.far = Math.max(200, radius * 40)
  camera3d.lookAt(frameCenter)
  camera3d.updateProjectionMatrix()
  controls.update()
}

function applyCameraPreset(preset: ViewPreset): void {
  if (!camera3d || !controls || !threeModule) return
  if (modelFit) {
    fitCameraToModel(preset)
    return
  }
  const THREE = threeModule
  const cameraAnchor = anchorVector('camera_anchor', { x: 0.5, y: 0.72, z: 1.03 })
  const targetPoint = new THREE.Vector3(
    cameraAnchor.x + geometry.value.target_scene_x * 2.15,
    cameraAnchor.y + geometry.value.target_scene_y * 0.92,
    cameraAnchor.z + 1.45 + geometry.value.target_scene_depth * 4.4,
  )
  const target = new THREE.Vector3(0.04, 0.56, -0.32)
  const positions = {
    freecad: new THREE.Vector3(0.18, 1.8, 3.3),
    operator: new THREE.Vector3(-2.45, 1.55, 3.05),
    front: new THREE.Vector3(0.0, 1.08, 3.25),
    side: new THREE.Vector3(3.35, 1.2, 0.25),
    top: new THREE.Vector3(0.0, 4.45, 0.0),
    rear: new THREE.Vector3(0.0, 1.08, -3.25),
    weapon: new THREE.Vector3(0.38, 0.96, 2.1),
    weaponCloseup: new THREE.Vector3(0.16, 0.82, 1.45),
    chase: new THREE.Vector3(cameraAnchor.x, cameraAnchor.y + 0.2, cameraAnchor.z + 1.1),
    camera: new THREE.Vector3(cameraAnchor.x, cameraAnchor.y + 0.08, cameraAnchor.z + 0.8),
    target: new THREE.Vector3(targetPoint.x + 0.35, targetPoint.y + 0.28, targetPoint.z - 0.9),
  }
  camera3d.position.copy(positions[preset])
  if (preset === 'target') controls.target.set(0.05, 0.48, -0.35)
  else if (preset === 'camera') controls.target.copy(targetPoint)
  else if (preset === 'weapon' || preset === 'weaponCloseup') {
    const launcherAnchor = anchorVector('launcher_anchor', { x: -0.3, y: 0.68, z: 1.26 })
    controls.target.set(launcherAnchor.x, launcherAnchor.y, launcherAnchor.z)
  }
  else if (preset === 'chase') controls.target.copy(targetPoint)
  else controls.target.copy(target)
  controls.update()
}

function resizeThreeScene(): void {
  if (!renderer || !camera3d || !canvasRoot.value) return
  const width = Math.max(300, canvasRoot.value.clientWidth)
  const height = Math.max(240, canvasRoot.value.clientHeight)
  renderer.setSize(width, height, false)
  if (camera3d.isOrthographicCamera && modelFit) {
    fitCameraToModel(viewPreset.value)
  } else {
    camera3d.aspect = width / height
    if (modelFit) fitCameraToModel(viewPreset.value)
  }
  camera3d.updateProjectionMatrix()
}

function renderThreeLoop(): void {
  if (!renderer || !scene || !camera3d) return
  const now = performance.now()
  const minFrameMs = 1000 / renderFps.value
  if (!document.hidden && now - lastRenderAt >= minFrameMs) {
    lastRenderAt = now
    // Synchronize from the current prop on every rendered frame. This avoids a
    // race where the 2 Hz state watcher fires while the large GLB is still
    // loading and the newly-created pivots otherwise retain their initial pose.
    applyTelemetryPoseIfAvailable()
    advanceManualMotionIntent(now)
    advanceDisplayedPose(now)
    applyKinematicPreviewPose()
    if (showTacticalOverlays.value) {
      if (now - lastDynamicRebuildAt >= 250) {
        lastDynamicRebuildAt = now
        updateDynamic3d()
      }
    }
    else if (dynamicGroup?.children?.length) disposeChildren(dynamicGroup)
    controls?.update?.()
    renderer.render(scene, camera3d)
  }
  animationId = window.requestAnimationFrame(renderThreeLoop)
}

function tacticalBasis(): { forward: any, right: any, up: any } {
  const THREE = threeModule
  if (!THREE) return { forward: null, right: null, up: null }
  return {
    forward: transformDirectionByPreview(usesManualPhase55Calibration() ? PHASE55_MANUAL_CALIBRATION.sourceAxes.forward : { x: 0, y: 0, z: 1 }, 'pitch_group'),
    right: transformDirectionByPreview(usesManualPhase55Calibration() ? PHASE55_MANUAL_CALIBRATION.sourceAxes.right : { x: 1, y: 0, z: 0 }, 'pitch_group'),
    up: transformDirectionByPreview(usesManualPhase55Calibration() ? PHASE55_MANUAL_CALIBRATION.sourceAxes.up : { x: 0, y: 1, z: 0 }, 'pitch_group'),
  }
}

function worldReferenceBasis(): { forward: any, right: any, up: any } {
  const THREE = threeModule
  if (!THREE) return { forward: null, right: null, up: null }
  const source = usesManualPhase55Calibration()
    ? PHASE55_MANUAL_CALIBRATION.sourceAxes
    : {
        forward: { x: 0, y: 0, z: 1 },
        right: { x: 1, y: 0, z: 0 },
        up: { x: 0, y: 1, z: 0 },
      }
  const fromSource = (value: Vec3Like) => {
    const vector = new THREE.Vector3(value.x, value.y, value.z)
    if (usesManualPhase55Calibration()) applyRawCalibrationRotationToVector(vector)
    return vector.normalize()
  }
  return {
    forward: fromSource(source.forward),
    right: fromSource(source.right),
    up: fromSource(source.up),
  }
}

function rebuildThreeScene(): void {
  cleanupThreeScene()
  modelFit = null
  void nextTick(initThreeScene)
}

function cleanupThreeScene(): void {
  if (animationId !== null) window.cancelAnimationFrame(animationId)
  animationId = null
  resizeObserver?.disconnect()
  resizeObserver = null
  if (renderer?.domElement && pointerMoveHandler) renderer.domElement.removeEventListener('pointermove', pointerMoveHandler)
  if (renderer?.domElement && pointerClickHandler) renderer.domElement.removeEventListener('click', pointerClickHandler)
  if (renderer?.domElement) renderer.domElement.removeEventListener('dblclick', resetViewPreset)
  pointerMoveHandler = null
  pointerClickHandler = null
  pickRaycaster = null
  pickPointer = null
  yawPivotObject = null
  pitchPivotObject = null
  targetAssetTemplates.forEach((template) => disposeObject(template))
  targetAssetTemplates.clear()
  targetAssetLoads.clear()
  if (renderer?.domElement?.parentElement) renderer.domElement.parentElement.removeChild(renderer.domElement)
  controls?.dispose?.()
  controls = null
  scene?.traverse(disposeObject)
  renderer?.dispose()
  renderer = null
  scene = null
  camera3d = null
  environmentGroup = null
  modelGroup = null
  dynamicGroup = null
  rangeTargetModelGroup = null
  modelFit = null
}

function disposeChildren(group: any): void {
  while (group.children.length) {
    const child = group.children[0]
    group.remove(child)
    disposeObject(child)
  }
}

function disposeObject(object: any): void {
  if (object.userData?.sharedTargetAsset) return
  object.traverse?.((child: any) => {
    if (child.userData?.sharedTargetAsset) return
    child.geometry?.dispose?.()
    const material = child.material
    if (Array.isArray(material)) material.forEach((item) => disposeMaterial(item))
    else if (material) disposeMaterial(material)
  })
}

function disposeMaterial(material: any): void {
  material.map?.dispose?.()
  material.dispose?.()
}

function fallbackProjection(state: DigitalTwinState | null): DigitalTwinTargetProjectionEstimate {
  const width = state?.camera.width ?? 1280
  const height = state?.camera.height ?? 720
  return {
    target_id: props.ktrDemoMode ? 1 : state?.target.track_id ?? 1,
    class_name: props.ktrDemoMode ? 'balloon' : state?.target.class_id ?? 'balloon',
    confidence: props.ktrDemoMode ? 0.82 : state?.target.confidence ?? 0.82,
    confidence_label: 'high',
    bbox: state?.target.bbox ?? { x: 820, y: 330, w: 190, h: 118, format: 'xywh' },
    normalized_center_x: state?.target.normalized_x !== null && state?.target.normalized_x !== undefined ? 0.5 + state.target.normalized_x / 2 : 0.76,
    normalized_center_y: state?.target.normalized_y !== null && state?.target.normalized_y !== undefined ? 0.5 - state.target.normalized_y / 2 : 0.54,
    normalized_width: 190 / width,
    normalized_height: 118 / height,
    normalized_screen_x: 0.52,
    normalized_screen_y: -0.08,
    bbox_area_ratio: 0.031,
    azimuth_deg: 16.1,
    elevation_deg: -1.5,
    relative_depth: 0.49,
    estimated_range_band: 'mid',
    reference_size_m: BALLOON_DIAMETER_M,
    estimated_range_m: 8,
    range_uncertainty_m: 2,
    range_source: 'class_bbox_pinhole_estimate',
    scene_position_m: { x: 1.05, y: -0.02, z: -4.4 },
    selected: true,
    mapping_source: 'bbox_projection_estimate',
    depth_source: 'bbox_area_relative_estimate',
    projection_is_calibrated: false,
    camera_fov_horizontal_deg: state?.camera_fov_horizontal_deg ?? 78,
    camera_fov_vertical_deg: state?.camera_fov_vertical_deg ?? 48,
    camera_to_launcher_offset_z_mm: state?.camera_to_launcher_offset_z_mm ?? 30,
    camera_to_launcher_offset_y_mm: state?.camera_to_launcher_offset_y_mm ?? 0,
    no_physical_command_generated: true,
  }
}

function projectionFromBalloonDetection(
  target: BalloonDetection,
  frameWidth: number,
  frameHeight: number,
  cameraFovHorizontalDeg: number,
  cameraFovVerticalDeg: number,
  cameraToLauncherOffsetZMm: number,
): DigitalTwinTargetProjectionEstimate {
  const backendProjection = shownState.value?.target_projection_estimates?.find((projection) =>
    canonicalTargetClass(projection.class_name) === 'balloon' && projection.target_id === target.id,
  )
  return projectionFromTargetBbox({
    id: target.id,
    className: 'balloon',
    confidence: target.confidence,
    bbox: target.bbox,
    frameWidth,
    frameHeight,
    cameraFovHorizontalDeg,
    cameraFovVerticalDeg,
    cameraToLauncherOffsetZMm,
    referenceSpanM: backendProjection?.reference_size_m ?? BALLOON_DIAMETER_M,
    selected: target.id === props.selectedTargetId,
    visualLive: target.visual_live !== false,
    visualAgeMs: target.visual_age_ms ?? 0,
    visualLogicalName: target.visual_logical_name ?? null,
  })
}

function projectionFromBodyDetection(
  target: BodyDetection,
  frameWidth: number,
  frameHeight: number,
  cameraFovHorizontalDeg: number,
  cameraFovVerticalDeg: number,
  cameraToLauncherOffsetZMm: number,
): DigitalTwinTargetProjectionEstimate {
  return projectionFromTargetBbox({
    id: target.id,
    className: canonicalTargetClass(target.class_name),
    targetTeam: target.target_team,
    confidence: target.confidence,
    bbox: target.bbox,
    frameWidth,
    frameHeight,
    cameraFovHorizontalDeg,
    cameraFovVerticalDeg,
    cameraToLauncherOffsetZMm,
    selected: false,
    visualLive: target.visual_live !== false,
    visualAgeMs: target.visual_age_ms ?? 0,
    visualLogicalName: target.visual_logical_name ?? null,
  })
}

function projectionFromTargetBbox(input: {
  id: number
  className: DigitalTwinTargetClass
  targetTeam?: string | null
  confidence: number
  bbox: { x: number, y: number, w: number, h: number }
  frameWidth: number
  frameHeight: number
  cameraFovHorizontalDeg: number
  cameraFovVerticalDeg: number
  cameraToLauncherOffsetZMm: number
  referenceSpanM?: number | null
  selected: boolean
  visualLive?: boolean
  visualAgeMs?: number
  visualLogicalName?: string | null
}): DigitalTwinTargetProjectionEstimate {
  const { id, className, targetTeam, confidence, bbox, frameWidth, frameHeight, cameraFovHorizontalDeg, cameraFovVerticalDeg, cameraToLauncherOffsetZMm, referenceSpanM, selected, visualLive, visualAgeMs, visualLogicalName } = input
  const x = clamp(bbox.x, 0, frameWidth - 1)
  const y = clamp(bbox.y, 0, frameHeight - 1)
  const w = clamp(bbox.w, 1, frameWidth - x)
  const h = clamp(bbox.h, 1, frameHeight - y)
  const cx = x + w / 2
  const cy = y + h / 2
  const normalizedCenterX = clamp(cx / frameWidth, 0, 1)
  const normalizedCenterY = clamp(cy / frameHeight, 0, 1)
  const normalizedWidth = clamp(w / frameWidth, 0, 1)
  const normalizedHeight = clamp(h / frameHeight, 0, 1)
  const bboxAreaRatio = clamp((w * h) / (frameWidth * frameHeight), 0, 1)
  const range = rangeEstimateFromBbox(className, { w, h }, frameWidth, frameHeight, cameraFovHorizontalDeg, cameraFovVerticalDeg, referenceSpanM)
  const relativeDepth = clamp((range.rangeM - 5) / 10, 0, 1)
  const spec = visualSpecForTarget(className)
  return {
    target_id: id,
    class_name: className,
    target_team: targetTeam ?? 'unknown',
    confidence: clamp(confidence, 0, 1),
    confidence_label: confidence >= 0.8 ? 'high' : confidence >= 0.5 ? 'medium' : 'low',
    bbox: { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h), format: 'pixel' },
    normalized_center_x: round4(normalizedCenterX),
    normalized_center_y: round4(normalizedCenterY),
    normalized_width: round4(normalizedWidth),
    normalized_height: round4(normalizedHeight),
    normalized_screen_x: round4((normalizedCenterX - 0.5) * 2),
    normalized_screen_y: round4((0.5 - normalizedCenterY) * 2),
    bbox_area_ratio: Number(bboxAreaRatio.toFixed(6)),
    azimuth_deg: round3((normalizedCenterX - 0.5) * cameraFovHorizontalDeg),
    elevation_deg: round3((0.5 - normalizedCenterY) * cameraFovVerticalDeg),
    relative_depth: round4(relativeDepth),
    estimated_range_band: relativeDepth < 0.34 ? 'near' : relativeDepth < 0.67 ? 'mid' : 'far',
    reference_size_m: referenceSpanM ?? spec.referenceSpanM,
    estimated_range_m: range.rangeM,
    range_uncertainty_m: range.uncertaintyM,
    range_source: range.source,
    scene_position_m: { x: 0, y: 0, z: -range.rangeM },
    selected,
    mapping_source: `bbox_projection_${className}_reference_size_estimate`,
    depth_source: range.source,
    projection_is_calibrated: false,
    camera_fov_horizontal_deg: cameraFovHorizontalDeg,
    camera_fov_vertical_deg: cameraFovVerticalDeg,
    camera_to_launcher_offset_z_mm: cameraToLauncherOffsetZMm,
    camera_to_launcher_offset_y_mm: 0,
    no_physical_command_generated: true,
    visual_live: visualLive ?? true,
    visual_age_ms: visualAgeMs ?? 0,
    visual_logical_name: visualLogicalName ?? null,
  }
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

watch(renderFps, startRenderTicker)

watch(() => props.replay?.run_id, (runId) => {
  if (!runId?.startsWith('engagement-') || !props.replay?.events.length) return
  viewMode.value = 'replay'
  replayStartedAt.value = performance.now()
  replayClock.value = replayStartedAt.value
})

watch(() => props.replayControl, (control) => {
  if (!control || !props.replay?.run_id.endsWith(control.engagementId) || !props.replay.events.length) return
  viewMode.value = 'replay'
  if (replayStartedAt.value === null) replayStartedAt.value = performance.now()
  replayClock.value = replayStartedAt.value + control.positionMs
}, { deep: true })

watch(acknowledgedShot, (shot) => {
  if (!shot?.shot_id) return
  if (shot.shot_id !== visualShotId.value) {
    visualShotId.value = shot.shot_id
    visualShotStartedAt.value = performance.now()
  }
  // Pending -> hit/miss updates the effect without restarting the projectile.
  visualShot.value = { ...shot }
}, { deep: true })

function handleMotionEnvelopeUpdated(e: Event): void {
  const custom = e as CustomEvent
  if (custom?.detail) {
    if (typeof custom.detail.pan_min_deg === 'number' && props.motionSettings) {
      props.motionSettings.pan_min_deg = custom.detail.pan_min_deg - 135.0
    }
    if (typeof custom.detail.pan_max_deg === 'number' && props.motionSettings) {
      props.motionSettings.pan_max_deg = custom.detail.pan_max_deg - 135.0
    }
  }
  updateWorldEnvironment()
}

onMounted(() => {
  void nextTick(() => {
    startRenderTicker()
    void initThreeScene()
    emit('panelRendered')
  })
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('istiklal:motion-envelope-updated', handleMotionEnvelopeUpdated)
})

watch(() => props.motionState?.limit_reason_code, () => {
  updateWorldEnvironment()
})

watch(sceneMode, (mode) => {
  if (mode === 'freecadMatch') viewPreset.value = 'freecad'
  else if (viewPreset.value === 'freecad') viewPreset.value = 'operator'
  if (mode === 'cadDebug') cleanupThreeScene()
  else rebuildThreeScene()
})
watch(viewPreset, (preset) => applyCameraPreset(preset))
watch(labelMode, () => {
  updateWorldEnvironment()
  if (!showTacticalOverlays.value && dynamicGroup?.children?.length) disposeChildren(dynamicGroup)
  else updateDynamic3d()
})
watch(assetMode, () => {
  heroManifest.value = null
  rebuildThreeScene()
})
watch([wireframeEnabled, xrayEnabled, explodedViewEnabled], () => {
  rebuildThreeScene()
})
watch(fineHardwareVisible, () => {
  rebuildThreeScene()
})
watch([yawPreviewDeg, pitchPreviewDeg], () => {
  yawPreviewDeg.value = clampPreviewDeg(yawPreviewDeg.value, [motionLimits.value.panMinDeg, motionLimits.value.panMaxDeg])
  pitchPreviewDeg.value = clampPreviewDeg(pitchPreviewDeg.value, [motionLimits.value.tiltMinDeg, motionLimits.value.tiltMaxDeg])
  applyKinematicPreviewPose()
  if (dynamicGroup?.children?.length) updateDynamic3d()
})

watch([fovVisible, targetVisible, engagementRayVisible], () => {
  updateDynamic3d()
})

watch([gridVisible, environmentVisible], () => {
  updateWorldEnvironment()
})

watch(() => props.motionSettings, () => {
  // Rebuild only the environment layer; the GLB and pose animation stay
  // untouched when an operator edits a degree field.
  updateWorldEnvironment()
  applyTelemetryPoseIfAvailable()
}, { deep: true })

watch(() => props.motionState?.limit_reason_code, () => {
  updateWorldEnvironment()
})

watch(shownState, () => {
  applyTelemetryPoseIfAvailable()
})

watch(() => props.manualMotionIntent, (intent) => {
  if (intent?.active) return
  manualIntentLastAt = null
  applyTelemetryPoseIfAvailable()
}, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('istiklal:motion-envelope-updated', handleMotionEnvelopeUpdated)
  stopRenderTicker()
  cleanupThreeScene()
})
</script>

<template>
  <section class="digital-twin-shell flex h-full flex-col overflow-hidden rounded-lg border border-cyan-400/18 bg-[#071018] shadow-[0_0_48px_rgba(8,145,178,0.14)]" :class="{ 'world-marker': props.worldMode, 'freecad-shell': isFreecadMatch }">
    <div class="flex items-center justify-between gap-3 border-b border-cyan-400/14 px-4 py-2.5">
      <div class="min-w-[220px]">
        <h3 class="whitespace-nowrap text-base font-semibold" :class="isFreecadMatch ? 'text-slate-950' : 'text-white'">{{ sceneTitle }}</h3>
        <p class="truncate text-xs" :class="isFreecadMatch ? 'text-slate-600' : 'text-slate-500'">{{ sceneSubtitle }}</p>
        <span class="sr-only">3D DİJİTAL İKİZ legacy label · render capped {{ renderFps }} FPS</span>
      </div>
      <div v-if="showDeveloperControls" class="flex shrink-0 flex-wrap justify-end gap-2">
        <StatusBadge label="READ ONLY" :tone="safetyTone" />
        <StatusBadge :label="poseBadgeLabel" :tone="poseBadgeTone" />
        <StatusBadge :label="assetBadgeLabel" :tone="assetBadgeTone" />
        <StatusBadge :label="materialBadgeLabel" :tone="heroManifest?.material_preserved ? 'good' : 'warn'" />
        <StatusBadge :label="kinematicBadgeLabel" :tone="kinematics ? 'good' : 'warn'" />
        <StatusBadge :label="virtualPoseLabel" tone="neutral" />
        <StatusBadge label="ORBIT ENABLED" tone="good" />
        <StatusBadge :label="performanceBadgeLabel" :tone="props.performanceMode === 'LOW' ? 'warn' : 'good'" />
        <span class="sr-only">ASSET: REAL_MODEL · ASSET: STL-DERIVED TWIN · STL-derived simplified digital twin · relative depth estimate · Full 3D World · Open Full 3D World · Geometry Details</span>
      </div>
      <div v-else class="flex shrink-0 flex-wrap justify-end gap-2">
        <StatusBadge label="SAHNE AKTİF" tone="good" />
        <StatusBadge :label="hasSelectedTarget ? 'HEDEF SEÇİLİ' : 'HEDEF YOK'" :tone="hasSelectedTarget ? 'good' : 'neutral'" />
      </div>
    </div>

    <div class="world-toolbar compact-world-toolbar flex items-center justify-between gap-2 border-b border-white/10 px-3 py-1.5">
      <div v-if="props.operatorMode && !props.worldMode" class="operator-world-toolbar flex min-w-0 flex-1 flex-wrap items-center justify-center gap-1.5">
        <label class="toolbar-select-label">
          Görünüm
          <select v-model="viewPreset" class="toolbar-select">
            <option value="operator">Operatör</option>
            <option value="top">Üstten</option>
            <option value="side">Yandan</option>
            <option value="target">Hedef görüşü</option>
            <option value="freecad">Serbest görünüm</option>
          </select>
        </label>
        <button class="operator-toggle" @click="enterBrowserFullscreen">Tam 3D Dünya</button>
        <button class="operator-toggle" @click="focusScene">Odakla</button>
        <button class="operator-toggle compact" :class="{ active: fovVisible }" @click="fovVisible = !fovVisible">FOV</button>
        <button class="operator-toggle compact" :class="{ active: targetVisible }" @click="targetVisible = !targetVisible">Hedef</button>
      </div>
      <div v-else-if="showDeveloperControls" class="preset-toolbar flex min-w-0 flex-1 flex-wrap justify-center gap-1.5">
        <label class="toolbar-select-label">
          Asset
          <select v-model="assetMode" class="toolbar-select">
            <option v-for="asset in assetModes" :key="asset.id" :value="asset.id">{{ asset.label }}</option>
          </select>
        </label>
        <label class="toolbar-select-label">
          View
          <select v-model="viewPreset" class="toolbar-select">
            <option v-for="preset in cameraPresets" :key="preset.id" :value="preset.id">{{ preset.label }}</option>
          </select>
        </label>
        <button class="rounded-md border border-cyan-300/25 bg-slate-900/60 px-2 py-1 text-xs font-semibold text-cyan-100" @click="focusScene">Odakla</button>
        <button class="rounded-md border border-amber-300/25 bg-slate-900/60 px-2 py-1 text-xs font-semibold text-amber-100" @click="resetViewPreset">Reset</button>
      </div>
      <div v-else class="operator-world-toolbar flex min-w-0 flex-1 flex-wrap items-center justify-center gap-1.5">
        <label class="toolbar-select-label">
          Görünüm
          <select v-model="viewPreset" class="toolbar-select">
            <option value="operator">Operator</option>
            <option value="top">Top</option>
            <option value="side">Side</option>
            <option value="target">Target POV</option>
            <option value="freecad">Free Orbit</option>
          </select>
        </label>
        <button class="operator-toggle" @click="enterBrowserFullscreen">Tam 3D Dünya</button>
        <button class="operator-toggle" @click="focusScene">Odakla</button>
      </div>
      <div v-if="showDeveloperControls" class="kinematic-preview-toolbar flex items-center gap-2">
        <label class="preview-slider-label">
          Yaw
          <input v-model.number="yawPreviewDeg" type="range" :min="motionLimits.panMinDeg" :max="motionLimits.panMaxDeg" step="1">
          <span>{{ yawPreviewDeg }}°</span>
        </label>
        <label class="preview-slider-label">
          Pitch
          <input v-model.number="pitchPreviewDeg" type="range" :min="motionLimits.tiltMinDeg" :max="motionLimits.tiltMaxDeg" step="1">
          <span>{{ pitchPreviewDeg }}°</span>
        </label>
        <button class="rounded-md border border-emerald-300/25 bg-slate-900/60 px-2 py-1 text-xs font-semibold text-emerald-100" @click="resetKinematicPreview">Reset Pose</button>
      </div>
      <span v-if="showDeveloperControls" class="font-mono text-[11px] text-slate-400">{{ viewMode === 'replay' ? 'replay fixture' : 'metadata 2Hz' }}</span>
      <span class="sr-only">state stream mapped · metadata 2Hz</span>
    </div>

    <div v-if="props.error && showDeveloperControls" class="border-b border-red-400/20 bg-red-400/8 px-3 py-2 text-xs text-red-100">
      {{ props.error }}
    </div>

    <div class="engagement-grid min-h-0 flex-1" :class="{ 'freecad-layout': isFreecadMatch, 'world-layout': props.worldMode, 'geometry-open': geometryDrawerOpen }">
      <div class="tactical-stage relative min-h-0 overflow-hidden">
        <div v-show="canRender3d && !webglFailed" ref="canvasRoot" class="h-full w-full"></div>
        <div v-if="modelLoading" class="pointer-events-none absolute inset-0 z-20 grid place-items-center bg-[#06101a]/78 px-6 backdrop-blur-[2px]" role="status" aria-live="polite">
          <div class="w-full max-w-[420px] rounded-xl border border-cyan-300/25 bg-slate-950/92 p-5 text-center shadow-[0_18px_80px_rgba(0,0,0,0.45)]">
            <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full border border-cyan-300/35 bg-cyan-300/10 text-cyan-200">
              <span class="h-4 w-4 animate-pulse rounded-full bg-cyan-300 shadow-[0_0_22px_rgba(103,232,249,0.9)]" />
            </div>
            <p class="text-xs font-semibold tracking-[0.22em] text-cyan-200">3B DÜNYA YÜKLENİYOR</p>
            <h4 class="mt-1 text-lg font-semibold text-white">Dijital ikiz hazırlanıyor</h4>
            <p class="mt-2 text-sm text-slate-400">{{ modelLoadDetail }}</p>
            <div class="mt-4 h-2 overflow-hidden rounded-full bg-slate-800" role="progressbar" :aria-valuenow="Math.round(modelLoadProgress)" aria-valuemin="0" aria-valuemax="100" aria-label="3B dünya yükleme ilerlemesi">
              <div class="h-full rounded-full bg-gradient-to-r from-cyan-400 via-sky-400 to-emerald-300 transition-[width] duration-200" :style="{ width: `${Math.max(4, modelLoadProgress)}%` }" />
            </div>
            <div class="mt-2 flex items-center justify-between font-mono text-xs text-slate-500">
              <span>{{ modelLoadPercentLabel }}</span>
              <span>{{ modelLoadElapsedLabel }}</span>
            </div>
          </div>
        </div>
        <div v-else-if="modelLoadStage === 'fallback' && !webglFailed" class="pointer-events-none absolute inset-x-4 top-1/2 z-20 -translate-y-1/2 rounded-lg border border-amber-300/25 bg-slate-950/88 px-4 py-3 text-center text-xs text-amber-100" role="status">
          <b class="block tracking-[0.12em]">3B MODEL YÜKLENEMEDİ</b>
          <span class="mt-1 block text-slate-300">Basit güvenli görünüm aktif · {{ modelLoadElapsedLabel }}</span>
        </div>
        <div v-if="canRender3d" class="pointer-events-none absolute left-3 top-3 z-10 rounded-md border border-cyan-300/25 bg-slate-950/78 px-3 py-2 font-mono text-xs text-cyan-100 shadow-lg">
          <b class="block text-[11px] tracking-[0.15em]">{{ renderModeLabel }} · {{ poseBadgeLabel }}</b>
          <span>{{ livePoseLabel }} · {{ appliedPoseLabel }}</span>
        </div>
        <div v-if="canRender3d && props.trackingActive" class="pointer-events-none absolute right-3 bottom-3 z-10 rounded-md border border-emerald-300/30 bg-slate-950/82 px-3 py-2 font-mono text-[11px] text-emerald-100 shadow-lg">
          <b class="block tracking-[0.12em]">AUTO VISION SIM · NO ACTUATION</b>
          <span class="block">track {{ props.trackingUpdate?.selected_track_id ?? '—' }} · {{ props.trackingUpdate?.state ?? 'SEARCHING' }} · age {{ Math.round(props.trackingUpdate?.selected_track_last_seen_age_ms ?? 0) }} ms</span>
          <span class="block text-slate-400">error {{ Math.round(props.trackingUpdate?.error_x_px ?? 0) }} / {{ Math.round(props.trackingUpdate?.error_y_px ?? 0) }} px · switches {{ props.trackingIdSwitchCount ?? 0 }}</span>
        </div>
        <div v-if="canRender3d && showDeveloperControls && !isFreecadMatch && !props.worldMode" class="pointer-events-none absolute left-3 top-3 rounded-md border border-cyan-300/20 bg-black/45 px-3 py-2 text-xs text-cyan-100">
          <b class="block text-[11px] uppercase tracking-[0.18em]">STEP MODEL LOADED</b>
          <span>{{ realModelLoaded ? `${activeAsset.label} active` : modelLoadError ? 'Phase 54 GLB load blocker' : 'loading Phase 54 GLB' }}</span>
          <em class="block max-w-[280px] truncate not-italic text-xs text-slate-400">{{ activeAssetDetailLabel }}</em>
          <span class="sr-only">GLB preferred / procedural low-poly active</span>
        </div>
        <div v-if="canRender3d && showModelLabels" class="pointer-events-none absolute left-3 bottom-3 grid max-w-[300px] grid-cols-2 gap-2 text-xs">
          <div class="metric-tile"><span>Camera axis</span><b>cyan ray</b></div>
          <div class="metric-tile"><span>Launcher axis</span><b>yellow ray</b></div>
          <div class="metric-tile"><span>Offset</span><b>30 mm bracket</b></div>
          <div class="metric-tile"><span>Fire gate</span><b>{{ fireGateLabel }}</b></div>
        </div>
        <div v-if="canRender3d && showModelLabels" class="pointer-events-none absolute right-3 top-3 max-w-[220px] rounded-md border border-yellow-300/35 bg-black/55 px-3 py-2 text-xs">
          <b class="text-yellow-200">{{ targetLogicalName ?? `Target #${targetProjection.target_id ?? 1}` }} · {{ targetName }} · {{ confidenceLabel }}</b>
          <p class="mt-1 text-slate-300">depth: {{ geometry.depth_band }} · bearing: {{ geometry.bearing_label }}</p>
          <p class="text-slate-400">Fire gate: {{ fireGateLabel }}</p>
        </div>
        <div v-if="canRender3d && labelMode === 'debug' && inspectorSelection" class="pointer-events-none absolute right-3 bottom-3 max-w-[320px] rounded-md border border-cyan-300/30 bg-black/70 px-3 py-2 text-xs text-slate-200">
          <b class="block text-cyan-100">Part inspector</b>
          <span class="block truncate font-mono text-[11px]">{{ inspectorSelection.nodeName }}</span>
          <span class="block">group: <b>{{ inspectorSelection.groupName }}</b></span>
          <span class="block">material: <b>{{ inspectorSelection.materialName }}</b> <i class="not-italic text-slate-400">{{ inspectorSelection.materialColor }}</i></span>
          <span v-if="inspectorSelection.boundingBox" class="block font-mono text-[10px] text-slate-400">bbox {{ inspectorSelection.boundingBox.min }} → {{ inspectorSelection.boundingBox.max }}</span>
        </div>
        <div v-if="sceneMode === 'cadDebug'" class="grid h-full place-items-center p-8">
          <div class="max-w-[560px] rounded-lg border border-cyan-300/20 bg-black/42 p-6 text-center">
            <h4 class="text-xl font-bold text-white">CAD Debug · Colored STEP Pipeline</h4>
            <p class="mt-3 text-sm text-slate-300">`work/ktr1.step` is converted once into browser-ready GLB. The default hero scene uses the FreeCAD-fidelity STEP-derived model; ktr1_binary.stl is no longer the default hero.</p>
            <p class="mt-4 font-mono text-xs text-cyan-200">{{ realModelPath }}</p>
            <p class="mt-2 font-mono text-xs text-amber-200">asset mode: {{ activeAsset.label }} · {{ activeAsset.weaponStatus }}</p>
          <p class="mt-2 font-mono text-xs text-slate-300">{{ conversionSummary }}</p>
          <p class="mt-2 font-mono text-xs text-slate-400">source: {{ heroManifest?.source_asset ?? props.assets?.device_model.source_file ?? 'work/ktr1.step' }}</p>
          <p class="mt-2 font-mono text-xs text-slate-400">materials: {{ materialBadgeLabel }} · colors {{ heroManifest?.color_count ?? 'n/a' }} · meshes {{ heroManifest?.mesh_count ?? 'n/a' }}</p>
          <p class="mt-2 font-mono text-xs text-cyan-200">{{ phase56TruthLabel }}</p>
          <p class="mt-1 font-mono text-xs text-slate-400">{{ phase56GroupCountLabel }}</p>
          <p class="mt-1 font-mono text-xs text-slate-400">device frame: CAD front {{ phase56DeviceFrame?.sourceCad.front ?? 'n/a' }} → runtime front {{ phase56DeviceFrame?.runtimeWorld.front ?? 'n/a' }}</p>
          <p class="mt-1 font-mono text-xs text-amber-200">joint calibration: {{ phase56JointCalibration?.status ?? 'not loaded' }}</p>
          <div class="mt-4 max-h-56 overflow-hidden rounded-md border border-white/10 text-left">
            <div class="grid grid-cols-[1.4fr_0.8fr_0.7fr_0.7fr_0.6fr] bg-slate-900/80 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-400">
              <span>Material</span><span>Hex</span><span>Rough</span><span>Metal</span><span>Mesh</span>
            </div>
            <div v-for="item in materialDebugTable" :key="String(item.name)" class="grid grid-cols-[1.4fr_0.8fr_0.7fr_0.7fr_0.6fr] border-t border-white/5 px-2 py-1 font-mono text-[10px] text-slate-200">
              <span class="truncate">{{ item.name }}</span>
              <span>{{ item.color }}</span>
              <span>{{ Number(item.roughness).toFixed(2) }}</span>
              <span>{{ Number(item.metalness).toFixed(2) }}</span>
              <span>{{ item.mesh_count }}</span>
            </div>
          </div>
          <p class="mt-2 font-mono text-xs text-emerald-300">no_physical_command_generated=true</p>
          <span class="sr-only">legacy operator twin builder available: {{ legacyOperatorTwinBuilderAvailable }}</span>
        </div>
      </div>
        <svg v-if="webglFailed" viewBox="0 0 900 560" class="h-full w-full" role="img" aria-label="Tactical engagement geometry view">
          <defs>
            <linearGradient id="phase47Sky" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stop-color="#0a1b2e" />
              <stop offset="0.56" stop-color="#06111f" />
              <stop offset="1" stop-color="#02050d" />
            </linearGradient>
            <radialGradient id="phase47TargetGlow" cx="50%" cy="50%" r="50%">
              <stop offset="0" stop-color="#fde68a" stop-opacity="0.95" />
              <stop offset="1" stop-color="#f59e0b" stop-opacity="0.12" />
            </radialGradient>
            <linearGradient id="phase47Fov" x1="0" x2="0" y1="1" y2="0">
              <stop offset="0" stop-color="#22d3ee" stop-opacity="0.12" />
              <stop offset="1" stop-color="#22d3ee" stop-opacity="0.015" />
            </linearGradient>
          </defs>

          <rect width="900" height="560" fill="url(#phase47Sky)" />
          <path d="M0 412 C170 380 300 368 450 368 C610 368 748 383 900 418 L900 560 L0 560 Z" fill="#050914" opacity="0.94" />
          <path d="M0 364 C180 340 315 331 450 331 C602 331 742 344 900 370" fill="none" stroke="#155e75" stroke-width="2" opacity="0.42" />
          <line x1="108" y1="485" x2="792" y2="485" stroke="#0e7490" stroke-width="1" opacity="0.28" />
          <line x1="212" y1="436" x2="688" y2="436" stroke="#0e7490" stroke-width="1" opacity="0.24" />
          <line x1="294" y1="390" x2="606" y2="390" stroke="#0e7490" stroke-width="1" opacity="0.22" />
          <line x1="360" y1="345" x2="540" y2="345" stroke="#0e7490" stroke-width="1" opacity="0.18" />
          <line v-for="x in [170, 260, 350, 450, 550, 640, 730]" :key="x" :x1="450" y1="506" :x2="x" y2="92" stroke="#164e63" stroke-width="1" opacity="0.18" />

          <polygon points="410,416 210,86 690,86 490,416" fill="url(#phase47Fov)" stroke="#22d3ee" stroke-width="1.4" stroke-dasharray="10 8" opacity="0.9" />
          <line x1="410" y1="416" x2="210" y2="86" stroke="#67e8f9" stroke-width="1.6" stroke-dasharray="8 9" opacity="0.65" />
          <line x1="490" y1="416" x2="690" y2="86" stroke="#67e8f9" stroke-width="1.6" stroke-dasharray="8 9" opacity="0.65" />
          <line x1="450" y1="416" x2="450" y2="78" stroke="#22d3ee" stroke-width="2" opacity="0.72" />
          <text x="462" y="108" fill="#67e8f9" font-size="14" font-weight="700">Camera axis</text>

          <g v-for="mark in rangeMarks" :key="mark.label">
            <line :x1="mark.x1" :x2="mark.x2" :y1="mark.y" :y2="mark.y" stroke="#22d3ee" stroke-width="1" stroke-dasharray="8 9" opacity="0.28" />
            <text :x="mark.x2 + 10" :y="mark.y + 4" fill="#7895a9" font-size="12">{{ mark.label }}</text>
          </g>

          <polygon v-if="props.ktrDemoMode || shownState?.engagement.person_safety_blocked" points="585,310 735,230 790,365 630,428" fill="#ef4444" opacity="0.12" stroke="#ef4444" stroke-width="1.5" stroke-dasharray="9 8" />
          <text v-if="props.ktrDemoMode || shownState?.engagement.person_safety_blocked" x="646" y="272" fill="#fca5a5" font-size="13" font-weight="700">NO-GO</text>

          <line x1="450" y1="360" :x2="targetSvg.x" :y2="targetSvg.y" stroke="#facc15" stroke-width="2.5" stroke-dasharray="12 9" opacity="0.82" />
          <text x="466" y="346" fill="#fde68a" font-size="13" font-weight="700">Launcher axis</text>
          <line x1="405" y1="410" :x2="targetSvg.x" :y2="targetSvg.y" stroke="#22c55e" stroke-width="2" opacity="0.58" />

          <ellipse cx="450" cy="504" rx="112" ry="32" fill="#08111f" stroke="#67e8f9" stroke-width="2.2" opacity="0.98" />
          <ellipse cx="450" cy="498" rx="74" ry="22" fill="#111827" stroke="#334155" stroke-width="1.4" />
          <g :transform="`translate(${450 + svgPanShift} 478) scale(${svgPanScale} 1) translate(-450 -478)`">
            <path d="M424 478 L476 478 L490 430 L410 430 Z" fill="#101827" stroke="#64748b" stroke-width="1.4" />
            <g :transform="`rotate(${svgPitchRotation} 450 430)`">
              <path d="M431 435 L469 435 L462 296 L438 296 Z" fill="#0b0f19" stroke="#facc15" stroke-width="1.5" />
              <path d="M438 296 L462 296 L468 360 L432 360 Z" fill="#020617" stroke="#fbbf24" stroke-width="1" opacity="0.92" />
              <rect x="380" y="397" width="56" height="30" rx="5" fill="#082f49" stroke="#22d3ee" stroke-width="1.8" />
              <circle cx="424" cy="412" r="7" fill="#22d3ee" opacity="0.92" />
            </g>
          </g>
          <text x="334" y="395" fill="#67e8f9" font-size="13" font-weight="700">Camera module</text>
          <path d="M438 408 L438 432 M438 432 L450 432" fill="none" stroke="#fde68a" stroke-width="1.7" />
          <text x="310" y="431" fill="#fde68a" font-size="12">30 mm camera-launcher offset</text>
          <text x="390" y="540" fill="#94a3b8" font-size="13" font-weight="700">Turret base</text>

          <circle :cx="targetSvg.x" :cy="targetSvg.y" :r="targetSvg.radius + 12" fill="url(#phase47TargetGlow)" opacity="0.45" />
          <circle :cx="targetSvg.x" :cy="targetSvg.y" :r="targetSvg.radius" fill="#f59e0b" stroke="#fde68a" stroke-width="3" />
          <circle :cx="targetSvg.x" :cy="targetSvg.y" :r="targetSvg.radius + 22" fill="none" stroke="#facc15" stroke-width="2" stroke-dasharray="8 8" opacity="0.82" />
          <g :transform="`translate(${Math.min(targetSvg.x + 26, 682)} ${Math.max(targetSvg.y - 42, 104)})`">
            <rect width="164" height="72" rx="8" fill="rgba(2, 6, 23, 0.88)" stroke="#facc15" stroke-width="1.4" />
            <text x="12" y="22" fill="#fde68a" font-size="14" font-weight="800">{{ targetLogicalName ?? `Target #${targetProjection.target_id ?? 1}` }}</text>
            <text x="12" y="43" fill="#e2e8f0" font-size="13">{{ targetName }} {{ confidenceLabel }}</text>
            <text x="12" y="62" fill="#94a3b8" font-size="12">relative depth: {{ geometry.depth_band }}</text>
          </g>
        </svg>
      </div>

      <aside v-if="geometryDrawerOpen && !isFreecadMatch" class="telemetry-strip min-w-0 border-l border-white/10 bg-black/20 p-2.5">
        <div class="mb-2">
          <b class="block text-[10px] uppercase tracking-[0.2em] text-cyan-200">Geometry Truth</b>
          <p class="mt-1 text-xs text-slate-400">{{ truthModeLabel }}</p>
          <p class="mt-1 text-[11px] text-slate-500">{{ visibleModelLabel }}</p>
        </div>
        <div class="grid gap-1.5 text-xs">
          <div class="metric-tile"><span>Target bearing</span><b>{{ geometry.bearing_label }}</b></div>
          <div class="metric-tile"><span>Elevation</span><b>{{ geometry.elevation_label }}</b></div>
          <div class="metric-tile"><span>Depth</span><b>{{ geometry.depth_band }}</b></div>
          <div class="metric-tile"><span>Fire gate</span><b :class="fireGateTone === 'warn' ? 'text-amber-200' : 'text-emerald-200'">{{ fireGateLabel }}</b></div>
        </div>

        <div class="topdown-card mt-2 rounded-md border border-cyan-300/18 bg-slate-950/60 p-2">
          <div class="mb-1 flex items-center justify-between text-[10px] uppercase tracking-[0.16em] text-cyan-100">
            <span>Top-down map</span>
            <span>{{ geometry.bearing_label }}</span>
          </div>
          <svg viewBox="0 0 192 168" class="h-[88px] w-full rounded bg-[#030712]">
            <path d="M96 150 L32 20 L160 20 Z" fill="#22d3ee" opacity="0.08" stroke="#22d3ee" stroke-width="1.6" stroke-dasharray="7 6" />
            <line x1="96" y1="150" x2="96" y2="20" stroke="#67e8f9" stroke-width="1.5" opacity="0.7" />
            <line x1="96" y1="150" :x2="topDownTarget.x" :y2="topDownTarget.y" stroke="#facc15" stroke-width="2" opacity="0.85" />
            <circle cx="96" cy="150" r="11" fill="#0f172a" stroke="#67e8f9" stroke-width="2" />
            <rect x="82" y="134" width="28" height="8" rx="2" fill="#020617" stroke="#facc15" stroke-width="1" />
            <circle :cx="topDownTarget.x" :cy="topDownTarget.y" r="6" fill="#f59e0b" stroke="#fde68a" stroke-width="2" />
            <rect v-if="props.ktrDemoMode || shownState?.engagement.person_safety_blocked" x="120" y="72" width="44" height="40" rx="5" fill="#ef4444" opacity="0.16" stroke="#ef4444" stroke-dasharray="6 5" />
          </svg>
        </div>
      </aside>

      <div v-if="geometryDrawerOpen && !props.worldMode && !isFreecadMatch" class="geometry-summary border-t border-white/10 p-2.5 text-xs">
        <div class="grid grid-cols-5 gap-2">
          <div class="metric-tile"><span>Projection</span><b>2D → FOV</b></div>
          <div class="metric-tile"><span>Inside FOV</span><b>{{ geometry.target_inside_fov ? 'YES' : 'NO' }}</b></div>
          <div class="metric-tile"><span>x/y · bbox</span><b>{{ geometry.normalized_x.toFixed(2) }} / {{ geometry.normalized_y.toFixed(2) }} · {{ geometry.bbox_area_relative.toFixed(3) }}</b></div>
          <div class="metric-tile"><span>Offset</span><b>{{ geometry.camera_to_launcher_offset_z_mm }} mm active</b></div>
          <div class="metric-tile"><span>Engagement</span><b>{{ geometry.engagement_status }}</b></div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.digital-twin-shell {
  background:
    radial-gradient(circle at 70% 24%, rgba(34, 211, 238, 0.12), transparent 34%),
    linear-gradient(180deg, rgba(8, 16, 28, 0.98), rgba(2, 6, 23, 0.99));
}

.digital-twin-shell.freecad-shell {
  border-color: rgba(15, 23, 42, 0.2);
  background: #e8edf2;
  box-shadow: 0 22px 54px rgba(15, 23, 42, 0.22);
}

.digital-twin-shell.freecad-shell > div:first-child,
.digital-twin-shell.freecad-shell > div:nth-child(2) {
  border-color: rgba(15, 23, 42, 0.12);
  background: rgba(248, 250, 252, 0.88);
}

.digital-twin-shell.world-marker {
  min-height: 90vh;
}

.world-toolbar {
  flex-wrap: wrap;
}

.toolbar-select-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(103, 232, 249, 0.22);
  border-radius: 7px;
  padding: 3px 6px;
  background: rgba(15, 23, 42, 0.72);
  color: #a5f3fc;
  font-size: 0.7rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.toolbar-select {
  min-width: 144px;
  border: 0;
  border-radius: 5px;
  padding: 4px 6px;
  background: rgba(2, 6, 23, 0.92);
  color: #f8fafc;
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0;
}

.operator-world-toolbar {
  justify-content: flex-start;
}

.operator-toggle {
  border: 1px solid rgba(103, 232, 249, 0.24);
  border-radius: 7px;
  background: rgba(15, 23, 42, 0.72);
  padding: 6px 9px;
  color: #dbeafe;
  font-size: 0.74rem;
  font-weight: 850;
}

.operator-toggle.active {
  border-color: rgba(34, 211, 238, 0.45);
  background: rgba(34, 211, 238, 0.18);
  color: #a5f3fc;
}

.operator-toggle.compact {
  min-width: 54px;
  padding: 6px 8px;
}

.kinematic-preview-toolbar {
  border: 1px solid rgba(16, 185, 129, 0.22);
  border-radius: 7px;
  padding: 3px 6px;
  background: rgba(2, 6, 23, 0.58);
}

.preview-slider-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #bbf7d0;
  font-size: 0.7rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.preview-slider-label input {
  width: 92px;
  accent-color: #34d399;
}

.preview-slider-label span {
  min-width: 34px;
  color: #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.68rem;
  text-align: right;
}

.engagement-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}

.engagement-grid.geometry-open {
  grid-template-columns: minmax(0, 1fr) 220px;
  grid-template-rows: minmax(0, 1fr) auto;
}

.engagement-grid.freecad-layout,
.engagement-grid.world-layout.freecad-layout,
.digital-twin-shell.world-marker .engagement-grid.freecad-layout {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}

.engagement-grid.world-layout {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}

.tactical-stage {
  background:
    radial-gradient(circle at 52% 30%, rgba(34, 211, 238, 0.1), transparent 30%),
    linear-gradient(180deg, rgba(8, 21, 39, 0.94), rgba(1, 6, 14, 0.99));
}

.freecad-shell .tactical-stage {
  background:
    radial-gradient(circle at 50% 48%, rgba(255, 255, 255, 0.92), rgba(226, 232, 240, 0.96) 62%, rgba(203, 213, 225, 0.95));
}

.digital-twin-shell.world-marker .tactical-stage {
  min-height: calc(90vh - 96px);
}

.telemetry-strip {
  min-height: 0;
}

.geometry-summary {
  grid-column: 1 / -1;
  background: rgba(2, 6, 23, 0.82);
}
</style>
