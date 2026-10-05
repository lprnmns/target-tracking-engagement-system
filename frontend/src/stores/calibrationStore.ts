import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  addCalibrationPoint,
  computeCalibration,
  deleteCalibrationPoint,
  estimateFov,
  fetchCalibrationStatus,
  fetchDirectionStatus,
  recordDirectionObservation,
  resetCalibration,
  resetDirectionProfile,
  saveDirectionProfile,
  simulateDirection,
  updateCalibrationConfig,
} from '../api/calibration'
import type {
  CalibrationPointCreate,
  CalibrationStatus,
  CameraCalibrationConfig,
  DirectionCalibrationProfile,
  DirectionCalibrationStatus,
  DirectionObservationRequest,
  DirectionObservationResult,
  DirectionSimulationRequest,
  DirectionSimulationResult,
  FovEstimateRequest,
  FovEstimateResponse,
} from '../types/calibration'

const defaultConfig: CameraCalibrationConfig = {
  camera_id: 'mock_camera_0',
  camera_name: 'Mock Camera',
  lens_profile: 'unknown',
  resolution_width: 640,
  resolution_height: 360,
  fps: 15,
  camera_height_cm: 60,
  target_height_cm: 130,
  table_height_cm: 60,
  hfov_deg: 45,
  vfov_deg: null,
  distortion_enabled: false,
  homography_enabled: false,
  calibration_status: 'not_started',
  updated_at: 0,
}

const defaultStatus: CalibrationStatus = {
  config: defaultConfig,
  calibration_points: [],
  homography_matrix: null,
  reprojection_error_px: null,
  inlier_count: 0,
  calibration_hash: null,
  homography_direction: 'world_plane_to_image_px',
  valid: false,
  warnings: ['backend_disconnected'],
  updated_at: 0,
}

const defaultDirectionProfile: DirectionCalibrationProfile = {
  profile_id: 'default_direction_profile',
  created_at: 0,
  updated_at: 0,
  source: 'manual_simulation',
  image_x_positive: 'right',
  image_y_positive: 'down',
  camera_mirror_x: false,
  camera_mirror_y: false,
  axis_swap: false,
  pan_positive_label: 'camera_right',
  tilt_positive_label: 'camera_up',
  x_axis_multiplier: 1,
  y_axis_multiplier: 1,
  target_error_convention: 'target_center_minus_frame_center',
  expected_pan_response: 'target_moves_opposite_to_camera_motion',
  expected_tilt_response: 'target_moves_opposite_to_camera_motion',
  advisory_only: true,
  physical_command_enabled: false,
  no_physical_command_generated: true,
  notes: 'Direction semantics profile is advisory only.',
}

const defaultDirectionStatus: DirectionCalibrationStatus = {
  profile: defaultDirectionProfile,
  latest_simulation: null,
  latest_observation: null,
  observation_count: 0,
  advisory_only: true,
  physical_command_enabled: false,
  no_physical_command_generated: true,
}

export const useCalibrationStore = defineStore('calibration', () => {
  const status = ref<CalibrationStatus>(defaultStatus)
  const fovEstimate = ref<FovEstimateResponse | null>(null)
  const directionStatus = ref<DirectionCalibrationStatus>(defaultDirectionStatus)
  const directionSimulation = ref<DirectionSimulationResult | null>(null)
  const directionObservation = ref<DirectionObservationResult | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      status.value = await fetchCalibrationStatus()
      directionStatus.value = await fetchDirectionStatus()
      error.value = null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Calibration refresh failed'
    }
  }

  function applyStatus(nextStatus: CalibrationStatus): void {
    status.value = nextStatus
  }

  async function saveConfig(config: CameraCalibrationConfig): Promise<void> {
    const updated = await updateCalibrationConfig(config)
    status.value = { ...status.value, config: updated }
  }

  async function addPoint(point: CalibrationPointCreate): Promise<void> {
    status.value = await addCalibrationPoint(point)
  }

  async function deletePoint(pointId: string): Promise<void> {
    status.value = await deleteCalibrationPoint(pointId)
  }

  async function compute(): Promise<void> {
    status.value = await computeCalibration()
  }

  async function reset(): Promise<void> {
    status.value = await resetCalibration()
  }

  async function estimate(request: FovEstimateRequest): Promise<void> {
    fovEstimate.value = await estimateFov(request)
  }

  async function simulate(request: DirectionSimulationRequest): Promise<void> {
    directionSimulation.value = await simulateDirection(request)
    directionStatus.value = await fetchDirectionStatus()
  }

  async function recordObservation(request: DirectionObservationRequest): Promise<void> {
    directionObservation.value = await recordDirectionObservation(request)
    directionStatus.value = await fetchDirectionStatus()
  }

  async function saveDirection(): Promise<void> {
    const profile = await saveDirectionProfile()
    directionStatus.value = { ...directionStatus.value, profile }
  }

  async function resetDirection(): Promise<void> {
    directionStatus.value = await resetDirectionProfile()
    directionSimulation.value = null
    directionObservation.value = null
  }

  return { status, fovEstimate, directionStatus, directionSimulation, directionObservation, error, refresh, applyStatus, saveConfig, addPoint, deletePoint, compute, reset, estimate, simulate, recordObservation, saveDirection, resetDirection }
})
