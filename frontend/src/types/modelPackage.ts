export interface ModelPackageMetadata {
  model_id: string
  model_name: string
  version: string
  created_by: string
  created_at: string
  model_format: string
  task_type: string
  input_size: number
  expected_classes: string[]
  class_id_to_name: Record<string, string>
  recommended_conf: number
  recommended_iou: number
  recommended_imgsz: number
  recommended_device: string
  notes: string | null
  safety_note: string
  provided_by: string
  production_ready: boolean
}

export interface ModelPackageThresholds {
  default_conf: number
  default_iou: number
  max_det: number
  per_class_thresholds: Record<string, number>
  recommended_runtime_preset: string
}

export interface ClassMappingReviewItem {
  class_id: number
  class_name: string
  mapped_role: string
  required: boolean
  status: string
}

export interface ModelPackageValidationResult {
  model_id: string
  version: string
  status: string
  valid: boolean
  can_activate: boolean
  production_ready: boolean
  class_mapping_status: string
  checks: Record<string, boolean>
  class_mapping: ClassMappingReviewItem[]
  warnings: string[]
  errors: string[]
  no_physical_command_generated: boolean
}

export interface ModelPackageRecord {
  model_id: string
  version: string
  package_name: string
  package_path: string
  model_file: string | null
  checksum_sha256: string | null
  metadata: ModelPackageMetadata | null
  thresholds: ModelPackageThresholds | null
  status: string
  active: boolean
  validation: ModelPackageValidationResult | null
  last_test_result: Record<string, unknown> | null
  last_benchmark_result: Record<string, unknown> | null
  imported_at: number
  activated_at: number | null
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface ModelPackageTestResult {
  model_id: string
  accepted: boolean
  source: string
  detections: Array<Record<string, unknown>>
  latency_ms: number
  warnings: string[]
  errors: string[]
  advisory_only: boolean
  no_physical_command_generated: boolean
  timestamp: number
}

export interface ModelPackageBenchmarkResult {
  model_id: string
  accepted: boolean
  estimated_fps: number
  estimated_latency_ms: number
  device: string
  warnings: string[]
  advisory_only: boolean
  no_physical_command_generated: boolean
  timestamp: number
}
