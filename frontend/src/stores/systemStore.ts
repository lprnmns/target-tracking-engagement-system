import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { TelemetrySocket } from '../api/websocket'
import type { PicoConnectionEvent, PinValidationResult } from '../types/pico'
import { useCalibrationStore } from './calibrationStore'
import { useColorStore } from './colorStore'
import { useSerialStore } from './serialStore'
import { useSelfTestStore } from './selfTestStore'
import { useReportsStore } from './reportsStore'
import { useDeviceRuntimeStore } from './deviceRuntimeStore'
import { useHardwareStore } from './hardwareStore'
import type { SerialLogEntry, SerialStatus } from '../types/serial'
import { useVisionStore } from './visionStore'
import { useDecisionStore } from './decisionStore'
import { useDataLabStore } from './dataLabStore'
import { useDemoStore } from './demoStore'
import { useMotionStore } from './motionStore'
import { useMissionStore } from './missionStore'
import type { DecisionState } from '../types/decision'
import type { CalibrationStatus } from '../types/calibration'
import type { ColorClassifierConfig, ColorDecisionResult, MaskPreviewResult } from '../types/color'
import type { MotionCommandResponse, MotionSettings, MotionState } from '../types/motion'
import type { HardwareStatus, HardwareTelemetry } from '../types/hardware'
import type { CameraRuntimeStatus, VisionRuntimeStatus } from '../types/deviceRuntime'
import type { MissionSnapshot } from '../types/mission'
import type {
  CameraStatus,
  ConnectionStatus,
  PicoTelemetry,
  RecentEvent,
  SafetyState,
  SystemState,
  VisionEvent,
  VisionStatus,
  VisionTargetsPayload,
  WebSocketEnvelope,
} from '../types/system'

const defaultSystemState: SystemState = {
  mode: 'DISARMED',
  armed: false,
  fire_policy: 'NO_FIRE',
  dry_run: true,
  hardware_enabled: false,
  ready: false,
  uptime_s: 0,
  reason: 'Backend disconnected.',
  blocking_reasons: ['backend_disconnected'],
}

const defaultSafetyState: SafetyState = {
  decision: 'NO_FIRE',
  gates: {
    armed: false,
    estop_released: false,
    pico_heartbeat: false,
    track_stable: false,
    target_enemy: false,
    balloon_detected: false,
    range_valid: false,
    aim_point_valid: false,
    zone_valid: true,
    operator_or_auto_permission: false,
    hardware_enabled: false,
    dry_run: true,
    motion_soft_limits: true,
    motion_estop: true,
    motion_fault_clear: true,
    motion_driver: false,
    motion_dry_run: true,
    person_safety_clear: true,
  },
  reason: 'Backend disconnected.',
  blocking_reasons: ['backend_disconnected'],
}

const defaultPicoTelemetry: PicoTelemetry = {
  connection_status: 'DISCONNECTED',
  port: null,
  baudrate: 115200,
  heartbeat_age_ms: null,
  firmware_version: 'offline',
  estop_state: 'UNKNOWN',
  driver_enabled: false,
  pan_position_steps: 0,
  tilt_position_steps: 0,
  pan_limit_left: false,
  pan_limit_right: false,
  tilt_limit_up: false,
  tilt_limit_down: false,
  last_error: 'backend_disconnected',
  updated_at: 0,
}

const defaultPicoConnection: PicoConnectionEvent = {
  connection_status: 'DISCONNECTED',
  port: null,
  baudrate: 115200,
  reason: 'Backend disconnected.',
}

const defaultPinValidation: PinValidationResult = {
  valid: false,
  can_apply: false,
  system_mode: 'DISARMED',
  system_armed: false,
  issues: [
    {
      level: 'WARNING',
      code: 'BACKEND_DISCONNECTED',
      message: 'Pin validation is unavailable until backend reconnects.',
      pin_name: null,
      function: null,
    },
  ],
}

function legacyDemoReadinessBlockerCount(payload: {
  blockers?: unknown[]
  reasons?: unknown[]
  summary?: string
}): number {
  if (Array.isArray(payload.blockers)) return payload.blockers.length
  if (Array.isArray(payload.reasons)) return payload.reasons.length
  const match = payload.summary?.match(/blockers=(\d+)/i)
  return match ? Number(match[1]) : 0
}

function hasSplitDemoReadinessContract(payload: {
  release_demo_blockers?: string[]
  competition_blockers?: string[]
  dataset_blockers?: string[]
}): boolean {
  return Array.isArray(payload.release_demo_blockers)
    || Array.isArray(payload.competition_blockers)
    || Array.isArray(payload.dataset_blockers)
}

function isLegacyDemoReadinessEvent(event: WebSocketEnvelope): boolean {
  if (event.type !== 'demo.readiness_checked') return false
  const payload = event.payload as {
    blockers?: unknown[]
    reasons?: unknown[]
    summary?: string
    release_demo_blockers?: string[]
    competition_blockers?: string[]
    dataset_blockers?: string[]
  }
  return !hasSplitDemoReadinessContract(payload)
    && (Array.isArray(payload.blockers)
      || Array.isArray(payload.reasons)
      || /blockers=\d+/i.test(payload.summary ?? ''))
}

let fireAudioContext: AudioContext | null = null
let lastFireSoundAt = 0

function playFireEffect(): void {
  const now = performance.now()
  if (now - lastFireSoundAt < 250) return
  lastFireSoundAt = now
  try {
    fireAudioContext ??= new AudioContext()
    const ctx = fireAudioContext
    const start = ctx.currentTime
    const duration = 0.18
    const oscillator = ctx.createOscillator()
    const gain = ctx.createGain()
    const filter = ctx.createBiquadFilter()

    oscillator.type = 'sawtooth'
    oscillator.frequency.setValueAtTime(1180, start)
    oscillator.frequency.exponentialRampToValueAtTime(180, start + duration)
    filter.type = 'bandpass'
    filter.frequency.setValueAtTime(1600, start)
    filter.Q.setValueAtTime(7, start)
    gain.gain.setValueAtTime(0.0001, start)
    gain.gain.exponentialRampToValueAtTime(0.38, start + 0.012)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration)

    oscillator.connect(filter)
    filter.connect(gain)
    gain.connect(ctx.destination)
    oscillator.start(start)
    oscillator.stop(start + duration)
  } catch {
    // Browser audio may be locked until the first user gesture.
  }
}

function websocketUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_WS_URL as string | undefined
  if (configured) {
    return configured
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  if (window.location.port && window.location.port !== '5173') {
    return `${protocol}//${window.location.host}/ws`
  }
  return `${protocol}//${window.location.hostname}:8000/ws`
}

function eventSummary(event: WebSocketEnvelope): string {
  const explicitSummary = (event.payload as { summary?: unknown })?.summary
  if (typeof explicitSummary === 'string' && explicitSummary.length > 0) return explicitSummary
  if (event.type === 'system.state') {
    const payload = event.payload as SystemState
    return `${payload.mode} / ${payload.fire_policy}`
  }
  if (event.type === 'decision.gates') {
    const payload = event.payload as SafetyState
    return `${payload.decision}: ${payload.blocking_reasons[0] ?? 'clear'}`
  }
  if (event.type === 'pico.telemetry') {
    const payload = event.payload as PicoTelemetry
    return `${payload.connection_status}, heartbeat_age=${payload.heartbeat_age_ms === null ? 'n/a' : `${payload.heartbeat_age_ms}ms`}`
  }
  if (event.type === 'pico.connection') {
    const payload = event.payload as PicoConnectionEvent
    return `${payload.connection_status}: ${payload.reason}`
  }
  if (event.type === 'pico.pin_validation') {
    const payload = event.payload as PinValidationResult
    return `${payload.valid ? 'valid' : 'invalid'}, issues=${payload.issues.length}`
  }
  if (event.type.startsWith('pico.readonly_')) {
    const payload = event.payload as {
      summary?: string
      ports?: unknown[]
      connected?: boolean
      telemetry_frames?: number
      status?: string
      no_physical_command_generated?: boolean
    }
    if (payload.summary) return payload.summary
    if (event.type === 'pico.readonly_ports_discovered') return `Pico read-only ports discovered; ports=${payload.ports?.length ?? 0}; no_physical_command_generated=true.`
    if (event.type === 'pico.readonly_connected') return `Pico read-only connection evaluated; connected=${payload.connected ?? false}; no_physical_command_generated=true.`
    if (event.type === 'pico.readonly_disconnected') return 'Pico read-only disconnected; no_physical_command_generated=true.'
    if (event.type === 'pico.readonly_status_checked') return `Pico read-only status checked; connected=${payload.connected ?? false}; no_physical_command_generated=true.`
    if (event.type === 'pico.readonly_telemetry_received') return `Pico read-only telemetry received; frames=${payload.telemetry_frames ?? 0}; no_physical_command_generated=true.`
    if (event.type === 'pico.readonly_evidence_recorded') return `Pico read-only evidence recorded; status=${payload.status ?? 'not_available'}; no_physical_command_generated=true.`
    return 'Pico read-only event; no_physical_command_generated=true.'
  }
  if (event.type.startsWith('pico.real_')) {
    const payload = event.payload as { summary?: string; connected?: boolean; telemetry_frames?: number; status?: string }
    if (payload.summary) return payload.summary
    if (event.type === 'pico.real_port_discovered') return 'Pico real port discovered; no_physical_command_generated=true.'
    if (event.type === 'pico.real_rxonly_connected') return `Pico real RX-only connection evaluated; connected=${payload.connected ?? false}; no_physical_command_generated=true.`
    if (event.type === 'pico.real_telemetry_sampled') return `Pico real telemetry sampled; frames=${payload.telemetry_frames ?? 0}; no_physical_command_generated=true.`
    if (event.type === 'pico.real_rxonly_evidence_captured') return `Pico real RX-only evidence captured; status=${payload.status ?? 'not_available'}; no_physical_command_generated=true.`
    if (event.type === 'pico.real_rxonly_disconnected') return 'Pico real RX-only disconnected; no_physical_command_generated=true.'
    return 'Pico real RX-only event; no_physical_command_generated=true.'
  }
  if (event.type === 'vision.targets') {
    const payload = event.payload as VisionTargetsPayload
    return `${payload.targets.length} target event`
  }
  if (event.type === 'serial.status') {
    const payload = event.payload as SerialStatus
    return `${payload.connection_state}, pending=${payload.pending_ack_count}`
  }
  if (event.type.startsWith('serial.')) {
    const payload = event.payload as SerialLogEntry
    return `${payload.kind}: ${payload.error ?? payload.raw ?? JSON.stringify(payload.message)}`
  }
  if (event.type === 'motion.status') {
    const payload = event.payload as MotionState
    return `${payload.motion_state}, pan=${payload.pan_position_deg}deg, tilt=${payload.tilt_position_deg}deg`
  }
  if (event.type.startsWith('motion.command')) {
    const payload = event.payload as MotionCommandResponse
    return `${payload.command_type}: ${payload.accepted ? 'accepted dry-run' : payload.blocking_reasons[0] ?? 'rejected'}`
  }
  if (event.type === 'motion.settings_updated') {
    const payload = event.payload as MotionSettings
    return `limits pan ${payload.pan_min_deg}..${payload.pan_max_deg}, tilt ${payload.tilt_min_deg}..${payload.tilt_max_deg}`
  }
  if (event.type.startsWith('calibration.')) {
    const payload = event.payload as CalibrationStatus
    return `${payload.config?.calibration_status ?? 'updated'}: ${payload.warnings?.[0] ?? 'no warning'}`
  }
  if (event.type === 'color.config_updated') {
    const payload = event.payload as ColorClassifierConfig
    return `${payload.color_space}, balloon_mask=${payload.balloon_mask_enabled}`
  }
  if (event.type === 'color.classification' || event.type === 'color.warning') {
    const payload = event.payload as ColorDecisionResult
    return `${payload.decision}, confidence=${payload.confidence}, mask=${payload.balloon_mask_applied}`
  }
  if (event.type === 'color.mask_preview') {
    const payload = event.payload as MaskPreviewResult
    return `mask_applied=${payload.balloon_mask_applied}`
  }
  if (event.type.startsWith('calibration.direction_')) {
    const payload = event.payload as { summary?: string; required_camera_motion?: string; axis_swap_suspected?: boolean }
    if (payload.summary) return payload.summary
    if (event.type === 'calibration.direction_simulated') return `Direction semantics simulated; required_motion=${payload.required_camera_motion ?? 'unknown'}; no_physical_command_generated=true.`
    if (event.type === 'calibration.direction_observation_recorded') return `Direction observation recorded; axis_swap_suspected=${payload.axis_swap_suspected ?? false}; no_physical_command_generated=true.`
    if (event.type === 'calibration.direction_profile_saved') return 'Direction calibration profile saved; no_physical_command_generated=true.'
    if (event.type === 'calibration.direction_profile_reset') return 'Direction calibration profile reset; no_physical_command_generated=true.'
  }
  if (event.type.startsWith('report.')) {
    const payload = event.payload as { export_id?: string; kind?: string; status?: string }
    return `${payload.kind ?? 'report'} ${payload.status ?? 'updated'}: ${payload.export_id ?? 'no export id'}`
  }
  if (event.type.startsWith('release.package') || event.type === 'release.zip_generated') {
    const payload = event.payload as { summary?: string; files_count?: number; zip_path?: string }
    if (payload.summary) return payload.summary
    if (event.type === 'release.package_generated') return `Portable release package generated; files=${payload.files_count ?? 0}; no_physical_command_generated=true.`
    if (event.type === 'release.zip_generated') return `Portable release zip generated; path=${payload.zip_path ?? 'not_available'}; no_physical_command_generated=true.`
    if (event.type === 'release.package_validated') return 'Portable release package validated; no_physical_command_generated=true.'
  }
  if (event.type.startsWith('model.')) {
    const payload = event.payload as {
      summary?: string
      package_kind?: string
      production_ready?: boolean
      competition_ready?: boolean
      no_physical_command_generated?: boolean
      model_id?: string
    }
    if (payload.summary) return payload.summary
    if (event.type === 'model.package_validation_passed') return 'Model package schema validation passed.'
    if (event.type === 'model.activated') {
      return payload.package_kind === 'production'
        ? 'Production model activated.'
        : 'Test adapter activated; production readiness remains blocked.'
    }
    if (event.type === 'model.test_completed') return 'Model dry-run test completed; no physical command generated.'
    if (event.type === 'model.runtime_recommended_applied') return 'Recommended vision runtime settings applied; safety state unchanged.'
    if (event.type === 'model.deactivated') return 'Active model deactivated; vision falls back to no production model.'
    return `${event.type}: ${payload.model_id ?? payload.package_kind ?? 'model event'}`
  }
  if (event.type.startsWith('data_lab.')) {
    const payload = event.payload as {
      summary?: string
      source?: string
      sessions_count?: number
      detection_events_count?: number
      source_session_id?: string | null
      detections_replayed?: number
      review_status?: string
      dataset_ready_for_training?: boolean
      no_physical_command_generated?: boolean
    }
    if (payload.summary) return payload.summary
    if (event.type === 'data_lab.session_recorded') return `Data Lab session recorded; source=${payload.source ?? 'unknown'}; no physical command generated.`
    if (event.type === 'data_lab.export_completed') return `Data Lab evidence export completed; sessions=${payload.sessions_count ?? 0}; detection_events=${payload.detection_events_count ?? 0}; no physical command generated.`
    if (event.type === 'data_lab.replay_completed') return `Data Lab replay completed; session=${payload.source_session_id ?? 'none'}; detections=${payload.detections_replayed ?? 0}; no physical command generated.`
    if (event.type === 'data_lab.annotation_reviewed') return `Annotation candidate reviewed; status=${payload.review_status ?? 'unknown'}; no physical command generated.`
    if (event.type === 'data_lab.dataset_health_checked') return `Dataset health checked; dataset_ready_for_training=${payload.dataset_ready_for_training ?? false}.`
    if (event.type === 'data_lab.legacy_perception_exported') return payload.summary ?? 'Legacy perception evidence exported; files=4; no_physical_command_generated=true.'
    return 'Data Lab event; no physical command generated.'
  }
  if (event.type.startsWith('demo.')) {
    const payload = event.payload as {
      summary?: string
      release_demo_ready?: boolean
      competition_ready?: boolean
      events?: unknown[]
      release_demo_blockers?: string[]
      competition_blockers?: string[]
      dataset_blockers?: string[]
      blockers?: unknown[]
      reasons?: unknown[]
      files?: unknown[]
      report_export_id?: string
    }
    if (event.type === 'demo.timeline_generated') return `Demo evidence timeline generated; steps=${payload.events?.length ?? 0}; no physical command generated.`
    if (event.type === 'demo.run_completed') return `End-to-end demo run completed; release_demo_ready=${payload.release_demo_ready ?? false}; competition_ready=false.`
    if (event.type === 'demo.evidence_index_generated') return `Demo evidence index generated; files=${payload.files?.length ?? 0}; no_physical_command_generated=true.`
    if (event.type === 'demo.operator_script_generated') return 'Demo operator script generated; no_physical_command_generated=true.'
    if (event.type === 'demo.jury_package_generated') return `Jury demo package generated; export_id=${payload.report_export_id ?? 'not_available'}; no_physical_command_generated=true.`
    if (event.type === 'demo.readiness_checked') {
      if (hasSplitDemoReadinessContract(payload)) {
        return `Demo readiness checked; release_demo_ready=${payload.release_demo_ready ?? false}; release_blockers=${payload.release_demo_blockers?.length ?? 0}; competition_blockers=${payload.competition_blockers?.length ?? 0}; dataset_blockers=${payload.dataset_blockers?.length ?? 0}; no_physical_command_generated=true.`
      }
      return `Legacy demo readiness event; old combined blockers=${legacyDemoReadinessBlockerCount(payload)}; see newer split readiness events for release/competition/dataset semantics; no_physical_command_generated=true.`
    }
    if (payload.summary) return payload.summary
    return 'Demo event; no physical command generated.'
  }
  if (event.type === 'hardware.status') {
    const payload = event.payload as HardwareStatus
    return `${payload.transport_mode}, ${payload.connection_state}, commands=${payload.physical_command_enabled ? 'enabled' : 'disabled'}`
  }
  if (event.type === 'camera.runtime_status') {
    const payload = event.payload as CameraRuntimeStatus
    return `${payload.profile.source_type}, ${payload.actual_width}x${payload.actual_height}@${payload.actual_fps}`
  }
  if (event.type === 'vision.runtime_status') {
    const payload = event.payload as VisionRuntimeStatus
    return `${payload.profile.inference_adapter}, conf=${payload.profile.conf}, iou=${payload.profile.iou}`
  }
  if (event.type === 'vision.legacy_presets_loaded') {
    const payload = event.payload as { presets_count?: number; summary?: string }
    return payload.summary ?? `Legacy perception presets loaded; presets=${payload.presets_count ?? 0}; no_physical_command_generated=true.`
  }
  if (event.type === 'vision.real_camera_status_checked') {
    const payload = event.payload as { status?: string; summary?: string }
    return payload.summary ?? `Real camera evidence status checked; status=${payload.status ?? 'unknown'}; no_physical_command_generated=true.`
  }
  if (event.type === 'vision.real_camera_evidence_recorded') {
    const payload = event.payload as { status?: string; summary?: string }
    return payload.summary ?? `Real camera evidence recorded; status=${payload.status ?? 'unknown'}; no_physical_command_generated=true.`
  }
  if (
    event.type === 'vision.camera_host_diagnosed' ||
    event.type === 'vision.camera_inventory_parsed' ||
    event.type === 'vision.camera_selected' ||
    event.type === 'vision.camera_device_inventory_recorded' ||
    event.type === 'vision.real_camera_capture_attempted' ||
    event.type === 'vision.real_camera_capture_blocked' ||
    event.type === 'vision.usb_camera_capture_attempted' ||
    event.type === 'vision.usb_camera_capture_completed' ||
    event.type === 'vision.usb_camera_capture_failed'
  ) {
    const payload = event.payload as { summary?: string; camera_acceptance_status?: string; blocker_reason?: string }
    return payload.summary ?? `${event.type}; status=${payload.camera_acceptance_status ?? payload.blocker_reason ?? 'unknown'}; no_physical_command_generated=true.`
  }
  if (event.type.startsWith('devices.')) return event.type.replace('devices.', 'Device ')
  if (event.type.startsWith('camera.profile_')) return event.type.replace('camera.', 'Camera ')
  if (event.type.startsWith('vision.settings_') || event.type.startsWith('vision.benchmark') || event.type === 'vision.model_reload') return event.type.replace('vision.', 'Vision ')
  if (event.type === 'hardware.telemetry' || event.type === 'hardware.telemetry_received') {
    const payload = event.payload as HardwareTelemetry
    return `${payload.device ?? 'device unknown'}, safe=${payload.safe_state ?? 'unknown'}`
  }
  return 'telemetry update'
}

export const useSystemStore = defineStore('system', () => {
  const connectionStatus = ref<ConnectionStatus>('disconnected')
  const systemState = ref<SystemState>(defaultSystemState)
  const safetyState = ref<SafetyState>(defaultSafetyState)
  const picoTelemetry = ref<PicoTelemetry>(defaultPicoTelemetry)
  const picoConnection = ref<PicoConnectionEvent>(defaultPicoConnection)
  const pinValidation = ref<PinValidationResult>(defaultPinValidation)
  const latestEvents = ref<RecentEvent[]>([])
  const lastError = ref<string | null>(null)
  let socket: TelemetrySocket | null = null

  const isOffline = computed(() => connectionStatus.value !== 'connected')

  function connect(): void {
    if (socket !== null) {
      return
    }
    connectionStatus.value = 'connecting'
    socket = new TelemetrySocket({
      url: websocketUrl(),
      onOpen: () => {
        connectionStatus.value = 'connected'
        lastError.value = null
      },
      onClose: () => {
        connectionStatus.value = 'disconnected'
      },
      onError: () => {
        lastError.value = 'WebSocket error'
      },
      onMessage: handleEnvelope,
    })
    socket.connect()
  }

  function disconnect(): void {
    socket?.disconnect()
    socket = null
    connectionStatus.value = 'disconnected'
  }

  function handleEnvelope(event: WebSocketEnvelope): void {
    const serialStore = useSerialStore()
    const visionStore = useVisionStore()
    const decisionStore = useDecisionStore()
    const dataLabStore = useDataLabStore()
    const demoStore = useDemoStore()
    const selfTestStore = useSelfTestStore()
    const reportsStore = useReportsStore()
    const deviceRuntimeStore = useDeviceRuntimeStore()
    const hardwareStore = useHardwareStore()
    const motionStore = useMotionStore()
    const missionStore = useMissionStore()
    const calibrationStore = useCalibrationStore()
    const colorStore = useColorStore()
    if (event.type === 'system.state') {
      systemState.value = event.payload as SystemState
    }
    if (event.type === 'decision.gates') {
      safetyState.value = event.payload as SafetyState
    }
    if (event.type === 'pico.telemetry') {
      picoTelemetry.value = event.payload as PicoTelemetry
    }
    if (event.type === 'pico.connection') {
      picoConnection.value = event.payload as PicoConnectionEvent
    }
    if (event.type === 'pico.pin_validation') {
      pinValidation.value = event.payload as PinValidationResult
    }
    if (event.type === 'vision.status') visionStore.applyVisionStatus(event.payload as VisionStatus)
    if (event.type === 'vision.frame' || event.type === 'vision.detections') {
      visionStore.applyVisionEvent(event.payload as VisionEvent)
    }
    if (event.type === 'vision.warning') {
      const payload = event.payload as { warning: string }
      visionStore.applyWarning(payload.warning)
    }
    if (event.type === 'camera.status') visionStore.applyCameraStatus(event.payload as CameraStatus)
    if (event.type === 'serial.status') {
      serialStore.applyStatus(event.payload as SerialStatus)
    }
    if (event.type.startsWith('serial.') && event.type !== 'serial.status') {
      serialStore.upsertLog(event.payload as SerialLogEntry)
    }
    if (event.type === 'decision.updated') {
      decisionStore.applyDecision(event.payload as DecisionState)
    }
    if (event.type === 'motion.status') {
      motionStore.applyStatus(event.payload as MotionState)
    }
    if (event.type === 'motion.settings_updated') {
      motionStore.applySettings(event.payload as MotionSettings)
    }
    if (event.type.startsWith('motion.command') || event.type === 'motion.stopped') {
      motionStore.applyCommand(event.payload as MotionCommandResponse)
    }
    if (event.type === 'tracking.status') {
      motionStore.applyTrackingStatus(event.payload as any)
    }
    if (event.type === 'tracking.update') {
      motionStore.applyTrackingUpdate(event.payload as any)
    }
    if (event.type === 'tracking.fire_pulse') {
      playFireEffect()
    }
    if (event.type === 'mission.status' || event.type === 'mission.updated' || event.type === 'mission.reset') {
      missionStore.applySnapshot(event.payload as MissionSnapshot)
    }
    if (event.type === 'calibration.status' || event.type === 'calibration.updated' || event.type === 'calibration.warning') {
      calibrationStore.applyStatus(event.payload as CalibrationStatus)
    }
    if (event.type === 'color.config_updated') {
      colorStore.applyConfig(event.payload as ColorClassifierConfig)
    }
    if (event.type === 'color.classification' || event.type === 'color.warning') {
      colorStore.applyDecision(event.payload as ColorDecisionResult)
    }
    if (event.type === 'color.mask_preview') {
      colorStore.applyMaskPreview(event.payload as MaskPreviewResult)
    }
    if (event.type.startsWith('safety.')) {
      decisionStore.addEvent(event.type, event.payload)
    }
    if (event.type.startsWith('model.') || event.type.startsWith('session.') || event.type.startsWith('dataset.') || event.type.startsWith('data_lab.') || event.type.startsWith('replay.') || event.type.startsWith('annotation.')) {
      dataLabStore.applyEvent(event.type, event.payload)
    }
    if (event.type.startsWith('demo.')) {
      demoStore.applyEvent(event.type, event.payload)
    }
    if (event.type.startsWith('self_test.')) {
      selfTestStore.applyEvent(event.type, event.payload)
    }
    if (event.type.startsWith('report.')) {
      reportsStore.applyEvent(event.type, event.payload)
    }
    if (event.type === 'hardware.status') {
      hardwareStore.applyStatus(event.payload as HardwareStatus)
    }
    if (event.type === 'hardware.telemetry' || event.type === 'hardware.telemetry_received') {
      hardwareStore.applyTelemetry(event.payload as HardwareTelemetry)
    }
    if (event.type === 'camera.runtime_status') {
      deviceRuntimeStore.applyCameraStatus(event.payload as CameraRuntimeStatus)
    }
    if (event.type === 'vision.runtime_status') {
      deviceRuntimeStore.applyVisionStatus(event.payload as VisionRuntimeStatus)
    }

    latestEvents.value = [
      {
        type: event.type,
        ts: event.ts,
        seq: event.seq,
        summary: eventSummary(event),
        payload: event.payload,
        legacy_format: isLegacyDemoReadinessEvent(event),
        format_warning: isLegacyDemoReadinessEvent(event) ? 'OLD READINESS CONTRACT' : undefined,
      },
      ...latestEvents.value,
    ].slice(0, 200)
  }

  return {
    connectionStatus,
    systemState,
    safetyState,
    picoTelemetry,
    picoConnection,
    pinValidation,
    latestEvents,
    lastError,
    isOffline,
    connect,
    disconnect,
  }
})
