import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  fetchMotionSettings,
  fetchMotionStatus,
  goToMotion,
  homeMotion,
  jogMotion,
  startScan,
  stopMotion,
  stopScan,
  trackDryRun,
  updateMotionSettings,
} from '../api/motion'
import {
  selectTrackingTarget,
  startTracking as apiStartTracking,
  stopTracking as apiStopTracking,
  fetchTrackingPriority,
  fetchTrackingStatus,
  updateTrackingConfig as apiUpdateTrackingConfig,
} from '../api/tracking'
import type {
  MotionCommandResponse,
  MotionGoToRequest,
  MotionJogRequest,
  MotionSettings,
  MotionState,
  MotionTrackDryRunRequest,
} from '../types/motion'
import type {
  TargetPriorityStatus,
  TrackingConfigUpdate,
  TrackingStatus,
  TrackingUpdate,
} from '../types/tracking'

const defaultState: MotionState = {
  motion_state: 'IDLE',
  pan_position_deg: 0,
  tilt_position_deg: 0,
  pan_target_deg: 0,
  tilt_target_deg: 0,
  pan_position_steps: 0,
  tilt_position_steps: 0,
  pan_error_deg: 0,
  tilt_error_deg: 0,
  pan_limit_left: false,
  pan_limit_right: false,
  tilt_limit_up: false,
  tilt_limit_down: false,
  homed: false,
  homing_phase: 'IDLE',
  limit_reason_code: null,
  physical_pan_deg: null,
  physical_tilt_deg: null,
  driver_enabled: false,
  estop_state: false,
  dry_run: true,
  last_command: null,
  last_error: 'backend_disconnected',
  updated_at: 0,
}

const defaultSettings: MotionSettings = {
  pan_min_deg: -60,
  pan_max_deg: 60,
  tilt_min_deg: -20,
  tilt_max_deg: 45,
  pan_steps_per_degree: 10,
  tilt_steps_per_degree: 10,
  pan_max_speed_deg_s: 20,
  tilt_max_speed_deg_s: 15,
  pan_accel_deg_s2: 50,
  tilt_accel_deg_s2: 40,
  jog_step_deg: 1,
  deadband_px: 12,
  tracking_gain_x: 0.05,
  tracking_gain_y: 0.05,
  backlash_compensation_enabled: false,
  soft_limits_enabled: false,
  scan_enabled: false,
  scan_min_deg: -45,
  scan_max_deg: 45,
  scan_speed_deg_s: 10,
}

const defaultTrackingStatus: TrackingStatus = {
  active: false,
  state: 'IDLE',
  target_count: 0,
  lost_count: 0,
  total_frames: 0,
  pid_kp_x: 1200,
  pid_ki_x: 0,
  pid_kd_x: 120,
  pid_kp_y: 800,
  pid_ki_y: 0,
  pid_kd_y: 130,
  config_revision: 0,
  config_applied_at: 0,
  smoothing_alpha: 0.9,
  command_rate_hz: 30,
  max_speed: 1000,
  max_speed_x: 1000,
  max_speed_y: 500,
  bbox_lock_enabled: true,
  momentum_lock_enabled: true,
  momentum_velocity_threshold_px_s: 12,
  aim_offset_x_px: 0,
  aim_offset_y_px: -26.0,
  invert_x: false,
  invert_y: true,
  lead_enabled: false,
  lead_latency_multiplier: 1,
  lead_max_horizon_ms: 120,
  last_update: null,
  last_fire_result: null,
  multi_target_tracker: {
    tracker_kind: 'nearest_neighbor_last_observation',
    active_track_count: 0,
    id_switch_count: 0,
    rejected_stale_frames: 0,
    last_frame_id: null,
    last_timestamp_ms: null,
    tracks: [],
    updated_at: 0,
  },
  controller_mode: 'GENERAL',
  updated_at: 0,
}

const defaultTargetPriority: TargetPriorityStatus = {
  selected_track_id: null,
  ranked_candidates: [],
  excluded_track_ids: [],
  updated_at: 0,
}

export const useMotionStore = defineStore('motion', () => {
  const state = ref<MotionState>(defaultState)
  const settings = ref<MotionSettings>({ ...defaultSettings })
  const latestCommand = ref<MotionCommandResponse | null>(null)
  const commandLog = ref<MotionCommandResponse[]>([])
  const error = ref<string | null>(null)

  // ---- Tracking state ----
  const trackingStatus = ref<TrackingStatus>({ ...defaultTrackingStatus })
  const trackingUpdate = ref<TrackingUpdate | null>(null)
  const targetPriority = ref<TargetPriorityStatus>({ ...defaultTargetPriority })
  const trackingActive = computed(() => trackingStatus.value.active)

  const isDryRun = computed(() => state.value.dry_run)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextState, nextSettings] = await Promise.all([fetchMotionStatus(), fetchMotionSettings()])
      state.value = nextState
      settings.value = nextSettings
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Motion refresh failed'
    }
  }

  function applyStatus(nextState: MotionState): void {
    state.value = nextState
  }

  function applyCommand(response: MotionCommandResponse): void {
    latestCommand.value = response
    state.value = response.state
    commandLog.value = [response, ...commandLog.value.filter((item) => item.command_id !== response.command_id)].slice(0, 40)
  }

  function applySettings(nextSettings: MotionSettings): void {
    settings.value = nextSettings
  }

  // ---- Tracking WebSocket handlers ----
  function applyTrackingStatus(status: TrackingStatus): void {
    trackingStatus.value = status
  }

  function applyTrackingUpdate(update: TrackingUpdate): void {
    trackingUpdate.value = update
  }

  async function saveSettings(nextSettings: MotionSettings): Promise<void> {
    settings.value = await updateMotionSettings(nextSettings)
  }

  async function jog(request: MotionJogRequest): Promise<void> {
    applyCommand(await jogMotion(request))
  }

  async function goTo(request: MotionGoToRequest): Promise<void> {
    applyCommand(await goToMotion(request))
  }

  async function home(): Promise<void> {
    applyCommand(await homeMotion())
  }

  async function stop(): Promise<void> {
    applyCommand(await stopMotion())
  }

  async function scanStart(): Promise<void> {
    applyCommand(await startScan())
  }

  async function scanStop(): Promise<void> {
    applyCommand(await stopScan())
  }

  async function trackingPreview(request: MotionTrackDryRunRequest): Promise<void> {
    applyCommand(await trackDryRun(request))
  }

  // ---- Tracking API actions ----
  async function startTracking(): Promise<void> {
    const status = await apiStartTracking()
    trackingStatus.value = status
  }

  async function stopTracking(): Promise<void> {
    const status = await apiStopTracking()
    trackingStatus.value = status
    trackingUpdate.value = null
  }

  async function refreshTrackingStatus(): Promise<void> {
    const [status, priority] = await Promise.all([fetchTrackingStatus(), fetchTrackingPriority()])
    trackingStatus.value = status
    targetPriority.value = priority
  }

  async function updateTrackingConfig(config: TrackingConfigUpdate): Promise<void> {
    trackingStatus.value = await apiUpdateTrackingConfig(config)
  }

  async function selectTarget(payload: { x: number, y: number, detection_id?: number, frame_id?: number }): Promise<void> {
    trackingStatus.value = await selectTrackingTarget(payload)
  }

  return {
    state,
    settings,
    latestCommand,
    commandLog,
    error,
    isDryRun,
    // Tracking
    trackingStatus,
    trackingUpdate,
    trackingActive,
    targetPriority,
    // Actions
    refresh,
    applyStatus,
    applyCommand,
    applySettings,
    applyTrackingStatus,
    applyTrackingUpdate,
    saveSettings,
    jog,
    goTo,
    home,
    stop,
    scanStart,
    scanStop,
    trackingPreview,
    startTracking,
    stopTracking,
    refreshTrackingStatus,
    updateTrackingConfig,
    selectTarget,
  }
})
