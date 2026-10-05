export interface Stage3RangeObservation {
  observation_id: string
  class_name: 'f16' | 'helicopter' | 'ballistic_missile' | 'mini_micro_uav'
  distance_m: number
  bbox_height_px: number
  capture_id: string
  note: string | null
  recorded_at: number
}

export interface Stage3RangeClassFit {
  class_name: string
  scale_px_m: number
  sample_count: number
  calibration_distances_m: number[]
  mean_abs_error_m: number
  uncertainty_m: number
}

export interface Stage3RangeCalibrationStatus {
  valid: boolean
  reason_codes: string[]
  body_model_id: string | null
  body_model_hash: string | null
  calibration_hash: string | null
  observations: Stage3RangeObservation[]
  fits: Stage3RangeClassFit[]
  validated_at: number | null
  updated_at: number
}
